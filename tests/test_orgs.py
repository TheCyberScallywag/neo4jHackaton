"""One login across organisations; joining vs switching; role and tenant isolation."""

from tests.conftest import AUDIT, CS, DK, FOOD_BANK, USERS, Web


def test_one_login_works_across_both_organisations(web: Web):
    web.sign_in("asha")
    assert "Food Bank Demand Dashboard" in web.get(f"/o/{DK}/projects").text
    assert "Winter Outreach Kitchen" in web.get(f"/o/{CS}/projects").text
    home = web.get("/home").text
    assert "Winter Outreach Kitchen" in home and "Food Bank Demand Dashboard" in home


def test_switching_active_organisation_needs_no_new_sign_in(web: Web):
    web.sign_in("asha")
    cookie = web.client.cookies.get("dvn_session")
    for org in (DK, CS, DK):
        r = web.post("/orgs/switch", {"org_id": org})
        assert r.status_code == 303 and r.headers["location"].startswith(f"/o/{org}/projects")
        assert f'<option value="{org}" selected' in web.get("/home").text
    assert web.client.cookies.get("dvn_session") == cookie


def test_switching_is_not_joining(web: Web):
    web.sign_in("bryn")  # volunteers with Cyber Scallywags only
    assert web.post("/orgs/switch", {"org_id": DK}).status_code == 403
    assert web.get(f"/o/{DK}/projects").status_code == 403
    assert web.post(f"/orgs/{DK}/join").status_code == 303
    assert web.post("/orgs/switch", {"org_id": DK}).status_code == 303
    assert "Food Bank Demand Dashboard" in web.get(f"/o/{DK}/projects").text


def test_opting_out_removes_access_and_candidacy(web: Web):
    web.sign_in("asha")
    assert web.post(f"/orgs/{CS}/leave").status_code == 303
    assert web.get(f"/o/{CS}/projects").status_code == 403
    web.sign_in("kofi")  # Cyber Scallywags reviewer
    page = web.get(f"/o/{CS}/coverage").text  # candidates are drawn from Relate
    assert "Asha" not in page and "Bryn" in page


def test_unknown_organisation_is_not_found(web: Web):
    web.sign_in("asha")
    assert web.get("/o/org_doesnotexist/projects").status_code == 404
    assert web.post("/orgs/org_doesnotexist/join").status_code == 404


def test_review_pages_need_the_reviewer_role_for_that_organisation(web: Web):
    web.sign_in("kofi")  # reviewer for Cyber Scallywags, volunteer for DataKind UK
    assert web.get(f"/o/{CS}/review").status_code == 200
    assert web.get(f"/o/{DK}/review").status_code == 403
    assert web.get(f"/o/{DK}/coverage").status_code == 403
    assert web.get(f"/o/{CS}/coverage").status_code == 200
    # ...and he can volunteer in the other one
    assert "Food Bank Demand Dashboard" in web.get("/matches?scope=" + DK).text


def test_forged_ids_cannot_cross_tenants(web: Web):
    web.sign_in("kofi")
    # a DataKind UK submission is invisible to a Cyber Scallywags reviewer
    assert web.get(f"/submissions/{AUDIT}").status_code == 404
    review = {"action": "approved", "reason": "forged", "revision": "1", "version": "1"}
    # posting through his own organisation's URL with the other org's submission ID
    assert web.post(f"/o/{CS}/review/{AUDIT}", review).status_code == 404
    # posting through the other organisation's URL
    assert web.post(f"/o/{DK}/review/{AUDIT}", review).status_code == 403
    # withdrawing another organisation's project
    r = web.post(f"/o/{CS}/projects/{FOOD_BANK}/withdraw", {"reason": "forged", "version": "2"})
    assert r.status_code == 404
    # a project ID under the wrong organisation
    assert web.get(f"/o/{CS}/projects/{FOOD_BANK}").status_code == 404
    with web.rt.db.read() as conn:
        assert conn.execute("SELECT state FROM submissions WHERE id = ?", (AUDIT,)).fetchone()[
            0
        ] == ("submitted")


def test_candidate_lists_are_scoped_and_hide_contact_details(web: Web):
    web.sign_in("rhiannon")  # DataKind UK reviewer
    page = web.get(f"/o/{DK}/coverage").text
    for name in ("Dev", "Asha", "Cerys", "Kofi"):
        assert name in page
    assert "Bryn" not in page and "Gwen" not in page  # Cyber Scallywags only
    assert "@demo.invalid" not in page
    assert USERS["asha"] not in page  # opaque IDs stay server-side too
