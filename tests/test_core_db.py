import time
import pytest
from core.db import get_db, _run_migrations, _seed_categories, _seed_channels


def test_migrations_idempotent():
    conn = get_db()
    _run_migrations(conn)
    conn.close()
    conn2 = get_db()
    _run_migrations(conn2)
    conn2.close()


def test_seed_channels_creates_defaults():
    conn = get_db()
    _seed_channels(conn)
    rows = conn.execute("SELECT name FROM channels ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    assert "#lobby" in names
    assert "#random" in names
    assert "#dev" in names
    assert "#infra" in names
    assert "🔊 General" in names
    assert "🔊 Music" in names


def test_seed_channels_idempotent():
    conn = get_db()
    _seed_channels(conn)
    count1 = conn.execute("SELECT COUNT(*) as c FROM channels").fetchone()["c"]
    _seed_channels(conn)
    count2 = conn.execute("SELECT COUNT(*) as c FROM channels").fetchone()["c"]
    assert count1 == count2


def test_seed_categories_creates_defaults():
    conn = get_db()
    _seed_categories(conn)
    rows = conn.execute("SELECT name FROM channel_categories ORDER BY position").fetchall()
    names = [r["name"] for r in rows]
    assert names == ["TEXTO", "VOZ"]


def test_seed_categories_idempotent():
    conn = get_db()
    _seed_categories(conn)
    count1 = conn.execute("SELECT COUNT(*) as c FROM channel_categories").fetchone()["c"]
    _seed_categories(conn)
    count2 = conn.execute("SELECT COUNT(*) as c FROM channel_categories").fetchone()["c"]
    assert count1 == count2


def test_alter_table_idempotent():
    conn = get_db()
    from core.db import _run_idempotent_alter
    _run_idempotent_alter(conn, "users", "extra_col", "TEXT")
    _run_idempotent_alter(conn, "users", "extra_col", "TEXT")


def test_wal_mode():
    conn = get_db()
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_foreign_keys_on():
    conn = get_db()
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_all_tables_exist():
    conn = get_db()
    from notes_store import init_table
    init_table(conn)
    tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    expected = {"channels", "channel_members", "messages", "reactions", "attachments", "channel_categories", "voice_notes"}
    # ponytail: users and sessions used to live here but moved to
    # late-auth-service. The local `users` table still gets created
    # on demand by tests that need it for FK targets (see
    # _create_test_user in conftest.py), but it's not part of the
    # baseline schema.
    for t in expected:
        assert t in tables, f"Missing table: {t}"


def test_seed_channels_does_not_resurrect_deleted_seeds():
    # ponytail: the seeder used to run on every container start with
    # INSERT OR IGNORE, which silently re-inserted any seed channel
    # (#lobby, #random, #dev, #infra, 🔊 General, 🔊 Music) that an
    # admin had hard-deleted. The flag in the meta table is what
    # makes the seed a one-time migration.
    conn = get_db()
    _run_migrations(conn)
    _seed_channels(conn)
    # Admin hard-deletes #dev and 🔊 General.
    conn.execute("DELETE FROM channels WHERE name IN ('#dev', '🔊 General')")
    conn.commit()
    # Container "restarts" — seeder runs again.
    _seed_channels(conn)
    rows = conn.execute("SELECT name FROM channels").fetchall()
    names = [r["name"] for r in rows]
    assert "#dev" not in names
    assert "🔊 General" not in names
    # The other seeds are still there because they were never
    # deleted.
    assert "#lobby" in names
    assert "#random" in names
    assert "#infra" in names
    assert "🔊 Music" in names


def test_seed_channels_backfills_flag_on_existing_db():
    # ponytail: pre-existing databases that already had all six
    # seed channels installed predate the meta flag. On the first
    # migration run, the seeder should detect the absence of the
    # flag, do nothing, and stamp the flag so future restarts
    # stay one-time.
    conn = get_db()
    _run_migrations(conn)
    # Simulate a pre-existing DB that already has channels but
    # no meta row yet.
    conn.execute("DELETE FROM meta WHERE key = 'channels_seeded'")
    conn.commit()
    assert conn.execute("SELECT value FROM meta WHERE key = 'channels_seeded'").fetchone() is None
    _seed_channels(conn)
    # Flag is stamped, channel count is unchanged.
    flag = conn.execute("SELECT value FROM meta WHERE key = 'channels_seeded'").fetchone()
    assert flag is not None
    count_after = conn.execute("SELECT COUNT(*) as c FROM channels").fetchone()["c"]
    # Re-running the seeder must be a no-op now.
    _seed_channels(conn)
    count_after_again = conn.execute("SELECT COUNT(*) as c FROM channels").fetchone()["c"]
    assert count_after == count_after_again


def test_ensure_unique_channel_name_collapses_duplicates():
    # ponytail: the live DB predates the UNIQUE on channels.name
    # (the table was created with an older schema version), and
    # the old seeder ran on every container restart, so production
    # has hundreds of duplicate seed channels. The migration that
    # adds the index must collapse the duplicates first, reassigning
    # FK references from the duplicates to the lowest-id copy, so
    # an admin's hard-delete of one copy no longer leaves 255
    # ghosts in the channel list.
    #
    # We model the bad state by recreating the channels table
    # WITHOUT the UNIQUE on name (so we can insert duplicates
    # like the production DB does), then re-running the
    # migration. _ensure_unique_channel_name must detect the
    # duplicates, collapse them, and create the index.
    conn = get_db()
    # ponytail: the test DB is fresh, so the current schema has
    # the column-level UNIQUE on name. Production didn't — its
    # table predates the UNIQUE-bearing schema. Recreate the
    # table without UNIQUE to mirror production. The autoindex
    # sqlite_autoindex_channels_1 is what enforces the column-
    # level UNIQUE; dropping it plus the new explicit index is
    # what removes the constraint.
    conn.executescript("""
        DROP TABLE IF EXISTS channels;
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            is_public INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at INTEGER NOT NULL
        );
        DROP INDEX IF EXISTS idx_channels_name;
    """)
    conn.execute("DELETE FROM meta WHERE key = 'channels_seeded'")
    # Insert duplicates of #lobby. With UNIQUE gone, this works.
    now = int(time.time())
    for extra_id in (1000, 1001, 1002):
        conn.execute(
            "INSERT INTO channels (id, name, description, is_public, created_at) VALUES (?, '#lobby', 'dup', 1, ?)",
            (extra_id, now),
        )
        conn.execute(
            "INSERT INTO messages (channel_id, user_id, content, created_at) VALUES (?, 1, 'orphan-msg', ?)",
            (extra_id, now),
        )
    conn.commit()
    # Sanity check: we have three #lobby rows and no UNIQUE index.
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM channels WHERE name = '#lobby'"
    ).fetchone()["c"] == 3
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_channels_name'"
    ).fetchone() is None
    # Re-run the migration. _ensure_unique_channel_name must
    # detect the duplicates, collapse them (reassigning the
    # messages to the original #lobby), and create the index.
    _run_migrations(conn)
    # The UNIQUE index now exists.
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_channels_name'"
    ).fetchone()
    assert idx is not None
    # Only one #lobby remains.
    rows = conn.execute("SELECT id, name FROM channels WHERE name = '#lobby'").fetchall()
    assert len(rows) == 1
    kept_id = rows[0]["id"]
    # The orphan messages now belong to the kept row.
    orphans = conn.execute(
        "SELECT channel_id FROM messages WHERE content = 'orphan-msg'"
    ).fetchall()
    assert all(o["channel_id"] == kept_id for o in orphans)
