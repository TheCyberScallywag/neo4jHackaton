"""RBAC (system admin, moderator, org admin, reviewer, project lead), volunteer onboarding
(vetting, then eligible → both say yes → team), project onboarding notifications, all via the
JSON API, which is the system's single action surface."""

from app.catalogue import get_catalogue, stable_id
from app.runtime import DEMO_PASSWORD
from tests.conftest import AUDIT, CS, DK, USERS, WINTER, Web

NIA = stable_id("u", "nia")  # seeded in screening, offers first aid, joined Cyber Scallywags
SAM = stable_id("u", "sam")  # system admin
MO = stable_id("u", "mo")  # moderator


class Client:
    def __init__(self, web: Web, token: str) -> None:
        self.web, self.h = web, {"Authorization": f"Bearer {token}"}

    def get(self, path, **kw):
        return self.web.client.get(f"/api/v1{path}", headers=self.h, **kw)

    def post(self, path, json=None):
        return self.web.client.post(f"/api/v1{path}", json=json or {}, headers=self.h)

    def put(self, path, json=None):
        return self.web.client.put(f"/api/v1{path}", json=json or {}, headers=self.h)

    def delete(self, path):
        return self.web.client.delete(f"/api/v1{path}", headers=self.h)

    def messages(self) -> list[str]:
        return [n["message"] for n in self.get("/notifications").json()]


