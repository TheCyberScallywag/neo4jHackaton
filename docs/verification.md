# Verification record

Recorded on 13 November 2026 on macOS (Darwin 25.6, arm64), CPython 3.14.5, uv 0.12.19, Relate CLI
v1.0.1, `relate-db` 1.0.1, Docker 29.9.2 and Node 24.21. These are the actual results from this
session.

## Reference PoC and engine checks

| Check | Result |
|---|---|
| Original PoC: `uv run --locked python dvn.py matches` in `~/Desktop/data-volunteer-network-poc` | **Pass.** Output matched the PoC README (Winter Outreach: Bryn for van driving, Asha for cooking, Asha and Farah for listening; 27 nodes). The PoC was left unchanged; uv created its own ignored `.venv` there. |
| `relate-db==1.0.1` installs on CPython 3.14.5 (abi3 wheel) | **Pass** |
| Write → close → reopen with the embedded binding | **Pass.** Data persisted in a single 32 KB `.rl` file. |
| `relate` 1.0.1 CLI reads a store written by the binding | **Pass** |
| Concurrent opener while a connection is held (binding or CLI, read-only included) | **Locked** (`storage locked`, CLI exit code 5). This is why the app opens per unit of work and uses a single in-process writer with bounded retry. |
| Open, read and close latency on the demo graph | About 8 ms |
| Cypher features used (`EXISTS {}` subqueries, scoped `CALL (p) {}`, pattern predicates, `UNWIND` upserts, `DETACH DELETE`) | **Pass** on Relate 1.0.1 (prototyped, then covered by the tests) |

**Engine decision:** use the embedded binding `relate-db==1.0.1`, pinned in `pyproject.toml` and
`uv.lock`. It runs on 3.14 and produces files the 1.0.1 CLI can read, so the PoC's subprocess CLI
adapter is not needed. The CLI is still used for inspection.

## Automated tests

```text
$ uv run task lint
All checks passed!
48 files already formatted

$ uv run task test
======================== 81 passed, 2 skipped ========================
TOTAL coverage 87% (src/app/admin excluded; serve.py and the AuraDB module are not exercised)
SKIPPED tests/test_aura.py: no designated dev AuraDB credentials (AURA_URI / AURA_PASSWORD)
SKIPPED tests/test_compose.py: COMPOSE_WEB_URL not set   (run separately, below)
```

| Area | Tests |
|---|---|
| Relate adapter | write/close/reopen, rollback, lock retry then `GraphBusy`, bounded reads, syntax errors, bundled `data/demo.rl` equals a fresh build, CLI reads the bundle (`test_relate_store.py`) |
| Identity | email normalisation and uniqueness, signup grants no role, Argon2id hashes, hashed session tokens, cookie flags over HTTP and HTTPS, CSRF on every state-changing form, revocation, security headers (`test_auth.py`) |
| Organisations | one login across both organisations, switching without signing in again, switching is not joining, opt-out removes access and candidacy, role checks per organisation, forged organisation, submission and project IDs, scoped candidate lists without contact details (`test_orgs.py`) |
| Suggestions | private until approved, submitter and target set on the server, catalogue validation, changes requested → revise → approve, stale/old-revision/replayed approvals rejected (exactly one approval recorded), self-approval blocked, volunteers can't review, rejected suggestion keeps its feedback, approval publishes, withdrawal removes from browse and matching (`test_submissions.py`) |
| Matching rules | one test per row of the rules table, plus preferred-only, has-but-not-offer, profile edits reaching Relate, and gaps on the reviewer page (`test_matching.py`) |
| Projection | startup projection, idempotent replay, failure → pending → withheld → counted as failing → replay at restart, approved but not published until projected, persistence across restarts (`test_projection.py`) |
| Mobile API, CLI, configuration | bearer sessions, membership checks, cookies rejected on the API, inspect and matches output, production guards, empty Compose variables, configurable organisation names (`test_api.py`, `test_cli.py`) |

**Mobile (Ionic):** `npm run lint` reported "All files pass linting". `npm run test:ci` passed
**25 tests in 10 files** (re-run after adding RBAC, onboarding, Opportunities and Notifications). `npm run build -- --configuration production` succeeded. The Docker
`mobile` image runs the same lint and test stage during its build, and that build passed.

## Clean-start paths

