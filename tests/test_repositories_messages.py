import time
import pytest
from core.db import db
from repositories.messages import (
    send_message, list_messages, hide_message, delete_message,
    get_message, forward_message,
)


def test_send_message(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "Hello world")
    assert msg["content"] == "Hello world"
    assert msg["user_id"] == user["id"]
    assert msg["display_name"] == user["display_name"]
    assert msg["reactions"] == []
    assert msg["hidden"] == 0


def test_send_message_with_reply(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg1 = send_message(lobby["id"], user["id"], "Original")
    msg2 = send_message(lobby["id"], user["id"], "Reply", reply_to=msg1["id"])
    assert msg2["reply_to"] == msg1["id"]
    assert msg2["reply_to_content"] == "Original"
    assert msg2["reply_to_author"] == user["display_name"]


def test_send_message_reply_cross_channel_ignored(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        random = conn.execute("SELECT id FROM channels WHERE name = '#random'").fetchone()
    msg1 = send_message(lobby["id"], user["id"], "In lobby")
    msg2 = send_message(random["id"], user["id"], "Reply from random", reply_to=msg1["id"])
    assert msg2["reply_to"] is None


def test_send_message_parses_me_action(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "/me waves hello", is_action=True)
    assert msg["is_action"] == 1
    assert msg["content"] == "/me waves hello"


def test_send_message_mentions_by_nick(consume_admin_slot, make_session):
    _, user1 = make_session("sub-mention-1", "alice@example.com", "Alice")
    _, user2 = make_session("sub-mention-2", "bob@example.com", "Bob")
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user1["id"], "Hey @Bob check this")
    assert user2["id"] in msg["mentioned_user_ids"]


def test_send_message_mass_mention_todos(consume_admin_slot, make_session):
    _, user = make_session("sub-mass-1", "admin@example.com", "Admin")
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        conn.execute("UPDATE channel_members SET role = 'admin' WHERE channel_id = ? AND user_id = ?", (lobby["id"], user["id"]))
    msg = send_message(lobby["id"], user["id"], "@todos check this")
    assert msg["is_mass_mention"] == 1


def test_send_message_mass_mention_here(consume_admin_slot, make_session):
    _, user = make_session("sub-mass-2", "mod@example.com", "Mod")
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        conn.execute("UPDATE channel_members SET role = 'mod' WHERE channel_id = ? AND user_id = ?", (lobby["id"], user["id"]))
    msg = send_message(lobby["id"], user["id"], "@here anyone around?")
    assert msg["is_mass_mention"] == 1


def test_send_message_mass_mention_requires_admin_or_mod(consume_admin_slot, make_session):
    make_session("sub-mass-consumer", "consumer@example.com", "Consumer")
    _, user = make_session("sub-mass-3", "regular@example.com", "Regular")
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "@todos check")
    assert msg["is_mass_mention"] == 0


