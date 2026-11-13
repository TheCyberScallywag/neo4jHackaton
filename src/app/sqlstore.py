"""SQLite: the transactional, authoritative store.

Holds identities, sessions, organisation membership and roles, profile edits, submissions,
review history and the projection outbox. None of the private columns here (email, password
hash, session tokens, private review notes) are ever written to the graph.
"""

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,          -- normalised: stripped + lower-cased
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    profile_version INTEGER NOT NULL DEFAULT 0,
    -- Onboarding: draft -> screening -> active (or info_requested / declined / suspended)
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                      'draft', 'screening', 'info_requested', 'active', 'declined', 'suspended')),
    status_version INTEGER NOT NULL DEFAULT 0,
    vetted_by     TEXT REFERENCES users(id),
    vetted_at     TEXT,
    created_at    TEXT NOT NULL
);

-- System-wide roles: admin (all access) and moderator (vets volunteers). Never from a form.
CREATE TABLE IF NOT EXISTS system_roles (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('admin', 'moderator')),
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (user_id, role)
);

CREATE TABLE IF NOT EXISTS vetting_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id             TEXT NOT NULL REFERENCES users(id),
    from_status          TEXT NOT NULL,
    to_status            TEXT NOT NULL,
    note_for_volunteer   TEXT NOT NULL DEFAULT '',
    private_note         TEXT NOT NULL DEFAULT '',  -- moderators and admins only
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT PRIMARY KEY,               -- sha256 of the opaque token; never the token
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('cookie', 'bearer')),
    csrf_token    TEXT NOT NULL,
    active_org_id TEXT REFERENCES organisations(id),
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    revoked_at    TEXT
);

CREATE TABLE IF NOT EXISTS organisations (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

-- Volunteering is opt-in per organisation; opting out keeps the row with opted_in = 0.
CREATE TABLE IF NOT EXISTS memberships (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id     TEXT NOT NULL REFERENCES organisations(id),
    opted_in   INTEGER NOT NULL CHECK (opted_in IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, org_id)
);

-- Organisation roles: org_admin (manages the organisation's projects and volunteers, reviews)
-- and reviewer (reviews suggestions). Granted by a system admin, the seed or the CLI only.
CREATE TABLE IF NOT EXISTS org_roles (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id     TEXT NOT NULL REFERENCES organisations(id),
    role       TEXT NOT NULL CHECK (role IN ('reviewer', 'org_admin')),
    granted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, org_id, role)
);

CREATE TABLE IF NOT EXISTS causes (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_skills (
    user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id),
    has      INTEGER NOT NULL CHECK (has IN (0, 1)),
    offers   INTEGER NOT NULL CHECK (offers IN (0, 1)),
    excluded INTEGER NOT NULL CHECK (excluded IN (0, 1)),
    CHECK (NOT (excluded AND offers)),
    PRIMARY KEY (user_id, skill_id)
);

CREATE TABLE IF NOT EXISTS profile_causes (
    user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cause_id TEXT NOT NULL REFERENCES causes(id),
    stance   TEXT NOT NULL CHECK (stance IN ('prefer', 'exclude')),
    PRIMARY KEY (user_id, cause_id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organisations(id),
    author_id         TEXT NOT NULL REFERENCES users(id),
    lead_id           TEXT REFERENCES users(id),        -- project lead; the author by default
    state             TEXT NOT NULL CHECK (state IN (
                          'submitted', 'changes_requested', 'rejected',
                          'approved', 'published', 'withdrawn')),
    current_revision  INTEGER NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,   -- bumped on every transition
    approved_revision INTEGER,
    approved_by       TEXT REFERENCES users(id),
    approved_at       TEXT,
    published_at      TEXT,
    withdrawn_by      TEXT REFERENCES users(id),
    withdrawn_at      TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS submissions_org_state ON submissions(org_id, state);

CREATE TABLE IF NOT EXISTS submission_revisions (
    submission_id TEXT NOT NULL REFERENCES submissions(id),
    revision      INTEGER NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    cause_id      TEXT NOT NULL REFERENCES causes(id),
    created_at    TEXT NOT NULL,
    PRIMARY KEY (submission_id, revision)
);

CREATE TABLE IF NOT EXISTS revision_skills (
    submission_id TEXT NOT NULL,
    revision      INTEGER NOT NULL,
    skill_id      TEXT NOT NULL REFERENCES skills(id),
    required      INTEGER NOT NULL CHECK (required IN (0, 1)),
    PRIMARY KEY (submission_id, revision, skill_id),
    FOREIGN KEY (submission_id, revision) REFERENCES submission_revisions
);

CREATE TABLE IF NOT EXISTS reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id     TEXT NOT NULL REFERENCES submissions(id),
    revision          INTEGER NOT NULL,
    reviewer_id       TEXT NOT NULL REFERENCES users(id),
    action            TEXT NOT NULL CHECK (action IN (
                          'changes_requested', 'rejected', 'approved', 'withdrawn')),
    reason_for_author TEXT NOT NULL,
    private_note      TEXT NOT NULL DEFAULT '',     -- reviewers of the organisation only
    created_at        TEXT NOT NULL
);

-- A volunteer and a project that Relate found eligible. Both sides must say yes before the
-- volunteer joins the team (state 'team', projected as (:Volunteer)-[:ON_TEAM]->(:Project)).
CREATE TABLE IF NOT EXISTS placements (
    project_id     TEXT NOT NULL REFERENCES submissions(id),
    volunteer_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state          TEXT NOT NULL CHECK (state IN (
                       'eligible', 'team', 'declined', 'lapsed', 'removed', 'left')),
    volunteer_said TEXT CHECK (volunteer_said IN ('yes', 'no')),
    lead_said      TEXT CHECK (lead_said IN ('yes', 'no')),
    matched_skills TEXT NOT NULL DEFAULT '[]',       -- JSON list, from the Relate match
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (project_id, volunteer_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    link       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    read_at    TEXT
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, read_at);

-- Retryable projection to Relate. Items name an entity; the projector reconciles that entity
-- from the current SQLite state, so replay is idempotent and order-insensitive.
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL CHECK (
                        kind IN ('catalogue', 'organisation', 'volunteer', 'project')),
    entity_id       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TEXT NOT NULL,
    done_at         TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(done_at, kind, entity_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


SCHEMA_VERSION = 2


class SchemaOutdated(RuntimeError):
    pass


def init_schema(path: Path) -> None:
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if has_tables and version != SCHEMA_VERSION:
            raise SchemaOutdated(
                f"{path} has schema version {version}, expected {SCHEMA_VERSION}. The demo data "
                "is disposable: run `uv run task reset` (or `docker compose down -v`)."
            )
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()


class Database:
    """Opens a short-lived connection per unit of work (safe across threads and processes)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def init(self) -> None:
        init_schema(self.path)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One IMMEDIATE transaction: committed on success, rolled back on error."""
        conn = connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            yield conn
        finally:
            conn.close()


def enqueue(conn: sqlite3.Connection, kind: str, entity_id: str) -> None:
    """Add a projection item in the caller's transaction, so it commits with the change."""
    stamp = now()
    conn.execute(
        "INSERT INTO outbox (kind, entity_id, created_at, next_attempt_at) VALUES (?, ?, ?, ?)",
        (kind, entity_id, stamp, stamp),
    )
