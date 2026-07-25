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