def test_list_messages(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    send_message(lobby["id"], user["id"], "Msg 1")
    send_message(lobby["id"], user["id"], "Msg 2")
    msgs = list_messages(lobby["id"])
    assert len(msgs) == 2
    assert msgs[0]["content"] == "Msg 1"
    assert msgs[1]["content"] == "Msg 2"


# ponytail: messages whose content carries an attachment marker
# should be enriched with the attachment's metadata (id, kind,
# mime, width, height) so the chat client can reserve a row of
# the exact size before the bytes load. The JOIN is done via a
# single batched query per `list_messages` / `send_message` call
# to avoid N+1 on the channel history load.
def test_send_image_message_attaches_image_meta(consume_admin_slot, make_session):
    from repositories.attachments import create_attachment
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    now = int(time.time())
    create_attachment("img1", lobby["id"], user["id"], "image", "photo.png", "image/png", 1024, "/tmp/photo.png", now + 86400, width=1280, height=720)
    msg = send_message(lobby["id"], user["id"], "Check this __late_image__:img1")
    assert msg.get("attachment") is not None
    assert msg["attachment"]["id"] == "img1"
    assert msg["attachment"]["kind"] == "image"
    assert msg["attachment"]["width"] == 1280
    assert msg["attachment"]["height"] == 720


def test_send_text_message_has_no_attachment(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "Just text")
    assert "attachment" not in msg


def test_send_image_message_with_missing_attachment_no_crash(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "Ghost __late_image__:doesnotexist")
    assert "attachment" not in msg


def test_send_audio_message_attaches_audio_meta(consume_admin_slot, make_session):
    from repositories.attachments import create_attachment
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    now = int(time.time())
    create_attachment("aud1", lobby["id"], user["id"], "audio", "song.mp3", "audio/mpeg", 4096, "/tmp/song.mp3", now + 86400)
    msg = send_message(lobby["id"], user["id"], "Listen __late_audio__:aud1")
    assert msg.get("attachment") is not None
    assert msg["attachment"]["id"] == "aud1"
    assert msg["attachment"]["kind"] == "audio"
    # No dimensions for non-image kinds.
    assert msg["attachment"]["width"] is None
    assert msg["attachment"]["height"] is None


def test_list_messages_enriches_attachments(consume_admin_slot, make_session):
    from repositories.attachments import create_attachment
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    now = int(time.time())
    create_attachment("img2", lobby["id"], user["id"], "image", "wide.jpg", "image/jpeg", 2048, "/tmp/wide.jpg", now + 86400, width=1920, height=1080)
    send_message(lobby["id"], user["id"], "Caption __late_image__:img2")
    msgs = list_messages(lobby["id"])
    assert len(msgs) == 1
    assert msgs[0].get("attachment") is not None
    assert msgs[0]["attachment"]["width"] == 1920
    assert msgs[0]["attachment"]["height"] == 1080


def test_list_messages_skips_expired_attachments(consume_admin_slot, make_session):
    from repositories.attachments import create_attachment
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    now = int(time.time())
    # Insert a row whose attachment has already expired. The
    # join should drop the `attachment` key — the client falls
    # back to the max-h-72 placeholder path.
    create_attachment("exp1", lobby["id"], user["id"], "image", "old.png", "image/png", 1024, "/tmp/old.png", now - 100, width=100, height=100)
    send_message(lobby["id"], user["id"], "Old pic __late_image__:exp1")
    msgs = list_messages(lobby["id"])
    assert "attachment" not in msgs[0]


def test_list_messages_with_before(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    send_message(lobby["id"], user["id"], "Msg 1")
    send_message(lobby["id"], user["id"], "Msg 2")
    send_message(lobby["id"], user["id"], "Msg 3")
    msgs = list_messages(lobby["id"], before=3)
    assert len(msgs) == 2
    assert msgs[-1]["content"] == "Msg 2"


def test_list_messages_respects_limit(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    for i in range(10):
        send_message(lobby["id"], user["id"], f"Msg {i}")
    msgs = list_messages(lobby["id"], limit=3)
    assert len(msgs) == 3


def test_hide_message(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "To hide")
    hide_message(msg["id"])
    hidden = get_message(msg["id"])
    assert hidden["hidden"] == 1


def test_delete_message(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "To delete")
    delete_message(msg["id"])
    deleted = get_message(msg["id"])
    assert deleted["content"] == "[eliminado]"
    assert deleted["hidden"] == 1


def test_get_message(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "Get me")
    found = get_message(msg["id"])
    assert found is not None
    assert found["id"] == msg["id"]
    assert get_message(99999) is None


def test_forward_message(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        random = conn.execute("SELECT id FROM channels WHERE name = '#random'").fetchone()
    orig = send_message(lobby["id"], user["id"], "Forward me")
    forwarded = forward_message(orig["id"], random["id"], user["id"])
    assert forwarded["content"] == "Forward me"
    assert forwarded["forwarded_from"] is not None
    assert forwarded["forwarded_from"]["message_id"] == orig["id"]


def test_forward_message_raises_on_hidden(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        random = conn.execute("SELECT id FROM channels WHERE name = '#random'").fetchone()
    orig = send_message(lobby["id"], user["id"], "Hidden msg")
    hide_message(orig["id"])
    with pytest.raises(ValueError, match="hidden"):
        forward_message(orig["id"], random["id"], user["id"])


def test_forward_message_succeeds_for_any_target(consume_admin_slot, make_session):
    # ponytail: every user belongs to every channel, so the
    # "not a member of the target channel" branch in forward_message
    # is gone. The forward goes through regardless of the target
    # channel's membership state.
    _, user = make_session()
    _, other = make_session("sub-other", "other@example.com", "Other")
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        random = conn.execute("SELECT id FROM channels WHERE name = '#random'").fetchone()
    orig = send_message(lobby["id"], user["id"], "Forward me")
    forwarded = forward_message(orig["id"], random["id"], other["id"])
    assert forwarded["content"] == "Forward me"
    assert forwarded["forwarded_from"] is not None


def test_list_messages_includes_reactions(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    msg = send_message(lobby["id"], user["id"], "React to me")
    from repositories.reactions import toggle_reaction
    toggle_reaction(msg["id"], user["id"], "heart")
    msgs = list_messages(lobby["id"])
    assert len(msgs) == 1
    assert len(msgs[0]["reactions"]) == 1
    assert msgs[0]["reactions"][0]["emoji"] == "heart"
