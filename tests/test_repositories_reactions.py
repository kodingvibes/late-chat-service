import pytest
from core.db import db
from repositories.reactions import toggle_reaction, get_reactions, ALLOWED_EMOJIS


def test_allowed_emojis_contains_expected():
    assert "heart" in ALLOWED_EMOJIS
    assert "smile" in ALLOWED_EMOJIS
    assert "thumbsup" in ALLOWED_EMOJIS
    assert len(ALLOWED_EMOJIS) == 28


def test_toggle_reaction_add(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    from repositories.messages import send_message
    msg = send_message(lobby["id"], user["id"], "React me")
    action, reactions = toggle_reaction(msg["id"], user["id"], "heart")
    assert action == "added"
    assert len(reactions) == 1
    assert reactions[0]["emoji"] == "heart"
    assert reactions[0]["user_id"] == user["id"]


def test_toggle_reaction_remove(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    from repositories.messages import send_message
    msg = send_message(lobby["id"], user["id"], "React me")
    toggle_reaction(msg["id"], user["id"], "heart")
    action, reactions = toggle_reaction(msg["id"], user["id"], "heart")
    assert action == "removed"
    assert len(reactions) == 0


def test_get_reactions(consume_admin_slot, make_session):
    _, user = make_session()
    with db() as conn:
        lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
    from repositories.messages import send_message
    msg = send_message(lobby["id"], user["id"], "React me")
    toggle_reaction(msg["id"], user["id"], "smile")
    reactions = get_reactions(msg["id"])
    assert len(reactions) == 1
    assert reactions[0]["emoji"] == "smile"
