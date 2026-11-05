"""Phase 19A — security primitive tests (SSRF guard, redaction, safe errors)."""
from __future__ import annotations

import httpx
import pytest

from jobagent import security
from jobagent.security import (UrlValidationError, redact_secrets,
                               redact_url, safe_request, sanitize_error_message,
                               validate_url)


# ---------------------------------------------------------------- validate_url

class TestValidateUrl:
    def test_accepts_public_https(self, monkeypatch):
        monkeypatch.setattr(security, "_resolve_all", lambda host: ["93.184.216.34"])
        check = validate_url("https://boards.greenhouse.io/acme/jobs/123")
        assert check.scheme == "https"
        assert check.host == "boards.greenhouse.io"
        assert check.port == 443

    def test_accepts_public_http_with_port(self):
        check = validate_url("http://jobs.example.com:8080/apply", resolve_dns=False)
        assert check.port == 8080

    @pytest.mark.parametrize("url", [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,hello",
        "gopher://example.com",
    ])
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(UrlValidationError) as e:
            validate_url(url)
        assert e.value.code == "invalid"

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/",
        "http://127.1/",                       # short-form loopback
        "https://localhost/api",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/router",
        "http://172.16.0.9/internal",
        "http://172.31.255.255/x",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://metadata.google.internal/",           # GCP metadata
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",          # IPv4-mapped IPv6 bypass
        "http://[fe80::1]/",                   # link-local v6
        "http://[fc00::1]/",                   # ULA
        "http://[fd12:3456:789a::1]/",
    ])
    def test_rejects_internal_targets(self, url):
        with pytest.raises(UrlValidationError) as e:
            validate_url(url, resolve_dns=False)
        assert e.value.code in ("blocked", "invalid")

    def test_rejects_dotless_host(self):
        with pytest.raises(UrlValidationError) as e:
            validate_url("http://intranet/secret", resolve_dns=False)
        assert e.value.code == "blocked"

    def test_rejects_empty_and_garbage(self):
        for bad in ("", None, "not a url", "     "):
            with pytest.raises(UrlValidationError):
                validate_url(bad)

    def test_rejects_oversized_url(self):
        with pytest.raises(UrlValidationError):
            validate_url("https://example.com/?" + "a" * 2100)

    def test_dns_resolution_blocked_when_private(self, monkeypatch):
        # example-internal.test resolves to a private IP -> must be blocked.
        monkeypatch.setattr(security, "_resolve_all", lambda host: ["10.1.2.3"])
        with pytest.raises(UrlValidationError) as e:
            validate_url("https://internal-corp.test/jobs")
        assert e.value.code == "blocked"
        assert "10.1.2.3" not in e.value.reason  # never leak the IP

    def test_dns_rebinding_all_records_checked(self, monkeypatch):
        # One public + one private A record -> still blocked (rebinding guard).
        monkeypatch.setattr(security, "_resolve_all",
                            lambda host: ["93.184.216.34", "192.168.0.1"])
        with pytest.raises(UrlValidationError):
            validate_url("https://rebind.test/apply")

    def test_dns_failure_raises(self, monkeypatch):
        monkeypatch.setattr(security, "_resolve_all", lambda host: [])
        with pytest.raises(UrlValidationError) as e:
            validate_url("https://nonexistent.invalid/x")
        assert e.value.code == "dns"

    def test_valid_public_dns_passes(self, monkeypatch):
        monkeypatch.setattr(security, "_resolve_all", lambda host: ["93.184.216.34"])
        check = validate_url("https://boards.example.org/jobs")
        assert check.host == "boards.example.org"


# ---------------------------------------------------------------- safe_request

class TestSafeRequestRedirects:
    def test_redirect_to_blocked_target_rejected(self, httpserver):
        # Local server (loopback allowed, test mode) redirects to a
        # cloud-metadata address — link-local stays blocked in every mode.
        httpserver.expect_request("/start").respond_with_data(
            status=302, headers={"Location": "http://169.254.169.254/meta"})
        with pytest.raises(UrlValidationError):
            safe_request(httpserver.url_for("/start"), allow_loopback=True)

    def test_redirect_chain_revalidated_each_hop(self, httpserver):
        # hop1 -> hop2 (loopback, allowed in test mode) -> private range (blocked).
        httpserver.expect_request("/hop1").respond_with_data(
            status=302, headers={"Location": "/hop2"})
        httpserver.expect_request("/hop2").respond_with_data(
            status=302, headers={"Location": "http://10.0.0.1/x"})
        with pytest.raises(UrlValidationError):
            safe_request(httpserver.url_for("/hop1"), allow_loopback=True)

    def test_safe_redirect_chain_succeeds(self, httpserver):
        httpserver.expect_request("/hop1").respond_with_data(
            status=302, headers={"Location": "/hop2"})
        httpserver.expect_request("/hop2").respond_with_data("final page")
        resp = safe_request(httpserver.url_for("/hop1"), allow_loopback=True)
        assert resp.status_code == 200
        assert resp.text == "final page"

    def test_redirect_loop_terminates(self, httpserver):
        httpserver.expect_request("/loop").respond_with_data(
            status=302, headers={"Location": "/loop"})
        with pytest.raises(UrlValidationError) as e:
            safe_request(httpserver.url_for("/loop"), allow_loopback=True)
        assert e.value.code == "redirects"

    def test_loopback_blocked_in_production_mode(self, httpserver):
        # Without allow_loopback the local test server itself is blocked.
        httpserver.expect_request("/x").respond_with_data("ok")
        with pytest.raises(UrlValidationError) as e:
            safe_request(httpserver.url_for("/x"))
        assert e.value.code == "blocked"


