"""The JSON API: every action in the system is available here, under /api/v1.

The mobile companion and the Streamlit admin console use only this API. The server-rendered web
pages call the same service functions in-process, so the rules (roles, onboarding states,
versions) are enforced in one place, app.services / app.placements, whichever way in.

Bearer tokens only: the API never reads the web session cookie, so cross-site form posts cannot
ride on it and no CSRF token is needed. Tokens are opaque, revocable sessions (kind 'bearer').
Roles: system `admin` (all access), system `moderator` (vets volunteers), per-organisation
`org_admin` (its projects and volunteers, reviews) and `reviewer` (reviews suggestions), plus the
project lead for their own project. Errors are JSON: 401 signed out, 403 role missing, 404 unknown
or not visible, 409 stale version or wrong state, 422 invalid input.

Interactive docs: /api/docs (OpenAPI at /api/openapi.json).
"""

from contextlib import suppress
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import auth, matching, notify, placements, profile_view, services
from app.graph import cypher

router = APIRouter(prefix="/api/v1")
_bearer = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------ plumbing
def _rt(request: Request):
    return request.app.state.rt


def bearer_session(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> auth.Session:
    session = auth.load_session(
        _rt(request).db, creds.credentials if creds else None, kind="bearer"
    )
    if session is None:
        raise HTTPException(401, "Sign in first", headers={"WWW-Authenticate": "Bearer"})
    return session


Api = Annotated[auth.Session, Depends(bearer_session)]


def _drain(rt) -> None:
    with suppress(Exception):  # a failure stays queued; the background loop retries
        rt.projector.drain()


# ------------------------------------------------------------------ request bodies
class SignIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class SignUp(SignIn):
    display_name: str = Field(max_length=60)


class ActiveOrg(BaseModel):
    org_id: str = Field(max_length=64)


class ProfileIn(BaseModel):
    display_name: str = Field(max_length=60)
    version: int
    skills: dict[str, str] = Field(max_length=200)  # skill_id -> none|has|offers|exclude
    causes: dict[str, str] = Field(max_length=50)  # cause_id -> neutral|prefer|exclude


class DraftIn(BaseModel):
    title: str = Field(max_length=200)
    summary: str = Field(max_length=4000)
    cause_id: str = Field(max_length=64)
    required: list[str] = Field(max_length=50)
    optional: list[str] = Field(default=[], max_length=50)

    def draft(self) -> services.Draft:
        return services.Draft(self.title, self.summary, self.cause_id, self.required, self.optional)


class SubmissionIn(DraftIn):
    org_id: str = Field(max_length=64)


class RevisionIn(DraftIn):
    version: int


class DecisionIn(BaseModel):
    action: Literal["changes_requested", "rejected", "approved"]
    reason: str = Field(max_length=2000)
    private_note: str = Field(default="", max_length=4000)
    revision: int
    version: int


class ReasonIn(BaseModel):
    reason: str = Field(max_length=2000)


class WithdrawIn(ReasonIn):
    version: int


class LeadIn(BaseModel):
    user_id: str = Field(max_length=64)


class AnswerIn(BaseModel):
    answer: Literal["yes", "no"]
    version: int


class VetIn(BaseModel):
    action: Literal["approve", "request_info", "decline", "suspend", "reinstate"]
    note: str = Field(default="", max_length=2000)
    private_note: str = Field(default="", max_length=4000)
    version: int


class OrganisationIn(BaseModel):
    key: str = Field(max_length=64)
    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=1000)


# ------------------------------------------------------------------ auth
@router.post("/auth/signup", status_code=201, tags=["auth"])
def sign_up(body: SignUp, request: Request) -> dict:
    """Create an account (status `draft`) and sign in. Grants no role and joins nothing."""
    rt = _rt(request)
    try:
        user_id = auth.signup(
            rt.db, email=body.email, display_name=body.display_name, password=body.password
        )
    except auth.AuthError as error:
        raise HTTPException(422, str(error)) from error
    token = auth.create_session(
        rt.db, user_id, kind="bearer", ttl_hours=rt.settings.session_ttl_hours
    )
    return {"token": token, "user_id": user_id}


@router.post("/session", tags=["auth"])
def sign_in(body: SignIn, request: Request) -> dict:
    rt = _rt(request)
    user_id = auth.authenticate(rt.db, email=body.email, password=body.password)
    if user_id is None:
        raise HTTPException(401, "Wrong email or password")
    token = auth.create_session(
        rt.db, user_id, kind="bearer", ttl_hours=rt.settings.session_ttl_hours
    )
    return {"token": token}


