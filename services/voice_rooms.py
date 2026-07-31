import asyncio
import logging
from services.broadcaster import ws_manager

log = logging.getLogger("chat-bridge")


async def _describe(user_ids: list[int]) -> list[dict]:
    """Resolve ids to {user_id, display_name, avatar_url} for the wire.

    ponytail: this used to be `SELECT id, display_name FROM users`
    against the chat DB. That table is not in core/db.py's schema any
    more - identity moved to late-auth in v1.39.0 - so the query hit
    whatever stale rows survived the split and returned "" for everyone
    who signed up afterwards, which is why participants rendered as a
    bare "?" tile. user_cache is the supported path and is where the
    avatar comes from too.

    to_thread because fetch_users does a blocking httpx call and this
    runs on the event loop.
    """
    if not user_ids:
        return []
    from services.user_cache import fetch_users
    info = await asyncio.to_thread(fetch_users, user_ids, True)
    return [
        {
            "user_id": uid,
            "display_name": info.get(uid, {}).get("display_name", "") or "",
            "avatar_url": info.get(uid, {}).get("avatar_url") or "",
        }
        for uid in user_ids
    ]

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
        return await _describe(member_ids)

    # Exposed so ws.py can describe a single user (the joiner) with the
    # same name/avatar resolution the roster uses.
    describe = staticmethod(_describe)

    async def same_room(self, a: int, b: int) -> bool:
        """True if both users are currently in the same voice room.

        Gate for every signaling relay. Without it the server forwards
        an offer to any user_id a client names, and the receiving client
        answers unsolicited offers and attaches its mic - so any logged
        in account could open a one-way listen on any other user.
        """
        async with self.lock:
            room = self.user_room.get(a)
            return bool(room) and self.user_room.get(b) == room

    async def peers(self, user_id: int) -> set[int]:
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return set()
            return {u for u in self.rooms.get(room_id, set()) if u != user_id}

    async def peers_with_names(self, user_id: int) -> list[dict]:
        """Like peers(), but also returns each peer's display name and
        avatar, so the joiner can render existing peers without waiting
        for a peer_joined event."""
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return []
            peer_ids = sorted(u for u in self.rooms.get(room_id, set()) if u != user_id)
        return await _describe(peer_ids)

    async def broadcast(self, user_id: int, message: dict, exclude_self=True):
        async with self.lock:
            room_id = self.user_room.get(user_id)
            if not room_id:
                return
            # Snapshot: send_to_user awaits, and a concurrent join/leave
            # mutating this set mid-iteration raises "Set changed size
            # during iteration" out of the ws handler, which only
            # catches JSONDecodeError and so kills the socket.
            peers = set(self.rooms.get(room_id, set()))
        for pid in peers:
            if exclude_self and pid == user_id:
                continue
            await ws_manager.send_to_user(pid, message)

voice_rooms = VoiceRoomManager()
