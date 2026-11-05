"""Phase 19K — multiple Groq API keys with automatic rotation.

Why: a single Groq key is rate-limited (free tier especially). With several
keys the agent rotates on every request (round-robin), and when one key is
rate-limited (HTTP 429) it *rests* for a cooldown while the others keep
working — no sleep-and-retry burn, no failed runs.

Key sources, all optional and combined (duplicates by secret removed):
  1. ``GROQ_API_KEYS`` in .env — comma / semicolon / newline separated list
  2. ``GROQ_API_KEY``  in .env — the classic single key (unchanged behaviour)
  3. The credential vault — every non-revoked credential with provider
     ``groq`` (added via System → Credential Manager, AES-256-GCM encrypted)

Per-key states:
  active   — in rotation
  cooling  — rate-limited; skipped until its cooldown elapses, then re-enters
             rotation automatically. Two consecutive 429s double the cooldown
             (capped) so a hot key actually gets to rest.
  disabled — the API rejected the key itself (401/403). Stays out of rotation
             until an operator resets it (bad keys don't get retried: that
             only burns quota and log noise).

Thread safety: one process-wide pool behind a lock; ``acquire`` is O(n).
Secrets never leave this module except into the OpenAI client — status
output is masked, ids are hashes.

Clock and sleep are injectable for deterministic tests.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

log = logging.getLogger("jobagent.groq_keys")

DEFAULT_COOLDOWN_S = 60.0      # base rest after a 429 (llm.key_cooldown_seconds)
MAX_COOLDOWN_S = 900.0         # hard cap: 15 minutes (llm.key_cooldown_max_seconds)
DEFAULT_WAIT_S = 30.0          # max time to wait when ALL keys are cooling (llm.key_wait_seconds)

# Injectable for tests -------------------------------------------------------
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def _short_id(secret: str) -> str:
    """Stable, safe-to-show id for a key (hash prefix — never the secret)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def parse_key_list(raw: str) -> list[str]:
    """Split a GROQ_API_KEYS value: commas, semicolons and newlines all work.

    Whitespace-only entries are dropped; empty string → []. Duplicates are
    removed later by the pool (env list may overlap GROQ_API_KEY).
    """
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def retry_after_seconds(exc: BaseException, default: float = DEFAULT_COOLDOWN_S,
                        cap: float = MAX_COOLDOWN_S) -> float:
    """Best-effort 'how long should this key rest?' from a 429 exception.

    Groq/OpenAI send ``retry-after`` (seconds or HTTP-date) and
    ``x-ratelimit-reset-requests`` (e.g. ``1s``, ``12ms``). Anything missing
    or malformed falls back to the configured default; the result is capped.
    """
    headers: dict[str, str] = {}
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
        except Exception:  # noqa: BLE001 — headers are best-effort only
            headers = {}

    def _cap(v: float) -> float:
        return max(1.0, min(cap, v))

    ra = headers.get("retry-after")
    if ra:
        try:
            return _cap(float(ra))
        except ValueError:
            try:  # HTTP-date form
                delta = (parsedate_to_datetime(ra) -
                         datetime.now(timezone.utc)).total_seconds()
                if delta > 0:
                    return _cap(delta)
            except Exception:  # noqa: BLE001
                pass
    for h in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        v = headers.get(h, "")
        if v:
            mult = 0.001 if v.endswith("ms") else 1.0
            try:
                return _cap(float(v.rstrip("ms")) * mult)
            except ValueError:
                continue
    return _cap(default)


# ---------------------------------------------------------------- key handle

@dataclass
class GroqKeyHandle:
    secret: str
    source: str                       # "env:GROQ_API_KEYS" | "env:GROQ_API_KEY" | "vault:<name>"
    vault_name: str | None = None     # set when sourced from the vault
    state: str = "active"             # active | cooling | disabled
    cooldown_until: float = 0.0       # monotonic deadline while cooling
    failure_reason: str = ""
    consecutive_429: int = 0
    # stats
    requests: int = 0
    successes: int = 0
    rate_limited: int = 0
    auth_failures: int = 0
    last_used_at: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def id(self) -> str:
        return _short_id(self.secret)

    def refresh(self, now: float | None = None) -> None:
        """Cooling keys whose deadline passed re-enter rotation."""
        if self.state == "cooling" and (now or _monotonic()) >= self.cooldown_until:
            self.state = "active"
            self.cooldown_until = 0.0
            self.failure_reason = ""

    def available(self, now: float | None = None) -> bool:
        with self._lock:
            self.refresh(now)
            return self.state == "active"


# ---------------------------------------------------------------- pool

