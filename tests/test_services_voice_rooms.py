import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from services.voice_rooms import voice_rooms, VoiceRoomManager


class TestVoiceRoomManager:
    @pytest.fixture(autouse=True)
    def reset(self):
        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()

    @pytest.fixture(autouse=True)
    def mock_broadcast(self):
        with patch.object(voice_rooms, "_broadcast_participants", new=AsyncMock()):
            yield

    async def test_join_new_room(self):
        await voice_rooms.join(1, "lobby")
        assert voice_rooms.participant_count("lobby") == 1
        assert voice_rooms.user_room[1] == "lobby"

    async def test_join_switches_room(self):
        await voice_rooms.join(1, "lobby")
        await voice_rooms.join(1, "music")
        assert voice_rooms.participant_count("lobby") == 0
        assert voice_rooms.participant_count("music") == 1

    async def test_leave(self):
        await voice_rooms.join(1, "lobby")
        await voice_rooms.leave(1)
        assert voice_rooms.participant_count("lobby") == 0
        assert 1 not in voice_rooms.user_room

    async def test_leave_not_in_room(self):
        await voice_rooms.leave(999)

    async def test_peers(self):
        await voice_rooms.join(1, "lobby")
        await voice_rooms.join(2, "lobby")
        peers = await voice_rooms.peers(1)
        assert 2 in peers
        assert 1 not in peers

    async def test_peers_not_in_room(self):
        peers = await voice_rooms.peers(999)
        assert peers == set()

    async def test_broadcast(self):
        await voice_rooms.join(1, "lobby")
        await voice_rooms.join(2, "lobby")
        with patch("services.voice_rooms.ws_manager.send_to_user", new=AsyncMock()) as mock_send:
            await voice_rooms.broadcast(1, {"type": "test"})
            mock_send.assert_called_once_with(2, {"type": "test"})

    async def test_broadcast_excludes_self(self):
        await voice_rooms.join(1, "lobby")
        with patch("services.voice_rooms.ws_manager.send_to_user", new=AsyncMock()) as mock_send:
            await voice_rooms.broadcast(1, {"type": "test"})
            mock_send.assert_not_called()

    async def test_roster_empty_room(self):
        roster = await voice_rooms.roster("nonexistent")
        assert roster == []

    async def test_roster_returns_display_names(self, make_session):
        _, user1 = make_session("sub-roster-1", "roster1@example.com", "Roster One")
        _, user2 = make_session("sub-roster-2", "roster2@example.com", "Roster Two")
        await voice_rooms.join(user1["id"], "lobby")
        await voice_rooms.join(user2["id"], "lobby")
        roster = await voice_rooms.roster("lobby")
        expected = sorted(
            [
                {"user_id": user1["id"], "display_name": user1["display_name"]},
                {"user_id": user2["id"], "display_name": user2["display_name"]},
            ],
            key=lambda p: p["user_id"],
        )
        assert roster == expected


class TestBroadcastParticipantsPayload:
    @pytest.fixture(autouse=True)
    def reset(self):
        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()

    async def test_includes_participants(self, make_session):
        _, user1 = make_session("sub-bp-1", "bp1@example.com", "BP One")
        async with voice_rooms.lock:
            voice_rooms.rooms.setdefault("lobby", set()).add(user1["id"])
            voice_rooms.user_room[user1["id"]] = "lobby"
        with patch("services.voice_rooms.ws_manager.send_to_user", new=AsyncMock()) as mock_send, \
                patch("services.voice_rooms.ws_manager.connections", {user1["id"]: object()}), \
                patch("services.voice_rooms.ws_manager.lock", asyncio.Lock()):
            await voice_rooms._broadcast_participants("lobby")
            mock_send.assert_called_once()
            uid, payload = mock_send.call_args[0]
            assert uid == user1["id"]
            assert payload["type"] == "voice.participants"
            data = payload["data"]
            assert data["room_id"] == "lobby"
            assert data["count"] == 1
            assert data["participants"] == [{"user_id": user1["id"], "display_name": user1["display_name"]}]
