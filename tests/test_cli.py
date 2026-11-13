"""Operator CLI: inspection output and demo guards."""

import pytest

from app import cli
from app.config import BUNDLED_GRAPH, Settings
from app.runtime import reset_demo


def test_inspect_bundled_store(capsys):
    assert cli.main(["inspect", "--store", str(BUNDLED_GRAPH)]) == 0
    out = capsys.readouterr().out
    assert "Winter Outreach Kitchen" in out and "— coverage gap —" in out
    assert "Cerys (u_" in out and "@" not in out


def test_matches_command(capsys):
    assert cli.main(["matches", "--org", "datakind-uk", "--store", str(BUNDLED_GRAPH)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("|")[1].strip() for line in lines[2:]] == ["Dev", "Asha", "Cerys", "Kofi"]


def test_demo_seed_is_refused_in_production(tmp_path):
    settings = Settings(data_dir=tmp_path, app_env="production", _env_file=None)
    with pytest.raises(RuntimeError, match="production"):
        reset_demo(settings)


def test_production_refuses_a_database_with_demo_accounts(tmp_path):
    from fastapi.testclient import TestClient

    from app.web.main import create_app

    reset_demo(Settings(data_dir=tmp_path, _env_file=None))  # a test-env demo database
    production = Settings(data_dir=tmp_path, app_env="production", _env_file=None)
    with pytest.raises(RuntimeError, match="demo accounts"), TestClient(create_app(production)):
        pass


def test_empty_compose_variables_mean_defaults(monkeypatch, tmp_path):
    for name in ("ORG_NAMES", "COOKIE_SECURE", "SESSION_TTL_HOURS", "AURA_URI"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.org_names == {} and settings.cookie_secure is None
    assert settings.session_ttl_hours == 12


def test_org_names_are_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("ORG_NAMES", '{"datakind-uk": "Organisation A"}')
    settings = Settings(data_dir=tmp_path, _env_file=None)
    rt = reset_demo(settings)
    with rt.db.read() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM organisations ORDER BY name")]
    assert names == ["Cyber Scallywags", "Organisation A"]
    graph_names = rt.graph.read("MATCH (o:Organisation) RETURN o.name AS name")
    assert {"name": "Organisation A"} in graph_names
