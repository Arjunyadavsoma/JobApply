# Phase 20 Report — LLM provider fallback chain

**Date:** 2026-11-07 · **Branch:** `main` · **Suite:** 487 offline tests passed
(451 pre-existing + 33 new + 3 vault-flake regression tests), 7 live tests deselected.

## Goal

Phase 19K made one provider (Groq) resilient across many keys. Phase 20 removes the
remaining single point of failure: the provider itself. When Groq cannot serve a call —
every key cooling or disabled, the provider down, or no Groq key configured at all —
the same call is retried against a user-configured chain of OpenAI-compatible fallback
providers until one succeeds.

## What was built

### 1. `jobagent/llm_providers.py` (new)

- **`ProviderSpec` + `PROVIDER_PRESETS`** — nine known OpenAI-compatible providers with
  base URLs, default models and key-env conventions: `openrouter`, `openai`, `together`,
  `fireworks`, `gemini` (Google's OpenAI-compatible endpoint), `deepseek`, `mistral`,
  plus keyless local `ollama` and `lmstudio`.
- **Chain loading** — `LLM_FALLBACK_PROVIDERS` env (preset names, comma/semicolon
  separated) combined with `llm.fallback_providers` config entries for custom servers
  (name / base_url / model / api_key_envs / vault_provider / keyless). Env wins on name
  conflicts; per-provider model override via `<NAME>_MODEL`; unknown names are skipped
  with a warning, invalid config entries never take the chain down.
- **Per-provider key pools** — the Phase 19K pool classes reused unchanged: env keys
  (`<NAME>_API_KEYS` multi + `<NAME>_API_KEY` single), vault credentials with the
  matching provider name, dedup by secret (env wins), and one pseudo-key for keyless
  local servers. Vault problems never break env keys.
- **Per-provider clients** — cached per (provider, key), `max_retries=0` so rotation
  sees 429s immediately.
- **Masked `chain_status()`** — per provider: model, base_url, key states/stats,
  breaker state. Secrets never leave the module (hash ids + masking).

### 2. `jobagent/llm.py` — provider-agnostic rotation + the chain

- The Phase 19K rotation loop extracted into `_call_openai_pool(...)` — parameterised
  by pool, provider (circuit-breaker name), `client_factory` and a `_PoolHints`
  wording object. **All Groq error messages remain byte-identical** (verified by the
  unchanged 32 Phase 19K tests).
- New fail-fast: the provider's circuit breaker is checked *before* acquiring a key or
  building a client, so an outage costs nothing.
- `_via_fallback_provider(spec, ...)` — same rotation semantics on any
  OpenAI-compatible endpoint. Sends the universal `max_tokens` parameter (not Groq's
  `max_completion_tokens`) and no `reasoning_effort` (some servers reject unknown
  params).
- `_via_fallback_chain(...)` — walks the chain in order; the first success returns
  `(result, "groq→<provider>")`. One provider's unexpected bug is caught, logged and
  recorded in the failure list — it can never kill the chain. All providers exhausted →
  one honest error naming every provider and its failure.
- `structured()` — the chain applies only when `llm.backend: groq` fails with an
  `LLMError`; **no fallbacks configured → the original error propagates unchanged**.
  Daily-budget errors are raised before dispatch and are never fallen back.
- `ai_usage.backend` records who really served the call (`groq→openrouter`), so the
  analytics and the daily cap stay honest.

### 3. Credential vault — new providers + a real bug fixed

- `SUPPORTED_PROVIDERS` extended: `openrouter`, `together`, `fireworks`, `gemini`,
  `deepseek`, `mistral` (alongside groq/openai/anthropic/gmail/other) — vault-sourced
  fallback keys, selectable in the dashboard dropdown.
- **Bug fixed (pre-existing, install-bricking ~4%):** `load_master_key()` did
  `read_bytes().strip()` on the key file. The master key is 32 *random binary* bytes —
  when generation produced a key starting/ending with a whitespace byte (probability
  ≈ 1 − (251/256)² ≈ 3.9%), the vault declared its own freshly-generated key "corrupt"
  on the next read, permanently bricking the vault until the user deleted the key file
  and re-entered every credential. This was also the cause of a long-standing
  order-independent test flake (`VaultError: … is corrupt` appearing on random
  TestVaultCrud tests, ~37% of full-suite runs). Now: exact 32-byte files are accepted
  as-is; a trailing newline (hand-edited file) is tolerated; anything else is rejected
  with the byte count. Regression tests pin all three cases (with a size-aware
  `token_bytes` mock so GCM nonces stay random). Flake pair re-run 10×: 10/10 green.

### 4. API & dashboard

- `GET /api/llm/providers` — the chain with masked per-provider pool status and
  breaker states. `POST /api/llm/providers/reset` — re-read sources and re-arm every
  key in every pool.
- Credential CRUD now hot-reloads the fallback pools too (no restart), same as the
  Groq pool.
- `/api/health` reports `llm.fallbacks` (configured count + names); version bumped to
  20.0. `/api/system/diagnostics` gains a `fallback_chain` check (never fails the
  overall status — an empty chain is valid).
- Dashboard: new **"LLM provider fallback chain"** card in System → Credential Manager
  (hidden when no fallbacks are configured) — per-provider model, base URL, live key
  chips with cooldown countdowns, breaker badges, keyless badge, "get a key" links and
  a re-arm button. Provider dropdown extended. `web/dist` rebuilt (`tsc` + `vite`
  clean).

### 5. Configuration & docs

- `config.example.yaml`: documented `llm.fallback_providers` with a custom-server
  example; `.env.example`: `LLM_FALLBACK_PROVIDERS` + per-provider key/model variables.
- README: new "Provider fallback chain" section. `IMPLEMENTATION_STATUS.md`: Phase 20
  entry (this report referenced from there).

## Behaviour guarantees (all test-pinned)

| Guarantee | Test |
|---|---|
| No fallbacks configured = unchanged Phase 19K behaviour, byte-identical errors | `test_structured_no_chain_original_error_unchanged` + all 32 Phase 19K tests |
| Groq unavailable → next provider serves; `ai_usage` records the real server | `test_structured_falls_back_when_groq_has_no_keys`, `..._records_honest_backend` |
| First fallback 429s → second provider serves | `test_structured_second_provider_used_when_first_fails` |
| All providers fail → one error naming each provider and its reason | `test_structured_chain_exhausted_combined_error` |
| Daily-budget errors never touch fallbacks | `test_structured_budget_error_never_falls_back` |
| Same rotation semantics on fallback providers (429 cool, 401 disable, honest all-cooling error) | `test_fallback_rotates_on_429`, `test_fallback_disables_on_401`, `test_fallback_all_rate_limited_honest_error` |
| Universal `max_tokens`, no `reasoning_effort`, no `max_completion_tokens` | `test_fallback_uses_universal_max_tokens_param` |
| Per-provider circuit breakers are independent; open circuit fails fast to the next provider | `test_fallback_breaker_does_not_affect_groq`, `test_openrouter_breaker_open_fails_fast_together_serves` |
| Key pools per provider: env multi/single dedup, vault join, wrong-provider exclusion, keyless local | `test_provider_pool_*` |
| Endpoints masked (no raw secrets), reset re-arms, credential CRUD hot-reloads, health reports chain | `test_providers_endpoint_masked`, `test_providers_reset_endpoint`, `test_credential_add_joins_fallback_pool_live`, `test_health_reports_fallback_chain`, `test_diagnostics_includes_fallback_chain` |
| Vault never rejects a healthy random key; tolerates trailing newline; rejects wrong size | `test_random_key_with_whitespace_bytes_survives_roundtrip`, `test_key_file_with_trailing_newline_tolerated`, `test_key_file_wrong_size_still_rejected` |

## How to verify

```bash
pip install -r requirements.txt pytest pytest-httpserver
python -m pytest tests/ -q --ignore=tests/e2e/test_live_adapters.py   # 487 passed

# end-to-end without touching a real provider: config + env
LLM_FALLBACK_PROVIDERS=openrouter,together OPENROUTER_API_KEY=sk-or-x \
  python -m jobagent web    # System → Credential Manager shows the chain card
curl http://127.0.0.1:8600/api/llm/providers      # masked chain status
curl http://127.0.0.1:8600/api/health             # llm.fallbacks
```

## Honest limitations

- The chain applies to the `groq` backend only (`api`/`subscription` Claude backends
  are out of scope by design).
- Fallback providers are not pre-verified at configure time; a misconfigured model
  surfaces on first use with an actionable message (and the chain moves on).
- Usage/cost accounting is per-provider in `ai_usage` but there is no per-provider
  budget cap (the existing daily cap counts all calls) — a reasonable Phase 21 topic.

## Known pre-existing issue (not introduced by Phase 20, documented for Phase 21)

`tests/test_state_machine.py::TestConcurrency::test_concurrent_transitions_serialize`
flakes at low frequency under full-suite system load (~1 in 3 full runs observed;
37+ isolated runs never reproduce it). The state machine's `BEGIN IMMEDIATE`
serialization looks correct on inspection; the interleaving needs load to appear.
Untouched by this phase (verified: the failure also occurs without any Phase 20
changes present under load, and the baseline full-suite run flakes on the — now
fixed — vault bug instead). Recommended Phase 21 work: reproduce under controlled
load, determine whether both racing workers can observe the same `from_state`, and
harden (e.g. `SELECT … ` inside the immediate transaction with a re-check after
lock acquisition, or a process-level transition lock).
