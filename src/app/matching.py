"""Match views. Relate decides who matches what (app.graph.cypher.MATCH_CANDIDATES); SQLite is
consulted only for access, freshness and display names:

* access:    the caller's membership/role in the target organisation (app.services)
* freshness: while a relevant projection is pending, results are withheld rather than stale,
             and graph rows for projects no longer published in SQLite are dropped
* names:     volunteer graph nodes carry only opaque IDs; display names come from SQLite and
             only for volunteers who are currently opted in to that organisation
"""

from collections import defaultdict
from dataclasses import dataclass, field

from app import services
from app.catalogue import get_catalogue
from app.graph import cypher
from app.graph.store import GraphStore
from app.sqlstore import Database

_WAIT = "Results are withheld until it catches up; refresh in a moment."


@dataclass
class Withheld:
    reason: str


@dataclass
class VolunteerMatches:
    withheld: Withheld | None = None
    matches: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)  # own exclusion explanations


@dataclass
class ProjectCoverage:
    withheld: Withheld | None = None
    projects: list[dict] = field(default_factory=list)


def _pending_count(conn, sql: str, params: tuple) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _volunteer_pending(conn, user_id: str, org_ids: list[str]) -> str | None:
    marks = ",".join("?" * len(org_ids)) or "''"
    if _pending_count(
        conn,
        "SELECT count(*) FROM outbox WHERE done_at IS NULL AND ("
        "(kind = 'volunteer' AND entity_id = ?) OR kind IN ('catalogue', 'organisation'))",
        (user_id,),
    ):
        return (
            "Your latest profile or membership change is still being applied to the graph. " + _WAIT
        )
    if _pending_count(
        conn,
        "SELECT count(*) FROM outbox o JOIN submissions s ON s.id = o.entity_id "
        f"WHERE o.done_at IS NULL AND o.kind = 'project' AND s.org_id IN ({marks})",
        tuple(org_ids),
    ):
        return "A project publication or withdrawal is still being applied to the graph. " + _WAIT
    return None


def _org_pending(conn, org_id: str, project_id: str = "") -> str | None:
    if _pending_count(
        conn,
        "SELECT count(*) FROM outbox o JOIN submissions s ON s.id = o.entity_id "
        "WHERE o.done_at IS NULL AND o.kind = 'project' AND s.org_id = ? "
        "AND (? = '' OR s.id = ?)",
        (org_id, project_id, project_id),
    ):
        return "A project publication or withdrawal is still being applied to the graph. " + _WAIT
    if _pending_count(
        conn,
        "SELECT count(*) FROM outbox o JOIN memberships m ON m.user_id = o.entity_id "
        "WHERE o.done_at IS NULL AND o.kind = 'volunteer' AND m.org_id = ?",
        (org_id,),
    ) or _pending_count(
        conn,
        "SELECT count(*) FROM outbox WHERE done_at IS NULL "
        "AND kind IN ('catalogue', 'organisation')",
        (),
    ):
        return (
            "A volunteer's profile or membership change is still being applied to the graph. "
            + _WAIT
        )
    return None


def _match_params(**overrides) -> dict:
    return {
        "orgs": [],
        "project": "",
        "volunteer": "",
        "preferred_only": False,
        "limit": 200,
    } | overrides


def volunteer_matches(
    db: Database,
    graph: GraphStore,
    user_id: str,
    org_ids: list[str],
    *,
    preferred_only: bool = False,
) -> VolunteerMatches:
    """Projects the volunteer could contribute to, in organisations they are opted in to."""
    with db.read() as conn:
        allowed = [o for o in org_ids if services.org_access(conn, user_id, o).volunteer]
        if len(allowed) != len(org_ids):
            raise services.Forbidden("volunteer")
        status = services.load_actor(conn, user_id).status
        if status != "active":
            label = services.VOLUNTEER_STATUS_LABELS[status].lower()
            return VolunteerMatches(
                withheld=Withheld(
                    f"Your account is {label}. You'll be matched to projects once a moderator "
                    "has approved it."
                )
            )
        if reason := _volunteer_pending(conn, user_id, allowed):
            return VolunteerMatches(withheld=Withheld(reason))
    if not allowed:
        return VolunteerMatches()
    with graph.reader() as tx:
        rows = tx.read(
            cypher.MATCH_CANDIDATES,
            _match_params(orgs=allowed, volunteer=user_id, preferred_only=preferred_only),
        )
        excluded = tx.read(
            cypher.EXCLUDED_FOR_VOLUNTEER, {"volunteer": user_id, "orgs": allowed, "limit": 200}
        )
        optional_excluded = tx.read(
            cypher.OPTIONAL_SKILLS_EXCLUDED, {"volunteer": user_id, "orgs": allowed, "limit": 200}
        )
    with db.read() as conn:
        fresh = services.published_state(
            conn, sorted({r["project"] for r in rows} | {r["project"] for r in excluded})
        )
        org_names = dict(conn.execute("SELECT id, name FROM organisations").fetchall())
    opt_out = {r["project"]: sorted(r["skills"]) for r in optional_excluded}
    return VolunteerMatches(
        matches=[
            _explain(r, org_names, opt_out.get(r["project"], []))
            for r in rows
            if r["project"] in fresh
        ],
        excluded=[_explain_exclusion(r, org_names) for r in excluded if r["project"] in fresh],
    )


