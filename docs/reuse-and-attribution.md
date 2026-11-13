# Reuse, new work, AI assistance and licences

This file is for judges and reviewers. It says what this entry reuses, what was built for it, how
AI was used, and the licences involved. **The project owner should check it against the
hackathon's current rules before submitting.** The Devpost overview and the rules have worded the
policy on extending earlier work differently, so the current rules decide which text applies.

## Starting points that were reused

### 1. The full-stack template (this repository's baseline)

This repository started as an uncommitted copy of the owner's `sgaffald` template
(Copier answers in `.copier-answers.yml`; MIT, © 2026 Cyber Scallywags). Before any change, the
untouched template was recorded as git tree object `ecfee3ce58cd966c4ccaf375e5811969da9aece7`.
Run `git diff ecfee3ce58cd966c4ccaf375e5811969da9aece7` to review everything that changed.

| Kept from the template | Status |
|---|---|
| Repository scaffolding: `LICENSE`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CONTRIBUTING.md`, `cliff.toml`, commit and branch hook scripts, pre-commit setup | kept; pre-commit trimmed |
| `brand/` and `examples/brands/neutral/` (CC0 neutral brand kit: colours, mark, favicon) | kept unchanged; the web app uses the mark, favicon and colours |
| `src/app/serve.py` (one entry point per service) | rewritten for `web` and `admin` |
| `src/app/config.py` (pydantic-settings pattern) | rewritten with new settings |
| `src/app/meta.py` (reads `brand.toml`) | simplified |
| `docker/Dockerfile` (uv multi-stage build, non-root) | adapted: one image for web and admin |
| `docker/mobile.Dockerfile`, `docker/mobile.nginx.conf` (Ionic build with lint and test gate, nginx proxy) | adapted: proxy now points at the web app's `/api/` |
| `mobile/` Angular, Ionic and Capacitor project setup (build config, lint config, `main.ts`, interceptor pattern, token-store injection token) | kept; app code rewritten (see below) |
| `src/app/admin/` Streamlit service slot | rewritten |
| `.github/workflows/codeql.yml` | kept |

| Removed from the template (not applicable to this entry) |
|---|
| FastUI web UI, the JWT mock sign-on API, the Pydantic AI agent and the `Item` sample entity |
| Neo4j server container and Neo4j-backed item store (replaced by embedded Relate) |
| Docs build pipeline (Zensical, Sphinx), template handbook, generated reference pages |
| Brand-sync, TypeScript type generation, `new_entity` and template-smoke scripts, and their tests |
| Production compose overlay, release, scorecard and issue workflows |

### 2. The earlier proof of concept (`data-volunteer-network-poc`, kept intact)

This is a separate folder, left unmodified. It is a 180-line Python 3.14 CLI (`dvn.py`) that
matched fictional volunteers to projects through the Relate CLI (v1.0.1) on a bundled
`data/demo.rl`, with an optional AuraDB backend. It was run during this session and its output
matched its README (Winter Outreach: Bryn for driving, Asha for cooking, Asha and Farah for
listening; 27 nodes).

| Reused from the PoC | How |
|---|---|
| Cause and skill vocabulary (`data/fixtures.json`) | extended to 5 causes and 15 skills; IDs made canonical (`driving` → `van-driving`, `listening` → `active-listening`, `welsh` → `welsh-translation`, `tutoring` → `literacy-tutoring`, `data` → `data-analysis`) |
| Fictional first names (Asha, Bryn, Cerys, Dev, Gwen, Farah) and the Winter Outreach and Food Pantry project ideas | reused in a new scenario file, `data/fixtures/demo.json` |
| Pattern of a committed, read-only single-file `.rl` store plus a seed command | kept: `data/demo.rl` and `python -m app.cli bundle` |
| Relate v1.0.1 pin, `--read-only` inspection commands, the `relate` CLI workflow | kept and documented |
| AuraDB connection handling (environment variables, `neo4j+s://` check, `RoutingControl`) and the scope-prefixed seed idea | reimplemented in `src/app/graph/aura.py` as a push plus parity check |
| Compose single-volume idea | extended to a three-service stack |

The PoC's matching query (`HAS_SKILL` plus `INTERESTED_IN`) was **not** reused. The new query adds
membership, offers versus skills, exclusions, preferences, optional skills, ranking and gaps.

## Built for this entry (new work in the contest period)

