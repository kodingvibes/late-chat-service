import time
import pytest
from core.auth import (
    get_session_user,
    generate_session_id,
    display_name_from_email,
    require_admin_or_mod,
    get_channel_role,
    validate_display_name,
)
from core.db import db
from fastapi import HTTPException


class TestGetSessionUser:
    async def test_missing_header(self):
        with pytest.raises(HTTPException) as exc:
            await get_session_user(authorization=None)
        assert exc.value.status_code == 401

    async def test_missing_bearer(self):
        with pytest.raises(HTTPException) as exc:
            await get_session_user(authorization="Token abc")
        assert exc.value.status_code == 401

    async def test_invalid_token(self, mock_late_auth):
        # ponytail: no session registered for this id, mock returns 401.
        with pytest.raises(HTTPException) as exc:
            await get_session_user(authorization="Bearer invalidtoken")
        assert exc.value.status_code == 401

    async def test_valid_session(self, make_session):
        session_id, user = make_session()
        result = await get_session_user(authorization=f"Bearer {session_id}")
        assert result["user_id"] == user["id"]
        assert result["display_name"] == user["display_name"]


def test_generate_session_id():
    ids = {generate_session_id() for _ in range(100)}
    assert len(ids) == 100
    for sid in ids:
        assert len(sid) > 20


def test_display_name_from_email():
    assert display_name_from_email("alice@example.com") == "alice"
    assert display_name_from_email("bob+test@example.com") == "bob+test"
    assert display_name_from_email("a@b.co") == "a"


def test_is_member(consume_admin_slot, make_session):
    # ponytail: is_member is now a back-compat shim that always
    # returns True. The cross-join migration in core/db.py keeps
    # every (user, channel) pair populated so the function never
    # needs to look anything up.
    _, user = make_session()
    from repositories.channels import is_member as ch_is_member
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    assert ch_is_member(lobby["id"], user["id"]) is True
    assert ch_is_member(99999, user["id"]) is True


def test_require_admin_or_mod(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        assert require_admin_or_mod(lobby["id"], user["id"], conn) is False
        conn.execute("UPDATE channel_members SET role = 'admin' WHERE channel_id = ? AND user_id = ?", (lobby["id"], user["id"]))
        assert require_admin_or_mod(lobby["id"], user["id"], conn) is True
        conn.execute("UPDATE channel_members SET role = 'mod' WHERE channel_id = ? AND user_id = ?", (lobby["id"], user["id"]))
        assert require_admin_or_mod(lobby["id"], user["id"], conn) is True


def test_get_channel_role(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    assert get_channel_role(lobby["id"], user["id"]) is None
    with db() as conn:
        conn.execute("UPDATE channel_members SET role = 'admin' WHERE channel_id = ? AND user_id = ?", (lobby["id"], user["id"]))
    assert get_channel_role(lobby["id"], user["id"]) == "admin"


def test_validate_display_name():
    assert validate_display_name("alice") is None
    assert validate_display_name("a") is not None
    assert validate_display_name("") is not None
    assert validate_display_name("a" * 33) is not None
    assert validate_display_name("alice!@#") is not None
    assert validate_display_name("alice_123") is None
    assert validate_display_name("Alice-123") is None