# ---------------------------------------------------------------- redaction

class TestRedactSecrets:
    @pytest.mark.parametrize("secret", [
        "gsk_Abc123def456ghi789jkl012",
        "sk-proj-abcdefghijklmnop1234",
        "github_pat_11A3QXTOA0abcdefGHIJKL",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "ya29.a0ARrdaM-abcdefghijk",
    ])
    def test_known_key_formats_masked(self, secret):
        out = redact_secrets(f"call failed with key {secret} at line 3")
        assert secret not in out

    def test_bearer_header_masked(self):
        out = redact_secrets("Authorization: Bearer eyJhbGciOi.very.long.token.value")
        assert "eyJhbGciOi" not in out
        assert "Bearer ***" in out

    def test_key_value_pairs_masked(self):
        out = redact_secrets("api_key=supersecretvalue123 token: 'anothersecret99'")
        assert "supersecretvalue123" not in out
        assert "anothersecret99" not in out

    def test_long_base64_blob_masked(self):
        blob = ("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2"
                "d3h5ejAxMjM0NTY3ODlhYmNkZWZnaGlqa2xtbjA=" )
        out = redact_secrets(f"payload: {blob}")
        assert blob not in out

    def test_normal_text_untouched(self):
        text = "Applied to Greenhouse job 12345 at Acme Corp, Hyderabad. Match 82."
        assert redact_secrets(text) == text

    def test_none_and_non_string(self):
        assert redact_secrets(None) == ""
        assert redact_secrets(42) == "42"


class TestRedactUrl:
    def test_strips_query_secrets(self):
        out = redact_url("https://ats.example.com/apply?job=123&token=abc&foo=bar")
        assert "token=abc" not in out
        assert "job=123" in out
        assert "foo=bar" in out

    def test_strips_userinfo(self):
        out = redact_url("https://user:pass@ats.example.com/apply")
        assert "user:pass" not in out

    def test_empty(self):
        assert redact_url("") == ""
        assert redact_url(None) == ""


# ---------------------------------------------------------------- safe errors

class TestSanitizeError:
    def test_no_stack_no_paths(self):
        try:
            open("/home/someuser/secret/config.yaml")
        except OSError as exc:
            out = sanitize_error_message(exc, context="resume-load")
            assert "/home/someuser" not in out
            assert "resume-load" in out
            assert "FileNotFoundError" in out

    def test_secrets_inside_error_masked(self):
        err = RuntimeError("connection refused with gsk_Ab12Cd34Ef56Gh78Ij90Kl12")
        out = sanitize_error_message(err)
        assert "gsk_Ab12Cd34Ef56Gh78Ij90Kl12" not in out

    def test_truncates_long_messages(self):
        err = ValueError("x" * 1000)
        assert len(sanitize_error_message(err)) < 320

    def test_empty_message(self):
        assert sanitize_error_message(ValueError()) == "ValueError"


# ---------------------------------------------------------------- integration

class TestVerifyEngineUsesGuard:
    """The verification engine must route fetches through the SSRF guard."""

    def test_verify_job_row_rejects_internal_url(self):
        from jobagent.verify import verify_job_row
        row = {
            "id": "x", "source": "test", "company": "Acme", "title": "SDE",
            "url": "http://169.254.169.254/latest/meta-data",
            "apply_url": "http://169.254.169.254/latest/meta-data",
            "description": "d", "posted_at": "2026-11-01", "discovered_at": "2026-11-04",
            "deadline": "", "salary_min": None, "salary_max": None,
            "salary_text": "", "fresher_flag": 0, "experience_min": None,
            "fraud_flags": "[]", "status": "new",
            "first_verified_at": None, "last_verified_at": None,
        }
        result = verify_job_row(row)
        # The metadata URL must be refused before any fetch is attempted.
        assert result.state == "suspicious"
        assert any("private" in f for f in result.fraud_flags)

    def test_verify_job_row_blocks_redirect_to_internal(self, monkeypatch):
        """A public URL that redirects to a private target stays blocked."""
        import httpx
        from jobagent import verify as verify_mod
        from jobagent.security import safe_request as _unused  # noqa: F401

        def fake_safe_request(url, *, method="GET", client=None, **kw):
            from jobagent.security import UrlValidationError
            raise UrlValidationError("'evil.test' resolves to a private network address")

        monkeypatch.setattr("jobagent.security.safe_request", fake_safe_request)
        row = {
            "id": "x", "source": "test", "company": "Acme", "title": "SDE",
            "url": "https://evil.test/job", "apply_url": "https://evil.test/job",
            "description": "d", "posted_at": "2026-11-01", "discovered_at": "2026-11-04",
            "deadline": "", "salary_min": None, "salary_max": None,
            "salary_text": "", "fresher_flag": 0, "experience_min": None,
            "fraud_flags": "[]", "status": "new",
            "first_verified_at": None, "last_verified_at": None,
        }
        result = verify_mod.verify_job_row(row)
        assert result.state == "suspicious"
