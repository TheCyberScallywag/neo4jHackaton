"""Wiring (SQLite + Relate + projector) and the disposable demo seed."""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app import placements
from app.auth import hash_password
from app.catalogue import get_catalogue, load_demo, stable_id
from app.config import BUNDLED_GRAPH, Settings
from app.graph.projector import Projector
from app.graph.store import GraphStore
from app.sqlstore import Database, enqueue, now

log = logging.getLogger(__name__)

# Every demo account shares this password. It only ever exists in the disposable local demo:
# `seed_demo` refuses to run when APP_ENV=production.
DEMO_PASSWORD = "demo-password-1"  # noqa: S105


@dataclass
class Runtime:
    settings: Settings
    db: Database
    graph: GraphStore
    projector: Projector


def build_runtime(settings: Settings) -> Runtime:
    db = Database(settings.sqlite_path)
    graph = GraphStore(settings.graph_path, max_rows=settings.graph_max_rows)
    projector = Projector(db, graph)
    projector.on_projected = lambda: placements.sweep(db, graph)
    return Runtime(settings, db, graph, projector)


def bootstrap(rt: Runtime) -> None:
    """Create the schema; seed the demo on an empty data dir (test env only); replay the outbox.

    Replaying here is what makes a restart catch up with anything a crash left pending.
    """
    fresh = not rt.settings.sqlite_path.exists()
    rt.db.init()
    if not rt.settings.demo_allowed:
        with rt.db.read() as conn:
            if conn.execute("SELECT 1 FROM users WHERE email LIKE '%@demo.invalid'").fetchone():
                raise RuntimeError(
                    "APP_ENV=production but the database holds demo accounts (@demo.invalid). "
                    "Start from an empty DATA_DIR."
                )
    if fresh and rt.settings.seed_demo_on_start and rt.settings.demo_allowed:
        seed_demo(rt, copy_bundle=True)
    else:
        seed_catalogue(rt)
    result = rt.projector.drain(ignore_backoff=True)
    log.info("startup replay: %s done, %s failed", result.done, result.failed)


def seed_catalogue(rt: Runtime) -> None:
    """Upsert the shared catalogue; enqueue a projection only if something changed."""
    cat = get_catalogue()
    with rt.db.tx() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO causes (id, name) VALUES (?, ?) ON CONFLICT (id) DO UPDATE SET "
            "name = excluded.name WHERE name IS NOT excluded.name",
            [(c.id, c.name) for c in cat.causes],
        )
        conn.executemany(
            "INSERT INTO skills (id, name, category) VALUES (?, ?, ?) ON CONFLICT (id) DO UPDATE "
            "SET name = excluded.name, category = excluded.category "
            "WHERE name IS NOT excluded.name OR category IS NOT excluded.category",
            [(s.id, s.name, s.category) for s in cat.skills],
        )
        if conn.total_changes != before:
            enqueue(conn, "catalogue", "catalogue")