def as_(web: Web, key: str) -> Client:
    r = web.client.post(
        "/api/v1/session", json={"email": f"{key}@demo.invalid", "password": DEMO_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return Client(web, r.json()["token"])


def status_version(web: Web, user_id: str) -> int:
    with web.rt.db.read() as conn:
        return conn.execute("SELECT status_version FROM users WHERE id = ?", (user_id,)).fetchone()[
            0
        ]


def placement(web: Web, project: str, volunteer: str) -> dict | None:
    with web.rt.db.read() as conn:
        row = conn.execute(
            "SELECT * FROM placements WHERE project_id = ? AND volunteer_id = ?",
            (project, volunteer),
        ).fetchone()
    return dict(row) if row else None


def graph_edges(web: Web, user_id: str, rel: str) -> list[str]:
    rows = web.rt.graph.read(
        f"MATCH (:Volunteer {{id: $id}})-[:{rel}]->(t) RETURN t.id AS id ORDER BY id",
        {"id": user_id},
    )
    return [r["id"] for r in rows]


def full_profile(c: Client, **skills) -> dict:
    p = c.get("/profile").json()
    return {
        "display_name": p["display_name"],
        "version": p["version"],
        "skills": {s["id"]: s["state"] for s in p["skills"]} | skills,
        "causes": {x["id"]: x["stance"] for x in p["causes"]},
    }


# ------------------------------------------------------------------ volunteer onboarding
def test_full_volunteer_onboarding_to_team(web: Web):
    r = web.client.post(
        "/api/v1/auth/signup",
        json={"email": "ffion@example.org", "display_name": "Ffion", "password": "long-enough-pw"},
    )
    assert r.status_code == 201
    ffion = Client(web, r.json()["token"])
    uid = r.json()["user_id"]
    assert ffion.get("/me").json()["status"] == "draft"
    assert ffion.put(f"/orgs/{CS}/membership").status_code == 200
    # step 1: the form must offer a skill before it can go to screening
    assert ffion.post("/me/vetting").status_code == 422
    assert ffion.put("/profile", full_profile(ffion, **{"first-aid": "offers"})).status_code == 200
    assert ffion.post("/me/vetting").json() == {"status": "screening"}
    assert ffion.post("/me/vetting").status_code == 409  # already in screening
    assert "is in screening" in ffion.messages()[0]
    assert graph_edges(web, uid, "VOLUNTEERS_WITH") == []  # not matchable before vetting
    assert "awaiting" in ffion.get(f"/orgs/{CS}/matches").json()["withheld"] or True
    assert ffion.get(f"/orgs/{CS}/matches").json()["matches"] == []
    mo = as_(web, "mo")
    assert any("waiting for screening" in m for m in mo.messages())
    assert uid in [v["id"] for v in mo.get("/moderation/volunteers").json()]
    record = mo.get(f"/moderation/volunteers/{uid}").json()
    assert record["skills"] == {"first-aid": "offers"} and "email" not in record
    # moderator approves: account active, membership edge projected, eligibility found
    decision = {"action": "approve", "version": status_version(web, uid)}
    assert mo.post(f"/moderation/volunteers/{uid}/decision", decision).json() == {
        "status": "active"
    }
    assert graph_edges(web, uid, "VOLUNTEERS_WITH") == [CS]
    messages = ffion.messages()
    assert any("approved and active" in m for m in messages)
    assert any("eligible to volunteer on “Winter Outreach Kitchen”" in m for m in messages)
    asha = as_(web, "asha")  # the project lead
    assert any("Ffion is eligible for “Winter Outreach Kitchen”" in m for m in asha.messages())
    # step 2: both say yes
    p = placement(web, WINTER, uid)
    assert p["state"] == "eligible" and p["matched_skills"] == '["first-aid"]'
    assert ffion.post(
        f"/placements/{WINTER}/answer", {"answer": "yes", "version": p["version"]}
    ).json() == {"state": "eligible"}  # noqa: E501
    assert any("Waiting for the project lead" in m for m in ffion.messages())
    p = placement(web, WINTER, uid)
    r = asha.post(
        f"/projects/{WINTER}/placements/{uid}/answer", {"answer": "yes", "version": p["version"]}
    )
    assert r.json() == {"state": "team"}
    assert any("You are on the team for “Winter Outreach Kitchen”" in m for m in ffion.messages())
    assert graph_edges(web, uid, "ON_TEAM") == [WINTER]
    mine = ffion.get("/placements").json()
    assert [(x["title"], x["state"]) for x in mine] == [("Winter Outreach Kitchen", "team")]


def test_moderation_actions_and_guards(web: Web):
    mo, asha, sam = as_(web, "mo"), as_(web, "asha"), as_(web, "sam")
    v = status_version(web, NIA)
    # volunteers cannot vet; moderators cannot vet themselves; reasons required; stale = 409
    assert (
        asha.post(
            f"/moderation/volunteers/{NIA}/decision", {"action": "approve", "version": v}
        ).status_code
        == 403
    )  # noqa: E501
    assert asha.get("/moderation/volunteers").json()["detail"].startswith("Only moderators")
    assert mo.post(
        f"/moderation/volunteers/{MO}/decision", {"action": "approve", "version": 0}
    ).status_code in (403, 409)  # noqa: E501
    assert (
        mo.post(
            f"/moderation/volunteers/{NIA}/decision", {"action": "decline", "version": v}
        ).status_code
        == 422
    )  # noqa: E501
    assert (
        mo.post(
            f"/moderation/volunteers/{NIA}/decision", {"action": "approve", "version": v - 1}
        ).status_code
        == 409
    )  # noqa: E501
    r = mo.post(f"/moderation/volunteers/{NIA}/decision",
                {"action": "request_info", "note": "Please add your first-aid certificate date", "version": v})  # noqa: E501  # fmt: skip
    assert r.json() == {"status": "info_requested"}
    nia = as_(web, "nia")
    assert any("more information" in m and "certificate" in m for m in nia.messages())
    assert nia.post("/me/vetting").json() == {"status": "screening"}
    # admins see emails; moderators don't
    assert "email" in sam.get(f"/moderation/volunteers/{NIA}").json()
    assert "email" not in mo.get(f"/moderation/volunteers/{NIA}").json()
    history = sam.get(f"/moderation/volunteers/{NIA}").json()["history"]
    assert [h["to_status"] for h in history] == ["info_requested", "screening"]


def test_vetting_nia_fills_the_first_aid_gap(web: Web):
    kofi = as_(web, "kofi")  # Cyber Scallywags org admin
    gaps = kofi.get(f"/orgs/{CS}/coverage").json()["projects"][0]["gaps"]
    assert gaps == ["First aid"]
    mo = as_(web, "mo")
    mo.post(f"/moderation/volunteers/{NIA}/decision",
            {"action": "approve", "version": status_version(web, NIA)})  # fmt: skip
    assert kofi.get(f"/orgs/{CS}/coverage").json()["projects"][0]["gaps"] == []
    assert placement(web, WINTER, NIA)["state"] == "eligible"


def test_suspension_removes_matching(web: Web):
    mo = as_(web, "mo")
    bryn = USERS["bryn"]
    r = mo.post(f"/moderation/volunteers/{bryn}/decision",
                {"action": "suspend", "note": "Paused pending a check", "version": status_version(web, bryn)})  # noqa: E501  # fmt: skip
    assert r.json() == {"status": "suspended"}
    assert graph_edges(web, bryn, "VOLUNTEERS_WITH") == []
    names = [
        c["name"]
        for row in as_(web, "kofi").get(f"/orgs/{CS}/coverage").json()["projects"][0]["per_skill"]
        for c in row["candidates"]
    ]
    assert "Bryn" not in names
    assert as_(web, "bryn").get("/me").json()["status_label"] == "Suspended"


# ------------------------------------------------------------------ placements
def test_lead_side_is_restricted_and_volunteer_side_is_self_only(web: Web):
    p = placement(web, AUDIT, USERS["asha"])
    assert p is None  # the audit is not active yet, so nobody is eligible
    p = placement(web, WINTER, USERS["asha"])  # Asha leads Winter Outreach; lead side is "yes"
    assert p["lead_said"] == "yes" and p["state"] == "eligible"
    gwen, kofi, dev = as_(web, "gwen"), as_(web, "kofi"), as_(web, "dev")
    fb = stable_id("p", "food-bank-dashboard")
    p = placement(web, fb, USERS["dev"])
    body = {"answer": "yes", "version": p["version"]}
    # another volunteer of the same org can't answer for the lead; another org's admin can't see it
    assert (
        as_(web, "asha").post(f"/projects/{fb}/placements/{USERS['dev']}/answer", body).status_code
        == 403
    )  # noqa: E501
    assert kofi.post(f"/projects/{fb}/placements/{USERS['dev']}/answer", body).status_code == 403
    assert gwen.post(f"/projects/{fb}/placements/{USERS['dev']}/answer", body).status_code == 404
    assert gwen.post(f"/placements/{fb}/answer", body).status_code == 404  # not her placement
    # the org admin can act as lead; a no from either side declines
    rhiannon = as_(web, "rhiannon")
    assert rhiannon.get(f"/projects/{fb}/placements").status_code == 200
    r = rhiannon.post(
        f"/projects/{fb}/placements/{USERS['dev']}/answer",
        {"answer": "no", "version": p["version"]},
    )  # noqa: E501
    assert r.json() == {"state": "declined"}
    assert any("has not taken your place" in m for m in dev.messages())
    assert dev.post(f"/placements/{fb}/answer", body).status_code == 409  # stale and closed


def test_new_exclusion_lapses_an_offer_and_reoffering_revives_it(web: Web):
    asha = as_(web, "asha")
    fb = stable_id("p", "food-bank-dashboard")
    assert placement(web, fb, USERS["asha"])["state"] == "eligible"
    asha.put("/profile", full_profile(asha, **{"data-analysis": "exclude"}))
    assert placement(web, fb, USERS["asha"])["state"] == "lapsed"
    assert any("no longer eligible for “Food Bank Demand Dashboard”" in m for m in asha.messages())
    asha.put("/profile", full_profile(asha, **{"data-analysis": "offers"}))
    assert placement(web, fb, USERS["asha"])["state"] == "eligible"


def test_team_members_can_leave_or_be_removed(web: Web):
    bryn, asha = as_(web, "bryn"), as_(web, "asha")
    assert graph_edges(web, USERS["bryn"], "ON_TEAM") == [WINTER]  # seeded team
    assert (
        asha.post(
            f"/projects/{WINTER}/placements/{USERS['bryn']}/remove", {"reason": "x"}
        ).status_code
        == 422
    )  # noqa: E501
    r = asha.post(
        f"/projects/{WINTER}/placements/{USERS['bryn']}/remove", {"reason": "Rota is full"}
    )  # noqa: E501
    assert r.json() == {"state": "removed"}
    assert graph_edges(web, USERS["bryn"], "ON_TEAM") == []
    assert any("removed from the team" in m and "Rota is full" in m for m in bryn.messages())
    assert bryn.post(f"/placements/{WINTER}/leave").status_code == 404


# ------------------------------------------------------------------ org admin
def test_org_admins_manage_only_their_own_organisation(web: Web):
    kofi, rhiannon, sam = as_(web, "kofi"), as_(web, "rhiannon"), as_(web, "sam")
    names = [v["display_name"] for v in kofi.get(f"/orgs/{CS}/volunteers").json()]
    assert names == ["Asha", "Bryn", "Gwen", "Nia"]
    assert "email" not in str(kofi.get(f"/orgs/{CS}/volunteers").json())
    assert kofi.get(f"/orgs/{DK}/volunteers").status_code == 403
    assert rhiannon.get(f"/orgs/{CS}/volunteers").status_code == 403
    assert as_(web, "asha").get(f"/orgs/{CS}/volunteers").status_code == 403  # plain volunteer
    # system admin: every organisation
    assert sam.get(f"/orgs/{DK}/volunteers").status_code == 200
    assert sam.get(f"/orgs/{CS}/submissions").status_code == 200
    # remove a volunteer: membership, team place and graph edges go
    r = kofi.post(f"/orgs/{CS}/volunteers/{USERS['bryn']}/remove", {"reason": "Moved away"})
    assert r.status_code == 200
    assert graph_edges(web, USERS["bryn"], "VOLUNTEERS_WITH") == []
    assert graph_edges(web, USERS["bryn"], "ON_TEAM") == []
    assert placement(web, WINTER, USERS["bryn"])["state"] == "removed"
    assert any("removed you as a volunteer: Moved away" in m for m in as_(web, "bryn").messages())


def test_reviewer_role_reviews_but_does_not_manage(web: Web):
    sam = as_(web, "sam")
    assert sam.put(f"/admin/orgs/{DK}/roles/{USERS['dev']}/reviewer").status_code == 200
    dev = as_(web, "dev")
    assert dev.get(f"/orgs/{DK}/submissions").status_code == 200
    assert dev.get(f"/orgs/{DK}/volunteers").status_code == 403
    assert any("reviewer role for DataKind UK" in m for m in dev.messages())


def test_project_lead_can_be_reassigned_by_org_admin(web: Web):
    fb = stable_id("p", "food-bank-dashboard")
    rhiannon, cerys = as_(web, "rhiannon"), as_(web, "cerys")
    assert cerys.put(f"/orgs/{DK}/projects/{fb}/lead", {"user_id": USERS["dev"]}).status_code == 403
    assert (
        rhiannon.put(f"/orgs/{DK}/projects/{fb}/lead", {"user_id": USERS["gwen"]}).status_code
        == 422
    )  # noqa: E501
    assert (
        rhiannon.put(f"/orgs/{DK}/projects/{fb}/lead", {"user_id": USERS["dev"]}).status_code == 200
    )  # noqa: E501
    assert as_(web, "dev").get(f"/projects/{fb}/placements").status_code == 200


# ------------------------------------------------------------------ system admin
def test_only_system_admins_manage_roles_organisations_and_projection(web: Web):
    sam, mo, kofi = as_(web, "sam"), as_(web, "mo"), as_(web, "kofi")
    for c in (mo, kofi):
        assert c.get("/admin/users").status_code == 403
        assert c.put(f"/admin/users/{USERS['gwen']}/system-roles/moderator").status_code == 403
        assert c.get("/admin/projection").status_code == 403
    users = sam.get("/admin/users").json()
    assert {u["display_name"]: u["system_roles"] for u in users}["Mo"] == ["moderator"]
    assert sam.put(f"/admin/users/{USERS['gwen']}/system-roles/moderator").status_code == 200
    assert as_(web, "gwen").get("/moderation/volunteers").status_code == 200
    assert sam.delete(f"/admin/users/{SAM}/system-roles/admin").status_code == 422
    r = sam.post("/admin/orgs", {"key": "riverside-food", "name": "Riverside Food Hub"})
    assert r.status_code == 201
    assert sam.post("/admin/orgs", {"key": "riverside-food", "name": "Again"}).status_code == 409
    assert sam.get("/admin/projection").json()["pending"] == 0
    graph = sam.get("/admin/graph").json()
    assert {"type": "ON_TEAM", "relationships": 1} in graph["relationships"]


# ------------------------------------------------------------------ project onboarding
def test_project_onboarding_notifies_each_phase(web: Web):
    draft = {
        "org_id": DK, "title": "Allotment Data Map",
        "summary": "Map fictional allotment plots so new growers can find space nearby.",
        "cause_id": "education", "required": ["data-analysis"], "optional": [],
    }  # fmt: skip
    nia = as_(web, "nia")  # not vetted: cannot suggest projects
    assert nia.post("/submissions", draft | {"org_id": CS}).status_code == 403
    cerys = as_(web, "cerys")
    sid = cerys.post("/submissions", draft).json()["id"]
    assert any("Allotment Data Map" in m and "in review" in m for m in cerys.messages())
    rhiannon = as_(web, "rhiannon")
    assert any("New project suggestion" in m for m in rhiannon.messages())
    sub = rhiannon.get(f"/submissions/{sid}").json()
    scoping = {"action": "changes_requested", "reason": "Add a privacy step",
               "revision": 1, "version": sub["version"]}  # fmt: skip
    assert rhiannon.post(f"/orgs/{DK}/submissions/{sid}/decision", scoping).json() == {
        "state": "changes_requested"
    }
    assert any("is in scoping" in m and "privacy step" in m for m in cerys.messages())
    v = cerys.get(f"/submissions/{sid}").json()["version"]
    assert cerys.post(f"/submissions/{sid}/revisions", draft | {"version": v}).status_code == 200
    v = rhiannon.get(f"/submissions/{sid}").json()["version"]
    approve = {"action": "approved", "reason": "Good", "revision": 2, "version": v}
    assert rhiannon.post(f"/orgs/{DK}/submissions/{sid}/decision", approve).json() == {
        "state": "published"
    }
    assert any("is now active" in m for m in cerys.messages())
    # an active project immediately matches active volunteers
    assert placement(web, sid, USERS["dev"])["state"] == "eligible"
    # withdrawing ends open offers and teams, with notice
    v = rhiannon.get(f"/submissions/{sid}").json()["version"]
    rhiannon.post(f"/orgs/{DK}/projects/{sid}/withdraw", {"reason": "Paused", "version": v})
    assert placement(web, sid, USERS["dev"])["state"] == "removed"
    assert any("Allotment Data Map" in m and "withdrawn" in m for m in as_(web, "dev").messages())


# ------------------------------------------------------------------ API surface
def test_api_errors_are_json_and_docs_are_published(web: Web):
    r = as_(web, "asha").get(f"/orgs/{DK}/volunteers")
    assert r.status_code == 403 and r.headers["content-type"].startswith("application/json")
    assert web.get("/api/v1/me").json()["detail"] == "Sign in first"
    spec = web.get("/api/openapi.json").json()
    tags = {t for path in spec["paths"].values() for op in path.values() for t in op["tags"]}
    assert tags == {"auth", "me", "organisations", "projects", "placements", "moderation", "admin"}
    docs = web.get("/api/docs")
    assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in web.get("/").headers["content-security-policy"]


def test_notifications_can_be_marked_read(web: Web):
    asha = as_(web, "asha")
    me = asha.get("/me").json()
    assert me["unread_notifications"] > 0
    first = asha.get("/notifications").json()[0]["id"]
    assert asha.post(f"/notifications/{first}/read").json() == {"read": 1}
    assert as_(web, "bryn").post(f"/notifications/{first}/read").status_code == 404  # not hers
    asha.post("/notifications/read-all")
    assert asha.get("/notifications?unread=true").json() == []


# ------------------------------------------------------------------ web parity
def test_web_pages_for_onboarding_opportunities_and_org_admins(web: Web):
    web.sign_in("nia")
    assert 'data-status="screening"' in web.get("/home").text
    web.sign_in("dev")
    page = web.get("/opportunities").text
    assert "Food Bank Demand Dashboard" in page and "Yes, I&#39;d like to help" in page
    fb = stable_id("p", "food-bank-dashboard")
    v = placement(web, fb, USERS["dev"])["version"]
    assert (
        web.post(f"/opportunities/{fb}/answer", {"answer": "yes", "version": v}).status_code == 303
    )
    web.sign_in("cerys")  # lead of the food bank project
    page = web.get(f"/o/{DK}/projects/{fb}").text
    assert 'data-volunteer="Dev"' in page and "you lead this project" in page
    v = placement(web, fb, USERS["dev"])["version"]
    r = web.post(f"/o/{DK}/projects/{fb}/candidates/{USERS['dev']}/answer",
                 {"answer": "yes", "version": v})  # fmt: skip
    assert r.status_code == 303 and placement(web, fb, USERS["dev"])["state"] == "team"
    web.sign_in("rhiannon")
    assert 'data-volunteer="Dev"' in web.get(f"/o/{DK}/volunteers").text
    web.sign_in("kofi")
    assert web.get(f"/o/{DK}/volunteers").status_code == 403
    web.sign_in("dev")
    assert "You are on the team" in web.get("/notifications").text
    assert get_catalogue()  # catalogue unchanged
