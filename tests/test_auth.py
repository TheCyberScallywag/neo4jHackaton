"""Identity, sessions and CSRF."""

from fastapi.testclient import TestClient

from app import auth
from app.web.main import create_app
from tests.conftest import Web


def _signup(web: Web, **fields):
    data = {"display_name": "Ffion", "email": "ffion@example.org", "password": "long-enough-pw"}
    data.update(fields)
    return web.post("/signup", data, csrf_from="/signup")


def test_signup_normalises_and_uniquely_constrains_email(web: Web):
    assert _signup(web, email="  Ffion@Example.ORG ").status_code == 303
    with web.rt.db.read() as conn:
        row = conn.execute("SELECT email FROM users WHERE display_name = 'Ffion'").fetchone()
        assert row[0] == "ffion@example.org"
    web.client.cookies.clear()
    r = _signup(web, email="FFION@example.org")
    assert r.status_code == 400 and "already exists" in r.text


def test_signup_never_grants_a_role_or_membership(web: Web):
    r = _signup(web, role="reviewer", org_id="anything", is_admin="1")
    assert r.status_code == 303
    with web.rt.db.read() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = 'ffion@example.org'").fetchone()[0]
        assert (
            conn.execute("SELECT count(*) FROM org_roles WHERE user_id = ?", (uid,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM memberships WHERE user_id = ?", (uid,)).fetchone()[0]
            == 0
        )


def test_passwords_are_argon2_hashed_and_tokens_stored_hashed(web: Web):
    web.sign_in("asha")
    token = web.client.cookies.get("dvn_session")
    with web.rt.db.read() as conn:
        hashed = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'asha@demo.invalid'"
        ).fetchone()[0]
        assert hashed.startswith("$argon2id$")
        assert (
            conn.execute("SELECT count(*) FROM sessions WHERE token_hash = ?", (token,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM sessions WHERE token_hash = ?", (auth.token_hash(token),)
            ).fetchone()[0]
            == 1
        )


def test_wrong_password_is_rejected(web: Web):
    r = web.client.post(
        "/signin",
        data={"email": "asha@demo.invalid", "password": "nope-nope-nope", "csrf_token": web.csrf()},
    )
    assert r.status_code == 400 and "Wrong email or password" in r.text


def test_session_cookie_flags_on_localhost_and_https(settings):
    for base, secure in (("http://testserver", False), ("https://testserver", True)):
        with TestClient(create_app(settings, background=False), base_url=base) as client:
            page = client.get("/signin")
            csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
            r = client.post(
                "/signin",
                data={
                    "email": "asha@demo.invalid",
                    "password": "demo-password-1",
                    "csrf_token": csrf,
                },
                follow_redirects=False,
            )
            cookie = r.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=lax" in cookie
            assert ("secure" in cookie) is secure


def test_state_changing_forms_require_csrf(web: Web):
    web.sign_in("asha")
    r = web.client.post("/orgs/switch", data={"org_id": "x"}, follow_redirects=False)
    assert r.status_code == 403 and "Form expired" in r.text
    r = web.client.post("/signout", data={"csrf_token": "forged"}, follow_redirects=False)
    assert r.status_code == 403
    # anonymous forms use a double-submit cookie
    web.client.cookies.clear()
    r = web.client.post(
        "/signin", data={"email": "asha@demo.invalid", "password": "demo-password-1"}
    )
    assert r.status_code == 403


def test_sign_out_revokes_the_session(web: Web):
    web.sign_in("asha")
    token = web.client.cookies.get("dvn_session")
    assert web.post("/signout").status_code == 303
    web.client.cookies.set("dvn_session", token)  # replay the old cookie
    r = web.get("/home", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/signin")


def test_protected_pages_redirect_anonymous_users(web: Web):
    for path in ("/home", "/profile", "/matches", "/submissions/new"):
        r = web.get(path, follow_redirects=False)
        assert r.status_code == 303 and "/signin" in r.headers["location"]


def test_security_headers(web: Web):
    r = web.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
