import os
import secrets
import sqlite3
import time
import logging
from pathlib import Path

log = logging.getLogger("chat-bridge.notes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_notes (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    amount INTEGER NOT NULL DEFAULT 50,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT 'audio/webm',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_voice_notes_channel ON voice_notes(channel_id, created_at);
"""


def init_table(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()


def insert_note(
    conn: sqlite3.Connection,
    user_id: int,
    channel_id: int,
    duration_ms: int,
    amount: int,
    data: bytes,
    mime: str = "audio/webm",
) -> dict:
    note_id = secrets.token_urlsafe(12)
    now = int(time.time())
    storage_filename = f"voice-{note_id}.webm"
    storage_path = os.path.join(
        os.environ.get("ATTACHMENT_DIR", "/var/lib/late-attachments"),
        storage_filename,
    )
    os.makedirs(os.path.dirname(storage_path), exist_ok=True)
    with open(storage_path, "wb") as f:
        f.write(data)

    conn.execute(
        "INSERT INTO voice_notes (id, user_id, channel_id, duration_ms, amount, size_bytes, storage_path, mime, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (note_id, user_id, channel_id, duration_ms, amount, len(data), storage_path, mime, now),
    )
    conn.commit()
    return {
        "id": note_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "duration_ms": duration_ms,
        "amount": amount,
        "size_bytes": len(data),
        "mime": mime,
        "created_at": now,
    }


def get_note(conn: sqlite3.Connection, note_id: str) -> dict | None:
    # ponytail: explicit columns instead of SELECT *. The shape is
    # part of the public contract of this helper (returned straight
    # to the WS layer); pulling every column and forgetting to
    # export a new one is a class of bug we don't want here.
    row = conn.execute(
        "SELECT id, user_id, channel_id, duration_ms, amount, size_bytes, storage_path, mime, created_at "
        "FROM voice_notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    if row:
        return dict(row)
    return None

