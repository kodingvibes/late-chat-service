import time
from core.db import db

ALLOWED_EMOJIS = {
    "smile", "laugh", "cry", "serious", "angry",
    "heart", "thumbsup", "thumbsdown", "point",
    "fire", "star", "sparkles",
    "rocket", "coffee", "lightbulb", "check", "x", "warning", "sparkle",
    "terminal", "wrench", "gear", "bug", "ghost", "music", "moon", "sun", "zzz",
}


def toggle_reaction(message_id: int, user_id: int, emoji: str) -> tuple[str, list[dict]]:
    with db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, emoji),
        ).fetchone()
        now = int(time.time())
        if existing:
            conn.execute(
                "DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                (message_id, user_id, emoji),
            )
            action = "removed"
        else:
            conn.execute(
                "INSERT INTO reactions (message_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)",
                (message_id, user_id, emoji, now),
            )
            action = "added"
        rows = conn.execute(
            "SELECT user_id, emoji, created_at FROM reactions "
            "WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        ).fetchall()
    return action, [dict(r) for r in rows]


def get_reactions(message_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, emoji, created_at FROM reactions "
            "WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        ).fetchall()
    return [dict(r) for r in rows]
