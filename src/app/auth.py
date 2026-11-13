"""Identity: Argon2id password hashes, opaque revocable sessions and per-session CSRF tokens.

Session tokens are 32 random bytes handed to the client once; only their SHA-256 is stored, so
a copy of the database cannot be replayed as a cookie. Signing out revokes the row.
"""

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.sqlstore import Database, new_id, now

_hasher = PasswordHasher()  # Argon2id with the library's maintained defaults
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


class AuthError(ValueError):
    """A sign-up or sign-in problem that is safe to show to the user."""


def normalise_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    token_hash: str
    user_id: str
    display_name: str
    kind: str
    csrf_token: str
    active_org_id: str | None


def signup(db: Database, *, email: str, display_name: str, password: str) -> str:
    """Create an account. Signup grants no organisation role and joins no organisation."""
    email = normalise_email(email)
    display_name = display_name.strip()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AuthError("Enter a valid email address.")
    if not 1 <= len(display_name) <= 60:
        raise AuthError("Display name must be 1–60 characters.")
    if len(password) < MIN_PASSWORD or len(password) > 256:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters.")
    user_id = new_id("u")
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO users (id, email, display_name, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, email, display_name, hash_password(password), now()),
            )
    except sqlite3.IntegrityError as error:
        raise AuthError("An account with that email already exists.") from error
    return user_id


def authenticate(db: Database, *, email: str, password: str) -> str | None:
    with db.read() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (normalise_email(email),)
        ).fetchone()
    try:
        if row is None:
            _hasher.verify(_DUMMY_HASH, password)  # same work whether or not the user exists
            return None
        _hasher.verify(row["password_hash"], password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return None
    if _hasher.check_needs_rehash(row["password_hash"]):
        with db.tx() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), row["id"]),
            )
    return row["id"]


def create_session(db: Database, user_id: str, *, kind: str, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    created = datetime.now(UTC)
    with db.tx() as conn:
        # Resume in the organisation the user last had active, if they still have access.
        last = conn.execute(
            "SELECT active_org_id FROM sessions WHERE user_id = ? AND active_org_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, kind, csrf_token, active_org_id, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash(token),
                user_id,
                kind,
                secrets.token_urlsafe(32),
                last["active_org_id"] if last else None,
                created.isoformat(timespec="seconds"),
                (created + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
            ),
        )
    return token


def load_session(db: Database, token: str | None, *, kind: str) -> Session | None:
    if not token or len(token) > 128:
        return None
    with db.read() as conn:
        row = conn.execute(
            "SELECT s.token_hash, s.user_id, u.display_name, s.kind, s.csrf_token, "
            "s.active_org_id FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.kind = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
            (token_hash(token), kind, now()),
        ).fetchone()
    return Session(**dict(row)) if row else None


def revoke_session(db: Database, token_hash_value: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now(), token_hash_value),
        )


def set_active_org(db: Database, token_hash_value: str, org_id: str | None) -> None:
    """Callers must have checked the user's access to org_id (app.services.org_access)."""
    with db.tx() as conn:
        conn.execute(
            "UPDATE sessions SET active_org_id = ? WHERE token_hash = ?",
            (org_id, token_hash_value),
        )


def csrf_matches(expected: str | None, supplied: str | None) -> bool:
    return bool(expected and supplied) and secrets.compare_digest(expected, supplied)
