"""Streamlit admin console: a client of the JSON API only (no direct database or graph access).

Staff sign in with their own account. What they see follows their roles, enforced again by the
API on every call:
  * moderator / system admin: volunteer screening queue and decisions
  * org admin (or system admin): the organisation's suggestions, volunteers, candidates and teams
  * system admin: users and roles, new organisations, projection health and the Relate graph
"""

import httpx
import pandas as pd
import streamlit as st

from app.config import get_settings

API = get_settings().api_url.rstrip("/") + "/api/v1"
st.set_page_config(page_title="Data Volunteer Network: admin", layout="wide")
st.title("Data Volunteer Network: admin")


class ApiError(RuntimeError):
    pass


def call(method: str, path: str, **kwargs) -> object:
    headers = {"Authorization": f"Bearer {st.session_state['token']}"}
    try:
        r = httpx.request(method, API + path, headers=headers, timeout=10, **kwargs)
    except httpx.HTTPError as error:
        raise ApiError(f"API unreachable: {error}") from error
    if r.status_code == 401:
        st.session_state.clear()
        st.rerun()
    if r.status_code >= 400:
        raise ApiError(r.json().get("detail", r.text) if r.content else str(r.status_code))
    return r.json() if r.content else None


def act(label: str, method: str, path: str, **kwargs) -> None:
    """Run an action and report the outcome, then refresh."""
    try:
        call(method, path, **kwargs)
    except ApiError as error:
        st.session_state["flash"] = ("error", f"{label}: {error}")
    else:
        st.session_state["flash"] = ("success", f"{label}: done")
    st.rerun()


# ------------------------------------------------------------------ sign in
if "token" not in st.session_state:
    with st.form("sign-in"):
        st.subheader("Staff sign in")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        try:
            r = httpx.post(
                f"{API}/session", json={"email": email, "password": password}, timeout=10
            )
        except httpx.HTTPError as error:
            st.error(f"API unreachable: {error}")
            st.stop()
        if r.status_code != 200:
            st.error("Wrong email or password")
        else:
            st.session_state["token"] = r.json()["token"]
            st.rerun()
    st.stop()

try:
    me = call("GET", "/me")
except ApiError as error:
    st.error(str(error))
    st.stop()
roles = set(me["system_roles"])
admin_orgs = [o for o in me["organisations"] if o["org_admin"]]
st.sidebar.write(f"Signed in as **{me['user']['display_name']}**")
st.sidebar.caption(
    ", ".join(sorted(roles) + [f"org admin: {o['name']}" for o in admin_orgs]) or "no staff role"
)
if st.sidebar.button("Sign out"):
    try:
        call("DELETE", "/session")
    finally:
        st.session_state.clear()
        st.rerun()
if flash := st.session_state.pop("flash", None):
    getattr(st, flash[0])(flash[1])

tabs = []
if roles & {"admin", "moderator"}:
    tabs.append("Volunteer screening")
if admin_orgs:
    tabs.append("Organisations")
if "admin" in roles:
    tabs += ["Users and roles", "System"]
if not tabs:
    st.warning("This console is for moderators, org admins and system admins.")
    st.stop()