@router.delete("/session", status_code=204, tags=["auth"])
def sign_out(request: Request, session: Api) -> None:
    auth.revoke_session(_rt(request).db, session.token_hash)


# ------------------------------------------------------------------ me
@router.get("/me", tags=["me"])
def me(request: Request, session: Api) -> dict:
    """Who I am: onboarding status, roles, organisations and unread notifications."""
    rt = _rt(request)
    actor = services.get_actor(rt.db, session.user_id)
    orgs = services.directory(rt.db, session.user_id)
    accessible = [o for o in orgs if o["opted_in"] or o["reviewer"]]
    active = (
        session.active_org_id if any(o["id"] == session.active_org_id for o in accessible) else None
    )
    return {
        "user": {"id": session.user_id, "display_name": session.display_name},
        "status": actor.status,
        "status_label": services.VOLUNTEER_STATUS_LABELS[actor.status],
        "system_roles": sorted(actor.system_roles),
        "organisations": [
            {k: o[k] for k in ("id", "name", "description")}
            | {
                "opted_in": bool(o["opted_in"]),
                "reviewer": bool(o["reviewer"]),
                "org_admin": bool(o["org_admin"]),
            }
            for o in orgs
        ],
        "active_org_id": active or (accessible[0]["id"] if accessible else None),
        "unread_notifications": notify.unread_count(rt.db, session.user_id),
    }


@router.put("/me/active-org", tags=["me"])
def set_active(body: ActiveOrg, request: Request, session: Api) -> dict:
    """Switch the organisation I'm looking at. Never grants access."""
    rt = _rt(request)
    services.require_access(rt.db, session.user_id, body.org_id)
    auth.set_active_org(rt.db, session.token_hash, body.org_id)
    return {"active_org_id": body.org_id}


def _profile(rt, user_id: str) -> dict:
    actor = services.get_actor(rt.db, user_id)
    return profile_view.overview(rt.db, rt.graph, user_id) | {
        "status": actor.status,
        "status_label": services.VOLUNTEER_STATUS_LABELS[actor.status],
        "can_submit_for_vetting": actor.status in ("draft", "info_requested", "declined"),
    }


@router.get("/profile", tags=["me"])
def get_profile(request: Request, session: Api) -> dict:
    """My skills, cause stances, onboarding status, and active and past projects."""
    return _profile(_rt(request), session.user_id)


@router.put("/profile", tags=["me"])
def put_profile(body: ProfileIn, request: Request, session: Api) -> dict:
    """Replace my profile; `version` guards against stale edits."""
    rt = _rt(request)
    services.update_profile(
        rt.db, session.user_id,
        display_name=body.display_name, skills=body.skills, causes=body.causes,
        expected_version=body.version,
    )  # fmt: skip
    _drain(rt)
    return _profile(rt, session.user_id)


@router.post("/me/vetting", tags=["me"])
def submit_for_vetting(request: Request, session: Api) -> dict:
    """Onboarding step 1: send my completed profile for screening by a moderator."""
    services.submit_for_vetting(_rt(request).db, session.user_id)
    return {"status": "screening"}


@router.get("/notifications", tags=["me"])
def notifications(request: Request, session: Api, unread: bool = False) -> list[dict]:
    return notify.list_for(_rt(request).db, session.user_id, unread_only=unread)


@router.post("/notifications/{notification_id}/read", tags=["me"])
def read_notification(notification_id: int, request: Request, session: Api) -> dict:
    if not notify.mark_read(_rt(request).db, session.user_id, notification_id):
        raise HTTPException(404, "Not found")
    return {"read": 1}


@router.post("/notifications/read-all", tags=["me"])
def read_all_notifications(request: Request, session: Api) -> dict:
    return {"read": notify.mark_read(_rt(request).db, session.user_id)}


# ------------------------------------------------------------------ organisations
@router.get("/orgs", tags=["organisations"])
def organisations(request: Request, session: Api) -> list[dict]:
    return [
        {k: o[k] for k in ("id", "key", "name", "description")}
        | {k: bool(o[k]) for k in ("opted_in", "reviewer", "org_admin")}
        for o in services.directory(_rt(request).db, session.user_id)
    ]


