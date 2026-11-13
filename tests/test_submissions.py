"""Suggest → review → publish → withdraw, with visibility and stale-form protection."""

from app.graph import cypher
from tests.conftest import AUDIT, CS, DK, PHISHING, Web

DRAFT = {
    "title": "Allotment Data Map",
    "summary": "Map fictional allotment plots so new growers can find space nearby.",
    "cause_id": "environment",
    "required": ["data-analysis", "gardening"],
    "optional": ["data-visualisation"],
}


def _submit(web: Web, org: str, **overrides) -> str:
    data = {**DRAFT, "org_id": org, **overrides}
    r = web.post("/submissions/new", data)
    assert r.status_code == 303, r.text
    return r.headers["location"].split("/submissions/")[1].split("?")[0]


def _state(web: Web, sid: str) -> dict:
    with web.rt.db.read() as conn:
        return dict(conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone())


def _review(web: Web, org: str, sid: str, action: str, **overrides):
    s = _state(web, sid)
    data = {
        "action": action,
        "reason": f"{action} because reasons",
        "private_note": "reviewer-only note",
        "revision": s["current_revision"],
        "version": s["version"],
        **overrides,
    }
    return web.post(f"/o/{org}/review/{sid}", data)


def _in_graph(web: Web, sid: str) -> bool:
    return bool(web.rt.graph.read("MATCH (p:Project {id: $id}) RETURN p.id AS id", {"id": sid}))


def test_submission_is_private_until_approved(web: Web):
    web.sign_in("cerys")
    sid = _submit(web, DK)
    assert _state(web, sid)["author_id"] and _state(web, sid)["org_id"] == DK
    assert "Allotment Data Map" in web.get(f"/submissions/{sid}").text
    assert "Allotment Data Map" in web.get("/submissions").text
    for other in ("asha", "kofi"):  # another volunteer; another organisation's reviewer
        web.sign_in(other)
        assert web.get(f"/submissions/{sid}").status_code == 404
        assert "Allotment Data Map" not in web.get(f"/o/{DK}/projects").text
    web.sign_in("rhiannon")
    assert "Allotment Data Map" in web.get(f"/o/{DK}/review").text
    assert not _in_graph(web, sid)


def test_submitter_and_target_come_from_the_server(web: Web):
    web.sign_in("bryn")  # Cyber Scallywags only
    r = web.post("/submissions/new", {**DRAFT, "org_id": DK, "author_id": "u_forged"})
    assert r.status_code == 403
    sid = _submit(web, CS, author_id="u_forged")
    assert _state(web, sid)["author_id"] != "u_forged"


def test_invalid_skills_are_rejected(web: Web):
    web.sign_in("cerys")
    r = web.post("/submissions/new", {**DRAFT, "org_id": DK, "required": ["juggling"]})
    assert r.status_code == 400 and "shared catalogue" in r.text
    r = web.post("/submissions/new", {**DRAFT, "org_id": DK, "required": []})
    assert r.status_code == 400


def test_changes_requested_then_revised_then_approved(web: Web):
    web.sign_in("cerys")
    sid = _submit(web, DK)
    web.sign_in("rhiannon")
    assert (
        _review(
            web, DK, sid, "changes_requested", reason="Please add a data protection step"
        ).status_code
        == 303
    )
    web.sign_in("cerys")
    page = web.get(f"/submissions/{sid}").text
    assert "Please add a data protection step" in page
    assert "reviewer-only note" not in page  # private review notes stay with reviewers
    s = _state(web, sid)
    revised = {**DRAFT, "title": "Allotment Data Map v2", "version": s["version"]}
    assert web.post(f"/submissions/{sid}/revise", revised).status_code == 303
    s = _state(web, sid)
    assert (s["state"], s["current_revision"]) == ("submitted", 2)
    web.sign_in("rhiannon")
    assert "reviewer-only note" in web.get(f"/submissions/{sid}").text
    assert _review(web, DK, sid, "approved").status_code == 303
    s = _state(web, sid)
    assert s["state"] == "published" and s["approved_revision"] == 2
    assert s["approved_by"] and s["approved_at"] and s["published_at"]
    assert "Allotment Data Map v2" in web.get(f"/o/{DK}/projects").text
    assert _in_graph(web, sid)


