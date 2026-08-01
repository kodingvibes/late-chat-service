from core.db import db
from services.broadcaster import ws_manager
from services.user_cache import fetch_users


def list_members(channel_id: int) -> list[dict]:
    """List the members of a channel with display_name from late-auth.

    The local `users` table is gone — the JOIN that used to live
    here would return zero rows. We pull user ids from the local
    channel_members table and resolve display_name + email through
    the user_cache (which fetches from late-auth on miss).
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, role, muted FROM channel_members "
            "WHERE channel_id = ? ORDER BY last_read_message_id DESC, user_id",
            (channel_id,),
        ).fetchall()
    if not rows:
        return []
    by_id = fetch_users([r["user_id"] for r in rows])
    out: list[dict] = []
    for r in rows:
        info = by_id.get(r["user_id"]) or {"display_name": "", "email": ""}
        display_name = info.get("display_name", "") or ""
        email = info.get("email", "") or ""
        # ponytail: when late-auth is unreachable or the user was
        # deleted, display_name comes back empty. Fall back to the
        # email prefix or a placeholder so the UI never shows "?"
        # or a blank row.
        if not display_name:
            display_name = email.split("@")[0] if email else f"Usuario {r['user_id']}"
        out.append({
            "id": r["user_id"],
            "display_name": display_name,
            "email": email,
            "active": ws_manager.is_online(r["user_id"]),
            "role": r["role"],
            "muted": bool(r["muted"]),
        })
    return out


def change_role(channel_id: int, target_user_id: int, role: str | None):
    with db() as conn:
        conn.execute(
            "UPDATE channel_members SET role = ? WHERE channel_id = ? AND user_id = ?",
            (role, channel_id, target_user_id),
        )


def change_mute(channel_id: int, target_user_id: int, muted: bool):
    with db() as conn:
        conn.execute(
            "UPDATE channel_members SET muted = ? WHERE channel_id = ? AND user_id = ?",
            (1 if muted else 0, channel_id, target_user_id),
        )


def get_member(channel_id: int, user_id: int) -> dict | None:
    """Return one member, or None. Display name comes from late-auth."""
    with db() as conn:
        row = conn.execute(
            "SELECT channel_id, user_id, role, muted FROM channel_members "
            "WHERE channel_id = ? AND user_id = ?",
            (channel_id, user_id),
        ).fetchone()
    if not row:
        return None
    info = fetch_users([user_id]).get(user_id) or {"display_name": "", "email": ""}
    display_name = info.get("display_name", "") or ""
    email = info.get("email", "") or ""
    if not display_name:
        display_name = email.split("@")[0] if email else f"Usuario {user_id}"
    return {
        "channel_id": row["channel_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "muted": bool(row["muted"]),
        "display_name": display_name,
        "email": email,
    }
