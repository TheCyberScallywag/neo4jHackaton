# Devpost submission draft

**Status: draft. Not submitted.** The project owner should check the current hackathon rules
first, add the video and repository links, and submit. The video has not been recorded yet.

- **Theme:** Context, connected
- **Project name:** Data Volunteer Network
- **Tagline:** One volunteer account for helping several organisations, with graph-backed
  explanations of why people and projects fit.

## Inspiration

Skilled volunteers often help more than one organisation. Each organisation keeps its own
spreadsheet of who can do what, so volunteers retype their skills everywhere and coordinators
can't see who might help or what is still missing. We wanted one profile that the volunteer
controls, shared only with the organisations they choose, and matches that explain themselves.

## What it does

- **One account, many organisations.** Sign up once, opt in to the organisations you want to
  help, and switch between them without signing in again. Each organisation sees you only after
  you opt in there.
- **A profile the volunteer controls.** For each skill in a shared catalogue (cooking, van
  driving, active listening, Welsh translation, literacy tutoring, bookkeeping, fundraising,
  carpentry, gardening, data analysis and more), you choose *have*, *offer* or *exclude*. You can
  also prefer or exclude causes such as Homelessness, Education, Food Security and Environment.
- **Verified projects.** Anyone signed in can suggest a project to one organisation. Only that
  organisation's reviewers can request changes, reject or approve it, and never their own
  suggestions. An approval records who verified which revision and when. Reviewers can also
  withdraw a project.
- **Explainable matching.** Volunteers see ranked opportunities across their organisations. Each
  shows the skills they match, whether they share the cause, and what is still needed from
  others. Coordinators see eligible candidates for each required skill, and the required skills
  nobody can cover.
- **Vetted onboarding with role-based access.** A moderator screens every new volunteer before
  they can be matched. When Relate finds a vetted volunteer eligible for an active project, the
  volunteer and the project lead are both notified in the app, and the volunteer joins the team
  once both say yes. Projects go through review, scoping and activation, with a notification at
  every step. System admins, moderators, org admins, reviewers and project leads each see only
  what their role allows.
- **One API for every action** (`/api/v1`, OpenAPI docs at `/api/docs`). The **mobile
  companion** (Ionic: matches, opportunities, notifications, profile) and the **Streamlit admin
  console** (screening, organisation management, roles, graph health) are both pure API clients.

## How we built it: how we use Relate, and why

Relate is the matching engine. Organisations, opaque volunteer IDs, skills, causes, approved
projects and every offer, preference and exclusion are nodes and relationships in one embedded,
single-file `.rl` store. A single Cypher query makes each matching decision by walking:

`Organisation -OWNS_PROJECT-> Project -SERVES_CAUSE-> Cause`
`Project -REQUIRES_SKILL-> Skill <-HAS_SKILL / OFFERS_SKILL- Volunteer -VOLUNTEERS_WITH-> Organisation`

`VOLUNTEERS_WITH` exists only for vetted volunteers, so the graph itself enforces "no matching
before screening". Accepted placements become `Volunteer -ON_TEAM-> Project`. The same query that
explains a match also drives the eligibility notifications.

Exclusions are checked with `NOT EXISTS {}`: an `EXCLUDES_CAUSE` edge to the project's cause, or
an `EXCLUDES_SKILL` edge to any of its required skills. `PREFERS_CAUSE` sets the ranking. The
same query returns the explanation (matched skills, optional skills, remaining needs, preferred
cause), so the explanation can never disagree with the decision.

- **SQLite** stays the transactional source of truth for identity, sessions, roles, profile
  edits and review history. Passwords, emails, sessions and private notes never reach the graph.
- **An outbox projector** reconciles each change into Relate with idempotent upserts and removal
  of stale edges. Results are withheld while a relevant update is pending, so nobody sees a stale
  match, and a restart replays anything left over.
- **The embedded `relate-db` binding** (PEP 249) runs in-process on Python 3.14. A real
  persistent store, `data/demo.rl`, is committed so judges can inspect it with `relate` or
  `uv run task inspect`.
- An optional, explicitly invoked AuraDB push and parity check runs the same Cypher in the cloud.

**Stack:** Python 3.14, uv, FastAPI, Jinja2, SQLite, Relate 1.0.1, Ionic and Angular, Streamlit
and Docker Compose.

## Challenges

- Relate locks the store exclusively, including for readers. We open it per unit of work, keep a
  single writer per process, and retry a bounded number of times when another process holds it.
  That lets the `relate` CLI inspect the live store while the app runs.
- Keeping two stores consistent without showing stale matches. The outbox reconciles to the
  current state rather than replaying events, and SQL freshness checks hold results back until
  the graph catches up.
- Agreeing on one precise reading of the rules. An exclusion beats any other matching skill, an
  excluded optional skill only drops out of scoring, and "has but doesn't offer" is not eligible.

## Accomplishments

- An end-to-end slice: signup, organisation selection, suggestion, scoped approval, publication
  and explainable matching. It runs from a clean clone with uv or with Compose.
- 66 automated tests against real Relate stores (plus gated AuraDB and Compose tests), including forged-ID tenant isolation, stale
  approvals, exclusion precedence and restart/replay.
- Changing one exclusion visibly changes the Relate result: a volunteer drops out of a project,
  and the coordinator sees new coverage gaps.

## What we learned

Modelling consent as edges (`VOLUNTEERS_WITH`, `OFFERS_SKILL`, `EXCLUDES_SKILL`) makes privacy
and matching rules easy to see in the query itself. An embedded graph is a good fit for
portable, reproducible demos.

## What's next

Availability and scheduling, reviewer invitations instead of operator-granted roles, richer
explanations (for example, "two more people with first aid would cover this"), and a native
build of the companion.

## Reuse and AI disclosure

This entry builds on the owner's own full-stack template and an earlier command-line proof of
concept. Most of the code was written with significant help from an AI coding assistant (Claude,
Anthropic). [docs/reuse-and-attribution.md](reuse-and-attribution.md) lists what was reused and
what was built for this entry.

## Try it

```sh
uv sync --locked && uv run task web        # http://localhost:8080
# demo login: asha@demo.invalid / demo-password-1 (reviewers: rhiannon@…, kofi@…)
uv run task inspect                        # inspect the bundled Relate graph
```
