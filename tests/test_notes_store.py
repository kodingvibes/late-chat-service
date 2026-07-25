import os
import secrets
import time
import pytest
from notes_store import init_table, insert_note, get_note
from core.db import get_db
from tests.conftest import _create_test_user  # type: ignore


@pytest.fixture
def conn():
    c = get_db()
    init_table(c)
    now = int(time.time())
    user = _create_test_user("notes-test-user", "notes@example.com", "Notes User")
    c.execute("INSERT INTO channels (name, description, is_public, created_at, channel_type) VALUES (?, ?, 1, ?, 'text')",
              ("#notes-test", "Notes test", now))
    ch_id = c.execute("SELECT id FROM channels WHERE name = ?", ("#notes-test",)).fetchone()["id"]
    c.execute("INSERT INTO channel_members (channel_id, user_id, joined_at, role) VALUES (?, ?, ?, 'admin')",
              (ch_id, user["id"], now))
    c.commit()
    yield c
    c.close()


def test_init_table_idempotent(conn):
    init_table(conn)
    init_table(conn)


def test_insert_and_get_note(conn):
    data = b"fake audio data"
    note = insert_note(conn, 1, 1, 5000, 50, data, "audio/webm")
    assert note["id"] is not None
    assert note["user_id"] == 1
    assert note["channel_id"] == 1
    assert note["duration_ms"] == 5000
    assert note["size_bytes"] == len(data)
    assert note["mime"] == "audio/webm"

    fetched = get_note(conn, note["id"])
    assert fetched is not None
    assert fetched["id"] == note["id"]
    assert fetched["storage_path"] is not None
    assert os.path.exists(fetched["storage_path"])
    with open(fetched["storage_path"], "rb") as f:
        assert f.read() == data


def test_get_note_not_found(conn):
    assert get_note(conn, "nonexistent") is None


def test_insert_note_creates_file(conn):
    data = b"test audio"
    note = insert_note(conn, 1, 1, 1000, 50, data)
    fetched = get_note(conn, note["id"])
    assert fetched is not None
    assert os.path.exists(fetched["storage_path"])
    os.unlink(fetched["storage_path"])
