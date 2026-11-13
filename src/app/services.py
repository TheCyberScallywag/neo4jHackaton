"""Domain operations. Every function takes the acting user's ID (from the server-side session,
never from the client) and checks access against the *target* organisation itself, so forged
IDs in URLs or forms fail closed. State-changing operations enqueue a projection item in the
same SQLite transaction (app.sqlstore.enqueue).
"""

import re
import sqlite3
from dataclasses import dataclass, field

from app.catalogue import get_catalogue
from app.notify import notify, notify_org_admins, notify_vetters
from app.sqlstore import Database, enqueue, new_id, now

SKILL_STATES = ("none", "has", "offers", "exclude")
CAUSE_STANCES = ("neutral", "prefer", "exclude")
TITLE_MAX, SUMMARY_MAX, REASON_MAX, NOTE_MAX = 120, 2000, 1000, 2000


class NotFound(LookupError):
    """Unknown, or not visible to this user (deliberately indistinguishable)."""


class Forbidden(PermissionError):
    """Known to this user, but the action needs a role or membership they lack."""


class Conflict(RuntimeError):
    """Stale or repeated form: the record changed since the form was rendered."""


class Invalid(ValueError):
    """Input that fails validation; the message is safe to show."""


@dataclass(frozen=True)
class Actor:
    """Who is acting: account status plus system-wide roles. Loaded from SQLite per request."""

    user_id: str
    status: str
    system_roles: frozenset[str]

    @property
    def admin(self) -> bool:
        return "admin" in self.system_roles

    @property
    def can_vet(self) -> bool:
        return bool(self.system_roles & {"admin", "moderator"})

    @property
    def active(self) -> bool:
        return self.status == "active"


def load_actor(conn: sqlite3.Connection, user_id: str) -> Actor:
    row = conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise NotFound("user")
    roles = frozenset(
        r[0] for r in conn.execute("SELECT role FROM system_roles WHERE user_id = ?", (user_id,))
    )
    return Actor(user_id, row["status"], roles)


def get_actor(db: Database, user_id: str) -> Actor:
    with db.read() as conn:
        return load_actor(conn, user_id)


@dataclass(frozen=True)
class Access:
    """A user's standing in one organisation. System admins hold every organisation role."""

    org_id: str
    volunteer: bool  # currently opted in
    reviewer: bool  # may review suggestions: reviewer, org_admin or system admin
    org_admin: bool = False  # manages projects and volunteers: org_admin or system admin
    system_admin: bool = False

    @property
    def any(self) -> bool:
        return self.volunteer or self.reviewer or self.org_admin


# ------------------------------------------------------------------ organisations
def get_org(conn: sqlite3.Connection, org_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, key, name, description FROM organisations WHERE id = ?", (org_id,)
    ).fetchone()
    if row is None:
        raise NotFound("organisation")
    return row


def org_access(conn: sqlite3.Connection, user_id: str, org_id: str) -> Access:
    get_org(conn, org_id)
    volunteer = conn.execute(
        "SELECT 1 FROM memberships WHERE user_id = ? AND org_id = ? AND opted_in = 1",
        (user_id, org_id),
    ).fetchone()
    roles = {
        r[0]
        for r in conn.execute(
            "SELECT role FROM org_roles WHERE user_id = ? AND org_id = ?", (user_id, org_id)
        )
    }
    admin = load_actor(conn, user_id).admin
    org_admin = admin or "org_admin" in roles
    return Access(
        org_id,
        volunteer=volunteer is not None,
        reviewer=org_admin or "reviewer" in roles,
        org_admin=org_admin,
        system_admin=admin,
    )


def require_access(db: Database, user_id: str, org_id: str, *, role: str = "any") -> Access:
    with db.read() as conn:
        access = org_access(conn, user_id, org_id)
    allowed = {
        "any": access.any,
        "volunteer": access.volunteer,
        "reviewer": access.reviewer,
        "org_admin": access.org_admin,
    }
    if not allowed[role]:
        raise Forbidden(role)
    return access


def require_active(db: Database, user_id: str) -> Actor:
    actor = get_actor(db, user_id)
    if not actor.active:
        raise Forbidden("active")
    return actor


