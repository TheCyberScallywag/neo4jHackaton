"""Optional AuraDB destination, invoked explicitly from the CLI only (never on a request path).

`push` copies the synthetic demo graph from a Relate store into a designated *dev* AuraDB
instance with the Neo4j Python driver; every node it writes carries `dvn_scope`, and a push
first deletes only nodes with that scope. `parity` runs the very same MATCH_CANDIDATES text on
both engines for every organisation and compares the normalised rows.
"""

from typing import Any

from app.config import Settings
from app.graph import cypher
from app.graph.store import GraphStore

SCOPE = "dvn-demo-v1"
LABELS = ("Organisation", "Project", "Cause", "Skill", "Volunteer")


class AuraNotConfigured(RuntimeError):
    pass


def _driver(settings: Settings):
    password = settings.aura_password.get_secret_value()
    if not settings.aura_uri.startswith("neo4j+s://") or not password:
        raise AuraNotConfigured(
            "Set AURA_URI (neo4j+s://...) and AURA_PASSWORD for a designated dev instance."
        )
    try:
        from neo4j import GraphDatabase
    except ModuleNotFoundError as error:
        raise AuraNotConfigured("Install the driver: uv sync --group aura") from error
    return GraphDatabase.driver(settings.aura_uri, auth=(settings.aura_username, password))


def _export(graph: GraphStore) -> tuple[list[dict], list[dict]]:
    with graph.reader() as tx:
        nodes = tx.read(cypher.ALL_NODES, {"limit": graph.max_rows})
        rels = tx.read(cypher.ALL_RELATIONSHIPS, {"limit": graph.max_rows})
    return nodes, rels


def push(settings: Settings, graph: GraphStore) -> dict[str, int]:
    nodes, rels = _export(graph)
    with _driver(settings) as driver:
        db = settings.aura_database
        driver.execute_query(
            "MATCH (n {dvn_scope: $scope}) DETACH DELETE n", scope=SCOPE, database_=db
        )
        for label in LABELS:
            rows = [n["props"] for n in nodes if n["label"] == label]
            # Label names come from the fixed LABELS tuple above, never from data.
            driver.execute_query(
                f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
                "SET n += row, n.dvn_scope = $scope",
                rows=rows, scope=SCOPE, database_=db,
            )  # fmt: skip
        by_type: dict[tuple[str, str, str], list[dict]] = {}
        for r in rels:
            by_type.setdefault((r["from_label"], r["type"], r["to_label"]), []).append(
                {"a": r["from_id"], "b": r["to_id"]}
            )
        allowed_types = {
            "OWNS_PROJECT", "SERVES_CAUSE", "REQUIRES_SKILL", "BENEFITS_FROM_SKILL",
            "VOLUNTEERS_WITH", "HAS_SKILL", "OFFERS_SKILL", "EXCLUDES_SKILL",
            "PREFERS_CAUSE", "EXCLUDES_CAUSE",
        }  # fmt: skip
        for (src, rel, dst), rows in by_type.items():
            if src not in LABELS or dst not in LABELS or rel not in allowed_types:
                raise ValueError(f"unexpected relationship {src}-{rel}->{dst}")
            driver.execute_query(
                f"UNWIND $rows AS row MATCH (a:{src} {{id: row.a, dvn_scope: $scope}}) "
                f"MATCH (b:{dst} {{id: row.b, dvn_scope: $scope}}) MERGE (a)-[:{rel}]->(b)",
                rows=rows, scope=SCOPE, database_=db,
            )  # fmt: skip
    return {"nodes": len(nodes), "relationships": len(rels)}


def _normalise(rows: list[dict[str, Any]]) -> list[tuple]:
    out = []
    for r in rows:
        out.append(
            tuple((k, tuple(sorted(v)) if isinstance(v, list) else v) for k, v in sorted(r.items()))
        )
    return sorted(out)


def parity(settings: Settings, graph: GraphStore, org_ids: list[str]) -> dict[str, Any]:
    params = {"project": "", "volunteer": "", "preferred_only": False, "limit": 500}
    report: dict[str, Any] = {"organisations": {}, "match": True}
    with _driver(settings) as driver:
        for org in org_ids:
            local = graph.read(cypher.MATCH_CANDIDATES, params | {"orgs": [org]})
            records, _, _ = driver.execute_query(
                cypher.MATCH_CANDIDATES, params | {"orgs": [org]}, database_=settings.aura_database
            )
            remote = [rec.data() for rec in records]
            same = _normalise(local) == _normalise(remote)
            report["organisations"][org] = {"local": len(local), "aura": len(remote), "same": same}
            report["match"] = report["match"] and same
    return report
