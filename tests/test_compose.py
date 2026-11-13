"""Smoke test of the running Compose stack (uv run task up). Skipped unless COMPOSE_WEB_URL is
set, e.g. COMPOSE_WEB_URL=http://localhost:8080 uv run pytest -m compose --no-cov."""

import os
import re

import httpx
import pytest

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(not os.environ.get("COMPOSE_WEB_URL"), reason="COMPOSE_WEB_URL not set"),
]


def test_stack_signs_in_and_matches():
    base = os.environ["COMPOSE_WEB_URL"]
    with httpx.Client(base_url=base, follow_redirects=True, timeout=10) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/signin").text).group(1)
        r = client.post(
            "/signin",
            data={"email": "asha@demo.invalid", "password": "demo-password-1", "csrf_token": csrf},
        )
        assert "Winter Outreach Kitchen" in r.text
    mobile = os.environ.get("COMPOSE_MOBILE_URL")
    if mobile:
        r = httpx.post(
            f"{mobile}/api/v1/session",
            json={"email": "asha@demo.invalid", "password": "demo-password-1"},
        )
        assert r.status_code == 200 and r.json()["token"]
    admin = os.environ.get("COMPOSE_ADMIN_URL")
    if admin:
        assert httpx.get(f"{admin}/_stcore/health").text == "ok"
