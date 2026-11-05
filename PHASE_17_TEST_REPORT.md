# Phase 17 Test Report

Generated 2026-11-04 20:45 by `scripts/e2e_report.py` — every number below comes from a real
test execution during this run. Nothing is estimated or copied from previous runs.

## Suite results

| Suite | Command | Result | Passed | Failed |
|---|---|---|---|---|
| Offline unit/integration suite | `pytest tests/ -m not e2e and not live` | 136 passed, 12 deselected, 267 warnings in 16.52s | 136 | 0 |
| E2E sandbox ATS suite (Chromium, local fixture) | `pytest tests/e2e/test_form_engine.py -m e2e` | 5 passed, 22 warnings in 11.58s | 5 | 0 |
| Live public-API adapters | `pytest tests/e2e/test_live_adapters.py -m live` | 7 passed in 41.79s | 7 | 0 |

**Total: 148 passed, 0 failed.**

## Live listing counts (this run)

Listings actually retrieved from each public API during report generation
(after keyword + India/worldwide filters):

| Source | Listings returned |
|---|---|
| remotive | 11 |
| arbeitnow | 300 |
| himalayas | 1 |
| jobicy | 1 |

## What the suites cover

- **Offline (unit/integration, no network):** verification engine (10 checks, 6 states,
  quality score, SSRF guard, typo-squat detection), queue gates + limits + actions,
  execution modes (assisted/authorized, sensitive-field stops, confirmation evidence),
  ATS registry detection for 15+ platforms, 8 Groq agents (untrusted framing, prompt
  versions, KB-first answers), search profiles + salary/location/skills filters,
  email classification + application matching + dedup, external-link source registry,
  new adapters (himalayas/jobicy) with mocked HTTP, DB migrations, and the full
  pre-existing 76-test regression suite.
- **E2E sandbox (real Chromium, local HTML fixture, NO external site, NO submission):**
  semantic field extraction (labels/aria/legends - not CSS selectors), text/select/
  radio/checkbox filling, file upload, multi-step navigation, sensitive-field
  (Aadhaar/consent) detection, CAPTCHA detection (never bypassed), apply-session
  checkpoint persistence, authorized-mode refusal to submit when sensitive fields
  remain.
- **Live adapters (real public APIs, read-only):** Remotive, Arbeitnow, Himalayas,
  Jobicy, Instahyre API reachability, LinkedIn guest search, one Greenhouse board.
  Never submits anything.

## Known limitations

1. LinkedIn guest search is rate-limited by design; low page counts only.
2. Naukri adapter needs a visible browser (captcha appears on datacenter IPs) -
   covered by the human-in-the-loop flow, not by automated tests.
3. Live results vary by the day's feed content; an empty result is a valid pass
   for reachability but the count may be 0.
4. Gmail OAuth sync is tested with mocks; real OAuth requires user credentials.
5. Authorized submission is tested against the sandbox fixture only - by policy it
   never runs against a real employer in tests.