import os
import time
import pytest
from core.db import db
from repositories.channels import (
    list_channels, get_channel, create_channel, update_channel,
    join_channel, leave_channel, is_member, delete_channel,
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


def test_delete_channel_cascades_everything(consume_admin_slot, make_session):
    # ponytail: schema has no FK constraints, so without an explicit
    # cascade the channel row deletion would orphan messages,
    # members, reactions, attachments, and voice notes. The
    # delete_channel repository function must wipe every related
    # row so listChannels can't surface a ghost on a stale id.
    from repositories.messages import send_message
    from repositories.reactions import toggle_reaction
    _, user = make_session()
    _, other = make_session(sub="other-del", email="other-del@example.com", name="OtherDel")
    ch = create_channel("#cascade", "Cascade", True, user["id"])
    msg = send_message(ch["id"], user["id"], "hello")
    # toggle twice to guarantee the row exists (first call adds,
    # second removes). Easier: insert a reaction row directly.
    with db() as conn:
        conn.execute(
            "INSERT INTO reactions (message_id, user_id, emoji, created_at) VALUES (?, ?, 'heart', ?)",
            (msg["id"], other["id"], int(time.time())),
        )
    with db() as conn:
        # Seed a fake attachment and a fake voice note row plus a
        # file on disk. We don't go through the upload path; the
        # delete must clean the row even if storage_path is
        # relative. Create the file in a temp dir under
        # ATTACHMENT_DIR.
        from core.config import ATTACHMENT_DIR
        os.makedirs(ATTACHMENT_DIR, exist_ok=True)
        att_path = "test-cascade-att.bin"
        with open(os.path.join(ATTACHMENT_DIR, att_path), "w") as f:
            f.write("blob")
        now = int(time.time())
        conn.execute(
            "INSERT INTO attachments (id, channel_id, user_id, kind, filename, mime, size_bytes, storage_path, created_at, expires_at) "
            "VALUES (?, ?, ?, 'image', 'a.png', 'image/png', 4, ?, ?, ?)",
            ("att-1", ch["id"], user["id"], att_path, now, now + 86400),
        )
        vn_path = "test-cascade-vn.webm"
        with open(os.path.join(ATTACHMENT_DIR, vn_path), "w") as f:
            f.write("audio")
        conn.execute(
            "INSERT INTO voice_notes (id, user_id, channel_id, duration_ms, amount, size_bytes, storage_path, mime, created_at) "
            "VALUES (?, ?, ?, 1000, 50, 5, ?, 'audio/webm', ?)",
            ("vn-1", user["id"], ch["id"], vn_path, now),
        )
        conn.execute(
            "INSERT INTO message_delivered (message_id, user_id, delivered_at) VALUES (?, ?, ?)",
            (msg["id"], other["id"], now),
        )
        conn.execute(
            "INSERT INTO message_reads (message_id, user_id, read_at) VALUES (?, ?, ?)",
            (msg["id"], other["id"], now),
        )

    removed = delete_channel(ch["id"])
    assert removed == 2  # the two files we wrote

    with db() as conn:
        assert conn.execute("SELECT id FROM channels WHERE id = ?", (ch["id"],)).fetchone() is None
        assert conn.execute("SELECT id FROM messages WHERE channel_id = ?", (ch["id"],)).fetchone() is None
        assert conn.execute("SELECT message_id FROM reactions WHERE message_id = ?", (msg["id"],)).fetchone() is None
        assert conn.execute("SELECT message_id FROM message_delivered WHERE message_id = ?", (msg["id"],)).fetchone() is None
        assert conn.execute("SELECT message_id FROM message_reads WHERE message_id = ?", (msg["id"],)).fetchone() is None
        assert conn.execute("SELECT user_id FROM channel_members WHERE channel_id = ?", (ch["id"],)).fetchone() is None
        assert conn.execute("SELECT id FROM attachments WHERE channel_id = ?", (ch["id"],)).fetchone() is None
        assert conn.execute("SELECT id FROM voice_notes WHERE channel_id = ?", (ch["id"],)).fetchone() is None

    # ponytail: files on disk unlinked. We don't care if the
    # row was missing storage_path; the unlink is best-effort.
    assert not os.path.exists(os.path.join(ATTACHMENT_DIR, att_path))
    assert not os.path.exists(os.path.join(ATTACHMENT_DIR, vn_path))


def test_delete_channel_missing_returns_zero(consume_admin_slot, make_session):
    # ponytail: deleting a non-existent channel is a no-op, not
    # an error. The router checks get_channel first to return 404,
    # but the repository helper itself just reports "nothing
    # changed" so callers that bypass the router don't crash.
    assert delete_channel(99999) == 0