def seed_demo(rt: Runtime, *, copy_bundle: bool) -> None:
    """Load data/fixtures/demo.json into an empty database and project it.

    With copy_bundle, the committed data/demo.rl is first copied to the writable store, so the
    app runs on a copy of the bundled graph; the projection that follows is then a no-op
    reconcile (and repairs any drift).
    """
    if not rt.settings.demo_allowed:
        raise RuntimeError("Demo seeding is disabled when APP_ENV=production.")
    rt.db.init()
    if copy_bundle and BUNDLED_GRAPH.is_file() and not rt.graph.path.exists():
        rt.graph.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLED_GRAPH, rt.graph.path)
        rt.graph.path.chmod(0o600)
    seed_catalogue(rt)
    demo = load_demo()
    names = rt.settings.org_names
    org_ids = {o["key"]: stable_id("org", o["key"]) for o in demo["organisations"]}
    user_ids = {p["key"]: stable_id("u", p["key"]) for p in demo["people"]}
    password_hash = hash_password(DEMO_PASSWORD)
    stamp = now()
    with rt.db.tx() as conn:
        for org in demo["organisations"]:
            conn.execute(
                "INSERT INTO organisations (id, key, name, description) VALUES (?, ?, ?, ?)",
                (org_ids[org["key"]], org["key"], names.get(org["key"], org["name"]),
                 org["description"]),
            )  # fmt: skip
            enqueue(conn, "organisation", org_ids[org["key"]])
        for person in demo["people"]:
            uid = user_ids[person["key"]]
            conn.execute(
                "INSERT INTO users (id, email, display_name, password_hash, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, person["email"], person["display_name"], password_hash,
                 person.get("status", "active"), stamp),
            )  # fmt: skip
            for role in person.get("system_roles", []):
                conn.execute(
                    "INSERT INTO system_roles (user_id, role, granted_at) VALUES (?, ?, ?)",
                    (uid, role, stamp),
                )
            for key in person["volunteers_with"]:
                conn.execute(
                    "INSERT INTO memberships (user_id, org_id, opted_in, updated_at) "
                    "VALUES (?, ?, 1, ?)",
                    (uid, org_ids[key], stamp),
                )
            for key, roles in person.get("org_roles", {}).items():
                for role in roles:
                    conn.execute(
                        "INSERT INTO org_roles (user_id, org_id, role, granted_at) "
                        "VALUES (?, ?, ?, ?)",
                        (uid, org_ids[key], role, stamp),
                    )
            for skill, state in person["skills"].items():
                conn.execute(
                    "INSERT INTO profile_skills (user_id, skill_id, has, offers, excluded) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uid, skill, int(state in ("has", "offers")), int(state == "offers"),
                     int(state == "exclude")),
                )  # fmt: skip
            for cause, stance in person["causes"].items():
                conn.execute(
                    "INSERT INTO profile_causes (user_id, cause_id, stance) VALUES (?, ?, ?)",
                    (uid, cause, stance),
                )
            enqueue(conn, "volunteer", uid)
        for sub in demo["submissions"]:
            sid = stable_id("p", sub["key"])
            approved = sub["outcome"] == "approved"
            reviewer = user_ids.get(sub.get("reviewer", ""))
            conn.execute(
                "INSERT INTO submissions (id, org_id, author_id, lead_id, state, current_revision, "
                "approved_revision, approved_by, approved_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (sid, org_ids[sub["org"]], user_ids[sub["author"]], user_ids[sub["author"]],
                 sub["outcome"],
                 1 if approved else None, reviewer if approved else None,
                 stamp if approved else None, stamp, stamp),
            )  # fmt: skip
            conn.execute(
                "INSERT INTO submission_revisions (submission_id, revision, title, summary, "
                "cause_id, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                (sid, sub["title"], sub["summary"], sub["cause"], stamp),
            )
            conn.executemany(
                "INSERT INTO revision_skills (submission_id, revision, skill_id, required) "
                "VALUES (?, 1, ?, ?)",
                [(sid, s, 1) for s in sub["required"]] + [(sid, s, 0) for s in sub["optional"]],
            )
            if reviewer:
                conn.execute(
                    "INSERT INTO reviews (submission_id, revision, reviewer_id, action, "
                    "reason_for_author, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                    (sid, reviewer, sub["outcome"], sub["reason"], stamp),
                )
            if approved:
                enqueue(conn, "project", sid)
    rt.projector.drain(ignore_backoff=True)  # also runs the placement sweep
    # Teams are formed through the real two-sided accept flow.
    for team in demo.get("teams", []):
        pid, vid = stable_id("p", team["project"]), user_ids[team["volunteer"]]
        with rt.db.read() as conn:
            lead = conn.execute("SELECT lead_id FROM submissions WHERE id = ?", (pid,)).fetchone()
            version = conn.execute(
                "SELECT version FROM placements WHERE project_id = ? AND volunteer_id = ?",
                (pid, vid),
            ).fetchone()[0]
        placements.volunteer_respond(rt.db, vid, pid, "yes", version=version)
        placements.lead_respond(rt.db, lead[0], pid, vid, "yes", version=version + 1)
    rt.projector.drain(ignore_backoff=True)


def reset_demo(settings: Settings) -> Runtime:
    """Delete the runtime data dir's stores and reseed. For the disposable demo only."""
    if not settings.demo_allowed:
        raise RuntimeError("Reset is disabled when APP_ENV=production.")
    for path in (settings.sqlite_path, settings.graph_path):
        for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
    rt = build_runtime(settings)
    seed_demo(rt, copy_bundle=True)
    return rt