def _explain(row: dict, org_names: dict, optional_excluded: list[str]) -> dict:
    cat = get_catalogue()
    return {
        **row,
        "org_name": org_names.get(row["org"], row["org"]),
        "matched": sorted(row["matched"]),
        "matched_names": [cat.skill_name(s) for s in sorted(row["matched"])],
        "optional_matched": sorted(row["optional_matched"]),
        "optional_names": [cat.skill_name(s) for s in sorted(row["optional_matched"])],
        "remaining": sorted(row["remaining"]),
        "remaining_names": [cat.skill_name(s) for s in sorted(row["remaining"])],
        "optional_excluded_names": [cat.skill_name(s) for s in optional_excluded],
        "partial": bool(row["remaining"]),
    }


def _explain_exclusion(row: dict, org_names: dict) -> dict:
    cat = get_catalogue()
    reasons = []
    if row["cause_excluded"]:
        reasons.append(f"you excluded the cause {row['cause_name']}")
    reasons += [
        f"it requires {cat.skill_name(s)}, which you excluded" for s in row["excluded_skills"]
    ]
    return {**row, "org_name": org_names.get(row["org"], row["org"]), "reasons": reasons}


def project_coverage(
    db: Database, graph: GraphStore, reviewer_id: str, org_id: str, project_id: str = ""
) -> ProjectCoverage:
    """For an organisation's reviewers: eligible candidates per required skill, and gaps."""
    services.require_access(db, reviewer_id, org_id, role="reviewer")
    with db.read() as conn:
        if reason := _org_pending(conn, org_id, project_id):
            return ProjectCoverage(withheld=Withheld(reason))
    with graph.reader() as tx:
        params = _match_params(orgs=[org_id], project=project_id, limit=500)
        projects = tx.read(cypher.BROWSE_PROJECTS, {"org": org_id, "limit": 200})
        rows = tx.read(cypher.MATCH_CANDIDATES, params)
        gaps = tx.read(cypher.COVERAGE_GAPS, {"org": org_id, "project": project_id, "limit": 500})
    with db.read() as conn:
        fresh = services.published_state(conn, [p["project"] for p in projects])
        names = dict(
            conn.execute(
                "SELECT u.id, u.display_name FROM users u JOIN memberships m "
                "ON m.user_id = u.id WHERE m.org_id = ? AND m.opted_in = 1",
                (org_id,),
            ).fetchall()
        )
    cat = get_catalogue()
    by_project: dict[str, dict] = {}
    for p in projects:
        if p["project"] not in fresh or (project_id and p["project"] != project_id):
            continue
        by_project[p["project"]] = {
            **p,
            "sql": fresh[p["project"]],
            "required_names": [cat.skill_name(s) for s in sorted(p["required"])],
            "optional_names": [cat.skill_name(s) for s in sorted(p["optional"])],
            "candidates": [],
            "per_skill": defaultdict(list),
            "gaps": [],
        }
    for r in rows:
        entry = by_project.get(r["project"])
        if entry is None or r["volunteer"] not in names:
            continue  # not published in SQL, or no longer opted in: fail closed
        candidate = {
            "volunteer": r["volunteer"],
            "name": names[r["volunteer"]],
            "preferred": r["preferred"],
            "matched_names": [cat.skill_name(s) for s in sorted(r["matched"])],
            "optional_names": [cat.skill_name(s) for s in sorted(r["optional_matched"])],
            "remaining_names": [cat.skill_name(s) for s in sorted(r["remaining"])],
            "coverage": r["coverage"],
        }
        entry["candidates"].append(candidate)
        for skill in sorted(r["matched"]):
            entry["per_skill"][skill].append(candidate)
    for g in gaps:
        if entry := by_project.get(g["project"]):
            entry["gaps"].append(g["skill_name"])
    for entry in by_project.values():
        entry["per_skill"] = [
            {"skill": cat.skill_name(s), "candidates": entry["per_skill"].get(s, [])}
            for s in sorted(entry["required"])
        ]
    return ProjectCoverage(projects=list(by_project.values()))


def browse(db: Database, graph: GraphStore, user_id: str, org_id: str) -> list[dict]:
    """Verified, published projects of one organisation the user belongs to."""
    services.require_access(db, user_id, org_id)
    rows = graph.read(cypher.BROWSE_PROJECTS, {"org": org_id, "limit": 200})
    with db.read() as conn:
        fresh = services.published_state(conn, [r["project"] for r in rows])
    cat = get_catalogue()
    return [
        {
            **r,
            "required_names": [cat.skill_name(s) for s in sorted(r["required"])],
            "optional_names": [cat.skill_name(s) for s in sorted(r["optional"])],
        }
        for r in rows
        if r["project"] in fresh
    ]
