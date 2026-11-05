"""Phase 19F — LLM provider resilience: retry, backoff, circuit breaker.

Groq (and any provider) calls are wrapped by:

* ``retry_with_backoff`` — exponential backoff with full jitter for
  transient errors (429 rate limit, 5xx, connection resets). Bounded
  attempts, never retries non-idempotent side effects (LLM calls are
  idempotent: same prompt -> acceptable re-send; the idempotency that
  matters for applications is enforced by the state machine, not here).
* ``CircuitBreaker`` — after N consecutive failures the circuit OPENS and
  calls fail fast for a cooldown window instead of hammering a down
  provider; one probe request re-closes it (half-open).

Both are thread-safe and unit-tested with fake clocks: no real network
and no sleeps in the test suite.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised when the circuit is open — call rejected without touching the provider."""

    def __init__(self, provider: str, retry_after_s: float):
        self.provider = provider
        self.retry_after_s = round(retry_after_s, 1)
        super().__init__(
            f"{provider} circuit open after repeated failures; "
            f"retry in ~{self.retry_after_s}s")


@dataclass
class CircuitBreaker:
    """Thread-safe consecutive-failure circuit breaker with half-open probe."""

    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    _state: str = "closed"                    # closed | open | half_open
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                    self._state = "half_open"  # cooldown elapsed -> probe allowed
            return self._state

    def check(self) -> None:
        """Raise CircuitOpenError when calls must fail fast."""
        if self.state == "open":
            elapsed = time.monotonic() - self._opened_at
            raise CircuitOpenError(self.name, self.cooldown_seconds - elapsed)

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    # test helpers ------------------------------------------------------
    def _force_state(self, state: str, failures: int = 0,
                     opened_at: float = 0.0) -> None:
        with self._lock:
            self._state = state
            self._failures = failures
            self._opened_at = opened_at


# One breaker per provider, shared process-wide.
_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def breaker_for(provider: str, *, failure_threshold: int = 5,
                cooldown_seconds: float = 60.0) -> CircuitBreaker:
    with _breakers_lock:
        if provider not in _breakers:
            _breakers[provider] = CircuitBreaker(
                provider, failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds)
        return _breakers[provider]


def breaker_states() -> dict[str, dict[str, Any]]:
    """Observable status for /api/system/diagnostics."""
    with _breakers_lock:
        return {name: {"state": b.state, "failures": b._failures}
                for name, b in _breakers.items()}


def retry_with_backoff(fn: Callable[[], T], *, attempts: int = 4,
                       base_delay: float = 0.5, max_delay: float = 8.0,
                       sleep: Callable[[float], None] = time.sleep,
                       retry_on: tuple[type[BaseException], ...] = (Exception,),
                       give_up_on: tuple[type[BaseException], ...] = (),
                       on_retry: Callable[[int, BaseException, float], None] | None = None,
                       ) -> T:
    """Call ``fn`` with exponential backoff + full jitter between attempts.

    ``give_up_on`` exceptions are never retried (e.g. auth errors — retrying
    a bad API key only burns quota). ``sleep`` is injectable for tests.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except give_up_on:
            raise
        except retry_on as exc:
            last_exc = exc
            if attempt == attempts:
                break
            # full jitter: uniform in [0, min(max_delay, base * 2^(n-1))]
            ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay = random.uniform(0, ceiling)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    assert last_exc is not None
    raise last_exc


def call_with_resilience(provider: str, fn: Callable[[], T], *,
                         attempts: int = 4, base_delay: float = 0.5,
                         max_delay: float = 8.0,
                         sleep: Callable[[float], None] = time.sleep,
                         retry_on: tuple[type[BaseException], ...] = (Exception,),
                         give_up_on: tuple[type[BaseException], ...] = (),
                         pass_through: tuple[type[BaseException], ...] = (),
                         ) -> T:
    """Circuit breaker + backoff retry around one provider call.

    Success closes the circuit; exhaustion opens it via record_failure for
    each failed attempt. The breaker check happens BEFORE any attempt.

    ``pass_through`` exceptions (Phase 19K): raised unchanged after ONE
    attempt — not retried, not recorded on the provider breaker. Used for
    per-key errors (429 rate limit, 401 bad key) that the caller handles by
    rotating to another key: a healthy key pool must not trip the
    provider-level circuit.
    """
    breaker = breaker_for(provider)
    breaker.check()

    def _once() -> T:
        try:
            result = fn()
            breaker.record_success()
            return result
        except pass_through:
            raise                      # key-level: rotation handles it, breaker untouched
        except give_up_on:
            breaker.record_failure()
            raise
        except retry_on as exc:
            breaker.record_failure()
            raise exc

    return retry_with_backoff(
        _once, attempts=attempts, base_delay=base_delay, max_delay=max_delay,
        sleep=sleep, retry_on=retry_on, give_up_on=give_up_on + pass_through)
