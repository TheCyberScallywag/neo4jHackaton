"""The web Profile page and the mobile profile API share one structure (app.profile_view)."""

import re

from app.catalogue import get_catalogue
from app.profile_view import CAUSE_STANCES, PROJECT_STATES, SKILL_STATES
from app.runtime import DEMO_PASSWORD
from tests.conftest import Web
from tests.test_matching import save_profile


def _api_profile(web: Web, key: str) -> dict:
    token = web.client.post(
        "/api/v1/session", json={"email": f"{key}@demo.invalid", "password": DEMO_PASSWORD}
    ).json()["token"]
    return web.get("/api/v1/profile", headers={"Authorization": f"Bearer {token}"}).json()


def test_web_profile_has_the_mobile_sections_and_skill_type_sub_accordions(web: Web):
    web.sign_in("asha")
    html = web.get("/profile").text
    assert re.findall(r'data-section="(\w+)"', html) == [
        "onboarding",
        "skills",
        "causes",
        "active",
        "past",
    ]
    categories = list(dict.fromkeys(s.category for s in get_catalogue().skills))
    assert re.findall(r'data-category="([^"]+)"', html) == categories
    for label in [*SKILL_STATES.values(), *CAUSE_STANCES.values()]:
        assert f">{label}</option>" in html
    assert "3 offered · 0 excluded" in html  # Skills header, as on mobile
    assert 'Technology</span><span class="note">1 offered' in html  # per-type header


def test_web_and_api_show_the_same_profile(web: Web):
    api = _api_profile(web, "bryn")
    web.sign_in("bryn")
    html = web.get("/profile").text
    for skill in api["skills"]:
        assert f'name="skill:{skill["id"]}"' in html
    assert [g["category"] for g in api["skill_groups"]] == re.findall(
        r'data-category="([^"]+)"', html
    )
    for project in api["projects"]["active"] + api["projects"]["past"]:
        assert project["title"] in html and PROJECT_STATES[project["state"]] in html
    assert "Phishing Awareness Pop-up" in html and "Rejected" in html  # past
    assert "Winter Outreach Kitchen" in html and "You match this" in html  # active


def test_saving_from_the_web_profile_is_seen_by_the_api(web: Web):
    save_profile(web, "asha", skills={"carpentry": "offers", "cooking": "exclude"})
    api = _api_profile(web, "asha")
    states = {s["id"]: s["state"] for s in api["skills"]}
    assert states["carpentry"] == "offers" and states["cooking"] == "exclude"
    web.sign_in("asha")
    assert (
        '<option value="exclude" selected>Exclude from matching</option>'
        in web.get("/profile").text
    )
