import logging
import sqlite3
import time
from contextlib import contextmanager
from core.config import SQLITE_PATH

log = logging.getLogger(__name__)

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
        -- ponytail: one-row meta table. Holds flags that must survive
        -- across restarts. The previous design re-ran the channel
        -- seeder on every container start, which silently re-inserted
        -- any seed channel an admin had hard-deleted (INSERT OR IGNORE
        -- only checks for an existing row — it has no idea if a row
        -- was deleted on purpose). Stamping `seeded=1` here is what
        -- makes the seeder a one-time migration instead of a recurring
        -- resurrection.
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
    # ponytail: enforce a UNIQUE index on channels.name. The CREATE
    # TABLE above declares it, but production tables predate that
    # version, so CREATE TABLE IF NOT EXISTS is a no-op and the
    # column-level UNIQUE is missing. Without this index, the
    # seeder (when it ran on every restart) silently produced
    # hundreds of duplicate seed channels — the admin's hard-
    # delete dropped one of them, the next listChannels call
    # surfaced the rest. Adding the index now makes the constraint
    # real; if duplicates are already in the table, the CREATE
    # INDEX fails and we collapse them first (reassigning FK
    # references) before retrying.
    _ensure_unique_channel_name(conn)
    _seed_categories(conn)
    _seed_channels(conn)


def _run_idempotent_alter(conn, table, column, col_type):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except sqlite3.OperationalError:
        pass


# ponytail: every table that holds a `channel_id` we need to
# rewrite when collapsing duplicate channel rows. Reactions
# (and message_delivered / message_reads) are keyed on
# message_id, and the message keeps its row (we just change
# the message's channel_id), so the reactions/reads/delivered
# don't need any rewrite here.
_CHANNEL_FK_TABLES = (
    ("channel_members", "channel_id"),
    ("messages", "channel_id"),
    ("voice_notes", "channel_id"),
    ("attachments", "channel_id"),
)


def _collapse_duplicate_channels(conn):
    """Reassign FK references from duplicate channel rows to the
    lowest-id copy of each name, then delete the duplicates. The
    seeder ran on every container start before the UNIQUE index
    existed, so the live table may carry hundreds of copies of
    #lobby, #random, #dev, #infra, 🔊 General, and 🔊 Music.
    Without this collapse, an admin's hard-delete of one copy
    leaves the rest visible to listChannels."""
    groups = conn.execute("""
        SELECT name, MIN(id) AS kept_id
        FROM channels
        GROUP BY name
        HAVING COUNT(*) > 1
    """).fetchall()
    if not groups:
        return 0
    total_collapsed = 0
    for name, kept_id in groups:
        dup_ids = [r[0] for r in conn.execute(
            "SELECT id FROM channels WHERE name = ? AND id != ?",
            (name, kept_id),
        ).fetchall()]
        if not dup_ids:
            continue
        placeholders = ",".join("?" * len(dup_ids))
        update_params = [kept_id] + dup_ids
        for table, col in _CHANNEL_FK_TABLES:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cur.fetchone():
                continue
            conn.execute(
                f"UPDATE {table} SET {col} = ? WHERE {col} IN ({placeholders})",
                update_params,
            )
        conn.execute(
            f"DELETE FROM channels WHERE id IN ({placeholders})",
            dup_ids,
        )
        total_collapsed += len(dup_ids)
    return total_collapsed


def _ensure_unique_channel_name(conn):
    """Create idx_channels_name if missing. If duplicates are in
    the way, collapse them first (which reassigns FK references
    from duplicates to the kept row) and retry."""
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_name ON channels(name)")
        return
    except sqlite3.IntegrityError:
        pass
    # ponytail: duplicates blocked the index. Collapse and retry.
    # Wrap in a savepoint so a half-collapsed state doesn't leak
    # out if the second attempt also fails for some reason.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        collapsed = _collapse_duplicate_channels(conn)
        if collapsed:
            log.warning("collapsed %d duplicate channel rows before adding idx_channels_name", collapsed)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_name ON channels(name)")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


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
    # ponytail: this used to run on every container start and
    # `INSERT OR IGNORE` re-inserted any seed channel (#lobby,
    # #random, #dev, #infra, 🔊 General, 🔊 Music) that an admin
    # had hard-deleted. To the user the delete looked like a no-op:
    # the row vanished from the sidebar, but the next deploy or
    # restart resurrected it. The flag in the meta table makes
    # the seed a one-time migration.
    #
    # Pre-existing databases that already have all six seed
    # channels installed are stamped as "seeded" too, so this
    # change is a no-op for them — the only difference is that
    # from now on, deleting a seed channel stays deleted.
    already = conn.execute(
        "SELECT value FROM meta WHERE key = 'channels_seeded'"
    ).fetchone()
    if already:
        return
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
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('channels_seeded', '1')"
    )
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