- Multi-organisation identity: signup, sign-in and sign-out with Argon2id, opaque revocable
  sessions, CSRF, active-organisation switching kept separate from opting in or out, and
  server-side role and tenant checks (`src/app/auth.py`, `src/app/services.py`,
  `src/app/web/main.py`).
- SQLite transactional model: users, sessions, memberships, roles, profile, submissions,
  revisions, reviews and outbox (`src/app/sqlstore.py`).
- Suggestion workflow: submit, request changes, revise, reject, approve, publish and withdraw,
  with optimistic concurrency and a self-approval block.
- Relate graph model and all Cypher: matching with exclusions, preferences, optional skills and
  deterministic ranking; coverage gaps; exclusion explanations; browse; inspection
  (`src/app/graph/cypher.py`).
- Embedded Relate adapter: single writer, bounded lock retry, bounded reads, transactions
  (`src/app/graph/store.py`).
- Outbox projector with idempotent reconcile, stale-edge removal, backoff, failure surfacing,
  withheld results and startup replay (`src/app/graph/projector.py`, `src/app/matching.py`).
- Server-rendered Jinja2 UI and stylesheet (`src/app/web/templates`, `src/app/web/static`).
- Bearer-token mobile JSON API (`src/app/web/api.py`) and a rewritten Ionic companion (sign-in,
  organisation switcher, explainable matches, a Profile page with accordions for editing skills
  and causes and for active and past projects, suggestion status) with new specs.
- RBAC (system admin, moderator, org admin, reviewer, project lead), volunteer vetting
  workflow, eligibility sweep with two-sided acceptance and `ON_TEAM` teams, in-app
  notifications (`src/app/services.py`, `src/app/placements.py`, `src/app/notify.py`).
- A JSON API covering every action, with OpenAPI docs (`src/app/web/api.py`).
- Streamlit admin console as a pure API client: screening, organisation management, users and
  roles, projection health and graph inspection.
- Operator CLI (reset, bundle, inspect, matches, status, drain, grant-reviewer, aura-push,
  aura-parity), the demo fixture, the bundled `data/demo.rl`, and the AuraDB push and parity
  check.
- Test suite (`tests/`): 83 tests (81 run locally; the AuraDB and Compose tests are gated), using
  temporary SQLite and real Relate stores; 25 mobile specs.
- Compose stack, CI workflow, and documentation (README, this file, `docs/verification.md`,
  `docs/devpost.md`, `docs/demo-script.md`).

## AI assistance (significant; disclose it)

Most of the code, tests, fixtures and documentation in this entry were written by an AI coding
assistant, **Claude (Anthropic), running in Claude Code**, in one working session on 13 November
2026. The project owner wrote the brief: stack, data-ownership rules, matching rules, judging
focus and submission materials. The assistant then:

- inspected the template and the PoC;
- ran the PoC and checked the Relate binding and CLI on Python 3.14;
- designed the schema, graph model and projection approach;
- wrote the application, mobile app, tests and docs;
- ran the tests, the uv path and the Compose stack, and reported the results in
  `docs/verification.md`.

The project owner remains responsible for reviewing, editing and submitting the work.

## Licences

| Component | Licence |
|---|---|
| This repository's code | MIT ([LICENSE](../LICENSE)) |
| `brand/`, `examples/brands/neutral/` (neutral brand kit) | CC0 1.0 ([brand/LICENSE](../brand/LICENSE)) |
| `data/fixtures/*.json`, `data/demo.rl` (synthetic, fictional data) | MIT, as part of this repository |
| Relate engine: `relate-db` 1.0.1 (PyPI) and `relate` 1.0.1 CLI | Apache-2.0 according to the package metadata; the CLI prints "Apache 2.0 (binary)". See https://relatedb.ai/docs/ |
| FastAPI 0.162 | MIT |
| Starlette, Uvicorn, Jinja2, httpx, pandas | BSD-3-Clause / BSD |
| python-multipart, Streamlit | Apache-2.0 |
| argon2-cffi, Pydantic, pydantic-settings, pytest | MIT |
| Neo4j Python driver 6.4 (optional) | Apache-2.0 AND Python-2.0 |
| Angular 22, Ionic 9, Ionicons 8, Capacitor 8 | MIT |
| RxJS 7.8 | Apache-2.0 |

The versions are those in `uv.lock` and `mobile/package-lock.json` when this was written.
*DataKind UK* and *Cyber Scallywags* are used only as illustrative, configurable names. Neither
endorsement nor partnership is implied, and no real organisation's branding or data is used.
