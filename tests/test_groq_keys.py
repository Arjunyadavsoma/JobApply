"""Phase 19K — multiple Groq API keys + rotation. Fully offline.

Covers:
- GROQ_API_KEYS parsing (comma / semicolon / newline) and dedup vs GROQ_API_KEY
- vault-sourced keys (added, revoked, wrong provider)
- round-robin distribution across requests
- 429: key cools down (retry-after honoured), skipped, re-enters after rest;
  back-to-back 429s double the cooldown (capped)
- 401/403: key disabled until operator reset
- all keys cooling: bounded wait, honest error message
- llm._via_groq rotates on 429/401 without tripping the provider circuit
- API endpoints: masked status, reset, live reload after vault changes
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent import groq_keys
from jobagent import llm
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


def _resp(content="", finish="stop", pt=100, ct=50, model="openai/gpt-oss-120b"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                 finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
        model=model,
    )


def _good():
    return json.dumps({"score": 82, "verdict": "apply", "reasons": ["python"],
                       "missing_requirements": []})


def _rate_limit(retry_after=None, x_reset=None):
    import openai
    headers = {}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    if x_reset is not None:
        headers["x-ratelimit-reset-requests"] = x_reset
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(429, request=req, headers=headers)
    return openai.RateLimitError("rate limited", response=resp, body=None)


def _auth_error(status=401):
    import openai
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    cls = openai.AuthenticationError if status == 401 else openai.PermissionDeniedError
    return cls("bad key", response=resp, body=None)


def _pool(*secrets, source="env:GROQ_API_KEYS") -> groq_keys.GroqKeyPool:
    return groq_keys.GroqKeyPool(
        [groq_keys.GroqKeyHandle(secret=s, source=source) for s in secrets])


@pytest.fixture(autouse=True)
def _close_groq_breaker():
    """Never leave the shared provider breaker open for other tests."""
    from jobagent import llm_resilience
    yield
    llm_resilience.breaker_for("groq").record_success()


@pytest.fixture(autouse=True)
def _fast_clock(env, monkeypatch):
    """Deterministic clock (advance() moves time) whose sleep() also advances it,
    so every bounded wait in the module terminates in zero real time."""
    ticks = {"t": 1000.0}
    slept: list[float] = []

    def _now() -> float:
        return ticks["t"]

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        ticks["t"] += seconds

    # Must be installed AFTER env() so the module reload doesn't rebind them.
    monkeypatch.setattr(groq_keys, "_monotonic", _now)
    monkeypatch.setattr(groq_keys, "_sleep", _sleep)

    def advance(seconds: float) -> None:
        ticks["t"] += seconds

    return SimpleNamespace(advance=advance, slept=slept, now=_now)


# ---------------------------------------------------------------- parsing

def test_parse_key_list_separators():
    assert groq_keys.parse_key_list("k1, k2;k3\nk4") == ["k1", "k2", "k3", "k4"]
    assert groq_keys.parse_key_list("") == []
    assert groq_keys.parse_key_list(" , ;\n ") == []
    assert groq_keys.parse_key_list("k1") == ["k1"]


def test_retry_after_seconds_numeric_header():
    assert groq_keys.retry_after_seconds(_rate_limit(retry_after=42)) == 42


def test_retry_after_seconds_capped():
    assert groq_keys.retry_after_seconds(_rate_limit(retry_after=99999)) == groq_keys.MAX_COOLDOWN_S


def test_retry_after_seconds_ms_header():
    # "12ms" is a per-request window reset — far too short to rest a key on,
    # so the pool floors every cooldown at 1s.
    assert groq_keys.retry_after_seconds(_rate_limit(x_reset="12ms"),
                                         default=60) == 1.0


def test_retry_after_seconds_default_when_missing():
    assert groq_keys.retry_after_seconds(_rate_limit(), default=60) == 60


def test_retry_after_seconds_minimum_one_second():
    assert groq_keys.retry_after_seconds(_rate_limit(retry_after=0), default=60) == 1.0


# ---------------------------------------------------------------- loading

def test_pool_loads_env_keys_and_dedups(env, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_a, gsk_b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_a")          # duplicate of the first
    pool = groq_keys.GroqKeyPool.load()
    assert [h.secret for h in pool.keys()] == ["gsk_a", "gsk_b"]
    assert all(h.source == "env:GROQ_API_KEYS" for h in pool.keys())


def test_pool_single_key_env_only(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_solo")
    pool = groq_keys.GroqKeyPool.load()
    assert len(pool) == 1
    assert pool.keys()[0].source == "env:GROQ_API_KEY"


def test_pool_empty_when_nothing_configured(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert len(groq_keys.GroqKeyPool.load()) == 0


def test_pool_loads_vault_keys(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from jobagent import credentials
    credentials.add_credential("groq-a", "groq", "gsk_vault_1")
    credentials.add_credential("groq-b", "groq", "gsk_vault_2")
    credentials.add_credential("mail", "gmail", "gmail-secret")   # wrong provider
    credentials.revoke_credential("groq-b")                       # revoked → excluded

    pool = groq_keys.GroqKeyPool.load()
    assert [h.secret for h in pool.keys()] == ["gsk_vault_1"]
    assert pool.keys()[0].source == "vault:groq-a"
    assert pool.keys()[0].vault_name == "groq-a"


def test_pool_dedups_across_env_and_vault(env, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_same")
    from jobagent import credentials
    credentials.add_credential("groq-main", "groq", "gsk_same")
    pool = groq_keys.GroqKeyPool.load()
    assert len(pool) == 1
    assert pool.keys()[0].source == "env:GROQ_API_KEY"   # env wins over vault


# ---------------------------------------------------------------- rotation

def test_round_robin_spreads_requests():
    pool = _pool("k1", "k2", "k3")
    picked = [pool.acquire().secret for _ in range(6)]
    assert picked == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_rate_limited_key_skipped_until_cooldown_elapses(_fast_clock):
    pool = _pool("k1", "k2")
    k1 = pool.keys()[0]
    pool.report_rate_limited(k1, 60)

    assert k1.state == "cooling"
    picked = [pool.acquire().secret for _ in range(3)]
    assert picked == ["k2", "k2", "k2"]                    # k1 skipped

    _fast_clock.advance(61)                                # cooldown over
    assert pool.acquire().secret == "k1"                   # back in rotation


def test_consecutive_429s_double_the_cooldown_capped(_fast_clock):
    pool = _pool("k1")
    k1 = pool.keys()[0]
    pool.report_rate_limited(k1, 60)
    assert k1.cooldown_until - _fast_clock.now() == pytest.approx(60)
    pool.report_rate_limited(k1, 60)                       # second hit doubles
    assert k1.cooldown_until - _fast_clock.now() == pytest.approx(120)
    pool.report_rate_limited(k1, 60)
    pool.report_rate_limited(k1, 60)
    assert k1.cooldown_until - _fast_clock.now() <= groq_keys.MAX_COOLDOWN_S  # capped
    # a success resets the escalation
    pool.report_success(k1)
    assert k1.consecutive_429 == 0 and k1.state == "active"


def test_auth_failure_disables_until_reset():
    pool = _pool("k1", "k2")
    k1 = pool.keys()[0]
    pool.report_auth_failure(k1, "401 - key rejected")
    assert k1.state == "disabled"
    assert pool.acquire().secret == "k2"

    pool.reset()
    assert all(h.state == "active" for h in pool.keys())


def test_wait_for_available_returns_when_soonest_frees(_fast_clock):
    pool = _pool("k1", "k2")
    k1, k2 = pool.keys()
    pool.report_rate_limited(k1, 30)
    pool.report_rate_limited(k2, 10)

    assert pool.acquire() is None
    key = pool.wait_for_available(timeout=30)   # fake sleep advances the clock
    assert key is not None and key.secret == "k2"          # sooner cooldown first


def test_wait_for_available_times_out(_fast_clock):
    pool = _pool("k1")
    pool.report_rate_limited(pool.keys()[0], 3600)         # long rest
    assert pool.wait_for_available(timeout=5) is None


def test_next_cooldown_remaining(_fast_clock):
    pool = _pool("k1")
    pool.report_rate_limited(pool.keys()[0], 45)
    assert pool.next_cooldown_remaining() == pytest.approx(45)


def test_status_is_masked_and_counts_states(_fast_clock):
    pool = _pool("gsk_aaaaaaaaaa1111", "gsk_bbbbbbbbbb2222", "gsk_cccccccccc3333")
    k1, k2, _k3 = pool.keys()
    pool.report_rate_limited(k1, 30)
    pool.report_auth_failure(k2, "401")
    st = pool.status()

    assert st["total"] == 3 and st["active"] == 1 and st["cooling"] == 1 and st["disabled"] == 1
    blob = json.dumps(st)
    for secret in ("gsk_aaaaaaaaaa1111", "gsk_bbbbbbbbbb2222", "gsk_cccccccccc3333"):
        assert secret not in blob                            # no raw secrets ever
    assert st["rotation"] == "round_robin"
    assert {k["source"] for k in st["keys"]} == {"env:GROQ_API_KEYS"}


# ---------------------------------------------------------------- llm integration

@pytest.fixture()
def rot(monkeypatch, env):
    """Install a multi-key pool and per-key scripted clients."""
    monkeypatch.setattr(llm, "_groq_model_name", lambda: "openai/gpt-oss-120b")
    monkeypatch.setattr(llm, "_groq_fallback_model", lambda: None)
    clients: dict[str, object] = {}
    fakes: dict[str, ScriptedCompletions] = {}

    def install(scripts: dict[str, list]):
        pool = groq_keys.GroqKeyPool(
            [groq_keys.GroqKeyHandle(secret=s, source="env:GROQ_API_KEYS")
             for s in scripts])
        monkeypatch.setattr(groq_keys, "_POOL", pool)
        fakes.clear()
        clients.clear()
        for secret, script in scripts.items():
            fake = ScriptedCompletions(script)
            fakes[secret] = fake
            clients[groq_keys._short_id(secret)] = \
                SimpleNamespace(chat=SimpleNamespace(completions=fake))
        monkeypatch.setattr(llm, "_client_for",
                            lambda key: clients[key.id])
        return SimpleNamespace(pool=pool, fakes=fakes)

    return install


def test_via_groq_rotates_on_429(rot):
    out = rot({"gsk_a": [_rate_limit(retry_after=60)],
               "gsk_b": [_resp(_good())]})
    result = llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    # key A is now cooling, B served the request
    states = {h.secret: h.state for h in out.pool.keys()}
    assert states["gsk_a"] == "cooling" and states["gsk_b"] == "active"
    assert len(out.fakes["gsk_b"].calls) == 1


def test_via_groq_rotates_on_401_and_disables(rot):
    out = rot({"gsk_a": [_auth_error(401)],
               "gsk_b": [_resp(_good())]})
    result = llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    states = {h.secret: h.state for h in out.pool.keys()}
    assert states["gsk_a"] == "disabled"
    assert out.pool.keys()[0].auth_failures == 1


def test_via_groq_all_rate_limited_clear_error(rot):
    rot({"gsk_a": [_rate_limit(retry_after=60)],
         "gsk_b": [_rate_limit(retry_after=60)]})
    with pytest.raises(llm.LLMError) as e:
        llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert "rate-limited" in str(e.value)
    assert "2 cooling" in str(e.value)


def test_via_groq_all_disabled_auth_error(rot):
    rot({"gsk_a": [_auth_error(401)],
         "gsk_b": [_auth_error(403)]})
    with pytest.raises(llm.LLMError) as e:
        llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert "auth failures" in str(e.value)


def test_via_groq_no_keys_at_all(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool([]))
    with pytest.raises(llm.LLMError) as e:
        llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert "No Groq API key configured" in str(e.value)


def test_rate_limit_rotation_does_not_trip_provider_breaker(rot):
    """Five 429s in a row would open the provider circuit if they were counted —
    with rotation they must not: only provider-level failures open it."""
    from jobagent import llm_resilience
    llm_resilience.breaker_for("groq").record_success()    # clean slate
    scripts = {f"gsk_limited_{i}": [_rate_limit(retry_after=60)] for i in range(5)}
    scripts["gsk_ok"] = [_resp(_good())]
    out = rot(scripts)

    result = llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    assert llm_resilience.breaker_states()["groq"]["failures"] == 0
    assert llm_resilience.breaker_states()["groq"]["state"] == "closed"
    assert len(out.fakes["gsk_ok"].calls) == 1             # the good key served


def test_single_key_waits_for_cooldown_then_succeeds(rot):
    out = rot({"gsk_a": [_rate_limit(retry_after=2), _resp(_good())]})
    # bounded wait: fake sleep advances the clock, the 2s rest passes, retry wins
    result = llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    assert result.score == 82
    assert len(out.fakes["gsk_a"].calls) == 2              # retried after the rest


def test_retry_after_header_shortens_the_rest(rot, _fast_clock):
    out = rot({"gsk_a": [_rate_limit(retry_after=3), _resp(_good())]})
    llm._via_groq(MatchResult, "s", "c", "low", 1000, [])
    k = out.pool.keys()[0]
    assert k.state == "active"                             # succeeded after rest
    assert k.rate_limited == 1


# ---------------------------------------------------------------- API layer

@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    from jobagent import server
    return TestClient(server.app)


def test_llm_keys_endpoint_masked(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)   # robust to a pre-set env
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_supersecret1111, gsk_supersecret2222")
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool.load())
    r = client.get("/api/llm/keys")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["active"] == 2
    assert "gsk_supersecret1111" not in r.text              # never the raw secret
    assert body["keys"][0]["masked"].startswith("gsk")


def test_llm_keys_reset_endpoint(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_a, gsk_b")
    pool = groq_keys.GroqKeyPool.load()
    monkeypatch.setattr(groq_keys, "_POOL", pool)
    pool.report_auth_failure(pool.keys()[0], "401")
    assert client.get("/api/llm/keys").json()["disabled"] == 1

    r = client.post("/api/llm/keys/reset")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/llm/keys").json()["disabled"] == 0


def test_credential_add_joins_pool_live(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool.load())
    assert client.get("/api/llm/keys").json()["total"] == 0

    r = client.post("/api/credentials", json={
        "name": "groq-extra", "provider": "groq",
        "secret": "gsk_from_vault_999", "note": "rotation key"})
    assert r.status_code == 200
    # no restart needed: the pool picked the new key up
    body = client.get("/api/llm/keys").json()
    assert body["total"] == 1
    assert body["keys"][0]["source"] == "vault:groq-extra"
    assert "gsk_from_vault_999" not in json.dumps(body)


def test_credential_delete_leaves_pool(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client.post("/api/credentials", json={"name": "groq-tmp", "provider": "groq",
                                          "secret": "gsk_temporary_key"})
    assert client.get("/api/llm/keys").json()["total"] == 1
    r = client.delete("/api/credentials/groq-tmp")
    assert r.status_code == 200
    assert client.get("/api/llm/keys").json()["total"] == 0


def test_health_reports_key_pool(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_h1, gsk_h2")
    monkeypatch.setattr(groq_keys, "_POOL", groq_keys.GroqKeyPool.load())
    body = client.get("/api/health").json()
    assert body["llm"]["groq_key_present"] is True
    assert body["llm"]["groq_keys"]["total"] == 2
    assert body["llm"]["groq_keys"]["active"] == 2
