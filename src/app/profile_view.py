"""The profile screen's data, shared by the web page (/profile) and the mobile API
(GET/PUT /api/v1/profile) so both show the same structure and wording:

    display name
    Skills            one sub-section per skill category (type), each skill: SKILL_STATES
    Causes            each cause: CAUSE_STANCES
    Active projects   own suggestions in progress or published, plus published projects matched
    Past projects     own suggestions that were rejected or withdrawn

There is no "volunteer joined a project" record, so active/past are derived from suggestions and
current matches. The mobile app mirrors these labels in mobile/src/app/pages/profile.
"""

from app import matching, services
from app.catalogue import get_catalogue
from app.graph.store import GraphStore
from app.sqlstore import Database

SKILL_STATES = {
    "none": "Not set",
    "has": "Have (not offering)",
    "offers": "Have and offer",
    "exclude": "Exclude from matching",
}
CAUSE_STANCES = {"neutral": "No preference", "prefer": "Prefer", "exclude": "Exclude"}
PROJECT_STATES = {
    "submitted": "In review",
    "changes_requested": "Scoping: more detail requested",
    "rejected": "Rejected",
    "approved": "Approved (activating…)",
    "published": "Active",
    "withdrawn": "Withdrawn",
}
ACTIVE_STATES = ("submitted", "changes_requested", "approved", "published")
PAST_STATES = ("rejected", "withdrawn")


def overview(db: Database, graph: GraphStore, user_id: str) -> dict:
    cat = get_catalogue()
    profile = services.get_profile(db, user_id)
    suggestions = services.my_submissions(db, user_id)
    volunteering = [o["id"] for o in services.directory(db, user_id) if o["opted_in"]]
    matches = matching.volunteer_matches(db, graph, user_id, volunteering)

    def suggestion(s: dict) -> dict:
        return {
            "id": s["id"], "title": s["title"], "organisation": s["org_name"],
            "org_id": s["org_id"], "relation": "suggested", "state": s["state"], "detail": None,
        }  # fmt: skip

    active = [suggestion(s) for s in suggestions if s["state"] in ACTIVE_STATES]
    seen = {a["id"] for a in active}
    active += [
        {
            "id": m["project"],
            "title": m["title"],
            "organisation": m["org_name"],
            "org_id": m["org"],
            "relation": "matched",
            "state": "published",
            "detail": "You match: " + ", ".join(m["matched_names"]),
        }  # fmt: skip
        for m in matches.matches
        if m["project"] not in seen
    ]
    skills = [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "state": profile.skills.get(s.id, "none"),
        }  # fmt: skip
        for s in cat.skills
    ]
    by_category: dict[str, list[dict]] = {}  # catalogue order, grouped by category
    for skill in skills:
        by_category.setdefault(skill["category"], []).append(skill)
    groups = [{"category": c, "skills": members} for c, members in by_category.items()]
    return {
        "display_name": profile.display_name,
        "version": profile.version,
        "skills": skills,
        "skill_groups": groups,
        "causes": [
            {"id": c.id, "name": c.name, "stance": profile.causes.get(c.id, "neutral")}
            for c in cat.causes
        ],
        "projects": {
            "active": active,
            "past": [suggestion(s) for s in suggestions if s["state"] in PAST_STATES],
            "matches_withheld": matches.withheld.reason if matches.withheld else None,
        },
    }