@router.put("/orgs/{org_id}/membership", tags=["organisations"])
def join(org_id: str, request: Request, session: Api) -> dict:
    """Opt in as a volunteer. Matching starts once my account is active (vetted)."""
    rt = _rt(request)
    services.set_membership(rt.db, session.user_id, org_id, opted_in=True)
    _drain(rt)
    return {"opted_in": True}


@router.delete("/orgs/{org_id}/membership", tags=["organisations"])
def leave(org_id: str, request: Request, session: Api) -> dict:
    rt = _rt(request)
    services.set_membership(rt.db, session.user_id, org_id, opted_in=False)
    _drain(rt)
    return {"opted_in": False}


@router.get("/orgs/{org_id}/matches", tags=["organisations"])
def org_matches(org_id: str, request: Request, session: Api, preferred_only: bool = False) -> dict:
    rt = _rt(request)
    result = matching.volunteer_matches(
        rt.db, rt.graph, session.user_id, [org_id], preferred_only=preferred_only
    )
    return {
        "withheld": result.withheld.reason if result.withheld else None,
        "matches": [
            {
                "project": m["project"],
                "title": m["title"],
                "organisation": m["org_name"],
                "cause": m["cause_name"],
                "preferred_cause": m["preferred"],
                "matched_skills": m["matched_names"],
                "optional_skills": m["optional_names"],
                "remaining_skills": m["remaining_names"],
            }
            for m in result.matches
        ],
        "excluded": [
            {"project": e["project"], "title": e["title"], "reasons": e["reasons"]}
            for e in result.excluded
        ],
    }


@router.get("/orgs/{org_id}/projects", tags=["organisations"])
def org_projects(org_id: str, request: Request, session: Api) -> list[dict]:
    """Active (published) projects of an organisation I belong to."""
    rt = _rt(request)
    return [
        {
            "project": r["project"],
            "title": r["title"],
            "summary": r["summary"],
            "cause": r["cause_name"],
            "required_skills": r["required_names"],
            "optional_skills": r["optional_names"],
        }
        for r in matching.browse(rt.db, rt.graph, session.user_id, org_id)
    ]


@router.get("/orgs/{org_id}/coverage", tags=["organisations"])
def org_coverage(org_id: str, request: Request, session: Api) -> dict:
    """Candidates per required skill and coverage gaps (reviewers and org admins)."""
    rt = _rt(request)
    cov = matching.project_coverage(rt.db, rt.graph, session.user_id, org_id)
    return {
        "withheld": cov.withheld.reason if cov.withheld else None,
        "projects": [
            {
                "project": p["project"],
                "title": p["title"],
                "gaps": p["gaps"],
                "per_skill": [
                    {
                        "skill": row["skill"],
                        "candidates": [
                            {
                                "volunteer": c["volunteer"],
                                "name": c["name"],
                                "preferred_cause": c["preferred"],
                            }  # fmt: skip
                            for c in row["candidates"]
                        ],
                    }
                    for row in p["per_skill"]
                ],
            }
            for p in cov.projects
        ],
    }


@router.get("/orgs/{org_id}/volunteers", tags=["organisations"])
def org_volunteers(org_id: str, request: Request, session: Api) -> list[dict]:
    """The organisation's opted-in volunteers (org admins). No contact details."""
    return services.org_volunteers(_rt(request).db, session.user_id, org_id)


@router.post("/orgs/{org_id}/volunteers/{user_id}/remove", tags=["organisations"])
def remove_volunteer(
    org_id: str, user_id: str, body: ReasonIn, request: Request, session: Api
) -> dict:
    """An org admin removes a volunteer from the organisation and its project teams."""
    rt = _rt(request)
    services.remove_volunteer(rt.db, session.user_id, org_id, user_id, reason=body.reason)
    _drain(rt)
    return {"removed": True}


# ------------------------------------------------------------------ project onboarding
@router.get("/submissions", tags=["projects"])
def my_submissions(request: Request, session: Api) -> list[dict]:
    return [
        {k: s[k] for k in ("id", "title", "state", "org_name", "current_revision", "updated_at")}
        for s in services.my_submissions(_rt(request).db, session.user_id)
    ]


@router.post("/submissions", status_code=201, tags=["projects"])
def create_submission(body: SubmissionIn, request: Request, session: Api) -> dict:
    """Project onboarding step 1: suggest a project (active accounts only)."""
    sid = services.create_submission(_rt(request).db, session.user_id, body.org_id, body.draft())
    return {"id": sid, "state": "submitted"}


