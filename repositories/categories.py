import time
from core.db import db


def list_categories() -> list[dict]:
    with db() as conn:
        # ponytail: explicit columns. The shape is part of the WS /
        # REST contract; new columns (server_id, is_collapsed) get
        # added by deliberate code review, not by accident.
        rows = conn.execute(
            "SELECT id, server_id, name, position, is_collapsed, created_at "
            "FROM channel_categories ORDER BY position, name"
        ).fetchall()
    return [dict(r) for r in rows]


def create_category(name: str) -> dict:
    with db() as conn:
        now = int(time.time())
        cur = conn.execute(
            "INSERT INTO channel_categories (name, position, created_at) VALUES (?, "
            "(SELECT COALESCE(MAX(position), 0) + 1 FROM channel_categories), ?)",
            (name, now),
        )
        cat_id = cur.lastrowid
        cat = conn.execute(
            "SELECT id, server_id, name, position, is_collapsed, created_at "
            "FROM channel_categories WHERE id = ?",
            (cat_id,),
        ).fetchone()
    return dict(cat)


def update_category(category_id: int, patch: dict) -> dict | None:
    with db() as conn:
        updates = []
        params = []
        if "name" in patch:
            updates.append("name = ?")
            params.append(patch["name"])
        if "is_collapsed" in patch:
            updates.append("is_collapsed = ?")
            params.append(1 if patch["is_collapsed"] else 0)
        if updates:
            params.append(category_id)
            conn.execute(f"UPDATE channel_categories SET {', '.join(updates)} WHERE id = ?", params)
        cat = conn.execute(
            "SELECT id, server_id, name, position, is_collapsed, created_at "
            "FROM channel_categories WHERE id = ?",
            (category_id,),
        ).fetchone()
    return dict(cat) if cat else None


def delete_category(category_id: int):
    with db() as conn:
        conn.execute("UPDATE channels SET category_id = NULL WHERE category_id = ?", (category_id,))
        conn.execute("DELETE FROM channel_categories WHERE id = ?", (category_id,))
