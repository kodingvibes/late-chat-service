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

    async def test_roster_returns_display_names_and_avatars(self):
        # Identity comes from late-auth via user_cache, not from a local
        # users table (there isn't one), so seed the cache directly.
        from services import user_cache
        user_cache._CACHE.clear()
        user_cache._store(41, {"display_name": "Roster One", "email": "r1@x", "avatar_url": "data:image/webp;base64,AAA"})
        user_cache._store(42, {"display_name": "Roster Two", "email": "r2@x", "avatar_url": ""})
        await voice_rooms.join(41, "lobby")
        await voice_rooms.join(42, "lobby")
        assert await voice_rooms.roster("lobby") == [
            {"user_id": 41, "display_name": "Roster One", "avatar_url": "data:image/webp;base64,AAA"},
            {"user_id": 42, "display_name": "Roster Two", "avatar_url": ""},
        ]

    async def test_roster_does_not_touch_the_local_db(self):
        """Regression: roster() used to run `SELECT id, display_name FROM
        users`, a table core/db.py no longer creates. It resolved to ""
        for every post-split user, which is what rendered as a bare "?"
        tile, and would raise outright on a DB without the leftover.
        """
        from services import user_cache
        user_cache._CACHE.clear()
        user_cache._store(7, {"display_name": "Cached", "email": "c@x", "avatar_url": ""})
        await voice_rooms.join(7, "lobby")
        with patch("core.db.db", side_effect=AssertionError("roster must not hit the chat DB")):
            assert (await voice_rooms.roster("lobby"))[0]["display_name"] == "Cached"


class TestBroadcastParticipantsPayload:
    @pytest.fixture(autouse=True)
    def reset(self):
        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()

    async def test_includes_participants(self):
        from services import user_cache
        user_cache._CACHE.clear()
        user_cache._store(51, {"display_name": "BP One", "email": "bp1@x", "avatar_url": "data:image/webp;base64,BBB"})
        async with voice_rooms.lock:
            voice_rooms.rooms.setdefault("lobby", set()).add(51)
            voice_rooms.user_room[51] = "lobby"
        with patch("services.voice_rooms.ws_manager.send_to_user", new=AsyncMock()) as mock_send, \
                patch("services.voice_rooms.ws_manager.connections", {51: object()}), \
                patch("services.voice_rooms.ws_manager.lock", asyncio.Lock()):
            await voice_rooms._broadcast_participants("lobby")
            mock_send.assert_called_once()
            uid, payload = mock_send.call_args[0]
            assert uid == 51
            assert payload["type"] == "voice.participants"
            data = payload["data"]
            assert data["room_id"] == "lobby"
            assert data["count"] == 1
            assert data["participants"] == [
                {"user_id": 51, "display_name": "BP One", "avatar_url": "data:image/webp;base64,BBB"}
            ]


class TestSignalingGuards:
    """Regressions for two bugs that were live in production."""

    @pytest.fixture(autouse=True)
    def reset(self):
        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()

    async def test_signaling_is_gated_on_being_in_the_same_room(self):
        """An unsolicited voice.offer to any user_id used to be relayed
        verbatim, and the receiving client answers offers and attaches
        its mic - a one-way wiretap on any call from any account.
        """
        with patch.object(voice_rooms, "_broadcast_participants", new=AsyncMock()):
            await voice_rooms.join(1, "room-a")
            await voice_rooms.join(2, "room-a")
            await voice_rooms.join(3, "room-b")
        assert await voice_rooms.same_room(1, 2) is True
        assert await voice_rooms.same_room(1, 3) is False   # different room
        assert await voice_rooms.same_room(99, 1) is False  # not in voice at all

    async def test_broadcast_survives_a_concurrent_join(self):
        """broadcast() iterated the live room set across an await, so a
        join landing mid-send raised "Set changed size during iteration"
        out of a handler that only catches JSONDecodeError.
        """
        with patch.object(voice_rooms, "_broadcast_participants", new=AsyncMock()):
            for uid in range(1, 6):
                await voice_rooms.join(uid, "lobby")

            async def mutate(_uid, _msg):
                voice_rooms.rooms["lobby"].add(1000 + _uid)
            with patch("services.voice_rooms.ws_manager.send_to_user", new=mutate):
                await voice_rooms.broadcast(1, {"type": "x"})  # must not raise
