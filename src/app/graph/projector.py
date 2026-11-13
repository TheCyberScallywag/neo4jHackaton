"""Outbox projector: makes Relate catch up with SQLite.

Each outbox item names an entity (a volunteer, a project, an organisation or the catalogue).
The projector reads that entity's *current* state from SQLite and reconciles the graph to it
with idempotent upserts and removal of stale edges, all inside one Relate transaction. Only
then is the item marked done. A failure leaves the item pending with its error and a backoff,
so it is retried by the background loop, the next request, a restart, or `app.cli drain`.

A project becomes `published` (discoverable and matchable) only after its approved revision has
been written to Relate.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.graph import cypher
from app.graph.store import GraphError, GraphStore, Tx
from app.notify import notify
from app.sqlstore import Database, now

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 60
FAILED_AFTER_ATTEMPTS = 3  # surfaced as "failed" in the UI from here on, still retried

VOLUNTEER_EDGES = (
    "VOLUNTEERS_WITH",
    "HAS_SKILL",
    "OFFERS_SKILL",
    "EXCLUDES_SKILL",
    "PREFERS_CAUSE",
    "EXCLUDES_CAUSE",
)


@dataclass
class DrainResult:
    done: int = 0
    failed: int = 0


@dataclass
class OutboxStatus:
    pending: int
    failed: int
    items: list[dict]


class Projector:
    def __init__(self, db: Database, graph: GraphStore) -> None:
        self.db = db
        self.graph = graph
        self._drain_lock = threading.Lock()
        # Called after a drain that projected something (app.runtime wires the placement sweep).
        self.on_projected: Callable[[], object] | None = None
        self._hook_due = False

    # ------------------------------------------------------------------ public
    def drain(self, *, limit: int = 500, ignore_backoff: bool = False) -> DrainResult:
        """Project every due item. Safe to call from any thread; concurrent calls coalesce."""
        result = DrainResult()
        if not self._drain_lock.acquire(blocking=False):
            return result
        try:
            with self.db.read() as conn:
                due = "" if ignore_backoff else "AND next_attempt_at <= ?"
                params: tuple = () if ignore_backoff else (now(),)
                rows = conn.execute(
                    "SELECT id, kind, entity_id FROM outbox WHERE done_at IS NULL "
                    f"{due} ORDER BY id LIMIT {int(limit)}",
                    params,
                ).fetchall()
            # Coalesce: one reconcile per entity covers every earlier item for it.
            groups: dict[tuple[str, str], list[int]] = {}
            for row in rows:
                groups.setdefault((row["kind"], row["entity_id"]), []).append(row["id"])
            # Catalogue and organisations first so volunteer/project edges have targets.
            order = {"catalogue": 0, "organisation": 1, "volunteer": 2, "project": 3}
            for (kind, entity_id), ids in sorted(
                groups.items(), key=lambda g: (order[g[0][0]], min(g[1]))
            ):
                try:
                    self._project(kind, entity_id)
                except (GraphError, sqlite3.Error, ValueError) as error:
                    log.warning("projection of %s %s failed: %s", kind, entity_id, error)
                    self._mark_failed(ids, str(error))
                    result.failed += len(ids)
                else:
                    self._mark_done(ids)
                    result.done += len(ids)
            if (result.done or self._hook_due) and self.on_projected:
                try:
                    self.on_projected()
                    self._hook_due = False
                except (GraphError, sqlite3.Error) as error:
                    self._hook_due = True  # retried on the next drain, even if nothing is queued
                    log.warning("post-projection hook failed: %s", error)
        finally:
            self._drain_lock.release()
        return result

    def status(self, limit: int = 50) -> OutboxStatus:
        with self.db.read() as conn:
            pending = conn.execute("SELECT count(*) FROM outbox WHERE done_at IS NULL").fetchone()[
                0
            ]
            failed = conn.execute(
                "SELECT count(*) FROM outbox WHERE done_at IS NULL AND attempts >= ?",
                (FAILED_AFTER_ATTEMPTS,),
            ).fetchone()[0]
            items = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, kind, entity_id, attempts, last_error, created_at, "
                    "next_attempt_at FROM outbox WHERE done_at IS NULL ORDER BY id LIMIT ?",
                    (limit,),
                )
            ]
        return OutboxStatus(pending=pending, failed=failed, items=items)

    # ------------------------------------------------------------------ bookkeeping
    def _mark_done(self, ids: list[int]) -> None:
        marks = ",".join("?" * len(ids))
        with self.db.tx() as conn:
            conn.execute(
                f"UPDATE outbox SET done_at = ?, last_error = NULL WHERE id IN ({marks})",
                (now(), *ids),
            )

    def _mark_failed(self, ids: list[int], error: str) -> None:
        marks = ",".join("?" * len(ids))
        with self.db.tx() as conn:
            attempts = conn.execute(
                f"SELECT max(attempts) FROM outbox WHERE id IN ({marks})", ids
            ).fetchone()[0]
            delay = min(2 ** (attempts or 0), MAX_BACKOFF_SECONDS)
            retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ?, next_attempt_at = ? "
                f"WHERE id IN ({marks})",
                (error[:500], retry_at, *ids),
            )

    # ------------------------------------------------------------------ reconcile
    def _project(self, kind: str, entity_id: str) -> None:
        match kind:
            case "catalogue":
                self._project_catalogue()
            case "organisation":
                self._project_organisation(entity_id)
            case "volunteer":
                self._project_volunteer(entity_id)
            case "project":
                self._project_project(entity_id)
            case _:
                raise ValueError(f"unknown outbox kind {kind!r}")

    def _project_catalogue(self) -> None:
        with self.db.read() as conn:
            causes = [dict(r) for r in conn.execute("SELECT id, name FROM causes ORDER BY id")]
            skills = [
                dict(r) for r in conn.execute("SELECT id, name, category FROM skills ORDER BY id")
            ]
        with self.graph.write() as tx:
            tx.run(cypher.UPSERT_CAUSES, {"rows": causes})
            tx.run(cypher.UPSERT_SKILLS, {"rows": skills})

    def _project_organisation(self, org_id: str) -> None:
        with self.db.read() as conn:
            org = conn.execute(
                "SELECT id, name FROM organisations WHERE id = ?", (org_id,)
            ).fetchone()
        if org is None:
            raise ValueError(f"organisation {org_id} not found")
        with self.graph.write() as tx:
            tx.run(cypher.UPSERT_ORGANISATION, {"id": org["id"], "name": org["name"]})

    def _project_volunteer(self, user_id: str) -> None:
        with self.db.read() as conn:
            user = conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
            if user is None:
                raise ValueError(f"user {user_id} not found")
            # Membership edges exist only for vetted (active) volunteers, so screening,
            # declined or suspended accounts can never match.
            orgs = [
                r[0]
                for r in conn.execute(
                    "SELECT org_id FROM memberships WHERE user_id = ? AND opted_in = 1",
                    (user_id,),
                )
            ] if user["status"] == "active" else []  # fmt: skip
            skills = conn.execute(
                "SELECT skill_id, has, offers, excluded FROM profile_skills WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            causes = conn.execute(
                "SELECT cause_id, stance FROM profile_causes WHERE user_id = ?", (user_id,)
            ).fetchall()
        targets = {
            "VOLUNTEERS_WITH": orgs,
            "HAS_SKILL": [s["skill_id"] for s in skills if s["has"] and not s["excluded"]],
            "OFFERS_SKILL": [s["skill_id"] for s in skills if s["offers"] and not s["excluded"]],
            "EXCLUDES_SKILL": [s["skill_id"] for s in skills if s["excluded"]],
            "PREFERS_CAUSE": [c["cause_id"] for c in causes if c["stance"] == "prefer"],
            "EXCLUDES_CAUSE": [c["cause_id"] for c in causes if c["stance"] == "exclude"],
        }
        with self.graph.write() as tx:
            tx.run(cypher.VOLUNTEER_UPSERT, {"id": user_id})
            for rel in VOLUNTEER_EDGES:
                params = {"id": user_id, "targets": sorted(targets[rel])}
                tx.run(cypher.VOLUNTEER_EDGE_PRUNE[rel], params)
                tx.run(cypher.VOLUNTEER_EDGE_MERGE[rel], params)

    def _project_project(self, submission_id: str) -> None:
        with self.db.read() as conn:
            sub = conn.execute(
                "SELECT id, org_id, state, approved_revision FROM submissions WHERE id = ?",
                (submission_id,),
            ).fetchone()
            if sub is None:
                raise ValueError(f"submission {submission_id} not found")
            live = sub["state"] in ("approved", "published") and sub["approved_revision"]
            if live:
                rev = conn.execute(
                    "SELECT title, summary, cause_id FROM submission_revisions "
                    "WHERE submission_id = ? AND revision = ?",
                    (submission_id, sub["approved_revision"]),
                ).fetchone()
                skills = conn.execute(
                    "SELECT skill_id, required FROM revision_skills "
                    "WHERE submission_id = ? AND revision = ? ORDER BY skill_id",
                    (submission_id, sub["approved_revision"]),
                ).fetchall()
                team = [
                    r[0]
                    for r in conn.execute(
                        "SELECT volunteer_id FROM placements WHERE project_id = ? "
                        "AND state = 'team' ORDER BY volunteer_id",
                        (submission_id,),
                    )
                ]
        if not live:
            with self.graph.write() as tx:
                tx.run(cypher.PROJECT_REMOVE, {"id": submission_id})
            return
        targets = {
            "SERVES_CAUSE": [rev["cause_id"]],
            "REQUIRES_SKILL": [s["skill_id"] for s in skills if s["required"]],
            "BENEFITS_FROM_SKILL": [s["skill_id"] for s in skills if not s["required"]],
        }
        with self.graph.write() as tx:
            self._upsert_project(tx, sub, rev, targets)
            tx.run(cypher.PROJECT_TEAM_PRUNE, {"id": submission_id, "targets": team})
            tx.run(cypher.PROJECT_TEAM_MERGE, {"id": submission_id, "targets": team})
        if sub["state"] == "approved":
            stamp = now()
            with self.db.tx() as conn:
                published = conn.execute(
                    "UPDATE submissions SET state = 'published', published_at = ?, "
                    "updated_at = ?, version = version + 1 "
                    "WHERE id = ? AND state = 'approved' AND approved_revision = ?",
                    (stamp, stamp, submission_id, sub["approved_revision"]),
                ).rowcount
                if published:
                    author = conn.execute(
                        "SELECT author_id FROM submissions WHERE id = ?", (submission_id,)
                    ).fetchone()[0]
                    notify(conn, author, "project",
                           f"“{rev['title']}” is now active: visible and matching volunteers.",
                           f"/o/{sub['org_id']}/projects/{submission_id}")  # fmt: skip

    @staticmethod
    def _upsert_project(tx: Tx, sub: sqlite3.Row, rev: sqlite3.Row, targets: dict) -> None:
        tx.run(
            cypher.PROJECT_UPSERT,
            {
                "id": sub["id"],
                "org": sub["org_id"],
                "title": rev["title"],
                "summary": rev["summary"],
                "revision": sub["approved_revision"],
            },
        )
        for rel, ids in targets.items():
            params = {"id": sub["id"], "targets": sorted(ids)}
            tx.run(cypher.PROJECT_EDGE_PRUNE[rel], params)
            tx.run(cypher.PROJECT_EDGE_MERGE[rel], params)
        owned = tx.read(cypher.PROJECT_OWNED, {"id": sub["id"], "org": sub["org_id"]})
        if not owned or owned[0]["n"] == 0:
            raise GraphError(f"organisation {sub['org_id']} is not in the graph yet")


def pending_for(conn: sqlite3.Connection, kind: str, entity_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM outbox WHERE done_at IS NULL AND kind = ? AND entity_id = ? LIMIT 1",
            (kind, entity_id),
        ).fetchone()
        is not None
    )