def test_stale_and_repeated_approvals_cannot_publish_twice(web: Web):
    web.sign_in("cerys")
    sid = _submit(web, DK)
    web.sign_in("rhiannon")
    stale = _state(web, sid)
    assert _review(web, DK, sid, "changes_requested").status_code == 303
    # approving from the form rendered before the change request
    r = _review(
        web, DK, sid, "approved", revision=stale["current_revision"], version=stale["version"]
    )
    assert r.status_code == 409
    web.sign_in("cerys")
    s = _state(web, sid)
    web.post(f"/submissions/{sid}/revise", {**DRAFT, "version": s["version"]})
    web.sign_in("rhiannon")
    # approving revision 1 when revision 2 is current
    s = _state(web, sid)
    assert _review(web, DK, sid, "approved", revision=1, version=s["version"]).status_code == 409
    form = _state(web, sid)
    assert _review(web, DK, sid, "approved").status_code == 303
    # replaying the same form
    r = _review(
        web, DK, sid, "approved", revision=form["current_revision"], version=form["version"]
    )
    assert r.status_code == 409
    with web.rt.db.read() as conn:
        approvals = conn.execute(
            "SELECT count(*) FROM reviews WHERE submission_id = ? AND action = 'approved'", (sid,)
        ).fetchone()[0]
    assert approvals == 1


def test_reviewers_cannot_approve_their_own_suggestions(web: Web):
    web.sign_in("kofi")  # reviewer for Cyber Scallywags
    sid = _submit(web, CS)
    assert "another reviewer must decide" in web.get(f"/submissions/{sid}").text
    r = _review(web, CS, sid, "approved")
    assert r.status_code == 403
    assert _state(web, sid)["state"] == "submitted"


def test_volunteers_cannot_review(web: Web):
    web.sign_in("dev")
    assert _review(web, DK, AUDIT, "approved").status_code == 403


def test_rejected_submission_stays_private_with_feedback(web: Web):
    web.sign_in("bryn")
    page = web.get(f"/submissions/{PHISHING}").text
    assert "Rejected" in page and "Already covered by an existing programme" in page
    assert "Revise and resubmit" not in page
    s = _state(web, PHISHING)
    assert (
        web.post(f"/submissions/{PHISHING}/revise", {**DRAFT, "version": s["version"]}).status_code
        == 409
    )
    web.sign_in("asha")
    assert web.get(f"/submissions/{PHISHING}").status_code == 404
    assert "Phishing" not in web.get(f"/o/{CS}/projects").text
    assert not _in_graph(web, PHISHING)


def test_approve_the_seeded_suggestion_as_the_correct_reviewer(web: Web):
    web.sign_in("rhiannon")
    assert _review(web, DK, AUDIT, "approved").status_code == 303
    web.sign_in("asha")
    assert "Outreach Data Audit" in web.get(f"/o/{DK}/projects").text
    rows = web.rt.graph.read(
        cypher.MATCH_CANDIDATES,
        {"orgs": [DK], "project": AUDIT, "volunteer": "", "preferred_only": False, "limit": 50},
    )
    assert rows  # matchable once projected


def test_withdraw_removes_from_browse_and_matching(web: Web):
    web.sign_in("rhiannon")
    _review(web, DK, AUDIT, "approved")
    s = _state(web, AUDIT)
    web.sign_in("kofi")  # wrong organisation's reviewer
    assert (
        web.post(
            f"/o/{DK}/projects/{AUDIT}/withdraw", {"reason": "nope", "version": s["version"]}
        ).status_code
        == 403
    )
    web.sign_in("rhiannon")
    r = web.post(
        f"/o/{DK}/projects/{AUDIT}/withdraw", {"reason": "Project paused", "version": s["version"]}
    )
    assert r.status_code == 303
    assert _state(web, AUDIT)["state"] == "withdrawn"
    assert not _in_graph(web, AUDIT)
    web.sign_in("asha")
    assert "Outreach Data Audit" not in web.get(f"/o/{DK}/projects").text
    assert "Outreach Data Audit" not in web.get("/matches?scope=all").text
    assert "Project paused" in web.get(f"/submissions/{AUDIT}").text
