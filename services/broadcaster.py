import asyncio
import json
import logging
from typing import Optional

log = logging.getLogger("chat-bridge")

class ConnectionManager:
    def __init__(self):
        self.connections: dict[int, set] = {}
        self.lock = asyncio.Lock()

    async def connect(self, user_id: int, ws) -> bool:
        async with self.lock:
            self.connections.setdefault(user_id, set()).add(ws)
            return len(self.connections[user_id]) == 1

    async def disconnect(self, user_id: int, ws) -> bool:
        async with self.lock:
            if user_id in self.connections:
                self.connections[user_id].discard(ws)
                if not self.connections[user_id]:
                    del self.connections[user_id]
                    return True
        return False

    async def broadcast_online(self, user_id: int, online: bool):
        await self.broadcast({"type": "presence.online", "data": {"user_id": user_id, "online": online}})

    async def broadcast(self, message: dict, exclude: set[int] | None = None):
        async with self.lock:
            sockets_by_user = {uid: list(socks) for uid, socks in self.connections.items() if not exclude or uid not in exclude}
        data = json.dumps(message)
        for uid, sockets in sockets_by_user.items():
            for ws in sockets:
                try:
                    await ws.send_text(data)
                except Exception as e:
                    log.warning("Failed to send to user %s: %s", uid, e)

    async def broadcast_to_channel_members(self, channel_id: int, message: dict, exclude: set[int] | None = None):
        from core.db import db
        with db() as conn:
            members = conn.execute(
                "SELECT user_id FROM channel_members WHERE channel_id = ?", (channel_id,)
            ).fetchall()
        for m in members:
            if exclude and m["user_id"] in exclude:
                continue
            await self.send_to_user(m["user_id"], message)

    async def broadcast_to_channel_members_for(self, channel_ids: list[int], message: dict, exclude: set[int] | None = None):
        from core.db import db
        if not channel_ids:
            return
        with db() as conn:
            placeholders = ",".join("?" for _ in channel_ids)
            members = conn.execute(
                f"SELECT DISTINCT user_id FROM channel_members WHERE channel_id IN ({placeholders})",
                channel_ids,
            ).fetchall()
        for m in members:
            if exclude and m["user_id"] in exclude:
                continue
            await self.send_to_user(m["user_id"], message)

    async def send_to_user(self, user_id: int, message: dict):
        async with self.lock:
            sockets = list(self.connections.get(user_id, []))
        data = json.dumps(message)
        log.info("send_to_user uid=%s sockets=%d", user_id, len(sockets))
        for i, ws in enumerate(sockets):
            try:
                log.info("send_to_user uid=%s sock=%d send_start", user_id, i)
                await ws.send_text(data)
                log.info("send_to_user uid=%s sock=%d send_ok", user_id, i)
            except Exception as e:
                log.warning(f"Failed to send to user {user_id}: {e}")

    def is_online(self, user_id: int) -> bool:
        return user_id in self.connections and len(self.connections[user_id]) > 0

ws_manager = ConnectionManager()
