"""Phase 19A — security primitives.

Every URL fetched from user input or job records is treated as untrusted.
This module centralizes:

1. SSRF-safe URL validation (scheme allowlist + DNS resolution to block
   loopback / private / link-local / cloud-metadata / IPv4-mapped targets).
2. A redirect-safe HTTP client factory that re-validates every hop.
3. Secret redaction for logs and error messages.
4. Safe error responses that never leak stack traces or internal paths.

The validators are pure and dependency-light so they can be used from the
server, the browser automation layer, and the verification engine alike.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urljoin

import httpx

# ---------------------------------------------------------------- constants

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 8
DEFAULT_TIMEOUT = 20.0

# Hostnames that resolve to the local machine regardless of DNS.
BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata.google.internal",  # GCP metadata
}

__all__ = [
    "UrlValidationError", "UrlCheck", "validate_url", "safe_client",
    "safe_request", "redact_secrets", "sanitize_error_message",
    "redact_url",
]


class UrlValidationError(ValueError):
    """Raised when a URL points somewhere we must not fetch from."""

    def __init__(self, reason: str, code: str = "blocked"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class UrlCheck:
    """Result of a validated URL: safe to fetch plus normalized form."""
    url: str
    host: str
    port: int
    scheme: str


# ---------------------------------------------------------------- internals

def _blocked_ip_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if ip.is_loopback:
        return "loopback address"
    if ip.is_private:
        return "private network address"
    if ip.is_link_local:
        return "link-local address (includes cloud metadata endpoints)"
    if ip.is_reserved or ip.is_unspecified:
        return "reserved/unspecified address"
    if ip.is_multicast:
        return "multicast address"
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — check the embedded IPv4 too.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_ip_reason(ip.ipv4_mapped)
    # IPv6-mapped-IPv4 style ::a.b.c.d
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        return _blocked_ip_reason(ip.sixtofour)
    return None


def _resolve_all(host: str) -> list[str]:
    """Resolve every A/AAAA record for host. Empty list => DNS failure."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    return [info[4][0] for info in infos]


# ---------------------------------------------------------------- public API

def validate_url(raw_url: str, *, resolve_dns: bool = True,
                 allow_loopback: bool = False) -> UrlCheck:
    """Validate an external URL for fetching.

    Raises UrlValidationError when the URL is not safe to fetch.
    ``resolve_dns=False`` performs the static checks only (scheme, literal
    IPs, blocked hostnames) — used for cheap pre-checks.
    ``allow_loopback=True`` permits 127.0.0.1/::1 targets ONLY — used for
    local sandbox fixtures in tests. Private ranges, link-local addresses
    and cloud-metadata endpoints remain blocked in every mode.
    """
    if not raw_url or not isinstance(raw_url, str):
        raise UrlValidationError("empty URL", code="invalid")
    url = raw_url.strip()
    if len(url) > 2048:
        raise UrlValidationError("URL exceeds 2048 characters", code="invalid")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlValidationError(f"scheme '{parsed.scheme or '(none)'}' not allowed",
                                 code="invalid")
    if not parsed.hostname:
        raise UrlValidationError("URL has no hostname", code="invalid")

    host = parsed.hostname.strip().rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme.lower()

    if host in BLOCKED_HOSTNAMES and not (allow_loopback and host == "localhost"):
        raise UrlValidationError(f"hostname '{host}' is blocked", code="blocked")

    # Literal IP (v4/v6) — check directly without DNS. Short-form IPv4
    # (e.g. "127.1", "10.1") is digits-and-dots: blocked as unsafe shorthand.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None and re.fullmatch(r"[0-9.]+", host or ""):
        raise UrlValidationError("numeric shorthand host not allowed", code="blocked")
    if literal is not None:
        reason = _blocked_ip_reason(literal)
        if reason and not (allow_loopback and literal.is_loopback):
            raise UrlValidationError(f"target is a {reason}", code="blocked")
        return UrlCheck(url=url, host=host, port=port, scheme=scheme)

    # Dotless single-label hosts (e.g. "intranet") are treated as internal.
    if "." not in host and ":" not in host and not (allow_loopback and host == "localhost"):
        raise UrlValidationError(f"single-label host '{host}' not allowed", code="blocked")

    if resolve_dns:
        addrs = _resolve_all(host)
        if not addrs:
            raise UrlValidationError(f"cannot resolve host '{host}'", code="dns")
        for addr in addrs:
            try:
                ip = ipaddress.ip_address(addr.split("%")[0])
            except ValueError:
                continue
            reason = _blocked_ip_reason(ip)
            if reason and not (allow_loopback and ip.is_loopback):
                # Never leak the resolved IP back in error text.
                raise UrlValidationError(
                    f"'{host}' resolves to a {reason}", code="blocked")

    return UrlCheck(url=url, host=host, port=port, scheme=scheme)


