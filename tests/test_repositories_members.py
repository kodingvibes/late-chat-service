import time
import pytest
from core.db import db
from repositories.members import list_members, change_role, change_mute, get_member


def test_list_members(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    members = list_members(lobby["id"])
    assert len(members) >= 1
    assert any(m["id"] == user["id"] for m in members)
    for m in members:
        assert "active" in m
        assert "role" in m
        assert "muted" in m


def test_change_role(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    change_role(lobby["id"], user["id"], "mod")
    member = get_member(lobby["id"], user["id"])
    assert member["role"] == "mod"


def test_change_role_to_none(consume_admin_slot, make_session):
    make_session("sub-role-consumer", "consumer@example.com", "Consumer")
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    change_role(lobby["id"], user["id"], "admin")
    change_role(lobby["id"], user["id"], None)
    member = get_member(lobby["id"], user["id"])
    assert member["role"] is None


def test_change_mute(consume_admin_slot, make_session):
    make_session("sub-mute-consumer", "muteconsumer@example.com", "MuteConsumer")
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    change_mute(lobby["id"], user["id"], True)
    member = get_member(lobby["id"], user["id"])
    assert member["muted"] == 1
    change_mute(lobby["id"], user["id"], False)
    member = get_member(lobby["id"], user["id"])
    assert member["muted"] == 0


def test_get_member(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    member = get_member(lobby["id"], user["id"])
    assert member is not None
    assert member["display_name"] == user["display_name"]
    assert get_member(99999, user["id"]) is None
    assert get_member(lobby["id"], 99999) is None