@router.get("/submissions/{sid}", tags=["projects"])
def get_submission(sid: str, request: Request, session: Api) -> dict:
    """Visible to the author and to the organisation's reviewers and admins."""
    return services.get_submission(_rt(request).db, session.user_id, sid)


@router.post("/submissions/{sid}/revisions", tags=["projects"])
def revise_submission(sid: str, body: RevisionIn, request: Request, session: Api) -> dict:
    """Scoping: the author adds the requested detail and resubmits."""
    services.revise_submission(
        _rt(request).db, session.user_id, sid, body.draft(), expected_version=body.version
    )
    return {"state": "submitted"}


@router.get("/orgs/{org_id}/submissions", tags=["projects"])
def review_queue(org_id: str, request: Request, session: Api) -> list[dict]:
    return services.review_queue(_rt(request).db, session.user_id, org_id)


@router.post("/orgs/{org_id}/submissions/{sid}/decision", tags=["projects"])
def decide(org_id: str, sid: str, body: DecisionIn, request: Request, session: Api) -> dict:
    """Review: request scoping detail, reject, or approve (activates once in Relate)."""
    rt = _rt(request)
    services.review_submission(
        rt.db, session.user_id, org_id, sid,
        action=body.action, reason=body.reason, private_note=body.private_note,
        revision=body.revision, expected_version=body.version,
    )  # fmt: skip
    _drain(rt)
    return {"state": services.get_submission(rt.db, session.user_id, sid)["state"]}


@router.post("/orgs/{org_id}/projects/{sid}/withdraw", tags=["projects"])
def withdraw(org_id: str, sid: str, body: WithdrawIn, request: Request, session: Api) -> dict:
    rt = _rt(request)
    services.withdraw_project(
        rt.db, session.user_id, org_id, sid, reason=body.reason, expected_version=body.version
    )
    _drain(rt)
    return {"state": "withdrawn"}


@router.put("/orgs/{org_id}/projects/{sid}/lead", tags=["projects"])
def set_lead(org_id: str, sid: str, body: LeadIn, request: Request, session: Api) -> dict:
    services.set_project_lead(_rt(request).db, session.user_id, org_id, sid, body.user_id)
    return {"lead_id": body.user_id}


# ------------------------------------------------------------------ placements (volunteer step 2)
@router.get("/placements", tags=["placements"])
def my_placements(request: Request, session: Api) -> list[dict]:
    """My eligible offers, teams and history."""
    return placements.for_volunteer(_rt(request).db, session.user_id)


@router.post("/placements/{project_id}/answer", tags=["placements"])
def answer_as_volunteer(project_id: str, body: AnswerIn, request: Request, session: Api) -> dict:
    rt = _rt(request)
    state = placements.volunteer_respond(
        rt.db, session.user_id, project_id, body.answer, version=body.version
    )
    _drain(rt)
    return {"state": state}


@router.post("/placements/{project_id}/leave", tags=["placements"])
def leave_team(project_id: str, request: Request, session: Api) -> dict:
    rt = _rt(request)
    placements.end_team_membership(rt.db, session.user_id, project_id, session.user_id)
    _drain(rt)
    return {"state": "left"}


@router.get("/projects/{project_id}/placements", tags=["placements"])
def project_placements(project_id: str, request: Request, session: Api) -> list[dict]:
    """Candidates and team, for the project lead and org admins."""
    return placements.for_project(_rt(request).db, session.user_id, project_id)


@router.post("/projects/{project_id}/placements/{volunteer_id}/answer", tags=["placements"])
def answer_as_lead(
    project_id: str, volunteer_id: str, body: AnswerIn, request: Request, session: Api
) -> dict:
    rt = _rt(request)
    state = placements.lead_respond(
        rt.db, session.user_id, project_id, volunteer_id, body.answer, version=body.version
    )
    _drain(rt)
    return {"state": state}


@router.post("/projects/{project_id}/placements/{volunteer_id}/remove", tags=["placements"])
def remove_from_team(
    project_id: str, volunteer_id: str, body: ReasonIn, request: Request, session: Api
) -> dict:
    rt = _rt(request)
    placements.end_team_membership(
        rt.db, session.user_id, project_id, volunteer_id, reason=body.reason
    )
    _drain(rt)
    return {"state": "removed"}