def safe_client(**kwargs: Any) -> httpx.Client:
    """httpx.Client that never follows redirects automatically.

    Use together with :func:`safe_request` so every redirect hop is
    re-validated. Direct use of ``follow_redirects=True`` with untrusted
    URLs is an SSRF bypass (DNS rebinding / redirect to metadata service).
    """
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("follow_redirects", False)
    return httpx.Client(**kwargs)


def safe_request(url: str, *, method: str = "GET", client: httpx.Client | None = None,
                 max_redirects: int = MAX_REDIRECTS, allow_loopback: bool = False,
                 **kwargs: Any) -> httpx.Response:
    """Fetch ``url`` after SSRF validation, re-validating each redirect hop.

    Returns the final (non-redirect) response. Raises UrlValidationError for
    unsafe targets and httpx errors for network failures.
    ``allow_loopback=True`` is for local sandbox fixtures only.
    """
    owns = client is None
    http = client or safe_client()
    try:
        current = validate_url(url, allow_loopback=allow_loopback)
        for _ in range(max_redirects + 1):
            resp = http.request(method, current.url, **kwargs)
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp
            location = resp.headers.get("location", "")
            if not location:
                return resp
            next_url = urljoin(str(resp.url), location)  # resolve relative hops
            current = validate_url(next_url, allow_loopback=allow_loopback)
            if resp.status_code == 303:
                method = "GET"
        raise UrlValidationError("too many redirects", code="redirects")
    finally:
        if owns:
            http.close()


# ---------------------------------------------------------------- redaction

# Secrets patterns: named keys/tokens, bearer auth, long opaque strings.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(sk-[A-Za-z0-9_\-]{16,})"), "sk-***"),
    (re.compile(r"(gsk_[A-Za-z0-9_\-]{16,})"), "gsk_***"),
    (re.compile(r"(github_pat_[A-Za-z0-9_]{16,})"), "github_pat_***"),
    (re.compile(r"(gh[pousr]_[A-Za-z0-9]{30,})"), "gh*_***"),
    (re.compile(r"(ya29\.[A-Za-z0-9_\-]{20,})"), "ya29.***"),
    (re.compile(r"(1//[A-Za-z0-9_\-]{30,})"), "1/***"),
    (re.compile(r"(AIza[A-Za-z0-9_\-]{30,})"), "AIza***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    (re.compile(r"((?:api[_-]?key|token|secret|password|passwd|client[_-]?secret)"
                r"([\"'\s:=]+))([^\s\"',;]{8,})", re.I), r"\1***"),
]


def redact_secrets(text: Any) -> str:
    """Return ``text`` with anything that looks like a secret masked.

    Applied before logging or returning error messages to the frontend.
    """
    if text is None:
        return ""
    out = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    # Long base64/hex blobs (potential raw keys) inside quotes or args.
    out = re.sub(r"[A-Za-z0-9+/]{80,}={0,2}", "***blob***", out)
    return out


def redact_url(url: Any) -> str:
    """Strip credentials and sensitive query params from a URL for display."""
    if not url:
        return ""
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return "***"
    # Rebuild WITHOUT userinfo (netloc includes user:pass — never echo it).
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    safe = f"{parsed.scheme}://{netloc}{parsed.path}" if parsed.scheme else str(url)
    sensitive_q = ("token", "key", "apikey", "api_key", "secret", "password",
                   "session", "auth", "code", "access_token", "refresh_token")
    pairs = [p for p in parsed.query.split("&") if p and not any(
        p.lower().startswith(s + "=") for s in sensitive_q)]
    if pairs:
        safe += "?" + "&".join(pairs)
    return safe


def sanitize_error_message(exc: BaseException, *, context: str = "") -> str:
    """Human-safe error text: no stack, no paths, no secrets."""
    base = exc.__class__.__name__
    msg = redact_secrets(str(exc) or "").strip()
    # Strip filesystem paths that could reveal the host layout.
    msg = re.sub(r"(?:[A-Za-z]:)?[/\\](?:[\w.\-]+[/\\]){2,}[\w.\-]+", "<path>", msg)
    if len(msg) > 300:
        msg = msg[:300] + "…"
    prefix = f"[{context}] " if context else ""
    return f"{prefix}{base}: {msg}" if msg else f"{prefix}{base}"
