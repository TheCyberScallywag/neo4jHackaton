"""Web app: FastAPI + Jinja2, server-rendered pages and plain forms (no client JavaScript needed).

Every protected route resolves the user from the opaque session cookie and checks access against
the organisation named in the URL or form, so switching the active organisation is only a
convenience and never a grant. Every POST carries a CSRF token.
"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from app import auth, matching, notify, placements, profile_view, services
from app.catalogue import get_catalogue
from app.config import BRAND_DIR, Settings, get_settings
from app.graph.store import GraphBusy, GraphError
from app.meta import get_meta
from app.runtime import Runtime, bootstrap, build_runtime
from app.web import api

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
SESSION_COOKIE = "dvn_session"
ANON_CSRF_COOKIE = "dvn_csrf"

MESSAGES = {
    "welcome": "Welcome! Join an organisation to start volunteering.",
    "signed_in": "Signed in.",
    "signed_out": "Signed out.",
    "joined": "You now volunteer with this organisation.",
    "left": "You have opted out. This organisation no longer sees you as a candidate.",
    "switched": "Active organisation switched.",
    "profile_saved": "Profile saved. Matches update once the graph has caught up.",
    "submitted": "Suggestion submitted for review.",
    "revised": "Revision submitted for review.",
    "reviewed": "Decision recorded.",
    "withdrawn": "Project withdrawn from browse and matching.",
    "vetting": "Thanks! Your profile is in screening. We'll notify you here when it's reviewed.",
    "answered": "Answer recorded.",
    "left_team": "You left the team.",
    "removed": "Volunteer removed.",
    "read": "Notifications marked as read.",
}


class LoginRequired(Exception):
    pass


class CsrfFailed(Exception):
    pass


# ------------------------------------------------------------------ app + lifecycle
def _projector_loop(rt: Runtime, stop: threading.Event) -> None:
    while not stop.wait(rt.settings.projector_interval_seconds):
        try:
            rt.projector.drain()
        except Exception:  # keep the loop alive; errors are recorded per item
            log.exception("background projection failed")


def create_app(settings: Settings | None = None, *, background: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = build_runtime(settings)
        bootstrap(rt)
        app.state.rt = rt
        stop = threading.Event()
        worker = None
        if background:
            worker = threading.Thread(target=_projector_loop, args=(rt, stop), daemon=True)
            worker.start()
        yield
        stop.set()
        if worker:
            worker.join(timeout=5)

    app = FastAPI(
        title="Data Volunteer Network API",
        description=(api.__doc__ or "").strip(),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.mount("/brand", StaticFiles(directory=BRAND_DIR / "images"), name="brand")
    app.include_router(api.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/docs":  # Swagger UI: its CDN assets and inline bootstrap
            policy = (
                "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
                "style-src 'self' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "frame-ancestors 'none'; base-uri 'none'"
            )
        else:
            policy = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; form-action 'self'; "
                "frame-ancestors 'none'; base-uri 'none'"
            )
        response.headers.setdefault("Content-Security-Policy", policy)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path not in ("/healthz",) and not request.url.path.startswith("/static"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    _register_errors(app)
    _register_routes(app)
    for route in app.routes:  # the OpenAPI docs describe the JSON API only
        if getattr(route, "path", "").startswith("/") and not route.path.startswith("/api/"):
            route.include_in_schema = False
    return app


templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["catalogue"] = get_catalogue
templates.env.globals["SKILL_STATES"] = profile_view.SKILL_STATES
templates.env.globals["CAUSE_STANCES"] = profile_view.CAUSE_STANCES
templates.env.globals["PROJECT_STATES"] = profile_view.PROJECT_STATES
templates.env.filters["skill_name"] = lambda s: get_catalogue().skill_name(s)
templates.env.filters["state_label"] = lambda s: (
    profile_view.PROJECT_STATES | placements.STATE_LABELS
).get(s, s)


# ------------------------------------------------------------------ request helpers
def get_rt(request: Request) -> Runtime:
    return request.app.state.rt


def optional_session(request: Request) -> auth.Session | None:
    return auth.load_session(get_rt(request).db, request.cookies.get(SESSION_COOKIE), kind="cookie")


def require_session(request: Request) -> auth.Session:
    session = optional_session(request)
    if session is None:
        raise LoginRequired
    return session


RT = Annotated[Runtime, Depends(get_rt)]
MaybeUser = Annotated[auth.Session | None, Depends(optional_session)]
User = Annotated[auth.Session, Depends(require_session)]


def _secure(request: Request) -> bool:
    settings = get_rt(request).settings
    if settings.cookie_secure is not None:
        return settings.cookie_secure
    if settings.trust_proxy_headers:
        return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    return request.url.scheme == "https"


def _anon_csrf(request: Request) -> str:
    token = request.cookies.get(ANON_CSRF_COOKIE)
    if not token or len(token) > 128:
        import secrets

        token = secrets.token_urlsafe(32)
        request.state.new_anon_csrf = token
    return token


async def checked_form(request: Request, session: auth.Session | None) -> FormData:
    form = await request.form()
    expected = session.csrf_token if session else request.cookies.get(ANON_CSRF_COOKIE)
    if not auth.csrf_matches(expected, str(form.get("csrf_token", ""))):
        raise CsrfFailed
    return form


def _safe_next(target: str | None, default: str) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def redirect(path: str, msg: str | None = None) -> RedirectResponse:
    if msg:
        path += ("&" if "?" in path else "?") + urlencode({"msg": msg})
    return RedirectResponse(path, status_code=303)


def active_org(rt: Runtime, session: auth.Session, orgs: list[dict]) -> dict | None:
    ids = {o["id"]: o for o in orgs}
    if session.active_org_id in ids:
        return ids[session.active_org_id]
    return orgs[0] if orgs else None


def render(
    request: Request,
    name: str,
    session: auth.Session | None = None,
    status_code: int = 200,
    **ctx,
) -> HTMLResponse:
    rt = get_rt(request)
    orgs = services.accessible_orgs(rt.db, session.user_id) if session else []
    current = active_org(rt, session, orgs) if session else None
    pending = unread = actor = None
    if session:
        with rt.db.read() as conn:
            pending = conn.execute(
                "SELECT count(*) FROM outbox WHERE done_at IS NULL AND kind = 'volunteer' "
                "AND entity_id = ?",
                (session.user_id,),
            ).fetchone()[0]
            actor = services.load_actor(conn, session.user_id)
        unread = notify.unread_count(rt.db, session.user_id)
    response = templates.TemplateResponse(
        request,
        name,
        {
            "session": session,
            "orgs": orgs,
            "active": current,
            "pending_mine": pending,
            "actor": actor,
            "unread": unread,
            "status_labels": services.VOLUNTEER_STATUS_LABELS,
            "csrf": session.csrf_token if session else _anon_csrf(request),
            "message": MESSAGES.get(request.query_params.get("msg", "")),
            "meta": get_meta(),
            "path": request.url.path,
            **ctx,
        },
        status_code=status_code,
    )
    if token := getattr(request.state, "new_anon_csrf", None):
        response.set_cookie(
            ANON_CSRF_COOKIE, token, httponly=True, samesite="strict", secure=_secure(request)
        )
    return response


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_rt(request).settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path="/",
    )


def _drain(rt: Runtime) -> None:
    """Project right away so the redirect usually shows fresh results; failures stay queued."""
    try:
        rt.projector.drain()
    except Exception:
        log.exception("inline projection failed; the background loop will retry")


# ------------------------------------------------------------------ errors
def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _json_error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


FORBIDDEN_DETAIL = {
    "reviewer": "Only this organisation's reviewers and admins can do that.",
    "org_admin": "Only this organisation's admins can do that.",
    "volunteer": "Join this organisation as a volunteer first.",
    "any": "You are not a member of this organisation.",
    "active": "Your account must be vetted and active first.",
    "moderator": "Only moderators and system admins can do that.",
    "admin": "Only system admins can do that.",
}


def _register_errors(app: FastAPI) -> None:  # noqa: C901 - one small handler per error
    @app.exception_handler(services.Invalid)
    async def _invalid(request: Request, exc: services.Invalid):
        if _is_api(request):
            return _json_error(422, str(exc))
        return render(
            request, "error.html", optional_session(request), 400,
            title="Check your input", detail=str(exc),
        )  # fmt: skip

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, _exc: LoginRequired):
        return redirect("/signin?" + urlencode({"next": request.url.path}))

    @app.exception_handler(CsrfFailed)
    async def _csrf(request: Request, _exc: CsrfFailed):
        return render(
            request, "error.html", optional_session(request), 403,
            title="Form expired", detail="Reload the page and submit the form again.",
        )  # fmt: skip

    @app.exception_handler(services.NotFound)
    async def _missing(request: Request, _exc: services.NotFound):
        if _is_api(request):
            return _json_error(404, "Not found")
        return render(
            request, "error.html", optional_session(request), 404,
            title="Not found", detail="That page does not exist or is not visible to you.",
        )  # fmt: skip

    @app.exception_handler(services.Forbidden)
    async def _forbidden(request: Request, exc: services.Forbidden):
        detail = FORBIDDEN_DETAIL.get(str(exc), str(exc))
        if _is_api(request):
            return _json_error(403, detail)
        return render(
            request, "error.html", optional_session(request), 403,
            title="Not allowed", detail=detail,
        )  # fmt: skip

    @app.exception_handler(services.Conflict)
    async def _conflict(request: Request, exc: services.Conflict):
        if _is_api(request):
            return _json_error(409, str(exc))
        return render(
            request, "error.html", optional_session(request), 409,
            title="Already changed", detail=str(exc),
        )  # fmt: skip

    @app.exception_handler(GraphError)
    async def _graph(request: Request, exc: GraphError):
        log.warning("graph error: %s", exc)
        detail = (
            "The matching graph is busy (another process holds the store). Try again shortly."
            if isinstance(exc, GraphBusy)
            else "The matching graph could not answer. Try again shortly."
        )
        if _is_api(request):
            return _json_error(503, detail)
        return render(
            request, "error.html", optional_session(request), 503,
            title="Matching unavailable", detail=detail,
        )  # fmt: skip


# ------------------------------------------------------------------ routes
def _register_routes(app: FastAPI) -> None:  # noqa: C901 - a flat list of small handlers
    @app.get("/healthz")
    async def healthz(rt: RT) -> dict:
        status = rt.projector.status(limit=0)
        return {"status": "ok", "pending_projections": status.pending, "failed": status.failed}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, session: MaybeUser):
        if session:
            return redirect("/home")
        return render(request, "index.html")

    # ---------------------------------------------------------------- identity
    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form(request: Request):
        return render(request, "signup.html")

    @app.post("/signup", response_class=HTMLResponse)
    async def signup(request: Request, rt: RT):
        form = await checked_form(request, None)
        # Only these three fields are read: a posted "role" or "org" is ignored by design.
        email, name = str(form.get("email", "")), str(form.get("display_name", ""))
        try:
            user_id = auth.signup(
                rt.db, email=email, display_name=name, password=str(form.get("password", ""))
            )
        except auth.AuthError as error:
            return render(request, "signup.html", error=str(error), email=email, display_name=name,
                          status_code=400)  # fmt: skip
        response = redirect("/orgs", "welcome")
        token = auth.create_session(
            rt.db, user_id, kind="cookie", ttl_hours=rt.settings.session_ttl_hours
        )
        _set_session_cookie(request, response, token)
        return response

    @app.get("/signin", response_class=HTMLResponse)
    async def signin_form(request: Request):
        return render(request, "signin.html", next=request.query_params.get("next", ""))

    @app.post("/signin", response_class=HTMLResponse)
    async def signin(request: Request, rt: RT):
        form = await checked_form(request, None)
        email = str(form.get("email", ""))
        user_id = auth.authenticate(rt.db, email=email, password=str(form.get("password", "")))
        nxt = str(form.get("next", ""))
        if user_id is None:
            return render(request, "signin.html", error="Wrong email or password.", email=email,
                          next=nxt, status_code=400)  # fmt: skip
        if old := request.cookies.get(SESSION_COOKIE):
            auth.revoke_session(rt.db, auth.token_hash(old))  # no session fixation
        response = redirect(_safe_next(nxt, "/home"), "signed_in")
        token = auth.create_session(
            rt.db, user_id, kind="cookie", ttl_hours=rt.settings.session_ttl_hours
        )
        _set_session_cookie(request, response, token)
        return response

    @app.post("/signout")
    async def signout(request: Request, rt: RT, session: User):
        await checked_form(request, session)
        auth.revoke_session(rt.db, session.token_hash)
        response = redirect("/", "signed_out")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ---------------------------------------------------------------- home + organisations
    @app.get("/home", response_class=HTMLResponse)
    async def home(request: Request, rt: RT, session: User):
        orgs = services.accessible_orgs(rt.db, session.user_id)
        volunteering = [o["id"] for o in orgs if o["opted_in"]]
        matches = matching.volunteer_matches(rt.db, rt.graph, session.user_id, volunteering)
        queues = {
            o["id"]: sum(
                1
                for s in services.review_queue(rt.db, session.user_id, o["id"])
                if s["state"] == "submitted"
            )
            for o in orgs
            if o["reviewer"]
        }
        return render(
            request, "home.html", session,
            matches=matches, queues=queues,
            submissions=services.my_submissions(rt.db, session.user_id)[:5],
        )  # fmt: skip

    @app.get("/orgs", response_class=HTMLResponse)
    async def orgs_page(request: Request, rt: RT, session: User):
        return render(
            request, "orgs.html", session, directory=services.directory(rt.db, session.user_id)
        )

    @app.post("/orgs/{org_id}/join")
    async def join(org_id: str, request: Request, rt: RT, session: User):
        await checked_form(request, session)
        services.set_membership(rt.db, session.user_id, org_id, opted_in=True)
        if session.active_org_id is None:
            auth.set_active_org(rt.db, session.token_hash, org_id)
        _drain(rt)
        return redirect("/orgs", "joined")

    @app.post("/orgs/{org_id}/leave")
    async def leave(org_id: str, request: Request, rt: RT, session: User):
        await checked_form(request, session)
        services.set_membership(rt.db, session.user_id, org_id, opted_in=False)
        _drain(rt)
        return redirect("/orgs", "left")

    @app.post("/orgs/switch")
    async def switch(request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        org_id = str(form.get("org_id", ""))
        services.require_access(rt.db, session.user_id, org_id)  # switching never grants
        auth.set_active_org(rt.db, session.token_hash, org_id)
        return redirect(_safe_next(str(form.get("next", "")), f"/o/{org_id}/projects"), "switched")

    # ---------------------------------------------------------------- profile
    @app.get("/profile", response_class=HTMLResponse)
    async def profile_page(request: Request, rt: RT, session: User):
        return render(
            request, "profile.html", session,
            view=profile_view.overview(rt.db, rt.graph, session.user_id),
        )  # fmt: skip

    @app.post("/profile", response_class=HTMLResponse)
    async def profile_save(request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        cat = get_catalogue()
        skills = {s.id: str(form.get(f"skill:{s.id}", "none")) for s in cat.skills}
        causes = {c.id: str(form.get(f"cause:{c.id}", "neutral")) for c in cat.causes}
        try:
            services.update_profile(
                rt.db, session.user_id,
                display_name=str(form.get("display_name", "")),
                skills=skills, causes=causes,
                expected_version=_int(form.get("version")),
            )  # fmt: skip
        except services.Invalid as error:
            view = profile_view.overview(rt.db, rt.graph, session.user_id)
            return render(request, "profile.html", session, view=view, error=str(error),
                          status_code=400)  # fmt: skip
        _drain(rt)
        return redirect("/profile", "profile_saved")

    # ---------------------------------------------------------------- projects + matches
    @app.get("/o/{org_id}/projects", response_class=HTMLResponse)
    async def projects(org_id: str, request: Request, rt: RT, session: User):
        with rt.db.read() as conn:
            org = dict(services.get_org(conn, org_id))
        rows = matching.browse(rt.db, rt.graph, session.user_id, org_id)
        return render(request, "projects.html", session, org=org, projects=rows)

    @app.get("/o/{org_id}/projects/{project_id}", response_class=HTMLResponse)
    async def project_detail(org_id: str, project_id: str, request: Request, rt: RT, session: User):
        access = services.require_access(rt.db, session.user_id, org_id)
        with rt.db.read() as conn:
            org = dict(services.get_org(conn, org_id))
        project = next(
            (p for p in matching.browse(rt.db, rt.graph, session.user_id, org_id)
             if p["project"] == project_id),
            None,
        )  # fmt: skip
        if project is None:
            raise services.NotFound("project")
        coverage = mine = team = None
        with rt.db.read() as conn:
            lead_id = conn.execute(
                "SELECT lead_id FROM submissions WHERE id = ?", (project_id,)
            ).fetchone()[0]
        if access.org_admin or lead_id == session.user_id:
            team = placements.for_project(rt.db, session.user_id, project_id)
        if access.reviewer:
            coverage = matching.project_coverage(
                rt.db, rt.graph, session.user_id, org_id, project_id
            )
        if access.volunteer:
            mine = matching.volunteer_matches(rt.db, rt.graph, session.user_id, [org_id])
        return render(
            request, "project.html", session, org=org, project=project, access=access,
            coverage=coverage, mine=mine, team=team, is_lead=(lead_id == session.user_id),
        )  # fmt: skip

    @app.get("/matches", response_class=HTMLResponse)
    async def matches(request: Request, rt: RT, session: User):
        orgs = services.accessible_orgs(rt.db, session.user_id)
        volunteering = [o for o in orgs if o["opted_in"]]
        scope = request.query_params.get("scope") or (
            (active_org(rt, session, volunteering) or {}).get("id", "all")
        )
        preferred_only = request.query_params.get("preferred_only") == "1"
        org_ids = [o["id"] for o in volunteering] if scope == "all" else [scope]
        result = matching.volunteer_matches(
            rt.db, rt.graph, session.user_id, org_ids, preferred_only=preferred_only
        )
        return render(
            request, "matches.html", session, result=result, scope=scope,
            volunteering=volunteering, preferred_only=preferred_only,
        )  # fmt: skip

    @app.get("/o/{org_id}/coverage", response_class=HTMLResponse)
    async def coverage(org_id: str, request: Request, rt: RT, session: User):
        with rt.db.read() as conn:
            org = dict(services.get_org(conn, org_id))
        result = matching.project_coverage(rt.db, rt.graph, session.user_id, org_id)
        return render(request, "coverage.html", session, org=org, coverage=result)

    # ---------------------------------------------------------------- suggestions
    @app.get("/submissions", response_class=HTMLResponse)
    async def my_submissions(request: Request, rt: RT, session: User):
        return render(
            request, "submissions.html", session,
            submissions=services.my_submissions(rt.db, session.user_id),
        )  # fmt: skip

    @app.get("/submissions/new", response_class=HTMLResponse)
    async def new_submission(request: Request, session: User):
        return render(request, "submission_form.html", session, draft=None,
                      target=request.query_params.get("org", ""))  # fmt: skip

    @app.post("/submissions/new", response_class=HTMLResponse)
    async def create_submission(request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        draft = _draft(form)
        target = str(form.get("org_id", ""))
        try:
            sid = services.create_submission(rt.db, session.user_id, target, draft)
        except services.Invalid as error:
            return render(request, "submission_form.html", session, draft=draft, target=target,
                          error=str(error), status_code=400)  # fmt: skip
        return redirect(f"/submissions/{sid}", "submitted")

    @app.get("/submissions/{sid}", response_class=HTMLResponse)
    async def submission(sid: str, request: Request, rt: RT, session: User):
        return render(
            request,
            "submission.html",
            session,
            sub=services.get_submission(rt.db, session.user_id, sid),
        )

    @app.post("/submissions/{sid}/revise", response_class=HTMLResponse)
    async def revise(sid: str, request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        draft = _draft(form)
        try:
            services.revise_submission(
                rt.db, session.user_id, sid, draft, expected_version=_int(form.get("version"))
            )
        except services.Invalid as error:
            sub = services.get_submission(rt.db, session.user_id, sid)
            return render(request, "submission.html", session, sub=sub, error=str(error),
                          status_code=400)  # fmt: skip
        return redirect(f"/submissions/{sid}", "revised")

    # ---------------------------------------------------------------- review
    @app.get("/o/{org_id}/review", response_class=HTMLResponse)
    async def review_queue(org_id: str, request: Request, rt: RT, session: User):
        queue = services.review_queue(rt.db, session.user_id, org_id)
        with rt.db.read() as conn:
            org = dict(services.get_org(conn, org_id))
        return render(request, "review_queue.html", session, org=org, queue=queue)

    @app.post("/o/{org_id}/review/{sid}", response_class=HTMLResponse)
    async def review(org_id: str, sid: str, request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        try:
            services.review_submission(
                rt.db, session.user_id, org_id, sid,
                action=str(form.get("action", "")),
                reason=str(form.get("reason", "")),
                private_note=str(form.get("private_note", "")),
                revision=_int(form.get("revision")),
                expected_version=_int(form.get("version")),
            )  # fmt: skip
        except services.Invalid as error:
            sub = services.get_submission(rt.db, session.user_id, sid)
            return render(request, "submission.html", session, sub=sub, error=str(error),
                          status_code=400)  # fmt: skip
        _drain(rt)
        return redirect(f"/submissions/{sid}", "reviewed")

    @app.post("/o/{org_id}/projects/{sid}/withdraw", response_class=HTMLResponse)
    async def withdraw(org_id: str, sid: str, request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        try:
            services.withdraw_project(
                rt.db, session.user_id, org_id, sid,
                reason=str(form.get("reason", "")), expected_version=_int(form.get("version")),
            )  # fmt: skip
        except services.Invalid as error:
            sub = services.get_submission(rt.db, session.user_id, sid)
            return render(request, "submission.html", session, sub=sub, error=str(error),
                          status_code=400)  # fmt: skip
        _drain(rt)
        return redirect(f"/submissions/{sid}", "withdrawn")

    # ---------------------------------------------------------------- onboarding + notifications
    @app.post("/me/vetting")
    async def submit_vetting(request: Request, rt: RT, session: User):
        await checked_form(request, session)
        services.submit_for_vetting(rt.db, session.user_id)
        return redirect("/profile", "vetting")

    @app.get("/notifications", response_class=HTMLResponse)
    async def notifications_page(request: Request, rt: RT, session: User):
        items = notify.list_for(rt.db, session.user_id)
        return render(request, "notifications.html", session, items=items)

    @app.post("/notifications/read-all")
    async def notifications_read(request: Request, rt: RT, session: User):
        await checked_form(request, session)
        notify.mark_read(rt.db, session.user_id)
        return redirect("/notifications", "read")

    # ---------------------------------------------------------------- placements
    @app.get("/opportunities", response_class=HTMLResponse)
    async def opportunities(request: Request, rt: RT, session: User):
        return render(
            request, "opportunities.html", session,
            items=placements.for_volunteer(rt.db, session.user_id),
        )  # fmt: skip

    @app.post("/opportunities/{project_id}/answer")
    async def answer(project_id: str, request: Request, rt: RT, session: User):
        form = await checked_form(request, session)
        placements.volunteer_respond(
            rt.db, session.user_id, project_id, str(form.get("answer", "")),
            version=_int(form.get("version")),
        )  # fmt: skip
        _drain(rt)
        return redirect("/opportunities", "answered")

    @app.post("/opportunities/{project_id}/leave")
    async def leave_team(project_id: str, request: Request, rt: RT, session: User):
        await checked_form(request, session)
        placements.end_team_membership(rt.db, session.user_id, project_id, session.user_id)
        _drain(rt)
        return redirect("/opportunities", "left_team")

    @app.post("/o/{org_id}/projects/{project_id}/candidates/{volunteer_id}/answer")
    async def lead_answer(
        org_id: str, project_id: str, volunteer_id: str, request: Request, rt: RT, session: User
    ):
        form = await checked_form(request, session)
        placements.lead_respond(
            rt.db, session.user_id, project_id, volunteer_id, str(form.get("answer", "")),
            version=_int(form.get("version")),
        )  # fmt: skip
        _drain(rt)
        return redirect(f"/o/{org_id}/projects/{project_id}", "answered")

    @app.post("/o/{org_id}/projects/{project_id}/team/{volunteer_id}/remove")
    async def lead_remove(
        org_id: str, project_id: str, volunteer_id: str, request: Request, rt: RT, session: User
    ):
        form = await checked_form(request, session)
        placements.end_team_membership(
            rt.db, session.user_id, project_id, volunteer_id, reason=str(form.get("reason", ""))
        )
        _drain(rt)
        return redirect(f"/o/{org_id}/projects/{project_id}", "removed")

    # ---------------------------------------------------------------- org admin: volunteers
    @app.get("/o/{org_id}/volunteers", response_class=HTMLResponse)
    async def org_volunteers(org_id: str, request: Request, rt: RT, session: User):
        people = services.org_volunteers(rt.db, session.user_id, org_id)
        with rt.db.read() as conn:
            org = dict(services.get_org(conn, org_id))
        return render(request, "org_volunteers.html", session, org=org, people=people)

    @app.post("/o/{org_id}/volunteers/{user_id}/remove")
    async def org_remove_volunteer(org_id: str, user_id: str, request: Request, rt: RT,
                                   session: User):  # fmt: skip
        form = await checked_form(request, session)
        services.remove_volunteer(
            rt.db, session.user_id, org_id, user_id, reason=str(form.get("reason", ""))
        )
        _drain(rt)
        return redirect(f"/o/{org_id}/volunteers", "removed")


def _int(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return -1  # fails the optimistic-concurrency check rather than raising


def _draft(form: FormData) -> services.Draft:
    return services.Draft(
        title=str(form.get("title", "")),
        summary=str(form.get("summary", "")),
        cause_id=str(form.get("cause_id", "")),
        required=[str(v) for v in form.getlist("required")],
        optional=[str(v) for v in form.getlist("optional")],
    )
