"""In-app notifications. Written in the caller's transaction, so a notification exists exactly
when the change it describes was committed."""

import sqlite3

from app.sqlstore import Database, now


def notify(conn: sqlite3.Connection, user_id: str, kind: str, message: str, link: str = "") -> None:
    conn.execute(
        "INSERT INTO notifications (user_id, kind, message, link, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, message, link, now()),
    )


def notify_org_admins(
    conn: sqlite3.Connection, org_id: str, kind: str, message: str, link: str = ""
) -> None:
    for (uid,) in conn.execute(
        "SELECT DISTINCT user_id FROM org_roles "
        "WHERE org_id = ? AND role IN ('org_admin', 'reviewer')",
        (org_id,),
    ).fetchall():
        notify(conn, uid, kind, message, link)


def notify_vetters(conn: sqlite3.Connection, kind: str, message: str, link: str = "") -> None:
    for (uid,) in conn.execute("SELECT DISTINCT user_id FROM system_roles").fetchall():
        notify(conn, uid, kind, message, link)


def list_for(
    db: Database, user_id: str, *, unread_only: bool = False, limit: int = 100
) -> list[dict]:
    with db.read() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, kind, message, link, created_at, read_at FROM notifications "
                "WHERE user_id = ? AND (? = 0 OR read_at IS NULL) ORDER BY id DESC LIMIT ?",
                (user_id, int(unread_only), limit),
            )
        ]


def unread_count(db: Database, user_id: str) -> int:
    with db.read() as conn:
        return conn.execute(
            "SELECT count(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)
        ).fetchone()[0]


def mark_read(db: Database, user_id: str, notification_id: int | None = None) -> int:
    """Mark one (or, with no id, all) of the user's own notifications read."""
    with db.tx() as conn:
        return conn.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL "
            "AND (? IS NULL OR id = ?)",
            (now(), user_id, notification_id, notification_id),
        ).rowcount