# ------------------------------------------------------------------ moderation (vetting)
@router.get("/moderation/volunteers", tags=["moderation"])
def vetting_queue(request: Request, session: Api, status: str = "screening") -> list[dict]:
    """Volunteers in a given onboarding status (moderators and admins)."""
    return services.vetting_queue(_rt(request).db, session.user_id, status)


@router.get("/moderation/volunteers/{user_id}", tags=["moderation"])
def vetting_record(user_id: str, request: Request, session: Api) -> dict:
    return services.vetting_record(_rt(request).db, session.user_id, user_id)


@router.post("/moderation/volunteers/{user_id}/decision", tags=["moderation"])
def vet(user_id: str, body: VetIn, request: Request, session: Api) -> dict:
    """Approve, request more information, decline, suspend or reinstate a volunteer."""
    rt = _rt(request)
    status = services.vet_volunteer(
        rt.db, session.user_id, user_id, action=body.action, note=body.note,
        private_note=body.private_note, expected_version=body.version,
    )  # fmt: skip
    _drain(rt)
    return {"status": status}


# ------------------------------------------------------------------ system admin
@router.get("/admin/users", tags=["admin"])
def admin_users(request: Request, session: Api) -> list[dict]:
    return services.list_users(_rt(request).db, session.user_id)


@router.put("/admin/users/{user_id}/system-roles/{role}", tags=["admin"])
def grant_system_role(user_id: str, role: str, request: Request, session: Api) -> dict:
    services.set_system_role(_rt(request).db, session.user_id, user_id, role, granted=True)
    return {"granted": role}


@router.delete("/admin/users/{user_id}/system-roles/{role}", tags=["admin"])
def revoke_system_role(user_id: str, role: str, request: Request, session: Api) -> dict:
    services.set_system_role(_rt(request).db, session.user_id, user_id, role, granted=False)
    return {"revoked": role}


@router.put("/admin/orgs/{org_id}/roles/{user_id}/{role}", tags=["admin"])
def grant_org_role(org_id: str, user_id: str, role: str, request: Request, session: Api) -> dict:
    services.set_org_role(_rt(request).db, session.user_id, org_id, user_id, role, granted=True)
    return {"granted": role}


@router.delete("/admin/orgs/{org_id}/roles/{user_id}/{role}", tags=["admin"])
def revoke_org_role(org_id: str, user_id: str, role: str, request: Request, session: Api) -> dict:
    services.set_org_role(_rt(request).db, session.user_id, org_id, user_id, role, granted=False)
    return {"revoked": role}


@router.post("/admin/orgs", status_code=201, tags=["admin"])
def create_org(body: OrganisationIn, request: Request, session: Api) -> dict:
    rt = _rt(request)
    org_id = services.create_organisation(
        rt.db, session.user_id, key=body.key, name=body.name, description=body.description
    )
    _drain(rt)
    return {"id": org_id}


def _require_admin(rt, session: auth.Session) -> None:
    if not services.get_actor(rt.db, session.user_id).admin:
        raise services.Forbidden("admin")


@router.get("/admin/projection", tags=["admin"])
def projection_status(request: Request, session: Api) -> dict:
    rt = _rt(request)
    _require_admin(rt, session)
    return rt.projector.status().__dict__


@router.post("/admin/projection/replay", tags=["admin"])
def projection_replay(request: Request, session: Api) -> dict:
    rt = _rt(request)
    _require_admin(rt, session)
    result = rt.projector.drain(ignore_backoff=True)
    return {"done": result.done, "failed": result.failed}


@router.get("/admin/graph", tags=["admin"])
def graph_overview(request: Request, session: Api) -> dict:
    """Label and relationship counts plus the org → project → skill → volunteer inspection."""
    rt = _rt(request)
    _require_admin(rt, session)
    with rt.graph.reader() as tx:
        labels = tx.read(cypher.COUNT_LABELS, {"limit": 50})
        rels = tx.read(cypher.COUNT_RELATIONSHIPS, {"limit": 50})
        inspect = tx.read(cypher.INSPECT, {"limit": 200})
    with rt.db.read() as conn:
        names = dict(conn.execute("SELECT id, display_name FROM users").fetchall())
    for row in inspect:
        row["eligible_volunteers"] = [names.get(v, v) for v in row["eligible_volunteers"]]
    return {"labels": labels, "relationships": rels, "inspect": inspect}
