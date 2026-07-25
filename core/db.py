import sqlite3
import time
from contextlib import contextmanager
from core.config import SQLITE_PATH

# ponytail: chat-bridge no longer owns users or sessions. The
# actual identity lives in late-auth-service
# (kodingvibes/late-auth-service), which validates every Bearer
# token. The `user_id` columns below are plain integers — there's
# no FK to a local users table because there isn't one. chat-bridge
# resolves author display_name and email through the late-auth
# user cache (services/user_cache.py) which fetches from
# /api/auth/users/{id} on miss.

def get_db():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _run_migrations(conn)
    return conn


def _run_migrations(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            is_public INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS channel_members (
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at INTEGER NOT NULL,
            last_read_message_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (channel_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            is_action INTEGER NOT NULL DEFAULT 0,
            og_data TEXT,
            created_at INTEGER NOT NULL,
            edited_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, id);
        CREATE TABLE IF NOT EXISTS reactions (
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (message_id, user_id, emoji)
        );
        CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);
        CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachments_expires ON attachments(expires_at);
        CREATE TABLE IF NOT EXISTS channel_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT NOT NULL DEFAULT 'default',
            name TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            is_collapsed INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS link_previews (
            url TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS message_delivered (
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            delivered_at INTEGER NOT NULL,
            PRIMARY KEY (message_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_message_delivered_msg ON message_delivered(message_id);
        CREATE TABLE IF NOT EXISTS message_reads (
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            read_at INTEGER NOT NULL,
            PRIMARY KEY (message_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_message_reads_msg ON message_reads(message_id);
        CREATE TABLE IF NOT EXISTS voice_notes (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            amount INTEGER NOT NULL DEFAULT 50,
            size_bytes INTEGER NOT NULL,
            storage_path TEXT NOT NULL,
            mime TEXT NOT NULL DEFAULT 'audio/webm',
            created_at INTEGER NOT NULL
        );
    """)
    _run_idempotent_alter(conn, "channel_members", "role", "TEXT")
    _run_idempotent_alter(conn, "messages", "reply_to", "INTEGER")
    _run_idempotent_alter(conn, "messages", "hidden", "INTEGER NOT NULL DEFAULT 0")
    _run_idempotent_alter(conn, "channel_members", "muted", "INTEGER NOT NULL DEFAULT 0")
    # ponytail: image dimensions on attachments so the chat
    # client can reserve a placeholder of the exact size before
    # the bytes load. NULL when the file is not an image, when
    # ffprobe can't read the stream, or when the row predates the
    # migration. The client falls back to max-h-72 when the field
    # is missing.
    _run_idempotent_alter(conn, "attachments", "width", "INTEGER")
    _run_idempotent_alter(conn, "attachments", "height", "INTEGER")
    for col, col_type in [
        ("forwarded_from_message_id", "INTEGER"),
        ("forwarded_from_channel_id", "INTEGER"),
        ("forwarded_from_user_id", "INTEGER"),
        ("forwarded_from_channel_name", "TEXT"),
        ("forwarded_from_display_name", "TEXT"),
    ]:
        _run_idempotent_alter(conn, "messages", col, col_type)
    _run_idempotent_alter(conn, "channels", "channel_type", "TEXT NOT NULL DEFAULT 'text'")
    _run_idempotent_alter(conn, "channels", "category_id", "INTEGER")
    _run_idempotent_alter(conn, "channels", "position", "INTEGER NOT NULL DEFAULT 0")
    _seed_categories(conn)
    _seed_channels(conn)


def _run_idempotent_alter(conn, table, column, col_type):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass


def _seed_categories(conn):
    now = int(time.time())
    for name, pos in [("TEXTO", 0), ("VOZ", 1)]:
        row = conn.execute("SELECT id FROM channel_categories WHERE name = ?", (name,)).fetchone()
        if not row:
            cur = conn.execute("INSERT INTO channel_categories (name, position, created_at) VALUES (?, ?, ?)", (name, pos, now))
            cat_id = cur.lastrowid
        else:
            cat_id = row["id"]
        ch_type = "text" if name == "TEXTO" else "voice"
        conn.execute(
            "UPDATE channels SET category_id = ?, position = id WHERE category_id IS NULL AND (channel_type IS NULL OR channel_type = ?)",
            (cat_id, ch_type),
        )


def _seed_channels(conn):
    now = int(time.time())
    for name, desc, ch_type in [
        ("#lobby", "General chat", "text"),
        ("#random", "Off topic", "text"),
        ("#dev", "Development talk", "text"),
        ("#infra", "Infrastructure", "text"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO channels (name, description, is_public, created_at, channel_type) VALUES (?, ?, 1, ?, ?)",
            (name, desc, now, ch_type),
        )
    for name, desc in [("🔊 General", "Voice chat"), ("🔊 Music", "Music & chill")]:
        conn.execute(
            "INSERT OR IGNORE INTO channels (name, description, is_public, created_at, channel_type) VALUES (?, ?, 1, ?, 'voice')",
            (name, desc, now),
        )
    # ponytail: voice channels are public; any authenticated user
    # joins on demand when they first interact with one.
    conn.commit()


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
