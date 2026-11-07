"""Phase 20 — LLM provider fallback chain. Fully offline.

Covers:
- chain loading: env presets, per-provider model override, config custom
  entries, env-over-config precedence, unknown-name skipping, empty default
- per-provider key pools: env keys (multi/single, dedup), vault keys,
  wrong-provider exclusion, keyless pseudo-key, empty pool
- fallback call path: same rotation semantics on a non-groq provider
  (429 cooldown, 401 disable, all-cooling honest error), universal
  max_tokens parameter, per-provider breaker isolation
- structured(): falls back when groq has no keys / all cooling; usage
  tracked with the honest backend label (groq->openrouter); budget errors
  are NOT fallen back; no chain configured = original error unchanged;
  chain exhausted = combined error naming every provider
- API: GET /api/llm/providers (masked), POST /api/llm/providers/reset,
  credential CRUD hot-reloads fallback pools, /api/health reports the chain
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent import groq_keys
from jobagent import llm
from jobagent import llm_providers
from jobagent.models import MatchResult


# ---------------------------------------------------------------- helpers

class ScriptedCompletions:
    """Per-key fake chat.completions: pops scripted results in order."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("unexpected extra API call")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(content="", finish="stop", pt=100, ct=50, model="meta-llama/llama-3.3-70b-instruct"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                 finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
        model=model,
    )


def _good():
    return json.dumps({"score": 82, "verdict": "apply", "reasons": ["python"],
                       "missing_requirements": []})


def _rate_limit(retry_after=None):
    import openai
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(429, request=req, headers=headers)
    return openai.RateLimitError("rate limited", response=resp, body=None)


def _auth_error(status=401):
    import openai
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    cls = openai.AuthenticationError if status == 401 else openai.PermissionDeniedError
    return cls("bad key", response=resp, body=None)


def _pool(*secrets, source="env:OPENROUTER_API_KEYS"):
    return groq_keys.GroqKeyPool(
        [groq_keys.GroqKeyHandle(secret=s, source=source) for s in secrets])


def _spec(name="openrouter", model="meta-llama/llama-3.3-70b-instruct", **kw):
    return llm_providers.ProviderSpec(
        name=name, base_url=f"https://{name}.example/v1", model=model,
        env_multi=f"{name.upper()}_API_KEYS", env_single=f"{name.upper()}_API_KEY",
        vault_provider=name, **kw)


@pytest.fixture(autouse=True)
def _close_breakers():
    """Never leave a breaker open for other tests."""
    from jobagent import llm_resilience
    yield
    for name in ("groq", "openrouter", "together", "openai", "gemini", "ollama"):
        llm_resilience.breaker_for(name).record_success()


@pytest.fixture(autouse=True)
def _fast_clock(env, monkeypatch):
    """Deterministic clock whose sleep() also advances it, so every bounded
    wait terminates in zero real time. Installed AFTER env() reloaded the
    modules, so the rebinding is visible to the code under test."""
    ticks = {"t": 1000.0}

    def _now() -> float:
        return ticks["t"]

    def _sleep(seconds: float) -> None:
        ticks["t"] += seconds

    monkeypatch.setattr(groq_keys, "_monotonic", _now)
    monkeypatch.setattr(groq_keys, "_sleep", _sleep)
    return SimpleNamespace(advance=lambda s: ticks.__setitem__("t", ticks["t"] + s))


def _install_chain(monkeypatch, specs, pools=None):
    """Pin the chain + per-provider pools; return handle for assertions."""
    monkeypatch.setattr(llm_providers, "_CHAIN", list(specs))
    monkeypatch.setattr(llm_providers, "_POOLS", dict(pools or {}))


@pytest.fixture()
def fallback(monkeypatch):
    """Scripted multi-key fallback provider ('openrouter' by default).

    Usage: out = fallback({"sk_a": [...], "sk_b": [...]}) — each list is that
    key's scripted responses; llm._via_fallback_provider / structured() then
    run against the fake clients with zero network."""
    def install(scripts, name="openrouter"):
        spec = _spec(name=name)
        pool = _pool(*scripts.keys())
        _install_chain(monkeypatch, [spec], {name: pool})
        fakes = {s: ScriptedCompletions(script) for s, script in scripts.items()}
        clients = {groq_keys._short_id(s): SimpleNamespace(
            chat=SimpleNamespace(completions=fakes[s])) for s in scripts}
        monkeypatch.setattr(llm_providers, "client_for",
                            lambda spec, key: clients[key.id])
        return SimpleNamespace(spec=spec, pool=pool, fakes=fakes)
    return install