class GroqKeyPool:
    """Round-robin pool with per-key cooldown and disable."""

    def __init__(self, keys: list[GroqKeyHandle]):
        self._keys = keys
        self._rr = 0
        self._lock = threading.RLock()

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls) -> "GroqKeyPool":
        """Read env vars + the vault. Vault problems never break env keys."""
        keys: list[GroqKeyHandle] = []
        seen: set[str] = set()

        def add(secret: str, source: str, vault_name: str | None = None) -> None:
            if not secret or secret in seen:
                return
            seen.add(secret)
            keys.append(GroqKeyHandle(secret=secret, source=source, vault_name=vault_name))

        multi = parse_key_list(os.environ.get("GROQ_API_KEYS", ""))
        for k in multi:
            add(k, "env:GROQ_API_KEYS")
        single = os.environ.get("GROQ_API_KEY", "").strip()
        add(single, "env:GROQ_API_KEY")

        try:
            from jobagent import credentials
            for row in credentials.list_credentials():
                if row["provider"] != "groq" or row["revoked"]:
                    continue
                try:
                    secret = credentials.get_secret(row["name"])
                except credentials.VaultError:
                    continue  # locked / undecryptable row: skip, never crash
                add(secret, f"vault:{row['name']}", vault_name=row["name"])
        except Exception as exc:  # noqa: BLE001 — vault is optional at runtime
            log.debug("vault unavailable for key pool: %s", type(exc).__name__)

        return cls(keys)

    # -- selection ----------------------------------------------------------
    def keys(self) -> list[GroqKeyHandle]:
        with self._lock:
            return list(self._keys)

    def __len__(self) -> int:
        with self._lock:
            return len(self._keys)

    def acquire(self) -> GroqKeyHandle | None:
        """Next available key, round-robin. None when everything is
        cooling/disabled or the pool is empty."""
        with self._lock:
            now = _monotonic()
            for h in self._keys:
                h.refresh(now)
            available = [h for h in self._keys if h.state == "active"]
            if not available:
                return None
            if self._rr >= len(available):
                self._rr = 0
            pick = available[self._rr % len(available)]
            self._rr = (self._rr + 1) % max(len(available), 1)
            with pick._lock:
                pick.requests += 1
                pick.last_used_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return pick

    def next_cooldown_remaining(self) -> float:
        """Seconds until the soonest cooling key becomes active (0 = none cooling)."""
        with self._lock:
            now = _monotonic()
            remaining = [h.cooldown_until - now for h in self._keys
                         if h.state == "cooling" and h.cooldown_until > now]
            return min(remaining) if remaining else 0.0

    def wait_for_available(self, timeout: float = DEFAULT_WAIT_S) -> GroqKeyHandle | None:
        """Block (bounded) when every key is cooling; None on timeout."""
        deadline = _monotonic() + max(0.0, timeout)
        while True:
            key = self.acquire()
            if key is not None:
                return key
            remaining = min(self.next_cooldown_remaining(), deadline - _monotonic())
            if remaining <= 0:
                return None
            _sleep(min(remaining, 1.0))

    # -- reporting -----------------------------------------------------------
    def report_success(self, key: GroqKeyHandle) -> None:
        with key._lock:
            key.successes += 1
            key.consecutive_429 = 0
            key.state = "active"
            key.cooldown_until = 0.0
            key.failure_reason = ""

    def report_rate_limited(self, key: GroqKeyHandle, cooldown_s: float) -> None:
        """429: rest this key; back-to-back 429s double the rest, capped."""
        with key._lock:
            key.rate_limited += 1
            key.consecutive_429 += 1
            rest = cooldown_s * (2 ** (key.consecutive_429 - 1))
            key.cooldown_until = _monotonic() + min(rest, MAX_COOLDOWN_S)
            key.state = "cooling"
            key.failure_reason = f"rate_limited (resting {min(rest, MAX_COOLDOWN_S):.0f}s)"
        log.info("groq key %s rate-limited — cooling %.0fs (%d keys in pool)",
                 key.id, min(cooldown_s * (2 ** (key.consecutive_429 - 1)), MAX_COOLDOWN_S), len(self))

    def report_auth_failure(self, key: GroqKeyHandle, reason: str) -> None:
        """401/403: the key itself is bad. Disable it until a manual reset."""
        with key._lock:
            key.auth_failures += 1
            key.state = "disabled"
            key.failure_reason = f"auth_failed: {reason[:80]}"
        log.warning("groq key %s rejected (%s) — disabled until reset", key.id, reason[:80])
        if key.vault_name:
            try:  # surface it in the Credential Manager too
                from jobagent import credentials
                credentials.record_test_result(key.vault_name, False,
                                               f"auth failed during use: {reason[:60]}")
            except Exception:  # noqa: BLE001 — best effort
                pass

    def reset(self) -> int:
        """Operator action: re-arm every cooling/disabled key."""
        with self._lock:
            for h in self._keys:
                with h._lock:
                    h.state = "active"
                    h.cooldown_until = 0.0
                    h.failure_reason = ""
                    h.consecutive_429 = 0
            return len(self._keys)

    # -- status ---------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Masked, JSON-safe pool status for /api/llm/keys and diagnostics."""
        from jobagent.credentials import mask_secret
        with self._lock:
            now = _monotonic()
            keys_out = []
            counts = {"active": 0, "cooling": 0, "disabled": 0}
            for h in self._keys:
                h.refresh(now)
                counts[h.state] = counts.get(h.state, 0) + 1
                keys_out.append({
                    "id": h.id,
                    "masked": mask_secret(h.secret),
                    "source": h.source,
                    "state": h.state,
                    "cooldown_s_remaining": round(h.cooldown_until - now, 1)
                    if h.state == "cooling" and h.cooldown_until > now else 0,
                    "failure_reason": h.failure_reason,
                    "requests": h.requests,
                    "successes": h.successes,
                    "rate_limited": h.rate_limited,
                    "auth_failures": h.auth_failures,
                    "last_used_at": h.last_used_at,
                })
            return {
                "rotation": "round_robin",
                "total": len(self._keys),
                **counts,
                "keys": keys_out,
            }


# ---------------------------------------------------------------- singleton

_POOL: GroqKeyPool | None = None
_POOL_LOCK = threading.Lock()


def pool() -> GroqKeyPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = GroqKeyPool.load()
        return _POOL


def reload_pool() -> GroqKeyPool:
    """Re-read env + vault (called after credential CRUD so changes apply live)."""
    global _POOL
    with _POOL_LOCK:
        _POOL = GroqKeyPool.load()
        return _POOL


def reset_pool() -> dict[str, Any]:
    """Reload sources and re-arm all keys (operator reset endpoint)."""
    p = reload_pool()
    n = p.reset()
    return {"ok": True, "keys": n, "status": p.status()}


def pool_status() -> dict[str, Any]:
    return pool().status()