def directory(db: Database, user_id: str) -> list[dict]:
    with db.read() as conn:
        admin = load_actor(conn, user_id).admin
        rows = conn.execute(
            "SELECT o.id, o.key, o.name, o.description, "
            "COALESCE(m.opted_in, 0) AS opted_in, "
            "(? OR EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = ? AND r.org_id = o.id)) "
            "  AS reviewer, "
            "(? OR EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = ? AND r.org_id = o.id "
            "              AND r.role = 'org_admin')) AS org_admin "
            "FROM organisations o LEFT JOIN memberships m ON m.org_id = o.id AND m.user_id = ? "
            "ORDER BY o.name",
            (int(admin), user_id, int(admin), user_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


def accessible_orgs(db: Database, user_id: str) -> list[dict]:
    return [o for o in directory(db, user_id) if o["opted_in"] or o["reviewer"]]


def set_membership(db: Database, user_id: str, org_id: str, *, opted_in: bool) -> None:
    """Join (opt in) or leave (opt out). Distinct from switching the active organisation."""
    with db.tx() as conn:
        org = get_org(conn, org_id)
        conn.execute(
            "INSERT INTO memberships (user_id, org_id, opted_in, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, org_id) DO UPDATE SET opted_in = excluded.opted_in, "
            "updated_at = excluded.updated_at",
            (user_id, org_id, int(opted_in), now()),
        )
        if opted_in and load_actor(conn, user_id).status != "active":
            notify(
                conn, user_id, "membership",
                f"You joined {org['name']}. You will be matched to its projects once your "
                "account has been vetted.", "/profile",
            )  # fmt: skip
        enqueue(conn, "volunteer", user_id)


def org_volunteers(db: Database, actor_id: str, org_id: str) -> list[dict]:
    """An organisation's opted-in volunteers, for its org admins (no emails)."""
    require_access(db, actor_id, org_id, role="org_admin")
    with db.read() as conn:
        rows = conn.execute(
            "SELECT u.id, u.display_name, u.status, m.updated_at AS joined_at, "
            "(SELECT group_concat(ps.skill_id) FROM profile_skills ps WHERE ps.user_id = u.id "
            " AND ps.offers = 1) AS offers, "
            "(SELECT count(*) FROM placements p JOIN submissions s ON s.id = p.project_id "
            " WHERE p.volunteer_id = u.id AND p.state = 'team' AND s.org_id = ?) AS teams "
            "FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.org_id = ? AND m.opted_in = 1 ORDER BY u.display_name, u.id",
            (org_id, org_id),
        ).fetchall()
    return [{**dict(r), "offers": sorted((r["offers"] or "").split(",")) if r["offers"] else []}
            for r in rows]  # fmt: skip


def remove_volunteer(
    db: Database, actor_id: str, org_id: str, user_id: str, *, reason: str
) -> None:
    """An org admin removes a volunteer from their organisation (and its project teams)."""
    reason = reason.strip()
    if not 3 <= len(reason) <= REASON_MAX:
        raise Invalid("Give the volunteer a reason (3–1000 characters).")
    require_access(db, actor_id, org_id, role="org_admin")
    with db.tx() as conn:
        org = get_org(conn, org_id)
        updated = conn.execute(
            "UPDATE memberships SET opted_in = 0, updated_at = ? "
            "WHERE user_id = ? AND org_id = ? AND opted_in = 1",
            (now(), user_id, org_id),
        ).rowcount
        if not updated:
            raise NotFound("volunteer")
        teams = conn.execute(
            "SELECT p.project_id FROM placements p JOIN submissions s ON s.id = p.project_id "
            "WHERE p.volunteer_id = ? AND s.org_id = ? AND p.state IN ('eligible', 'team')",
            (user_id, org_id),
        ).fetchall()
        conn.execute(
            "UPDATE placements SET state = 'removed', version = version + 1, updated_at = ? "
            "WHERE volunteer_id = ? AND state IN ('eligible', 'team') AND project_id IN "
            "(SELECT id FROM submissions WHERE org_id = ?)",
            (now(), user_id, org_id),
        )
        notify(conn, user_id, "membership",
               f"{org['name']} removed you as a volunteer: {reason}", "/orgs")  # fmt: skip
        enqueue(conn, "volunteer", user_id)
        for (pid,) in teams:
            enqueue(conn, "project", pid)


# ------------------------------------------------------------------ onboarding (vetting)
VOLUNTEER_STATUS_LABELS = {
    "draft": "Not yet submitted for vetting",
    "screening": "In screening",
    "info_requested": "More information requested",
    "active": "Active",
    "declined": "Not approved",
    "suspended": "Suspended",
}
VETTING_ACTIONS = {  # action -> (allowed from, to)
    "approve": (("screening", "info_requested"), "active"),
    "request_info": (("screening",), "info_requested"),
    "decline": (("screening", "info_requested"), "declined"),
    "suspend": (("active",), "suspended"),
    "reinstate": (("suspended",), "active"),
}
VETTING_MESSAGES = {
    "screening": "Your volunteer profile is in screening. A moderator will review it soon.",
    "info_requested": "A moderator asked for more information before approving you",
    "active": "You are approved and active. You can now be matched to projects",
    "declined": "Your volunteer application was not approved",
    "suspended": "Your account has been suspended",
}


def submit_for_vetting(db: Database, user_id: str) -> None:
    """Step 1 of onboarding: the volunteer submits their completed profile for screening."""
    with db.tx() as conn:
        user = conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise NotFound("user")
        if user["status"] not in ("draft", "info_requested", "declined"):
            raise Conflict("Your profile is already in screening or active.")
        offered = conn.execute(
            "SELECT count(*) FROM profile_skills WHERE user_id = ? AND offers = 1", (user_id,)
        ).fetchone()[0]
        if not offered:
            raise Invalid("Offer at least one skill on your profile before submitting.")
        _set_status(conn, user_id, user_id, user["status"], "screening", "", "")
        notify_vetters(conn, "vetting", "A volunteer profile is waiting for screening.",
                       "/admin")  # fmt: skip


def _set_status(conn, user_id, actor_id, old, new, note, private_note) -> None:
    stamp = now()
    conn.execute(
        "UPDATE users SET status = ?, status_version = status_version + 1, "
        "vetted_by = CASE WHEN ? = 'active' THEN ? ELSE vetted_by END, "
        "vetted_at = CASE WHEN ? = 'active' THEN ? ELSE vetted_at END WHERE id = ?",
        (new, new, actor_id, new, stamp, user_id),
    )
    conn.execute(
        "INSERT INTO vetting_events (user_id, actor_id, from_status, to_status, "
        "note_for_volunteer, private_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, actor_id, old, new, note, private_note, stamp),
    )
    message = VETTING_MESSAGES[new] + (f": {note}" if note else ".")
    notify(conn, user_id, "vetting", message.replace("..", "."), "/profile")
    enqueue(conn, "volunteer", user_id)  # only active volunteers get membership edges


def vetting_queue(db: Database, actor_id: str, status: str = "screening") -> list[dict]:
    if not get_actor(db, actor_id).can_vet:
        raise Forbidden("moderator")
    if status not in VOLUNTEER_STATUS_LABELS:
        raise Invalid("Unknown status.")
    with db.read() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT u.id, u.display_name, u.status, u.status_version, u.created_at, "
                "(SELECT count(*) FROM profile_skills ps WHERE ps.user_id = u.id "
                " AND ps.offers = 1) AS skills_offered "
                "FROM users u WHERE u.status = ? ORDER BY u.created_at, u.id",
                (status,),
            )
        ]


