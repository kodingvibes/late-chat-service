import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.db import db
from services.broadcaster import ws_manager, ConnectionManager


class TestConnectionManager:
    @pytest.fixture(autouse=True)
    def reset(self):
        ws_manager.connections.clear()

    async def test_connect_disconnect(self):
        ws = MagicMock()
        await ws_manager.connect(1, ws)
        assert 1 in ws_manager.connections
        assert ws in ws_manager.connections[1]
        await ws_manager.disconnect(1, ws)
        assert 1 not in ws_manager.connections

    async def test_send_to_user(self):
        ws = AsyncMock()
        await ws_manager.connect(1, ws)
        await ws_manager.send_to_user(1, {"type": "ping"})
        ws.send_text.assert_called_once_with(json.dumps({"type": "ping"}))

    async def test_send_to_user_no_connection(self):
        await ws_manager.send_to_user(999, {"type": "ping"})

    async def test_broadcast_to_channel_members(self, consume_admin_slot, make_session):
        _, user = make_session()
        with db() as conn:
            lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        ws = AsyncMock()
        await ws_manager.connect(user["id"], ws)
        await ws_manager.broadcast_to_channel_members(lobby["id"], {"type": "test"})
        ws.send_text.assert_called_once()

    async def test_broadcast_excludes(self, consume_admin_slot, make_session):
        _, user1 = make_session("sub-excl-1", "a@a.com", "A")
        _, user2 = make_session("sub-excl-2", "b@b.com", "B")
        with db() as conn:
            lobby = conn.execute("SELECT id FROM channels WHERE name = '#lobby'").fetchone()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await ws_manager.connect(user1["id"], ws1)
        await ws_manager.connect(user2["id"], ws2)
        await ws_manager.broadcast_to_channel_members(lobby["id"], {"type": "test"}, exclude={user1["id"]})
        ws1.send_text.assert_not_called()
        ws2.send_text.assert_called_once()
