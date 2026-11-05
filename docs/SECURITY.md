# Security Model — JobApply

Phase 19 hardened the platform's security. This document describes what is
protected, how, and what remains the user's responsibility.

## 1. Deployment model

JobApply is a **single-user, local-first tool**. The backend binds to
`127.0.0.1` by default. If you expose the port (Docker, SSH tunnel, shared
host) you **must** set `DASHBOARD_TOKEN` — every `/api/*` call then requires
the `X-Dashboard-Token` header (`/health/live`, `/health/ready` and
`/api/health` stay open by design, they leak no data).

Multi-user operation is NOT supported: there is no per-user database
isolation. Do not expose the dashboard to untrusted networks.

## 2. Secrets

### Where secrets live

| Secret | Storage | Notes |
|---|---|---|
| Groq / OpenAI / Anthropic keys | `.env` (env vars) **or** the encrypted vault | Vault preferred |
| Gmail OAuth client + refresh token | `.env` / vault (`gmail` provider) | Read-only scope only |
| Dashboard token | `DASHBOARD_TOKEN` env var | Required when non-local |
| Credential vault master key | `data/.credentials.key` (chmod 0600) or `JOBAGENT_MASTER_KEY` env var | **Never in the DB, never committed** |

### The credential vault (`jobagent/credentials.py`)

* Secrets are encrypted with **AES-256-GCM** (authenticated encryption) before
  touching SQLite. Tampering is detected (GCM tag), not silently ignored.
* The master key is generated once into `data/.credentials.key` (0600) or read
  from `JOBAGENT_MASTER_KEY` (base64, 32 bytes).
* **Wrong key = loud failure**: masked listings show `(locked — wrong master
  key)` and decryption raises; nothing pretends to work.
* Secrets are **never** returned by any API — only masked previews
  (`gsk_•••••7890`).
* If a master key cannot be established the vault **refuses to store** — it
  fails closed rather than storing plaintext.
* Revocation is a hard flag: revoked credentials fail every lookup.

### Key backup & rotation

```bash
# back up the master key (without it, vault contents are unrecoverable by design)
cp data/.credentials.key /safe/place/credentials.key

# rotate: set a new key env var, then re-enter credentials via the UI
# (System → Credential Manager) or the API
export JOBAGENT_MASTER_KEY=$(python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
```

## 3. SSRF protection (`jobagent/security.py`)

Every external URL the agent fetches is validated:

* scheme allowlist (`http`/`https` only — no `file:`, `ftp:`, `data:`)
* blocked targets: loopback, private ranges (10/8, 172.16/12, 192.168/16),
  link-local (incl. `169.254.169.254` cloud metadata), ULA, multicast,
  reserved, IPv4-mapped-IPv6, numeric shorthand IPs (`127.1`), dotless hosts
* **DNS resolution is checked for every A/AAAA record** (DNS-rebinding guard)
* **redirects are followed manually and every hop re-validated** — a public
  URL that redirects to `169.254.169.254` is refused
* a known-good URL that later redirects internal marks the job `suspicious`

`allow_loopback=True` exists ONLY for local test fixtures; metadata and
private ranges stay blocked even then.

## 4. Prompt-injection defence

All external text (job descriptions, careers pages, emails, ATS form content,
uploaded documents) enters prompts through `llm.untrusted()`: clipped,
tagged `source="external, untrusted"`, with an explicit instruction that the
content is data to analyse, never commands to follow. Layered on top,
`scan_injection()` logs common override patterns (`ignore previous
instructions`, `submit this application now`, …) so attempts are visible.
External content can never override: system rules, user approval, submission
policies.

## 5. AI answer grounding

AI-generated answers must come from verified profile data. The agents share a
`<candidate_context>` block of verified facts; missing facts surface as
`needs_human`, never as guesses. The code never fabricates experience, CGPA,
education, certifications, salary history, notice period, work authorization
or backlog status. Sensitive/legal questions are always handed to the human
(`execution.sensitive_fields`).

## 6. Submission safety

* **Approval gate**: the lifecycle state machine refuses any
  `SUBMISSION_ATTEMPTED` without a recorded `user`-actor `APPROVED`
  transition (graph-enforced **and** DB-level defense-in-depth).
* **Confirmation evidence**: a button click is not a submission. Only a
  platform confirmation page/reference counts; otherwise the state becomes
  `SUBMISSION_UNCERTAIN` and only a human verdict can move it on.
* **Idempotency keys** make submission replay safe; concurrent workers are
  serialized per job (`apply_locks` + `BEGIN IMMEDIATE` transactions).
* CAPTCHA / MFA / login walls / legal declarations are never bypassed.

## 7. Logging hygiene

`security.redact_secrets()` masks key-shaped strings (gsk_, sk-, github_pat_,
Bearer tokens, api_key=..., long base64 blobs) before anything is stored or
returned. Execution logs record job/step/action/error-class, never cookies,
credentials or full sensitive form values. Error messages shown to the UI are
sanitized (no stack traces, no host paths).

## 8. Uploads

PDF-only, 10 MB cap, content-based validation (not extension alone), stored
under `data/`/`output/` which are never served as executable. Resumes are
only reachable through authenticated API endpoints, never public URLs.

## 9. CI security scanning

`.github/workflows/ci.yml` runs **gitleaks** over the full git history on
every push/PR and a `pip-audit` dependency check (advisory until triaged).
The workflow uses least-privilege permissions (`contents: read`).

## 10. Known limitations (honest disclosure)

* No rate limiting on the API itself (single-user local threat model).
* No CSRF protection (token-header auth is not cookie-based; browser-default
  requests cannot set the header).
* The vault key file is only as safe as the host filesystem.
* `pip-audit` findings are reported, not yet blocking.