def vetting_record(db: Database, actor_id: str, user_id: str) -> dict:
    """What a moderator sees: profile, memberships and history. Emails only for admins."""
    actor = get_actor(db, actor_id)
    if not actor.can_vet:
        raise Forbidden("moderator")
    profile = get_profile(db, user_id)
    with db.read() as conn:
        user = dict(
            conn.execute(
                "SELECT id, display_name, email, status, status_version, created_at, vetted_at "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        )
        if not actor.admin:
            user.pop("email")
        orgs = [
            r[0]
            for r in conn.execute(
                "SELECT o.name FROM memberships m JOIN organisations o ON o.id = m.org_id "
                "WHERE m.user_id = ? AND m.opted_in = 1 ORDER BY o.name",
                (user_id,),
            )
        ]
        history = [
            dict(r)
            for r in conn.execute(
                "SELECT e.from_status, e.to_status, e.note_for_volunteer, e.private_note, "
                "e.created_at, a.display_name AS actor FROM vetting_events e "
                "JOIN users a ON a.id = e.actor_id WHERE e.user_id = ? ORDER BY e.id",
                (user_id,),
            )
        ]
    return {**user, "skills": profile.skills, "causes": profile.causes, "organisations": orgs,
            "history": history}  # fmt: skip


def vet_volunteer(
    db: Database, actor_id: str, user_id: str, *, action: str, note: str = "",
    private_note: str = "", expected_version: int,
) -> str:  # fmt: skip
    """A moderator or admin moves a volunteer through screening. Returns the new status."""
    if action not in VETTING_ACTIONS:
        raise Invalid("Unknown vetting action.")
    note, private_note = note.strip(), private_note.strip()
    if action != "approve" and not 3 <= len(note) <= REASON_MAX:
        raise Invalid("Give the volunteer a reason (3–1000 characters).")
    actor = get_actor(db, actor_id)
    if not actor.can_vet:
        raise Forbidden("moderator")
    if actor_id == user_id:
        raise Forbidden("Moderators cannot vet themselves.")
    allowed_from, new = VETTING_ACTIONS[action]
    with db.tx() as conn:
        user = conn.execute(
            "SELECT status, status_version FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise NotFound("user")
        if user["status"] not in allowed_from or user["status_version"] != expected_version:
            raise Conflict("This volunteer's status changed since you opened it.")
        _set_status(conn, user_id, actor_id, user["status"], new, note, private_note)
    return new


# ------------------------------------------------------------------ roles (system admin only)
def set_system_role(db: Database, actor_id: str, user_id: str, role: str, *, granted: bool) -> None:
    if not get_actor(db, actor_id).admin:
        raise Forbidden("admin")
    if role not in ("admin", "moderator"):
        raise Invalid("Unknown system role.")
    if not granted and role == "admin" and actor_id == user_id:
        raise Invalid("You cannot remove your own admin role.")
    with db.tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise NotFound("user")
        if granted:
            conn.execute(
                "INSERT OR IGNORE INTO system_roles (user_id, role, granted_at, granted_by) "
                "VALUES (?, ?, ?, ?)",
                (user_id, role, now(), actor_id),
            )
        else:
            conn.execute("DELETE FROM system_roles WHERE user_id = ? AND role = ?", (user_id, role))
        verb = "given" if granted else "removed from"
        notify(conn, user_id, "role", f"You were {verb} the system {role} role.")


def set_org_role(
    db: Database, actor_id: str, org_id: str, user_id: str, role: str, *, granted: bool
) -> None:
    if not get_actor(db, actor_id).admin:
        raise Forbidden("admin")
    if role not in ("org_admin", "reviewer"):
        raise Invalid("Unknown organisation role.")
    with db.tx() as conn:
        org = get_org(conn, org_id)
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise NotFound("user")
        if granted:
            conn.execute(
                "INSERT OR IGNORE INTO org_roles (user_id, org_id, role, granted_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, org_id, role, now()),
            )
        else:
            conn.execute(
                "DELETE FROM org_roles WHERE user_id = ? AND org_id = ? AND role = ?",
                (user_id, org_id, role),
            )
        label = role.replace("_", " ")
        notify(conn, user_id, "role",
               f"You were {'given' if granted else 'removed from'} the {label} role for "
               f"{org['name']}.")  # fmt: skip


def list_users(db: Database, actor_id: str) -> list[dict]:
    """Every account with its roles; system admins only (they may see emails)."""
    if not get_actor(db, actor_id).admin:
        raise Forbidden("admin")
    with db.read() as conn:
        users = [
            dict(r)
            for r in conn.execute(
                "SELECT id, display_name, email, status, created_at FROM users "
                "ORDER BY display_name, id"
            )
        ]
        for u in users:
            u["system_roles"] = [
                r[0] for r in conn.execute(
                    "SELECT role FROM system_roles WHERE user_id = ? ORDER BY role", (u["id"],))
            ]  # fmt: skip
            u["org_roles"] = [
                {"org_id": r[0], "org": r[1], "role": r[2]}
                for r in conn.execute(
                    "SELECT r.org_id, o.name, r.role FROM org_roles r JOIN organisations o "
                    "ON o.id = r.org_id WHERE r.user_id = ? ORDER BY o.name, r.role",
                    (u["id"],),
                )
            ]
    return users


def create_organisation(
    db: Database, actor_id: str, *, key: str, name: str, description: str
) -> str:
    if not get_actor(db, actor_id).admin:
        raise Forbidden("admin")
    key, name = key.strip().lower(), name.strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", key) or not 2 <= len(name) <= 80:
        raise Invalid("Key must be lower-case letters, digits and hyphens; name 2–80 characters.")
    org_id = new_id("org")
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO organisations (id, key, name, description) VALUES (?, ?, ?, ?)",
                (org_id, key, name, description.strip()[:500]),
            )
            enqueue(conn, "organisation", org_id)
    except sqlite3.IntegrityError as error:
        raise Conflict("An organisation with that key already exists.") from error
    return org_id


# ------------------------------------------------------------------ profile
@dataclass
class Profile:
    display_name: str
    version: int
    skills: dict[str, str] = field(default_factory=dict)  # skill_id -> SKILL_STATES
    causes: dict[str, str] = field(default_factory=dict)  # cause_id -> CAUSE_STANCES


def get_profile(db: Database, user_id: str) -> Profile:
    with db.read() as conn:
        user = conn.execute(
            "SELECT display_name, profile_version FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise NotFound("user")
        profile = Profile(user["display_name"], user["profile_version"])
        for r in conn.execute(
            "SELECT skill_id, has, offers, excluded FROM profile_skills WHERE user_id = ?",
            (user_id,),
        ):
            profile.skills[r["skill_id"]] = (
                "exclude" if r["excluded"] else "offers" if r["offers"] else "has"
            )
        for r in conn.execute(
            "SELECT cause_id, stance FROM profile_causes WHERE user_id = ?", (user_id,)
        ):
            profile.causes[r["cause_id"]] = r["stance"]
    return profile


def update_profile(
    db: Database,
    user_id: str,
    *,
    display_name: str,
    skills: dict[str, str],
    causes: dict[str, str],
    expected_version: int,
) -> None:
    catalogue = get_catalogue()
    display_name = display_name.strip()
    if not 1 <= len(display_name) <= 60:
        raise Invalid("Display name must be 1–60 characters.")
    if set(skills) - catalogue.skill_ids or set(skills.values()) - set(SKILL_STATES):
        raise Invalid("Unknown skill or skill state.")
    if set(causes) - catalogue.cause_ids or set(causes.values()) - set(CAUSE_STANCES):
        raise Invalid("Unknown cause or cause preference.")
    with db.tx() as conn:
        updated = conn.execute(
            "UPDATE users SET display_name = ?, profile_version = profile_version + 1 "
            "WHERE id = ? AND profile_version = ?",
            (display_name, user_id, expected_version),
        ).rowcount
        if not updated:
            raise Conflict("Your profile changed in another tab. Reload and try again.")
        conn.execute("DELETE FROM profile_skills WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO profile_skills (user_id, skill_id, has, offers, excluded) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    sid,
                    int(st in ("has", "offers")),
                    int(st == "offers"),
                    int(st == "exclude"),
                )
                for sid, st in sorted(skills.items())
                if st != "none"
            ],
        )
        conn.execute("DELETE FROM profile_causes WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO profile_causes (user_id, cause_id, stance) VALUES (?, ?, ?)",
            [(user_id, cid, st) for cid, st in sorted(causes.items()) if st != "neutral"],
        )
        enqueue(conn, "volunteer", user_id)