| Path | Result |
|---|---|
| uv: `DATA_DIR=<empty> PORT=8090 uv run python -m app.serve web` | **Pass.** The app seeded on first start by copying `data/demo.rl`, and `/healthz` reported 0 pending. A curl sign-in as Asha worked, and the home page listed both organisations' projects. |
| uv: approve over HTTP as Rhiannon, then stop the server | **Pass.** `relate --read-only` on the writable store showed the new *Outreach Data Audit* project. |
| uv: restart on the same data directory | **Pass.** The project was still listed, so the graph and SQLite persisted. |
| `relate` CLI on the live writable store while the server was running | **Pass** (the store is opened per unit of work) |
| Compose: `docker compose up -d --build --wait` | **Pass** on alternate ports (`WEB_PORT=18080 ADMIN_PORT=18501 MOBILE_PORT=18100`). The default ports 8080, 8501 and 8100 were held by another, unrelated local stack, which was left running. Host ports were made configurable for this reason. |
| Compose smoke: `pytest -m compose` against the stack | **1 passed.** Covered web health and sign-in, sign-in through the mobile nginx proxy to the API, and admin health. |
| Compose: `docker compose exec web python -m app.cli inspect` and `status` | **Pass** (expected table; 0 pending) |
| Admin console script (Streamlit `AppTest` inside the admin container, over the API) | **Pass.** No exceptions. Sam (system admin) saw the screening, organisations, users-and-roles and system tabs; Mo (moderator) saw screening only; Kofi (org admin) saw only his organisation. Run locally, Mo approved Nia through the console and the API then reported her as `active`. |
| Packaging check caught by Compose | The first Compose run of the new console failed: `httpx` was only a dev dependency. It is now in the `admin` extra; after rebuilding, the run above passed. |

A first Compose attempt failed because pydantic-settings rejected an empty `ORG_NAMES` from
Compose. This was fixed with `env_ignore_empty=True` and a regression test.

## RBAC and onboarding (added later)

`tests/test_rbac_onboarding.py`, 15 tests, all through the JSON API plus web-page parity:

- full volunteer onboarding: draft → screening → active → eligible → both yes → team, with
  `ON_TEAM` in Relate and a notification at each step
- moderation guards: self-vetting, stale versions, reasons required, email visible to admins only
- Nia's vetting closing the First aid gap
- suspension removing a volunteer from matching
- lead-side and volunteer-side authorisation
- lapse and revival of offers
- team removal
- org admins limited to their own organisation, and system admins reaching all of them
- reviewer versus org admin
- project lead reassignment
- system-admin-only endpoints
- project onboarding notifications
- JSON errors and OpenAPI tags
- notification read state
- web Opportunities, project lead and org volunteers pages

## AuraDB

**Not run.** No designated dev AuraDB instance or credentials were available in this session.
`aura-push`, `aura-parity` and `tests/test_aura.py` are implemented but **unverified against a
live instance**. To run them, see the README section "Optional AuraDB". If Aura rejects any of the
syntax (for example scoped `CALL (p) {}`, which needs Neo4j 5.23 or later), the parity command
reports the failing organisation.

## Blockers and remaining work

- **AuraDB parity unverified** (no credentials, as above).
- **Not done in this session, left to the owner:** recording the demo video, checking the current
  Devpost rules (the overview and rules word the extension policy differently), adding the
  repository and video links to `docs/devpost.md`, committing, publishing and submitting.
- **Git:** nothing has been committed. The repository has no commits yet. The untouched template
  baseline is recorded as tree `ecfee3ce58cd966c4ccaf375e5811969da9aece7`. A reviewable history
  would be: commit the baseline, then commit this work on a `feature/` branch (see "Suggested
  commits" below).
- **Not built (out of scope for the slice):** availability and scheduling; reviewer invitations
  from the UI (roles are granted by the seed or `app.cli grant-reviewer`); account deletion and
  data export; rate limiting on sign-in; email verification; a native Capacitor build of the
  companion (only the web build was tested).
- `examples/brands/neutral/` is a leftover from the template and is unused. It is harmless and
  can be deleted.
- Coverage gaps in the tests: `serve.py` and the AuraDB module are untested, and the CLI's
  `status`, `drain` and `grant-reviewer` commands are exercised only manually.

## Suggested commits (for the owner; not run)

```sh
git checkout -b feature/data-volunteer-network
git read-tree ecfee3ce58cd966c4ccaf375e5811969da9aece7 && git commit -m "TASK: template baseline"
git add -A && git commit -m "ADDED: Data Volunteer Network on Relate"
```
