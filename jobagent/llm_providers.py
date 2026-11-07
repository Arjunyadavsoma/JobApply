"""Phase 20 — LLM provider fallback chain (beyond Groq).

Why: even with multi-key rotation (Phase 19K) a single provider can become
unavailable — every key cooling, the provider down, or no Groq key at all.
With a fallback chain configured, the same call is retried against the next
OpenAI-compatible provider (OpenRouter, OpenAI, Together, Fireworks, Gemini,
DeepSeek, Mistral, or a local Ollama / LM Studio / vLLM server) so the
pipeline keeps working. Each fallback provider gets its OWN key pool with the
full Phase 19K rotation semantics (round-robin, 429 cooldown, 401/403
disable) and its own circuit breaker — one provider's outage never affects
another.

Configuration (both optional, combined; env names win over config entries):

1. ``LLM_FALLBACK_PROVIDERS`` in .env — comma/semicolon separated preset
   names, e.g. ``LLM_FALLBACK_PROVIDERS=openrouter,together``. Per-provider
   model override: ``<NAME>_MODEL`` (e.g. ``OPENROUTER_MODEL=...``).
2. ``llm.fallback_providers`` in config.yaml — full custom entries::

       llm:
         fallback_providers:
           - name: myvllm                    # any label
             base_url: http://gpu-box:8000/v1
             model: qwen2.5-32b-instruct
             # api_key_envs: [MY_VLLM_KEYS, MY_VLLM_KEY]   # optional env vars
             # vault_provider: other                          # optional vault provider
             # keyless: true                                  # local server, no key

Keys per provider come from (dedup by secret, env wins):
  * ``<NAME>_API_KEYS`` (multi) and ``<NAME>_API_KEY`` (single) in .env
  * non-revoked vault credentials with that provider name
  * keyless providers (ollama/lmstudio/…) use one pseudo-key

The chain applies ONLY when ``llm.backend: groq`` — the primary provider is
tried first with its full rotation; fallbacks are a safety net, not a
replacement. Nothing changes for users who configure no fallbacks.

Secrets never leave this module except into the OpenAI client — status
output is masked, ids are hashes (Phase 19K rules).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

from jobagent.config import cfg

log = logging.getLogger("jobagent.llm_providers")


# ---------------------------------------------------------------- presets

@dataclass(frozen=True)
class ProviderSpec:
    """One OpenAI-compatible fallback provider."""
    name: str
    base_url: str
    model: str
    fallback_model: str | None = None
    env_multi: str | None = None       # e.g. OPENROUTER_API_KEYS
    env_single: str | None = None      # e.g. OPENROUTER_API_KEY
    vault_provider: str | None = None  # credential-vault provider name
    keyless: bool = False              # local server: no key, one pseudo-key
    docs_url: str = ""

    def env_model_var(self) -> str:
        return f"{self.name.upper()}_MODEL"


# Known OpenAI-compatible endpoints. base_url + a sensible default model per
# provider; users override the model via <NAME>_MODEL or a config entry.
PROVIDER_PRESETS: dict[str, dict] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "docs_url": "https://openrouter.ai/keys",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "docs_url": "https://platform.openai.com/api-keys",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "docs_url": "https://api.together.ai/settings/api-keys",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "docs_url": "https://fireworks.ai/account/api-keys",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "docs_url": "https://aistudio.google.com/apikey",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "docs_url": "https://platform.deepseek.com/api_keys",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "docs_url": "https://console.mistral.ai/api-keys",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "keyless": True,
        "docs_url": "https://ollama.com",
    },
    "lmstudio": {
        "base_url": "http://localhost:1234/v1",
        "model": "",                    # whatever model is loaded — must be set
        "keyless": True,
        "docs_url": "https://lmstudio.ai",
    },
}


def _env_key_names(name: str) -> tuple[str | None, str | None]:
    """(multi, single) env var names for a provider: OPENROUTER_API_KEYS / _KEY."""
    if name in ("ollama", "lmstudio"):
        return None, None
    return f"{name.upper()}_API_KEYS", f"{name.upper()}_API_KEY"


def _preset_spec(name: str) -> ProviderSpec | None:
    preset = PROVIDER_PRESETS.get(name.lower())
    if not preset:
        return None
    env_multi, env_single = _env_key_names(name.lower())
    # per-provider model override: <NAME>_MODEL in the environment
    model = os.environ.get(f"{name.upper()}_MODEL", "").strip() or preset["model"]
    return ProviderSpec(
        name=name.lower(),
        base_url=preset["base_url"],
        model=model,
        env_multi=env_multi,
        env_single=env_single,
        vault_provider=name.lower(),      # vault providers match preset names
        keyless=bool(preset.get("keyless")),
        docs_url=preset.get("docs_url", ""),
    )


def _config_spec(entry: dict) -> ProviderSpec | None:
    """Custom provider from a config.yaml entry. Invalid entries are skipped
    (logged) — one bad line must not take the whole chain down."""
    if not isinstance(entry, dict) or not entry.get("name") or not entry.get("base_url"):
        log.warning("llm.fallback_providers entry without name/base_url skipped: %r",
                    {k: entry.get(k) for k in ("name", "base_url")} if isinstance(entry, dict) else entry)
        return None
    name = str(entry["name"]).strip().lower()
    envs = [str(e) for e in (entry.get("api_key_envs") or []) if str(e).strip()]
    multi = envs[0] if envs else None
    single = envs[1] if len(envs) > 1 else None
    return ProviderSpec(
        name=name,
        base_url=str(entry["base_url"]).strip(),
        model=str(entry.get("model") or "").strip(),
        fallback_model=(str(entry["fallback_model"]).strip() or None) if entry.get("fallback_model") else None,
        env_multi=multi,
        env_single=single,
        vault_provider=(str(entry["vault_provider"]).strip() or None) if entry.get("vault_provider") else None,
        keyless=bool(entry.get("keyless")),
        docs_url=str(entry.get("docs_url") or ""),
    )


# ---------------------------------------------------------------- chain

def load_chain() -> list[ProviderSpec]:
    """Ordered fallback chain. Env names first (presets), then config entries;
    duplicates by name are dropped (env wins). Empty when nothing configured."""
    names: list[str] = []
    raw = os.environ.get("LLM_FALLBACK_PROVIDERS", "")
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            names.append(chunk)

    specs: dict[str, ProviderSpec] = {}
    order: list[str] = []
    for n in names:
        spec = _preset_spec(n)
        if spec is None:
            log.warning("LLM_FALLBACK_PROVIDERS: unknown provider %r skipped "
                        "(known: %s, or add a custom entry in llm.fallback_providers)",
                        n, ", ".join(sorted(PROVIDER_PRESETS)))
            continue
        if spec.name not in specs:
            specs[spec.name] = spec
            order.append(spec.name)

    for entry in (cfg("llm.fallback_providers", []) or []):
        spec = _config_spec(entry)
        if spec is None or spec.name in specs:
            continue
        specs[spec.name] = spec
        order.append(spec.name)

    return [specs[n] for n in order]


# ---------------------------------------------------------------- key pools

def _load_pool(spec: ProviderSpec):
    """Key pool for one provider: env keys + vault keys (+ keyless pseudo-key).

    Reuses the Phase 19K pool classes — rotation, cooldown and disable
    semantics are identical for every provider. Vault problems never break
    env keys.
    """
    from jobagent import groq_keys

    handles = []
    seen: set[str] = set()

    def add(secret: str, source: str, vault_name: str | None = None) -> None:
        if not secret or secret in seen:
            return
        seen.add(secret)
        handles.append(groq_keys.GroqKeyHandle(secret=secret, source=source,
                                               vault_name=vault_name))

    if spec.env_multi:
        for k in groq_keys.parse_key_list(os.environ.get(spec.env_multi, "")):
            add(k, f"env:{spec.env_multi}")
    if spec.env_single:
        s = os.environ.get(spec.env_single, "").strip()
        if s:
            add(s, f"env:{spec.env_single}")

    if spec.vault_provider:
        try:
            from jobagent import credentials
            for row in credentials.list_credentials():
                if row["provider"] != spec.vault_provider or row["revoked"]:
                    continue
                try:
                    secret = credentials.get_secret(row["name"])
                except credentials.VaultError:
                    continue  # locked / undecryptable row: skip, never crash
                add(secret, f"vault:{row['name']}", vault_name=row["name"])
        except Exception as exc:  # noqa: BLE001 — vault is optional at runtime
            log.debug("vault unavailable for %s key pool: %s",
                      spec.name, type(exc).__name__)

    if not handles and spec.keyless:
        add(f"{spec.name}:local-no-key-required", "keyless:local")

    return groq_keys.GroqKeyPool(handles)


# One chain + one pool per provider, process-wide.
_CHAIN: list[ProviderSpec] | None = None
_POOLS: dict[str, object] = {}
_LOCK = threading.RLock()


def chain() -> list[ProviderSpec]:
    """Cached fallback chain (reload_providers() refreshes it)."""
    global _CHAIN
    with _LOCK:
        if _CHAIN is None:
            _CHAIN = load_chain()
        return list(_CHAIN)


def pool_for(spec: ProviderSpec):
    """Per-provider key-pool singleton."""
    with _LOCK:
        if spec.name not in _POOLS:
            _POOLS[spec.name] = _load_pool(spec)
        return _POOLS[spec.name]


def reload_providers() -> list[ProviderSpec]:
    """Re-read env + config + vault (called after credential CRUD so changes
    apply live, without a restart)."""
    global _CHAIN, _POOLS
    with _LOCK:
        _CHAIN = load_chain()
        _POOLS = {}
        return list(_CHAIN)


def reset_providers() -> dict:
    """Operator action: reload sources and re-arm every key in every pool."""
    with _LOCK:
        ch = reload_providers()
        armed = 0
        for spec in ch:
            armed += pool_for(spec).reset()
        return {"ok": True, "providers": len(ch), "keys": armed,
                "status": chain_status()}


# ---------------------------------------------------------------- clients

_clients: dict[tuple[str, str], object] = {}
_clients_lock = threading.Lock()


def client_for(spec: ProviderSpec, key):
    """Cached OpenAI client for (provider, key). max_retries=0 so rotation
    sees 429s immediately (Phase 19K rule); transient errors are retried by
    the shared resilience layer."""
    import openai

    with _clients_lock:
        client = _clients.get((spec.name, key.id))
        if client is None:
            client = openai.OpenAI(api_key=key.secret, base_url=spec.base_url,
                                   max_retries=0, timeout=600)
            _clients[(spec.name, key.id)] = client
        return client


def model_for(spec: ProviderSpec) -> str:
    """Model to use on this provider, with an honest error when unset."""
    if spec.model:
        return spec.model
    raise ValueError(
        f"{spec.name} has no model configured - set model: in its "
        f"llm.fallback_providers entry or {spec.env_model_var()} in .env.")


# ---------------------------------------------------------------- status

def chain_status() -> dict:
    """Masked, JSON-safe chain status for /api/llm/providers and diagnostics."""
    from jobagent.llm_resilience import breaker_states

    with _LOCK:
        out = []
        for spec in chain():
            pool = pool_for(spec)
            st = pool.status()
            out.append({
                "name": spec.name,
                "base_url": spec.base_url,
                "model": spec.model,
                "keyless": spec.keyless,
                "docs_url": spec.docs_url,
                "breaker": breaker_states().get(spec.name, {"state": "closed", "failures": 0}),
                **{k: v for k, v in st.items() if k != "keys"},
                "keys": st["keys"],
            })
        return {"configured": len(out), "providers": out}