# ------------------------------------------------------------------ submissions
@dataclass
class Draft:
    title: str
    summary: str
    cause_id: str
    required: list[str]
    optional: list[str]

    def validate(self) -> Draft:
        catalogue = get_catalogue()
        self.title, self.summary = self.title.strip(), self.summary.strip()
        self.required = sorted(set(self.required))
        self.optional = sorted(set(self.optional) - set(self.required))
        if not 3 <= len(self.title) <= TITLE_MAX:
            raise Invalid(f"Title must be 3–{TITLE_MAX} characters.")
        if not 10 <= len(self.summary) <= SUMMARY_MAX:
            raise Invalid(f"Summary must be 10–{SUMMARY_MAX} characters.")
        if self.cause_id not in catalogue.cause_ids:
            raise Invalid("Choose a cause from the catalogue.")
        if not self.required:
            raise Invalid("Choose at least one required skill.")
        if set(self.required + self.optional) - catalogue.skill_ids:
            raise Invalid("Choose skills from the shared catalogue.")
        return self


def _insert_revision(conn: sqlite3.Connection, sid: str, revision: int, draft: Draft) -> None:
    conn.execute(
        "INSERT INTO submission_revisions (submission_id, revision, title, summary, cause_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, revision, draft.title, draft.summary, draft.cause_id, now()),
    )
    conn.executemany(
        "INSERT INTO revision_skills (submission_id, revision, skill_id, required) "
        "VALUES (?, ?, ?, ?)",
        [(sid, revision, s, 1) for s in draft.required]
        + [(sid, revision, s, 0) for s in draft.optional],
    )


