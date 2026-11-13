"""Bearer-token JSON API for the mobile companion."""

from app.runtime import DEMO_PASSWORD
from tests.conftest import CS, DK, USERS, WINTER, Web


def _token(web: Web, key: str) -> dict:
    r = web.client.post(
        "/api/v1/session", json={"email": f"{key}@demo.invalid", "password": DEMO_PASSWORD}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_sign_in_me_and_matches(web: Web):
    h = _token(web, "asha")
    me = web.get("/api/v1/me", headers=h).json()
    assert me["user"]["display_name"] == "Asha"
    assert {o["name"] for o in me["organisations"] if o["opted_in"]} == {
        "DataKind UK",
        "Cyber Scallywags",
    }
    assert "email" not in str(me)
    m = web.get(f"/api/v1/orgs/{CS}/matches", headers=h).json()
    assert m["withheld"] is None
    assert m["matches"][0]["project"] == WINTER
    assert m["matches"][0]["matched_skills"] == ["Active listening", "Community cooking"]
    assert (
        web.client.put("/api/v1/me/active-org", json={"org_id": CS}, headers=h).status_code == 200
    )
    assert web.get("/api/v1/me", headers=h).json()["active_org_id"] == CS


def test_api_enforces_membership_and_rejects_cookies(web: Web):
    h = _token(web, "bryn")
    assert web.get(f"/api/v1/orgs/{DK}/matches", headers=h).status_code == 403
    assert (
        web.client.put("/api/v1/me/active-org", json={"org_id": DK}, headers=h).status_code == 403
    )
    web.sign_in("asha")  # a valid web cookie is not an API credential
    assert web.get("/api/v1/me").status_code == 401


def test_api_sign_out_revokes(web: Web):
    h = _token(web, "gwen")
    assert web.client.delete("/api/v1/session", headers=h).status_code == 204
    assert web.get("/api/v1/me", headers=h).status_code == 401
    assert (
        web.client.post(
            "/api/v1/session", json={"email": "gwen@demo.invalid", "password": "wrong-wrong"}
        ).status_code
        == 401
    )


def test_profile_lists_skills_causes_and_projects(web: Web):
    h = _token(web, "asha")
    p = web.get("/api/v1/profile", headers=h).json()
    skills = {s["id"]: s["state"] for s in p["skills"]}
    assert skills["cooking"] == "offers" and skills["carpentry"] == "none"
    assert {c["id"]: c["stance"] for c in p["causes"]}["homelessness"] == "prefer"
    active = {(a["title"], a["relation"]) for a in p["projects"]["active"]}
    # her own suggestions (pending + published) and a published project she matches
    assert ("Outreach Data Audit", "suggested") in active
    assert ("Winter Outreach Kitchen", "suggested") in active
    assert ("Food Bank Demand Dashboard", "matched") in active
    assert p["projects"]["past"] == []
    assert "email" not in str(p)


def test_past_projects_are_rejected_or_withdrawn_suggestions(web: Web):
    p = web.get("/api/v1/profile", headers=_token(web, "bryn")).json()
    assert [(x["title"], x["state"]) for x in p["projects"]["past"]] == [
        ("Phishing Awareness Pop-up", "rejected")
    ]


def test_profile_edit_updates_relate_and_guards_stale_versions(web: Web):
    h = _token(web, "asha")
    p = web.get("/api/v1/profile", headers=h).json()
    body = {
        "display_name": "Asha",
        "version": p["version"],
        "skills": {s["id"]: s["state"] for s in p["skills"]} | {"active-listening": "exclude"},
        "causes": {c["id"]: c["stance"] for c in p["causes"]},
    }
    r = web.client.put("/api/v1/profile", json=body, headers=h)
    assert r.status_code == 200
    titles = {a["title"] for a in r.json()["projects"]["active"] if a["relation"] == "matched"}
    assert "Winter Outreach Kitchen" not in titles  # suggested by her, but no longer matched
    edges = web.rt.graph.read(
        "MATCH (:Volunteer {id: $id})-[:EXCLUDES_SKILL]->(s:Skill) RETURN s.id AS id",
        {"id": USERS["asha"]},
    )
    assert edges == [{"id": "active-listening"}]
    assert web.client.put("/api/v1/profile", json=body, headers=h).status_code == 409  # stale
    bad = body | {"version": r.json()["version"], "skills": {"juggling": "offers"}}
    assert web.client.put("/api/v1/profile", json=bad, headers=h).status_code == 422
    assert web.client.put("/api/v1/profile", json=body).status_code == 401
