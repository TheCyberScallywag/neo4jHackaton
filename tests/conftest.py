"""Shared fixtures: every test gets its own temporary SQLite database and real Relate store,
seeded from data/fixtures/demo.json through the same code path as the demo."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.catalogue import stable_id
from app.config import Settings
from app.runtime import DEMO_PASSWORD, Runtime
from app.web.main import create_app

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')

DK = stable_id("org", "datakind-uk")
CS = stable_id("org", "cyber-scallywags")
USERS = {
    k: stable_id("u", k)
    for k in ("asha", "bryn", "cerys", "dev", "gwen", "farah", "kofi", "rhiannon")
}
FOOD_BANK = stable_id("p", "food-bank-dashboard")
WINTER = stable_id("p", "winter-outreach")
AUDIT = stable_id("p", "outreach-data-audit")  # submitted, awaiting DataKind UK review
PHISHING = stable_id("p", "phishing-workshop")  # rejected


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "var", projector_interval_seconds=3600, _env_file=None)


class Web:
    """A TestClient plus helpers to act as one signed-in person at a time."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    @property
    def rt(self) -> Runtime:
        return self.client.app.state.rt

    def csrf(self, path: str = "/signin") -> str:
        match = CSRF_RE.search(self.client.get(path).text)
        assert match, f"no CSRF token on {path}"
        return match.group(1)

    def sign_in(self, key: str, password: str = DEMO_PASSWORD) -> Web:
        self.client.cookies.clear()
        token = self.csrf("/signin")
        r = self.client.post(
            "/signin",
            data={"email": f"{key}@demo.invalid", "password": password, "csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        return self

    def post(self, path: str, data: dict | None = None, *, csrf_from: str = "/home", **kw):
        payload = {"csrf_token": self.csrf(csrf_from), **(data or {})}
        return self.client.post(path, data=payload, follow_redirects=False, **kw)

    def get(self, path: str, **kw):
        return self.client.get(path, **kw)


@pytest.fixture
def web(settings: Settings) -> Iterator[Web]:
    with TestClient(create_app(settings, background=False)) as client:
        yield Web(client)


@pytest.fixture
def rt(web: Web) -> Runtime:
    return web.rt
