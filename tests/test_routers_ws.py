import json
import pytest
from unittest.mock import AsyncMock, patch
from starlette.websockets import WebSocketDisconnect
from core.db import db


class TestChatWs:
    @pytest.fixture(autouse=True)
    def clear_ws_manager(self):
        from services.broadcaster import ws_manager
        ws_manager.connections.clear()
        from services.voice_rooms import voice_rooms
        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()

    async def test_ws_no_token(self):
        from routers.ws import chat_ws
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        await chat_ws(ws, token=None)
        ws.close.assert_called_once_with(code=4401)

    async def test_ws_invalid_token(self):
        from routers.ws import chat_ws
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        await chat_ws(ws, token="invalid")
        ws.close.assert_called_once_with(code=4401)

    async def test_ws_valid_token_sends_hello(self, make_session):
        from routers.ws import chat_ws
        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        await chat_ws(ws, token=session_id)
        ws.accept.assert_called_once()
        ws.send_text.assert_called_once()
        call_arg = ws.send_text.call_args[0][0]
        data = json.loads(call_arg)
        assert data["type"] == "hello"
        assert data["user"]["id"] == user["id"]

    async def test_ws_ping_pong(self, make_session):
        from routers.ws import chat_ws
        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "ping"}),
            WebSocketDisconnect(code=1000),
        ])
        await chat_ws(ws, token=session_id)
        calls = [json.loads(c[0][0]) for c in ws.send_text.call_args_list]
        assert any(c["type"] == "hello" for c in calls)
        assert any(c["type"] == "pong" for c in calls)

    async def test_ws_typing_broadcast(self, make_session):
        from routers.ws import chat_ws
        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        with db() as conn:
            lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "typing", "channel_id": lobby["id"], "typing": True}),
            WebSocketDisconnect(code=1000),
        ])
        await chat_ws(ws, token=session_id)

    async def test_ws_snapshot_of_active_voice_room(self, make_session):
        from routers.ws import chat_ws
        from services.voice_rooms import voice_rooms
        _, occupant = make_session("sub-occupant", "occupant@example.com", "Occupant")
        from services import user_cache
        user_cache._store(occupant["id"], {"display_name": "Occupant", "email": "occupant@example.com", "avatar_url": ""})
        await voice_rooms.join(occupant["id"], "lobby")

        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        await chat_ws(ws, token=session_id)
        calls = [json.loads(c[0][0]) for c in ws.send_text.call_args_list]
        snapshots = [c for c in calls if c["type"] == "voice.participants"]
        assert len(snapshots) == 1
        data = snapshots[0]["data"]
        assert data["room_id"] == "lobby"
        assert data["count"] == 1
        # display_name/avatar now come from user_cache (late-auth), not
        # from a local users table, so seed it above rather than assert
        # against the session row.
        assert data["participants"] == [
            {"user_id": occupant["id"], "display_name": "Occupant", "avatar_url": ""}
        ]

    async def test_ws_no_snapshot_when_no_active_rooms(self, make_session):
        from routers.ws import chat_ws
        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect(code=1000))
        await chat_ws(ws, token=session_id)
        calls = [json.loads(c[0][0]) for c in ws.send_text.call_args_list]
        assert not any(c["type"] == "voice.participants" for c in calls)

    async def test_ws_malformed_json(self, make_session):
        from routers.ws import chat_ws
        session_id, user = make_session()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            "not json",
            WebSocketDisconnect(code=1000),
        ])
        await chat_ws(ws, token=session_id)


class TestVoiceLeaveDeliversPeerLeft:
    async def test_voice_leave_notifies_the_room(self, make_session):
        """Regression: ws.py called leave() before broadcast(). leave()
        pops user_room[uid] and broadcast() reads it back to find the
        room, so peer_left reached nobody and every remaining client
        kept a dead tile plus a live RTCPeerConnection until ICE
        consent expired. Drives the real handler, not the manager.
        """
        from routers.ws import chat_ws
        from services.voice_rooms import voice_rooms
        from services import user_cache
        voice_rooms.rooms.clear(); voice_rooms.user_room.clear()

        _, other = make_session("sub-stayer", "stayer@example.com", "Stayer")
        user_cache._store(other["id"], {"display_name": "Stayer", "email": "s@x", "avatar_url": ""})
        await voice_rooms.join(other["id"], "lobby")

        session_id, me = make_session()
        user_cache._store(me["id"], {"display_name": me["display_name"], "email": "m@x", "avatar_url": ""})
        await voice_rooms.join(me["id"], "lobby")

        ws = AsyncMock()
        ws.accept = AsyncMock(); ws.close = AsyncMock()
        ws.receive_text = AsyncMock(side_effect=[
            json.dumps({"type": "voice.leave"}),
            WebSocketDisconnect(code=1000),
        ])
        with patch("services.voice_rooms.ws_manager.send_to_user", new=AsyncMock()) as send:
            await chat_ws(ws, token=session_id)

        peer_left = [c[0] for c in send.call_args_list if c[0][1].get("type") == "voice.peer_left"]
        assert len(peer_left) == 1, "voice.peer_left was delivered to nobody"
        assert peer_left[0][0] == other["id"]
        assert peer_left[0][1]["data"]["user_id"] == me["id"]