def create_submission(db: Database, user_id: str, org_id: str, draft: Draft) -> str:
    """Project onboarding, step 1: an active user suggests a project to one organisation they
    belong to. It goes to that organisation's admin team for review."""
    draft.validate()
    require_active(db, user_id)
    require_access(db, user_id, org_id)
    sid = new_id("p")
    stamp = now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO submissions (id, org_id, author_id, lead_id, state, current_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'submitted', 1, ?, ?)",
            (sid, org_id, user_id, user_id, stamp, stamp),
        )
        _insert_revision(conn, sid, 1, draft)
        link = f"/submissions/{sid}"
        notify(conn, user_id, "project", f"“{draft.title}” was submitted and is in review.", link)
        notify_org_admins(conn, org_id, "review", f"New project suggestion: “{draft.title}”.", link)
    return sid


def revise_submission(
    db: Database, user_id: str, sid: str, draft: Draft, *, expected_version: int
) -> None:
    """The author resubmits after changes were requested; starts a new revision."""
    draft.validate()
    with db.tx() as conn:
        sub = conn.execute(
            "SELECT author_id, state, current_revision, version FROM submissions WHERE id = ?",
            (sid,),
        ).fetchone()
        if sub is None or sub["author_id"] != user_id:
            raise NotFound("submission")
        if sub["state"] != "changes_requested" or sub["version"] != expected_version:
            raise Conflict("This suggestion is not awaiting your changes any more.")
        revision = sub["current_revision"] + 1
        _insert_revision(conn, sid, revision, draft)
        conn.execute(
            "UPDATE submissions SET state = 'submitted', current_revision = ?, "
            "version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
            (revision, now(), sid, expected_version),
        )