for tab, name in zip(st.tabs(tabs), tabs, strict=True):
    with tab:
        # -------------------------------------------------------------- moderation
        if name == "Volunteer screening":
            status = st.selectbox(
                "Status",
                ["screening", "info_requested", "active", "suspended", "declined", "draft"],
                key="vet-status",
            )
            queue = call("GET", "/moderation/volunteers", params={"status": status})
            if not queue:
                st.caption("Nobody in this status.")
            for person in queue:
                with st.expander(f"{person['display_name']} · offers {person['skills_offered']}"):
                    rec = call("GET", f"/moderation/volunteers/{person['id']}")
                    st.write({k: rec[k] for k in ("status", "created_at") if k in rec})
                    if "email" in rec:
                        st.caption(f"Email (admins only): {rec['email']}")
                    st.write("Skills:", rec["skills"] or "none")
                    st.write("Causes:", rec["causes"] or "none")
                    st.write("Organisations:", ", ".join(rec["organisations"]) or "none")
                    if rec["history"]:
                        st.dataframe(pd.DataFrame(rec["history"]), hide_index=True)
                    actions = {
                        "screening": ["approve", "request_info", "decline"],
                        "info_requested": ["approve", "decline"],
                        "active": ["suspend"],
                        "suspended": ["reinstate"],
                    }.get(rec["status"], [])
                    if actions:
                        with st.form(f"vet-{person['id']}"):
                            action = st.radio("Decision", actions, horizontal=True)
                            note = st.text_input("Message to the volunteer")
                            private = st.text_input("Private note (moderators only)")
                            if st.form_submit_button("Record decision"):
                                act("Vetting", "POST",
                                    f"/moderation/volunteers/{person['id']}/decision",
                                    json={"action": action, "note": note, "private_note": private,
                                          "version": rec["status_version"]})  # fmt: skip

        # -------------------------------------------------------------- org admin
        if name == "Organisations":
            org = st.selectbox(
                "Organisation", admin_orgs, format_func=lambda o: o["name"], key="org-pick"
            )
            st.subheader("Suggestions")
            queue = call("GET", f"/orgs/{org['id']}/submissions")
            for sub in queue:
                with st.expander(f"{sub['title']} · {sub['state']} · by {sub['author_name']}"):
                    detail = call("GET", f"/submissions/{sub['id']}")
                    st.write(detail["current"]["summary"])
                    st.write("Required:", ", ".join(detail["current"]["required"]))
                    if detail["can_review"]:
                        with st.form(f"review-{sub['id']}"):
                            action = st.radio(
                                "Decision", ["approved", "changes_requested", "rejected"],
                                format_func={"approved": "Approve and activate",
                                             "changes_requested": "Request scoping detail",
                                             "rejected": "Reject"}.get,
                                horizontal=True,
                            )  # fmt: skip
                            reason = st.text_input("Reason (shown to the author)")
                            if st.form_submit_button("Record decision"):
                                act("Review", "POST",
                                    f"/orgs/{org['id']}/submissions/{sub['id']}/decision",
                                    json={"action": action, "reason": reason,
                                          "revision": detail["current_revision"],
                                          "version": detail["version"]})  # fmt: skip
                    if sub["state"] == "published":
                        st.markdown("**Candidates and team**")
                        team = call("GET", f"/projects/{sub['id']}/placements")
                        for p in team:
                            cols = st.columns([3, 3, 2, 1, 1])
                            cols[0].write(p["display_name"])
                            cols[1].write(", ".join(p["matched_skills"]))
                            cols[2].write(p["state_label"])
                            if p["state"] == "eligible" and not p["lead_said"]:
                                for col, answer in ((cols[3], "yes"), (cols[4], "no")):
                                    if col.button(
                                        answer.title(),
                                        key=f"{sub['id']}-{p['volunteer_id']}-{answer}",
                                    ):  # noqa: E501
                                        path = (f"/projects/{sub['id']}/placements/"
                                                f"{p['volunteer_id']}/answer")  # fmt: skip
                                        body = {"answer": answer, "version": p["version"]}
                                        act("Candidate", "POST", path, json=body)
            st.subheader("Volunteers")
            people = call("GET", f"/orgs/{org['id']}/volunteers")
            st.dataframe(pd.DataFrame(people), hide_index=True, width="stretch")
            if people:
                with st.form("remove-volunteer"):
                    who = st.selectbox("Remove a volunteer", people,
                                       format_func=lambda p: p["display_name"])  # fmt: skip
                    reason = st.text_input("Reason (shown to the volunteer)")
                    if st.form_submit_button("Remove from organisation"):
                        act("Remove volunteer", "POST",
                            f"/orgs/{org['id']}/volunteers/{who['id']}/remove",
                            json={"reason": reason})  # fmt: skip
            cov = call("GET", f"/orgs/{org['id']}/coverage")
            st.subheader("Coverage gaps")
            st.write({p["title"]: p["gaps"] or "none" for p in cov["projects"]})

        # -------------------------------------------------------------- system admin
        if name == "Users and roles":
            users = call("GET", "/admin/users")
            st.dataframe(
                pd.DataFrame(
                    [{**u, "system_roles": ", ".join(u["system_roles"]),
                      "org_roles": ", ".join(f"{r['org']}: {r['role']}" for r in u["org_roles"])}
                     for u in users]
                ),
                hide_index=True, width="stretch",
            )  # fmt: skip
            orgs = [o for o in me["organisations"]]
            with st.form("roles"):
                who = st.selectbox("User", users, format_func=lambda u: u["display_name"])
                scope = st.selectbox(
                    "Role", ["system: admin", "system: moderator"]
                    + [f"{o['name']}: {r}" for o in orgs for r in ("org_admin", "reviewer")],
                )  # fmt: skip
                grant = st.radio("Change", ["grant", "revoke"], horizontal=True)
                if st.form_submit_button("Apply"):
                    method = "PUT" if grant == "grant" else "DELETE"
                    where, role = scope.split(": ")
                    if where == "system":
                        path = f"/admin/users/{who['id']}/system-roles/{role}"
                    else:
                        org_id = next(o["id"] for o in orgs if o["name"] == where)
                        path = f"/admin/orgs/{org_id}/roles/{who['id']}/{role}"
                    act("Role", method, path)
            with st.form("new-org"):
                st.markdown("**New organisation**")
                key = st.text_input("Key (lower-case, hyphens)")
                org_name = st.text_input("Name")
                description = st.text_input("Description")
                if st.form_submit_button("Create"):
                    body = {"key": key, "name": org_name, "description": description}
                    act("Organisation", "POST", "/admin/orgs", json=body)

        if name == "System":
            status = call("GET", "/admin/projection")
            c1, c2, c3 = st.columns(3)
            c1.metric("Pending graph updates", status["pending"])
            c2.metric("Failing (3+ attempts)", status["failed"])
            if c3.button("Replay outbox now"):
                act("Replay", "POST", "/admin/projection/replay")
            if status["items"]:
                st.dataframe(pd.DataFrame(status["items"]), width="stretch")
            graph = call("GET", "/admin/graph")
            g1, g2 = st.columns(2)
            g1.dataframe(pd.DataFrame(graph["labels"]), hide_index=True, width="stretch")
            g2.dataframe(pd.DataFrame(graph["relationships"]), hide_index=True, width="stretch")
            st.subheader("Organisation → project → cause / skill → eligible volunteers")
            st.dataframe(
                pd.DataFrame(
                    [{**r, "eligible_volunteers": ", ".join(r["eligible_volunteers"])
                      or "coverage gap"} for r in graph["inspect"]]
                ),
                hide_index=True, width="stretch",
            )  # fmt: skip
