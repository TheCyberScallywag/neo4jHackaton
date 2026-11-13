"""The matching rules, one test per row of the rule table in the README. Decisions come from the
Relate query; these tests drive it through the same web forms and services the app uses."""

from app import matching, services
from app.catalogue import get_catalogue
from tests.conftest import CS, DK, FOOD_BANK, USERS, WINTER, Web


def save_profile(web: Web, key: str, *, skills: dict | None = None, causes: dict | None = None):
    """Post the full profile form for `key`, changing only the given entries."""
    web.sign_in(key)
    current = services.get_profile(web.rt.db, USERS[key])
    form = {"display_name": current.display_name, "version": current.version}
    for s in get_catalogue().skills:
        form[f"skill:{s.id}"] = (skills or {}).get(s.id, current.skills.get(s.id, "none"))
    for c in get_catalogue().causes:
        form[f"cause:{c.id}"] = (causes or {}).get(c.id, current.causes.get(c.id, "neutral"))
    r = web.post("/profile", form)
    assert r.status_code == 303, r.text


def candidates(web: Web, org: str, project: str) -> list[dict]:
    reviewer = USERS["rhiannon" if org == DK else "kofi"]
    cov = matching.project_coverage(web.rt.db, web.rt.graph, reviewer, org, project)
    assert cov.withheld is None
    return cov.projects[0]["candidates"] if cov.projects else []


def gaps(web: Web, org: str, project: str) -> list[str]:
    reviewer = USERS["rhiannon" if org == DK else "kofi"]
    return matching.project_coverage(web.rt.db, web.rt.graph, reviewer, org, project).projects[0][
        "gaps"
    ]


def mine(web: Web, key: str, orgs: list[str], **kw) -> matching.VolunteerMatches:
    return matching.volunteer_matches(web.rt.db, web.rt.graph, USERS[key], orgs, **kw)


def test_only_opted_in_volunteers_and_published_projects_are_considered(web: Web):
    # Farah offers first aid, which Winter Outreach needs, but volunteers with DataKind UK only
    assert "Farah" not in [c["name"] for c in candidates(web, CS, WINTER)]
    assert gaps(web, CS, WINTER) == ["First aid"]
    web.sign_in("farah")
    web.post(f"/orgs/{CS}/join")
    assert "Farah" in [c["name"] for c in candidates(web, CS, WINTER)]
    assert gaps(web, CS, WINTER) == []


def test_exclusion_wins_even_when_another_skill_matches(web: Web):
    # Gwen offers cooking and van driving (both required) but excludes active listening (also
    # required), so the pair is excluded outright.
    assert "Gwen" not in [c["name"] for c in candidates(web, CS, WINTER)]
    result = mine(web, "gwen", [CS])
    assert result.matches == []
    assert result.excluded[0]["reasons"] == ["it requires Active listening, which you excluded"]


def test_excluded_cause_removes_the_pair(web: Web):
    save_profile(web, "asha", causes={"food-security": "exclude"})
    assert "Asha" not in [c["name"] for c in candidates(web, DK, FOOD_BANK)]
    excluded = mine(web, "asha", [DK]).excluded
    assert excluded[0]["reasons"] == ["you excluded the cause Food Security"]


def test_one_candidate_entry_per_eligible_skill(web: Web):
    reviewer = USERS["rhiannon"]
    project = matching.project_coverage(web.rt.db, web.rt.graph, reviewer, DK, FOOD_BANK).projects[
        0
    ]
    per_skill = {
        row["skill"]: [c["name"] for c in row["candidates"]] for row in project["per_skill"]
    }
    assert per_skill == {
        "Bookkeeping": ["Dev", "Cerys"],
        "Spreadsheet and data analysis": ["Dev", "Asha", "Kofi"],
    }


def test_preferred_cause_ranks_ahead_then_coverage_then_optional_then_ids(web: Web):
    ranked = [(c["name"], c["preferred"], c["coverage"]) for c in candidates(web, DK, FOOD_BANK)]
    # Dev and Asha prefer Food Security; Dev covers two required skills. Cerys and Kofi have no
    # preference and cover one each; Cerys also offers the optional Welsh translation.
    assert ranked == [("Dev", True, 2), ("Asha", True, 1), ("Cerys", False, 1), ("Kofi", False, 1)]
    # On the volunteer side: Asha's preferred, two-skill project first
    assert [m["project"] for m in mine(web, "asha", [DK, CS]).matches] == [WINTER, FOOD_BANK]


def test_no_cause_preference_still_gets_skills_based_matches(web: Web):
    assert services.get_profile(web.rt.db, USERS["cerys"]).causes == {}
    result = mine(web, "cerys", [DK])
    assert [(m["title"], m["matched_names"], m["preferred"]) for m in result.matches] == [
        ("Food Bank Demand Dashboard", ["Bookkeeping"], False)
    ]


def test_preferred_causes_only_filter(web: Web):
    assert mine(web, "cerys", [DK], preferred_only=True).matches == []
    save_profile(web, "cerys", causes={"food-security": "prefer"})
    assert len(mine(web, "cerys", [DK], preferred_only=True).matches) == 1
    page = web.get("/matches?scope=all&preferred_only=1").text
    assert "Food Bank Demand Dashboard" in page


def test_excluding_an_optional_skill_omits_it_from_explanation_and_score(web: Web):
    before = mine(web, "cerys", [DK]).matches[0]
    assert before["optional_names"] == ["Welsh translation"]
    save_profile(web, "cerys", skills={"welsh-translation": "exclude"})
    after = mine(web, "cerys", [DK]).matches[0]  # still a match: optional skills never exclude
    assert after["optional_names"] == []
    assert after["optional_excluded_names"] == ["Welsh translation"]
    ranked = [c["name"] for c in candidates(web, DK, FOOD_BANK)]
    assert ranked.index("Cerys") > ranked.index("Asha")


def test_have_but_not_offer_is_not_eligible(web: Web):
    # Farah has active listening but does not offer it
    save_profile(web, "farah", skills={"cooking": "has"})
    web.post(f"/orgs/{CS}/join")
    assert "Farah" in [c["name"] for c in candidates(web, CS, WINTER)]  # via first aid only
    entry = next(c for c in candidates(web, CS, WINTER) if c["name"] == "Farah")
    assert entry["matched_names"] == ["First aid"]


def test_profile_edit_propagates_to_relate_and_shows_a_gap(web: Web):
    """The demo moment: Asha excludes active listening and drops out of Winter Outreach. Nobody
    else offers active listening there, and Gwen (who cooks) is excluded too, so both become
    gaps."""
    assert "Asha" in [c["name"] for c in candidates(web, CS, WINTER)]
    save_profile(web, "asha", skills={"active-listening": "exclude"})
    assert "Asha" not in [c["name"] for c in candidates(web, CS, WINTER)]
    assert gaps(web, CS, WINTER) == ["Active listening", "Community cooking", "First aid"]
    page = web.get("/matches?scope=all").text
    assert "Hidden by your exclusions" in page and "Active listening, which you excluded" in page
    save_profile(web, "asha", skills={"active-listening": "offers"})
    assert gaps(web, CS, WINTER) == ["First aid"]


def test_reviewer_sees_gaps_on_the_project_page(web: Web):
    web.sign_in("kofi")
    page = web.get(f"/o/{CS}/projects/{WINTER}").text
    assert "Coverage gap" in page and "Asha" in page and "Bryn" in page and "Gwen" not in page