def _revision(conn: sqlite3.Connection, sid: str, revision: int) -> dict:
    rev = conn.execute(
        "SELECT revision, title, summary, cause_id, created_at FROM submission_revisions "
        "WHERE submission_id = ? AND revision = ?",
        (sid, revision),
    ).fetchone()
    skills = conn.execute(
        "SELECT skill_id, required FROM revision_skills WHERE submission_id = ? AND revision = ? "
        "ORDER BY skill_id",
        (sid, revision),
    ).fetchall()
    return {
        **dict(rev),
        "required": [s["skill_id"] for s in skills if s["required"]],
        "optional": [s["skill_id"] for s in skills if not s["required"]],
    }


def get_submission(db: Database, user_id: str, sid: str) -> dict:
    """Visible to its author and to reviewers of its organisation; anyone else gets NotFound.
    Private review notes are included only for reviewers."""
    with db.read() as conn:
        sub = conn.execute(
            "SELECT s.*, o.name AS org_name, u.display_name AS author_name "
            "FROM submissions s JOIN organisations o ON o.id = s.org_id "
            "JOIN users u ON u.id = s.author_id WHERE s.id = ?",
            (sid,),
        ).fetchone()
        if sub is None:
            raise NotFound("submission")
        is_author = sub["author_id"] == user_id
        is_reviewer = org_access(conn, user_id, sub["org_id"]).reviewer
        if not (is_author or is_reviewer):
            raise NotFound("submission")
        reviews = [
            {
                **dict(r),
                "private_note": r["private_note"] if is_reviewer else "",
            }
            for r in conn.execute(
                "SELECT r.revision, r.action, r.reason_for_author, r.private_note, r.created_at, "
                "u.display_name AS reviewer_name FROM reviews r JOIN users u "
                "ON u.id = r.reviewer_id WHERE r.submission_id = ? ORDER BY r.id",
                (sid,),
            )
        ]
        approver = None
        if sub["approved_by"]:
            approver = conn.execute(
                "SELECT display_name FROM users WHERE id = ?", (sub["approved_by"],)
            ).fetchone()["display_name"]
        current = _revision(conn, sid, sub["current_revision"])
    return {
        **dict(sub),
        "current": current,
        "reviews": reviews,
        "approved_by_name": approver,
        "is_author": is_author,
        "is_reviewer": is_reviewer,
        "can_review": is_reviewer and not is_author and sub["state"] == "submitted",
        "can_revise": is_author and sub["state"] == "changes_requested",
        "can_withdraw": is_reviewer and sub["state"] in ("approved", "published"),
    }


