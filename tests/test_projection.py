"""Outbox projection: failure, withholding, restart and replay, persistence after restart."""

import relate
from fastapi.testclient import TestClient

from app import matching
from app.graph import cypher
from app.graph.projector import FAILED_AFTER_ATTEMPTS
from app.web.main import create_app
from tests.conftest import AUDIT, CS, DK, USERS, WINTER, Web
from tests.test_matching import save_profile


def _graph_snapshot(web: Web) -> tuple:
    with web.rt.graph.reader() as tx:
        return (
            tx.read(cypher.ALL_NODES, {"limit": 500}),
            tx.read(cypher.ALL_RELATIONSHIPS, {"limit": 500}),
        )


def test_startup_projects_everything(web: Web):
    status = web.rt.projector.status()
    assert (status.pending, status.failed) == (0, 0)
    assert web.get("/healthz").json() == {"status": "ok", "pending_projections": 0, "failed": 0}


def test_replay_is_idempotent(web: Web):
    before = _graph_snapshot(web)
    with web.rt.db.tx() as conn:  # re-enqueue every entity
        conn.execute("INSERT INTO outbox (kind, entity_id, created_at, next_attempt_at) "
                     "SELECT kind, entity_id, created_at, created_at FROM outbox")  # fmt: skip
    result = web.rt.projector.drain(ignore_backoff=True)
    assert result.failed == 0 and result.done > 0
    assert _graph_snapshot(web) == before


def test_failed_projection_is_kept_withheld_and_replayed_after_restart(settings):
    with TestClient(create_app(settings, background=False)) as client:
        web = Web(client)
        web.rt.graph.attempts = 2  # make the locked store fail fast
        holder = relate.connect(str(web.rt.graph.path))  # another process holds the store
        try:
            save_profile(web, "asha", skills={"active-listening": "exclude"})
            status = web.rt.projector.status()
            assert status.pending == 1 and "locked" in status.items[0]["last_error"]
            # Affected results are withheld rather than stale...
            assert "still being applied" in web.get("/matches?scope=all").text
            assert "still being applied" in web.get("/home").text
            cov = matching.project_coverage(web.rt.db, web.rt.graph, USERS["kofi"], CS, WINTER)
            assert cov.withheld is not None
            # ...and repeated failures are counted and surfaced.
            for _ in range(FAILED_AFTER_ATTEMPTS):
                web.rt.projector.drain(ignore_backoff=True)
            assert web.rt.projector.status().failed == 1
        finally:
            holder.close()
    # Restart: a fresh app on the same data directory replays the outbox at startup.
    with TestClient(create_app(settings, background=False)) as client:
        web = Web(client)
        assert web.rt.projector.status().pending == 0
        cov = matching.project_coverage(web.rt.db, web.rt.graph, USERS["kofi"], CS, WINTER)
        assert "Asha" not in [c["name"] for c in cov.projects[0]["candidates"]]
        assert "Active listening" in cov.projects[0]["gaps"]


def test_approved_project_is_not_published_until_projected(settings):
    with TestClient(create_app(settings, background=False)) as client:
        web = Web(client)
        web.rt.graph.attempts = 2
        holder = relate.connect(str(web.rt.graph.path))
        try:
            web.sign_in("rhiannon")
            with web.rt.db.read() as conn:
                version = conn.execute(
                    "SELECT version FROM submissions WHERE id = ?", (AUDIT,)
                ).fetchone()[0]
            web.post(
                f"/o/{DK}/review/{AUDIT}",
                {"action": "approved", "reason": "Looks good", "revision": 1, "version": version},
            )
            with web.rt.db.read() as conn:
                assert conn.execute(
                    "SELECT state FROM submissions WHERE id = ?", (AUDIT,)
                ).fetchone()[0] == ("approved")
            assert "Approved (activating" in web.get(f"/submissions/{AUDIT}").text
        finally:
            holder.close()
        web.rt.projector.drain(ignore_backoff=True)
        with web.rt.db.read() as conn:
            assert conn.execute("SELECT state FROM submissions WHERE id = ?", (AUDIT,)).fetchone()[
                0
            ] == ("published")


def test_graph_persists_across_restart(settings):
    with TestClient(create_app(settings, background=False)) as client:
        web = Web(client)
        web.sign_in("rhiannon")
        web.post(
            f"/o/{DK}/review/{AUDIT}",
            {"action": "approved", "reason": "Looks good", "revision": 1, "version": 1},
        )
    assert settings.graph_path.is_file()
    with TestClient(create_app(settings, background=False)) as client:
        web = Web(client)
        web.sign_in("asha")
        assert "Outreach Data Audit" in web.get(f"/o/{DK}/projects").text
        with web.rt.db.tx() as conn:  # prove the result is read from the persisted Relate file
            conn.execute("DELETE FROM outbox")
        rows = web.rt.graph.read(
            "MATCH (:Organisation {id: $org})-[:OWNS_PROJECT]->(p:Project {id: $id}) "
            "RETURN p.title AS title",
            {"org": DK, "id": AUDIT},
        )
        assert rows == [{"title": "Outreach Data Audit"}]
