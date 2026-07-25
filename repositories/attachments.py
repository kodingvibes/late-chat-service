import time
from core.db import db


def create_attachment(id: str, channel_id: int, user_id: int, kind: str, filename: str, mime: str, size_bytes: int, storage_path: str, expires_at: int, width: int | None = None, height: int | None = None):
    with db() as conn:
        now = int(time.time())
        conn.execute(
            "INSERT INTO attachments (id, channel_id, user_id, kind, filename, mime, size_bytes, storage_path, created_at, expires_at, width, height) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, channel_id, user_id, kind, filename, mime, size_bytes, storage_path, now, expires_at, width, height),
        )


def get_attachment(attachment_id: str) -> dict | None:
    base_id = attachment_id.split(".")[0]
    with db() as conn:
        # ponytail: explicit columns. The result drives a FileResponse
        # — the consumer uses storage_path, mime, filename, expires_at
        # only. Spelled out so a future migration that adds, say, a
        # thumbnail_path column doesn't accidentally serve stale data
        # through a new key.
        row = conn.execute(
            "SELECT id, channel_id, user_id, kind, filename, mime, size_bytes, "
            "storage_path, created_at, expires_at, width, height "
            "FROM attachments WHERE id = ?", (base_id,),
        ).fetchone()
    return dict(row) if row else None


def get_attachment_meta(attachment_id: str) -> dict | None:
    base_id = attachment_id.split(".")[0]
    with db() as conn:
        row = conn.execute(
            "SELECT id, channel_id, user_id, kind, filename, mime, size_bytes, width, height, created_at, expires_at "
            "FROM attachments WHERE id = ?", (base_id,),
        ).fetchone()
    return dict(row) if row else None


def get_attachments_meta_bulk(attachment_ids: list[str]) -> dict[str, dict]:
    """Batched lookup for the chat list path. Returns a dict keyed
    by attachment id with the same shape as get_attachment_meta
    minus channel/user/created_at (the chat list already knows
    those for the surrounding message). One query, one row per
    id, no N+1. Empty input → empty dict. """
    if not attachment_ids:
        return {}
    seen: set[str] = set()
    deduped: list[str] = []
    for aid in attachment_ids:
        base = aid.split(".")[0]
        if base and base not in seen:
            seen.add(base)
            deduped.append(base)
    if not deduped:
        return {}
    placeholders = ",".join("?" * len(deduped))
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, kind, filename, mime, size_bytes, width, height, expires_at "
            f"FROM attachments WHERE id IN ({placeholders})",
            deduped,
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def delete_expired() -> list[dict]:
    with db() as conn:
        expired = conn.execute(
            "SELECT id, storage_path FROM attachments WHERE expires_at < ?",
            (int(time.time()),),
        ).fetchall()
        if expired:
            ids = ",".join("?" * len(expired))
            conn.execute(
                f"DELETE FROM attachments WHERE id IN ({ids})",
                [e["id"] for e in expired],
            )
    return [dict(r) for r in expired]