def my_submissions(db: Database, user_id: str) -> list[dict]:
    with db.read() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT s.id, s.org_id, s.state, s.current_revision, s.updated_at, "
                "o.name AS org_name, v.title FROM submissions s "
                "JOIN organisations o ON o.id = s.org_id "
                "JOIN submission_revisions v ON v.submission_id = s.id "
                "AND v.revision = s.current_revision WHERE s.author_id = ? "
                "ORDER BY s.updated_at DESC, s.id",
                (user_id,),
            )
        ]


def review_queue(db: Database, user_id: str, org_id: str) -> list[dict]:
    require_access(db, user_id, org_id, role="reviewer")
    with db.read() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT s.id, s.state, s.current_revision, s.updated_at, s.author_id, "
                "u.display_name AS author_name, v.title FROM submissions s "
                "JOIN users u ON u.id = s.author_id "
                "JOIN submission_revisions v ON v.submission_id = s.id "
                "AND v.revision = s.current_revision WHERE s.org_id = ? "
                "ORDER BY CASE s.state WHEN 'submitted' THEN 0 ELSE 1 END, s.updated_at DESC, s.id",
                (org_id,),
            )
        ]


REVIEW_ACTIONS = ("changes_requested", "rejected", "approved")


def review_submission(
    db: Database,
    reviewer_id: str,
    org_id: str,
    sid: str,
    *,
    action: str,
    reason: str,
    private_note: str = "",
    revision: int,
    expected_version: int,
) -> None:
    """Decide on the current revision. The WHERE clause re-checks state, revision and version,
    so a stale or replayed form cannot publish twice or approve an outdated revision."""
    if action not in REVIEW_ACTIONS:
        raise Invalid("Unknown review action.")
    reason, private_note = reason.strip(), private_note.strip()
    if not 3 <= len(reason) <= REASON_MAX:
        raise Invalid("Give the author a reason (3–1000 characters).")
    if len(private_note) > NOTE_MAX:
        raise Invalid("Private note is too long.")
    require_access(db, reviewer_id, org_id, role="reviewer")
    stamp = now()
    with db.tx() as conn:
        sub = conn.execute(
            "SELECT author_id, state, current_revision, version FROM submissions "
            "WHERE id = ? AND org_id = ?",
            (sid, org_id),
        ).fetchone()
        if sub is None:
            raise NotFound("submission")
        if sub["author_id"] == reviewer_id:
            raise Forbidden("Reviewers cannot review their own suggestions.")
        updated = conn.execute(
            "UPDATE submissions SET state = ?, version = version + 1, updated_at = ?, "
            "approved_revision = CASE WHEN ? = 'approved' THEN current_revision "
            "                    ELSE approved_revision END, "
            "approved_by = CASE WHEN ? = 'approved' THEN ? ELSE approved_by END, "
            "approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END "
            "WHERE id = ? AND org_id = ? AND state = 'submitted' AND current_revision = ? "
            "AND version = ?",
            (action, stamp, action, action, reviewer_id, action, stamp, sid, org_id,
             revision, expected_version),
        ).rowcount  # fmt: skip
        if not updated:
            raise Conflict("This suggestion changed since you opened it. Reload to see why.")
        conn.execute(
            "INSERT INTO reviews (submission_id, revision, reviewer_id, action, "
            "reason_for_author, private_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, revision, reviewer_id, action, reason, private_note, stamp),
        )
        if action == "approved":
            enqueue(conn, "project", sid)
        title = conn.execute(
            "SELECT title FROM submission_revisions WHERE submission_id = ? AND revision = ?",
            (sid, revision),
        ).fetchone()[0]
        message = {
            "changes_requested": f"“{title}” is in scoping: more detail requested. {reason}",
            "rejected": f"“{title}” was not approved: {reason}",
            "approved": f"“{title}” was approved and is being activated.",
        }[action]
        notify(conn, sub["author_id"], "project", message, f"/submissions/{sid}")


