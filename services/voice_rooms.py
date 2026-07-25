import asyncio
import logging
from services.broadcaster import ws_manager

log = logging.getLogger("chat-bridge")

class VoiceRoomManager:
    def __init__(self):
        self.rooms: dict[str, set[int]] = {}
        self.user_room: dict[int, str] = {}
        self.lock = asyncio.Lock()

    def participant_count(self, room_id: str | int) -> int:
        room = self.rooms.get(str(room_id))
        return len(room) if room else 0

    async def join(self, user_id: int, room_id: str):
        changed = False
        async with self.lock:
            prev_room = self.user_room.get(user_id)
            if prev_room and prev_room != room_id:
                self.rooms[prev_room].discard(user_id)
                if not self.rooms[prev_room]:
                    del self.rooms[prev_room]
            self.rooms.setdefault(room_id, set()).add(user_id)
            self.user_room[user_id] = room_id
            changed = True
        if changed:
            await self._broadcast_participants(room_id)

    async def leave(self, user_id: int):
        changed = False
        async with self.lock:
            room_id = self.user_room.pop(user_id, None)
            if room_id and room_id in self.rooms:
                self.rooms[room_id].discard(user_id)
                if not self.rooms[room_id]:
                    del self.rooms[room_id]
                changed = True
        if changed and room_id:
            await self._broadcast_participants(room_id)

    async def _broadcast_participants(self, room_id: str):
        participants = await self.roster(room_id)
        async with ws_manager.lock:
            uids = list(ws_manager.connections.keys())
        for uid in uids:
            await ws_manager.send_to_user(uid, {
                "type": "voice.participants",
                "data": {"room_id": room_id, "count": len(participants), "participants": participants},
            })

    async def roster(self, room_id: str) -> list[dict]:
        """Full roster of everyone currently in room_id, for pre-join visibility."""
        async with self.lock:
            member_ids = sorted(self.rooms.get(room_id, set()))
        if not member_ids:
            return []
        from core.db import db
        with db() as conn:
            placeholders = ",".join("?" for _ in member_ids)
            rows = conn.execute(
                f"SELECT id, display_name FROM users WHERE id IN ({placeholders})",
                member_ids,
            ).fetchall()
        by_id = {r["id"]: r["display_name"] for r in rows}
        return [{"user_id": mid, "display_name": by_id.get(mid, "")} for mid in member_ids]

    async def peers(self, user_id: int) -> set[int]:
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return set()
            return {u for u in self.rooms.get(room_id, set()) if u != user_id}

    async def peers_with_names(self, user_id: int) -> list[dict]:
        """Like peers(), but also returns each peer's display name.
        Looks up names from the users table so the joiner can label
        existing peers without having to wait for a peer_joined event."""
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return []
            peer_ids = [u for u in self.rooms.get(room_id, set()) if u != user_id]
        if not peer_ids:
            return []
        from core.db import db
        with db() as conn:
            placeholders = ",".join("?" for _ in peer_ids)
            rows = conn.execute(
                f"SELECT id, display_name FROM users WHERE id IN ({placeholders})",
                peer_ids,
            ).fetchall()
        by_id = {r["id"]: r["display_name"] for r in rows}
        return [{"user_id": pid, "display_name": by_id.get(pid, "")} for pid in peer_ids]

    async def broadcast(self, user_id: int, message: dict, exclude_self=True):
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return
            peers = self.rooms.get(room_id, set())
        for pid in peers:
            if exclude_self and pid == user_id:
                continue
            await ws_manager.send_to_user(pid, message)

voice_rooms = VoiceRoomManager()
