"""Operator and demo commands: python -m app.cli <command>.

reset --demo          wipe the runtime stores and reseed the disposable demo
bundle [--out PATH]   rebuild the committed read-only graph (data/demo.rl) from data/fixtures
inspect [--store P]   the sample graph inspection query (org -> project -> skill -> volunteers)
matches --org KEY     ranked candidates for one organisation, straight from Relate
status | drain        projection outbox status / project pending items now
grant-reviewer        give an existing account the reviewer role (operator only)
aura-push | aura-parity   optional, explicit AuraDB push and match parity check
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from app.catalogue import load_demo, stable_id
from app.config import BUNDLED_GRAPH, get_settings
from app.graph import cypher
from app.graph.store import GraphError, GraphStore
from app.runtime import build_runtime, reset_demo, seed_demo


def _table(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0])
    cells = [[", ".join(v) if isinstance(v, list) else str(v) for v in r.values()] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda vals: " | ".join(v.ljust(w) for v, w in zip(vals, widths, strict=True))  # noqa: E731
    return "\n".join([line(cols), "-+-".join("-" * w for w in widths), *map(line, cells)])


def _demo_names() -> dict[str, str]:
    return {stable_id("u", p["key"]): p["display_name"] for p in load_demo()["people"]}


def build_bundle(out: Path) -> None:
    """Seed into a throwaway directory and keep only the resulting Relate store."""
    settings = get_settings()
    with tempfile.TemporaryDirectory() as tmp:
        temp_settings = settings.model_copy(update={"data_dir": Path(tmp), "org_names": {}})
        rt = build_runtime(temp_settings)
        seed_demo(rt, copy_bundle=False)
        status = rt.projector.status()
        if status.pending:
            raise SystemExit(f"bundle aborted: {status.pending} projections still pending")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rt.graph.path, out)
        out.chmod(0o444)  # read-only: the app always works on a writable copy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset")
    reset.add_argument("--demo", action="store_true", required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--out", type=Path, default=BUNDLED_GRAPH)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--store", type=Path)
    inspect.add_argument("--json", action="store_true")
    matches = sub.add_parser("matches")
    matches.add_argument("--org", required=True, help="organisation key, e.g. datakind-uk")
    matches.add_argument("--store", type=Path)
    sub.add_parser("status")
    sub.add_parser("drain")
    grant = sub.add_parser("grant-reviewer")
    grant.add_argument("--email", required=True)
    grant.add_argument("--org", required=True, help="organisation key")
    sub.add_parser("aura-push")
    sub.add_parser("aura-parity")
    args = parser.parse_args(argv)
    settings = get_settings()

    def store(path: Path | None) -> GraphStore:
        return GraphStore(path or settings.graph_path, max_rows=settings.graph_max_rows)

    try:
        match args.command:
            case "reset":
                rt = reset_demo(settings)
                print(f"Demo reset in {settings.data_dir}: {rt.projector.status().pending} pending")
            case "bundle":
                if args.out.exists():
                    args.out.chmod(0o644)
                    args.out.unlink()
                build_bundle(args.out)
                print(f"Wrote {args.out}")
            case "inspect":
                rows = store(args.store).read(cypher.INSPECT, {"limit": 200})
                if not args.json:  # the graph holds opaque IDs; label known demo IDs for humans
                    names = _demo_names()
                    for row in rows:
                        row["eligible_volunteers"] = [
                            f"{names[v]} ({v})" if v in names else v
                            for v in row["eligible_volunteers"]
                        ] or ["— coverage gap —"]
                print(json.dumps(rows, indent=2) if args.json else _table(rows))
            case "matches":
                params = {
                    "orgs": [stable_id("org", args.org)], "project": "", "volunteer": "",
                    "preferred_only": False, "limit": 200,
                }  # fmt: skip
                rows = store(args.store).read(cypher.MATCH_CANDIDATES, params)
                names = _demo_names()
                for row in rows:
                    row["volunteer"] = names.get(row["volunteer"], row["volunteer"])
                    for key in ("matched", "optional_matched", "remaining"):
                        row[key] = sorted(row[key])
                keep = ("title", "volunteer", "preferred", "matched", "optional_matched",
                        "remaining")  # fmt: skip
                print(_table([{k: r[k] for k in keep} for r in rows]))
            case "status":
                rt = build_runtime(settings)
                print(json.dumps(rt.projector.status().__dict__, indent=2))
            case "drain":
                rt = build_runtime(settings)
                result = rt.projector.drain(ignore_backoff=True)
                print(f"projected {result.done}, failed {result.failed}")
                return 1 if result.failed else 0
            case "grant-reviewer":
                rt = build_runtime(settings)
                with rt.db.tx() as conn:
                    user = conn.execute(
                        "SELECT id FROM users WHERE email = ?", (args.email.strip().lower(),)
                    ).fetchone()
                    org = conn.execute(
                        "SELECT id FROM organisations WHERE key = ?", (args.org,)
                    ).fetchone()
                    if not user or not org:
                        raise SystemExit("unknown email or organisation key")
                    conn.execute(
                        "INSERT OR IGNORE INTO org_roles (user_id, org_id, role, granted_at) "
                        "VALUES (?, ?, 'reviewer', datetime('now'))",
                        (user["id"], org["id"]),
                    )
                print("granted")
            case "aura-push":
                from app.graph import aura

                print(json.dumps(aura.push(settings, store(None))))
            case "aura-parity":
                from app.graph import aura

                rt = build_runtime(settings)
                with rt.db.read() as conn:
                    orgs = [r[0] for r in conn.execute("SELECT id FROM organisations ORDER BY id")]
                report = aura.parity(settings, store(None), orgs)
                print(json.dumps(report, indent=2))
                return 0 if report["match"] else 1
    except GraphError as error:
        print(f"Relate: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