# ---------------------------------------------------------------- chain loading

def test_chain_empty_by_default(env, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    assert llm_providers.load_chain() == []


def test_chain_from_env_presets(env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter, together")
    chain = llm_providers.load_chain()
    assert [s.name for s in chain] == ["openrouter", "together"]
    assert chain[0].base_url == "https://openrouter.ai/api/v1"
    assert chain[0].model                        # every preset has a default model
    assert chain[0].env_single == "OPENROUTER_API_KEY"
    assert chain[0].vault_provider == "openrouter"


def test_chain_model_override_per_provider(env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    chain = llm_providers.load_chain()
    assert chain[0].model == "openai/gpt-4o-mini"


def test_chain_env_wins_over_config_entry(env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "env-model")
    monkeypatch.setattr(llm_providers, "cfg",
                        lambda p, d=None: [{"name": "openrouter", "base_url":
                                            "https://x.example/v1", "model": "cfg-model"}]
                        if p == "llm.fallback_providers" else d)
    chain = llm_providers.load_chain()
    assert len(chain) == 1                       # dedup by name: env wins
    assert chain[0].model == "env-model"


def test_chain_custom_config_entry(env, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.setattr(llm_providers, "cfg",
                        lambda p, d=None: [
                            {"name": "myvllm", "base_url": "http://gpu-box:8000/v1",
                             "model": "qwen2.5-32b-instruct",
                             "api_key_envs": ["MY_VLLM_KEYS", "MY_VLLM_KEY"],
                             "keyless": True},
                            {"name": "broken"},   # no base_url → skipped
                        ] if p == "llm.fallback_providers" else d)
    chain = llm_providers.load_chain()
    assert [s.name for s in chain] == ["myvllm"]
    assert chain[0].base_url == "http://gpu-box:8000/v1"
    assert chain[0].env_multi == "MY_VLLM_KEYS" and chain[0].env_single == "MY_VLLM_KEY"
    assert chain[0].keyless is True


def test_chain_unknown_provider_skipped(env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "nosuchprovider,openrouter")
    chain = llm_providers.load_chain()
    assert [s.name for s in chain] == ["openrouter"]


def test_chain_keyless_presets_have_no_env_vars(env, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "ollama")
    chain = llm_providers.load_chain()
    assert chain[0].keyless is True
    assert chain[0].env_single is None and chain[0].env_multi is None


# ---------------------------------------------------------------- key pools

def test_provider_pool_env_keys_and_dedup(env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEYS", "sk-or-a, sk-or-b")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-a")       # duplicate
    pool = llm_providers._load_pool(_spec())
    assert len(pool) == 2
    assert [h.secret for h in pool.keys()] == ["sk-or-a", "sk-or-b"]
    assert pool.keys()[0].source == "env:OPENROUTER_API_KEYS"


def test_provider_pool_vault_keys(env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from jobagent import credentials
    credentials.add_credential("or-1", "openrouter", "sk-or-vault-1")
    credentials.add_credential("g-1", "groq", "gsk_not_here")   # wrong provider
    pool = llm_providers._load_pool(_spec())
    assert [h.secret for h in pool.keys()] == ["sk-or-vault-1"]
    assert pool.keys()[0].source == "vault:or-1"


def test_provider_pool_keyless_local(env, monkeypatch):
    spec = _spec(name="ollama", keyless=True)
    spec = llm_providers.ProviderSpec(
        name="ollama", base_url="http://localhost:11434/v1", model="llama3.1",
        keyless=True)
    pool = llm_providers._load_pool(spec)
    assert len(pool) == 1
    assert pool.keys()[0].source == "keyless:local"


def test_provider_pool_empty_without_keys(env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert len(llm_providers._load_pool(_spec())) == 0


def test_pool_for_is_singleton_per_provider(env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-one")
    _install_chain(monkeypatch, [])
    spec = _spec()
    assert llm_providers.pool_for(spec) is llm_providers.pool_for(spec)


# ---------------------------------------------------------------- fallback call path

def test_fallback_rotates_on_429(fallback):
    out = fallback({"sk_a": [_rate_limit(retry_after=60)],
                    "sk_b": [_resp(_good())]})
    result = llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    states = {h.secret: h.state for h in out.pool.keys()}
    assert states["sk_a"] == "cooling" and states["sk_b"] == "active"


def test_fallback_disables_on_401(fallback):
    out = fallback({"sk_a": [_auth_error(401)],
                    "sk_b": [_resp(_good())]})
    result = llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    assert {h.secret: h.state for h in out.pool.keys()}["sk_a"] == "disabled"


def test_fallback_all_rate_limited_honest_error(fallback):
    out = fallback({"sk_a": [_rate_limit(retry_after=60)],
                    "sk_b": [_rate_limit(retry_after=60)]})
    with pytest.raises(llm.LLMError) as e:
        llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    assert "rate-limited" in str(e.value)
    assert "2 cooling" in str(e.value)


def test_fallback_uses_universal_max_tokens_param(fallback):
    out = fallback({"sk_a": [_resp(_good())]})
    llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    call = out.fakes["sk_a"].calls[0]
    assert call["max_tokens"] == 1000                     # universal parameter
    assert "max_completion_tokens" not in call
    assert "reasoning_effort" not in call                 # unknown params not sent


def test_fallback_no_keys_clear_error(fallback):
    out = fallback({"sk_zz": [_resp(_good())]})
    empty = _pool()
    from jobagent import llm_providers as lp
    lp._POOLS[out.spec.name] = empty
    with pytest.raises(llm.LLMError) as e:
        llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    assert "No openrouter API key configured" in str(e.value)


def test_fallback_provider_message_mentions_provider(fallback):
    out = fallback({"sk_a": [_auth_error(401)], "sk_b": [_auth_error(403)]})
    with pytest.raises(llm.LLMError) as e:
        llm._via_fallback_provider(out.spec, MatchResult, "s", "c", "low", 1000, [])
    assert "Openrouter keys were rejected" in str(e.value)


# ---------------------------------------------------------------- structured() chain

def _no_groq_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool.load())


def test_structured_falls_back_when_groq_has_no_keys(env, monkeypatch, fallback):
    _no_groq_keys(monkeypatch)
    out = fallback({"sk_a": [_resp(_good())]})
    monkeypatch.setattr(llm, "_groq_model_name", lambda: "openai/gpt-oss-120b")

    result = llm.structured(MatchResult, system="s", content="c", effort="low",
                            max_tokens=1000, task="test")
    assert result.score == 82
    assert len(out.fakes["sk_a"].calls) == 1              # served by openrouter


def test_structured_fallback_records_honest_backend(env, monkeypatch, fallback):
    from jobagent import db
    _no_groq_keys(monkeypatch)
    fallback({"sk_a": [_resp(_good())]})
    monkeypatch.setattr(llm, "_groq_model_name", lambda: "openai/gpt-oss-120b")

    llm.structured(MatchResult, system="s", content="c", effort="low",
                   max_tokens=1000, task="test")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT backend, ok FROM ai_usage ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "groq->openrouter"                   # who really served it
    assert row[1] == 1


def test_structured_falls_back_when_groq_all_cooling(env, monkeypatch, fallback):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_hot")
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool.load())
    groq_pool = groq_keys.pool()
    for h in groq_pool.keys():
        groq_pool.report_rate_limited(h, 900)             # every key cooling
    monkeypatch.setattr(llm, "_client_for", lambda key: pytest.fail(
        "must not touch the groq client when every key is cooling"))
    monkeypatch.setattr(llm, "_groq_model_name", lambda: "openai/gpt-oss-120b")

    out = fallback({"sk_a": [_resp(_good())]})
    result = llm.structured(MatchResult, system="s", content="c", effort="low",
                            max_tokens=1000, task="test")
    assert result.score == 82
    assert len(out.fakes["sk_a"].calls) == 1


def test_structured_no_chain_original_error_unchanged(env, monkeypatch):
    _no_groq_keys(monkeypatch)
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    _install_chain(monkeypatch, [])
    with pytest.raises(llm.LLMError) as e:
        llm.structured(MatchResult, system="s", content="c", effort="low",
                       max_tokens=1000, task="test")
    # byte-identical to the Phase 19K message
    assert str(e.value).startswith("No Groq API key configured")
    assert "https://console.groq.com/keys" in str(e.value)


def test_structured_chain_exhausted_combined_error(env, monkeypatch):
    _no_groq_keys(monkeypatch)
    or_out = _chain_two_providers(monkeypatch)
    with pytest.raises(llm.LLMError) as e:
        llm.structured(MatchResult, system="s", content="c", effort="low",
                       max_tokens=1000, task="test")
    msg = str(e.value)
    assert msg.startswith("All LLM providers failed.")
    assert "groq:" in msg and "openrouter:" in msg and "together:" in msg


def _chain_two_providers(monkeypatch):
    """openrouter (all keys 429) then together (all keys 401): both fail."""
    or_spec, tg_spec = _spec(name="openrouter"), _spec(name="together")
    or_pool = _pool("sk_a", source="env:OPENROUTER_API_KEYS")
    tg_pool = _pool("tg_a", source="env:TOGETHER_API_KEYS")
    _install_chain(monkeypatch, [or_spec, tg_spec],
                   {"openrouter": or_pool, "together": tg_pool})
    fakes = {"sk_a": ScriptedCompletions([_rate_limit(retry_after=60)]),
             "tg_a": ScriptedCompletions([_auth_error(401)])}
    clients = {groq_keys._short_id(s): SimpleNamespace(
        chat=SimpleNamespace(completions=fakes[s])) for s in fakes}
    monkeypatch.setattr(llm_providers, "client_for",
                        lambda spec, key: clients[key.id])
    return SimpleNamespace(fakes=fakes)


def test_structured_budget_error_never_falls_back(env, monkeypatch, fallback):
    from jobagent import db
    _no_groq_keys(monkeypatch)
    out = fallback({"sk_a": [_resp(_good())]})
    db.track_ai_usage("test", "groq", "m", ok=True)        # 1 call used today
    real_cfg = llm.cfg
    monkeypatch.setattr(llm, "cfg",
                        lambda p, d=None: 1 if p == "llm.max_requests_per_day"
                        else real_cfg(p, d))
    with pytest.raises(llm.LLMError) as e:
        llm.structured(MatchResult, system="s", content="c", effort="low",
                       max_tokens=1000, task="test")
    assert "Daily AI request limit" in str(e.value)
    assert out.fakes["sk_a"].calls == []                   # fallback untouched


def test_structured_second_provider_used_when_first_fails(env, monkeypatch):
    _no_groq_keys(monkeypatch)
    monkeypatch.setattr(llm, "_groq_model_name", lambda: "openai/gpt-oss-120b")
    or_spec, tg_spec = _spec(name="openrouter"), _spec(name="together")
    or_pool = _pool("sk_a", source="env:OPENROUTER_API_KEYS")
    tg_pool = _pool("tg_a", source="env:TOGETHER_API_KEYS")
    _install_chain(monkeypatch, [or_spec, tg_spec],
                   {"openrouter": or_pool, "together": tg_pool})
    fakes = {"sk_a": ScriptedCompletions([_rate_limit(retry_after=60)]),
             "tg_a": ScriptedCompletions([_resp(_good())])}
    clients = {groq_keys._short_id(s): SimpleNamespace(
        chat=SimpleNamespace(completions=fakes[s])) for s in fakes}
    monkeypatch.setattr(llm_providers, "client_for",
                        lambda spec, key: clients[key.id])

    result = llm.structured(MatchResult, system="s", content="c", effort="low",
                            max_tokens=1000, task="test")
    assert result.score == 82
    assert len(fakes["tg_a"].calls) == 1                   # together served it
    from jobagent import db
    with db.connect() as conn:
        row = conn.execute(
            "SELECT backend FROM ai_usage ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "groq->together"


# ---------------------------------------------------------------- breaker isolation

def test_fallback_breaker_does_not_affect_groq(fallback):
    from jobagent import llm_resilience
    out = fallback({"sk_a": [_resp(_good())]})
    # trip openrouter's breaker (5 consecutive failures)
    b = llm_resilience.breaker_for("openrouter")
    for _ in range(5):
        b.record_failure()
    assert b.state == "open"
    assert llm_resilience.breaker_for("groq").state == "closed"
    # the groq breaker object is a different instance per provider
    assert b is not llm_resilience.breaker_for("groq")
    assert out.spec.name == "openrouter"


def test_openrouter_breaker_open_fails_fast_together_serves(env, monkeypatch):
    from jobagent import llm_resilience
    _no_groq_keys(monkeypatch)
    or_spec, tg_spec = _spec(name="openrouter"), _spec(name="together")
    _install_chain(monkeypatch, [or_spec, tg_spec],
                   {"openrouter": _pool("sk_a"), "together": _pool("tg_a")})
    fakes = {"tg_a": ScriptedCompletions([_resp(_good())])}
    clients = {groq_keys._short_id("tg_a"): SimpleNamespace(
        chat=SimpleNamespace(completions=fakes["tg_a"]))}
    monkeypatch.setattr(llm_providers, "client_for",
                        lambda spec, key: clients[key.id])
    b = llm_resilience.breaker_for("openrouter")
    for _ in range(5):
        b.record_failure()
    assert b.state == "open"

    result = llm.structured(MatchResult, system="s", content="c", effort="low",
                            max_tokens=1000, task="test")
    assert result.score == 82                             # together took over


# ---------------------------------------------------------------- API layer

@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    from jobagent import server
    return TestClient(server.app)


def test_providers_endpoint_masked(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEYS", "sk-or-supersecret1111, sk-or-supersecret2222")
    monkeypatch.setattr(llm_providers, "_CHAIN", None)     # force re-read
    monkeypatch.setattr(llm_providers, "_POOLS", {})
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] == 1
    p = body["providers"][0]
    assert p["name"] == "openrouter" and p["total"] == 2
    assert "sk-or-supersecret1111" not in r.text            # never the raw secret
    assert p["keys"][0]["masked"].startswith("sk")


def test_providers_endpoint_empty_chain(client, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.setattr(llm_providers, "_CHAIN", [])
    r = client.get("/api/llm/providers")
    assert r.status_code == 200
    assert r.json()["configured"] == 0


def test_providers_reset_endpoint(client, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-a")
    monkeypatch.setattr(llm_providers, "_CHAIN", None)
    monkeypatch.setattr(llm_providers, "_POOLS", {})
    pool = llm_providers.pool_for(llm_providers.ProviderSpec(
        name="openrouter", base_url="https://openrouter.ai/api/v1",
        model="m", env_single="OPENROUTER_API_KEY", vault_provider="openrouter"))
    pool.report_auth_failure(pool.keys()[0], "401")
    assert client.get("/api/llm/providers").json()["providers"][0]["disabled"] == 1

    r = client.post("/api/llm/providers/reset")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/llm/providers").json()["providers"][0]["disabled"] == 0


def test_credential_add_joins_fallback_pool_live(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(llm_providers, "_CHAIN", None)
    monkeypatch.setattr(llm_providers, "_POOLS", {})
    assert client.get("/api/llm/providers").json()["providers"][0]["total"] == 0

    r = client.post("/api/credentials", json={
        "name": "or-extra", "provider": "openrouter",
        "secret": "sk-or-from-vault_999", "note": "fallback key"})
    assert r.status_code == 200
    body = client.get("/api/llm/providers").json()
    assert body["providers"][0]["total"] == 1
    assert body["providers"][0]["keys"][0]["source"] == "vault:or-extra"
    assert "sk-or-from-vault_999" not in json.dumps(body)


def test_health_reports_fallback_chain(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LLM_FALLBACK_PROVIDERS", "openrouter,gemini")
    monkeypatch.setattr(llm_providers, "_CHAIN", None)
    monkeypatch.setattr(llm_providers, "_POOLS", {})
    body = client.get("/api/health").json()
    fb = body["llm"]["fallbacks"]
    assert fb["configured"] == 2
    assert fb["names"] == ["openrouter", "gemini"]


def test_diagnostics_includes_fallback_chain(client, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.setattr(llm_providers, "_CHAIN", [])
    body = client.get("/api/system/diagnostics").json()
    check = body["checks"]["fallback_chain"]
    assert check["ok"] is True
    assert check["configured"] == 0
