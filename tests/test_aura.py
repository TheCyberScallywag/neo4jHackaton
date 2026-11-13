"""Optional AuraDB push and match parity. Runs only against a designated dev instance:
set AURA_URI (neo4j+s://...), AURA_PASSWORD and run `uv sync --group aura`."""

import os
import shutil

import pytest

from app.catalogue import stable_id
from app.config import BUNDLED_GRAPH, Settings
from app.graph import aura
from app.graph.store import GraphStore

pytestmark = [
    pytest.mark.aura,
    pytest.mark.skipif(
        not (os.environ.get("AURA_URI") and os.environ.get("AURA_PASSWORD")),
        reason="no designated dev AuraDB credentials (AURA_URI / AURA_PASSWORD)",
    ),
]


def test_push_and_parity(tmp_path):
    store_path = tmp_path / "demo.rl"
    shutil.copyfile(BUNDLED_GRAPH, store_path)
    settings = Settings()
    graph = GraphStore(store_path)
    counts = aura.push(settings, graph)
    assert counts == {"nodes": 32, "relationships": 62}
    report = aura.parity(
        settings, graph, [stable_id("org", "datakind-uk"), stable_id("org", "cyber-scallywags")]
    )
    assert report["match"], report