def withdraw_project(
    db: Database, reviewer_id: str, org_id: str, sid: str, *, reason: str, expected_version: int
) -> None:
    reason = reason.strip()
    if not 3 <= len(reason) <= REASON_MAX:
        raise Invalid("Give a reason for withdrawing (3–1000 characters).")
    require_access(db, reviewer_id, org_id, role="reviewer")
    stamp = now()
    with db.tx() as conn:
        sub = conn.execute(
            "SELECT current_revision FROM submissions WHERE id = ? AND org_id = ?",
            (sid, org_id),
        ).fetchone()
        if sub is None:
            raise NotFound("submission")
        updated = conn.execute(
            "UPDATE submissions SET state = 'withdrawn', version = version + 1, "
            "withdrawn_by = ?, withdrawn_at = ?, updated_at = ? WHERE id = ? AND org_id = ? "
            "AND state IN ('approved', 'published') AND version = ?",
            (reviewer_id, stamp, stamp, sid, org_id, expected_version),
        ).rowcount
        if not updated:
            raise Conflict("This project is not published any more, or it changed.")
        conn.execute(
            "INSERT INTO reviews (submission_id, revision, reviewer_id, action, "
            "reason_for_author, created_at) VALUES (?, ?, ?, 'withdrawn', ?, ?)",
            (sid, sub["current_revision"], reviewer_id, reason, stamp),
        )
        info = conn.execute(
            "SELECT s.author_id, v.title FROM submissions s JOIN submission_revisions v "
            "ON v.submission_id = s.id AND v.revision = s.current_revision WHERE s.id = ?",
            (sid,),
        ).fetchone()
        notify(conn, info["author_id"], "project", f"“{info['title']}” was withdrawn: {reason}",
               f"/submissions/{sid}")  # fmt: skip
        affected = conn.execute(
            "SELECT volunteer_id FROM placements WHERE project_id = ? "
            "AND state IN ('eligible', 'team')",
            (sid,),
        ).fetchall()
        conn.execute(
            "UPDATE placements SET state = 'removed', version = version + 1, updated_at = ? "
            "WHERE project_id = ? AND state IN ('eligible', 'team')",
            (stamp, sid),
        )
        for (vid,) in affected:
            notify(conn, vid, "placement",
                   f"“{info['title']}” was withdrawn, so your place on it has ended.",
                   "/opportunities")  # fmt: skip
        enqueue(conn, "project", sid)


def set_project_lead(db: Database, actor_id: str, org_id: str, sid: str, lead_id: str) -> None:
    """An org admin hands a project to a new lead: an active volunteer or admin of the org."""
    require_access(db, actor_id, org_id, role="org_admin")
    with db.tx() as conn:
        sub = conn.execute(
            "SELECT 1 FROM submissions WHERE id = ? AND org_id = ? "
            "AND state IN ('approved', 'published')",
            (sid, org_id),
        ).fetchone()
        if sub is None:
            raise NotFound("project")
        access = org_access(conn, lead_id, org_id)
        if not (access.org_admin or (access.volunteer and load_actor(conn, lead_id).active)):
            raise Invalid("The lead must be an active volunteer or an admin of this organisation.")
        conn.execute(
            "UPDATE submissions SET lead_id = ?, version = version + 1, updated_at = ? "
            "WHERE id = ?",
            (lead_id, now(), sid),
        )
        notify(conn, lead_id, "placement", "You are now the lead for a project.",
               f"/o/{org_id}/projects/{sid}")  # fmt: skip


def published_state(conn: sqlite3.Connection, project_ids: list[str]) -> dict[str, dict]:
    """SQL freshness check for graph results: which projects are published, at which revision."""
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    return {
        r["id"]: dict(r)
        for r in conn.execute(
            f"SELECT id, org_id, approved_revision, version FROM submissions "
            f"WHERE id IN ({marks}) AND state = 'published'",
            project_ids,
        )
    }
