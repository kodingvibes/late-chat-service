"""PATCH /api/chat/messages/{id} - author-only, time-boxed edit.

The broadcast is asserted by capturing ws_manager.broadcast_to_channel_members
instead of opening a real socket: the route's contract with the client is the
frame it emits, and that is what the micro-frontend's `edit` handler reads.
"""
import ipaddress
import socket
import time

import pytest

from core.db import db
from services import broadcaster, link_preview


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """Same stand-in as tests/test_link_preview.py: the OG re-enrichment path
    runs the SSRF host check, and example.com must not depend on live DNS."""
    try:
        ipaddress.ip_address(host)
        addr = host
    except ValueError:
        addr = "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(link_preview.socket, "getaddrinfo", _fake_getaddrinfo)


@pytest.fixture
def sent_frames(monkeypatch):
    """Collect every ws frame the route broadcasts, in order."""
    frames = []

    async def fake_broadcast(channel_id, payload, exclude=None):
        frames.append((channel_id, payload))

    monkeypatch.setattr(broadcaster.ws_manager, "broadcast_to_channel_members", fake_broadcast)
    return frames


async def _post(client, headers, content):
    ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
    msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": content}, headers=headers)).json()
    return ch, msg


def _row(message_id):
    with db() as conn:
        return conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()


def _backdate(message_id, seconds):
    with db() as conn:
        conn.execute(
            "UPDATE messages SET created_at = ? WHERE id = ?",
            (int(time.time()) - seconds, message_id),
        )


class TestEditMessage:
    async def test_edit_own(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "typo hre")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "typo here"}, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "typo here"
        assert body["edited_at"] > 0
        row = _row(msg["id"])
        assert row["content"] == "typo here"
        assert row["edited_at"] == body["edited_at"]

    async def test_broadcast_shape(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "before")
        await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "after"}, headers=headers)
        edits = [p for _, p in sent_frames if p["type"] == "edit"]
        assert len(edits) == 1
        data = edits[0]["data"]
        assert data == {
            "message_id": msg["id"],
            "channel_id": ch["id"],
            "content": "after",
            "edited_at": data["edited_at"],
        }
        assert isinstance(data["edited_at"], int)

    async def test_content_is_trimmed(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "x")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "  spaced  "}, headers=headers)
        assert r.json()["content"] == "spaced"

    async def test_not_author(self, client, auth_headers, make_session, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "mine")
        other_sid, _ = make_session(sub="other-sub", email="other@example.com", name="Other")
        other = {"Authorization": f"Bearer {other_sid}"}
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "hijacked"}, headers=other)
        assert r.status_code == 403
        assert _row(msg["id"])["content"] == "mine"

    async def test_not_found(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        r = await client.patch("/api/chat/messages/99999", json={"content": "ghost"}, headers=headers)
        assert r.status_code == 404

    async def test_unauthenticated(self, client, sent_frames):
        r = await client.patch("/api/chat/messages/1", json={"content": "nope"})
        assert r.status_code == 401

    async def test_hidden_message(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "regret")
        await client.delete(f"/api/chat/messages/{msg['id']}", headers=headers)
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "undelete"}, headers=headers)
        assert r.status_code == 400
        assert _row(msg["id"])["content"] == "[eliminado]"

    async def test_window_expired(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "old news")
        _backdate(msg["id"], 901)
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "new news"}, headers=headers)
        assert r.status_code == 400
        assert r.json()["detail"] == "Edit window expired"
        assert _row(msg["id"])["content"] == "old news"

    async def test_window_edge_still_allowed(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "just in time")
        _backdate(msg["id"], 899)
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "saved"}, headers=headers)
        assert r.status_code == 200

    async def test_attachment_message(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "look __late_image__:42")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "plain text"}, headers=headers)
        assert r.status_code == 400

    async def test_cannot_inject_attachment_marker(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "harmless")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "__late_image__:1"}, headers=headers)
        assert r.status_code == 400
        assert _row(msg["id"])["content"] == "harmless"

    async def test_empty(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "content")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "   "}, headers=headers)
        assert r.status_code == 400

    async def test_too_long(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "content")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "x" * 2_000_001}, headers=headers)
        assert r.status_code == 400

    async def test_edited_at_surfaces_in_list(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "v1")
        await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "v2"}, headers=headers)
        listed = (await client.get(f"/api/chat/channels/{ch['id']}/messages", headers=headers)).json()
        edited = [m for m in listed if m["id"] == msg["id"]][0]
        assert edited["content"] == "v2"
        assert edited["edited_at"] is not None


class TestEditOgReenrichment:
    """og_data has to follow the text: a card built from a link that is no
    longer in the message is a lie the client would keep rendering."""

    async def _seed_og(self, message_id):
        with db() as conn:
            conn.execute("UPDATE messages SET og_data = ? WHERE id = ?", ('{"url":"https://old.example.com","kind":"link"}', message_id))

    async def test_link_removed_clears_card(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "see https://old.example.com")
        await self._seed_og(msg["id"])
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "never mind"}, headers=headers)
        assert r.status_code == 200
        assert _row(msg["id"])["og_data"] is None
        ogs = [p for _, p in sent_frames if p["type"] == "message_og"]
        assert ogs and ogs[-1]["data"] == {"id": msg["id"], "og_data": None}

    async def test_same_link_keeps_card(self, client, auth_headers, sent_frames):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "see https://old.example.com")
        await self._seed_og(msg["id"])
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "really see https://old.example.com"}, headers=headers)
        assert r.status_code == 200
        assert _row(msg["id"])["og_data"] is not None
        assert not [p for _, p in sent_frames if p["type"] == "message_og"]

    async def test_new_link_reenriches(self, client, auth_headers, sent_frames, mock_httpx_og, public_dns):
        headers, user = auth_headers
        ch, msg = await _post(client, headers, "plain text")
        r = await client.patch(f"/api/chat/messages/{msg['id']}", json={"content": "now with https://example.com"}, headers=headers)
        assert r.status_code == 200
        # the enrichment is a background task; give the loop a turn to run it
        import asyncio
        for _ in range(50):
            await asyncio.sleep(0.01)
            if _row(msg["id"])["og_data"]:
                break
        assert _row(msg["id"])["og_data"] is not None
        ogs = [p for _, p in sent_frames if p["type"] == "message_og"]
        assert ogs[-1]["data"]["og_data"]["title"] == "OG Title"

    async def test_stale_enrichment_does_not_resurrect_removed_link(self, client, auth_headers, sent_frames, monkeypatch):
        """A slow send-time fetch that finishes AFTER the author edited the link
        out must not write the stale card back. This is the race the OG-clear on
        edit would otherwise lose to."""
        from routers import messages as messages_router

        async def slow_fetch(url):
            return {"url": url, "kind": "link", "title": "Stale Card"}

        monkeypatch.setattr(messages_router, "fetch_og", slow_fetch)
        headers, user = auth_headers
        # Message no longer carries the URL the in-flight task was fetching.
        ch, msg = await _post(client, headers, "link is gone now")
        await messages_router._enrich_og_and_broadcast(msg["id"], ch["id"], "https://old.example.com")
        assert _row(msg["id"])["og_data"] is None
        assert not [p for _, p in sent_frames if p["type"] == "message_og"]
