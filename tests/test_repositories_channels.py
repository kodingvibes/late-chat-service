import time
import pytest
from core.db import db
from repositories.channels import (
    list_channels, get_channel, create_channel, update_channel,
    join_channel, leave_channel, is_member,
)


def test_create_channel(consume_admin_slot, make_session):
    _, user = make_session()
    ch = create_channel("#test", "Test channel", True, user["id"])
    assert ch["id"] > 0
    assert ch["name"] == "#test"
    with db() as conn:
        member = conn.execute(
            "SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (ch["id"], user["id"]),
        ).fetchone()
        assert member["role"] == "admin"


def test_create_channel_without_hash(consume_admin_slot, make_session):
    _, user = make_session()
    ch = create_channel("test", "No hash", True, user["id"])
    assert ch["name"] == "test"


def test_get_channel(consume_admin_slot, make_session):
    _, user = make_session()
    ch = create_channel("#gettest", "Get test", True, user["id"])
    found = get_channel(ch["id"])
    assert found is not None
    assert found["name"] == "#gettest"
    assert get_channel(99999) is None


def test_list_channels(consume_admin_slot, make_session):
    _, user = make_session()
    chans = list_channels(user["id"], global_role="user")
    names = [c["name"] for c in chans]
    assert "#lobby" in names
    assert "#random" in names
    assert "#dev" in names
    assert "#infra" in names
    for c in chans:
        assert "member_count" in c
        assert "unread" in c
        assert "my_role" in c


def test_list_channels_includes_last_message(consume_admin_slot, make_session):
    _, user = make_session()
    chans = list_channels(user["id"], global_role="user")
    for c in chans:
        assert "last_message" in c


def test_list_channels_includes_public_unjoined(consume_admin_slot, make_session):
    # ponytail: every user belongs to every channel, so the concept of
    # "discoverable but not joined" is gone. Public-or-not doesn't
    # gate visibility anymore — membership is universal.
    _, user = make_session()
    _, other = make_session(sub="other-sub", email="other@example.com", name="Other")
    create_channel("#discoverable", "Public", True, other["id"])
    chans = list_channels(user["id"], global_role="user")
    by_name = {c["name"]: c for c in chans}
    assert "#discoverable" in by_name
    assert by_name["#discoverable"]["joined"] is True
    assert by_name["#discoverable"]["unread"] == 0
    for name in ("#lobby", "#random", "#dev", "#infra"):
        assert by_name[name]["joined"] is True


def test_super_admin_sees_admin_role_on_every_channel(consume_admin_slot, make_session, mock_late_auth):
    from core.db import db
    admin_id = consume_admin_slot
    # The admin user is the one upsert_user inserted as id=1 by
    # consume_admin_slot. Re-register that session with the
    # super_admin global_role so list_channels returns admin.
    admin_session = {
        "id": admin_id, "supabase_sub": "__admin_consumer__",
        "email": "admin-consumer@example.com", "name": "Admin Consumer",
        "display_name": "admin-consumer", "global_role": "super_admin",
    }
    mock_late_auth.register("super-admin-token", admin_session)
    _, user = make_session(user_id=admin_id, sub="admin-sub", email="admin@x.com", name="Admin")
    _, other = make_session(sub="other2-sub", email="other2@example.com", name="Other2")
    create_channel("#otherplace", "Other's", True, other["id"])
    chans = list_channels(user["id"], global_role="super_admin")
    by_name = {c["name"]: c for c in chans}
    for name in ("#lobby", "#random", "#dev", "#infra"):
        assert by_name[name]["my_role"] == "admin"
    # ponytail: every user is in every channel, so the formerly-foreign
    # channel is just another joined one. The role still gets the
    # global-admin upgrade.
    assert by_name["#otherplace"]["my_role"] == "admin"
    assert by_name["#otherplace"]["joined"] is True


def test_join_channel(consume_admin_slot, make_session):
    # ponytail: join_channel is a no-op. The row is created by
    # create_channel / upsert_user, not by joining later. The test now
    # asserts the row already exists and the helper is idempotent.
    _, user = make_session()
    ch = create_channel("#jointest", "Join test", True, user["id"])
    join_channel(ch["id"], user["id"])
    assert is_member(ch["id"], user["id"]) is True


def test_join_channel_idempotent(consume_admin_slot, make_session):
    _, user = make_session()
    ch = create_channel("#joinidem", "Idem", True, user["id"])
    join_channel(ch["id"], user["id"])
    join_channel(ch["id"], user["id"])
    assert is_member(ch["id"], user["id"]) is True


def test_leave_channel(consume_admin_slot, make_session):
    # ponytail: leave is a no-op. The membership invariant is that
    # everyone belongs to every channel, so leaving would break
    # unread counts and mute behavior. The helper exists for back-compat
    # with old frontends; it just does nothing.
    _, user = make_session()
    ch = create_channel("#leavetest", "Leave test", True, user["id"])
    leave_channel(ch["id"], user["id"])
    assert is_member(ch["id"], user["id"]) is True


def test_update_channel(consume_admin_slot, make_session):
    _, user = make_session()
    ch = create_channel("#updatetest", "Update test", True, user["id"])
    update_channel(ch["id"], {"position": 10})
    updated = get_channel(ch["id"])
    assert updated["position"] == 10


def test_is_member(consume_admin_slot, make_session):
    # ponytail: is_member always returns True now. The cross-join
    # migration in core/db.py guarantees a row for every (user, channel)
    # pair, and the helper is a back-compat shim.
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    assert is_member(lobby["id"], user["id"]) is True
    assert is_member(99999, user["id"]) is True
