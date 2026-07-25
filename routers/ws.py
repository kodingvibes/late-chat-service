import json
import asyncio
import logging
import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.broadcaster import ws_manager
from services.voice_rooms import voice_rooms
from core.config import EDIT_WINDOW_SECONDS, LATE_AUTH_URL, LATE_AUTH_SECRET
from core.db import db

log = logging.getLogger("chat-bridge")
router = APIRouter()

# ponytail: WS auth used to read the sessions table. Now it goes
# through late-auth /api/auth/validate exactly like the REST routes.


async def _validate_token(token: str) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{LATE_AUTH_URL}/api/auth/validate",
                headers={
                    "Authorization": f"Bearer {LATE_AUTH_SECRET}",
                    "X-Session-Id": token,
                },
                timeout=2.0,
            )
        if r.status_code != 200:
            return None
        return r.json().get("user")
    except Exception:
        return None


@router.websocket("/api/chat/ws")
async def chat_ws(ws: WebSocket, token: str = None):
    await ws.accept()
    if not token:
        await ws.close(code=4401)
        return
    user = await _validate_token(token)
    if not user:
        await ws.close(code=4401)
        return
    user_id = user["id"]
    became_online = await ws_manager.connect(user_id, ws)
    if became_online:
        await ws_manager.broadcast_online(user_id, True)
    try:
        # edit_window_seconds travels with the greeting so the client can
        # decide whether to offer the Edit action without hardcoding a number
        # that could drift from the server's. The server still re-checks it.
        await ws.send_text(json.dumps({
            "type": "hello",
            "user": {"id": user_id, "display_name": user.get("display_name")},
            "edit_window_seconds": EDIT_WINDOW_SECONDS,
        }))
        async with voice_rooms.lock:
            active_room_ids = [rid for rid, members in voice_rooms.rooms.items() if members]
        for rid in active_room_ids:
            roster = await voice_rooms.roster(rid)
            if roster:
                await ws.send_text(json.dumps({
                    "type": "voice.participants",
                    "data": {"room_id": rid, "count": len(roster), "participants": roster},
                }))
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                t = msg.get("type")
                if t == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                elif t == "typing":
                    channel_id = int(msg.get("channel_id", 0))
                    if channel_id and msg.get("typing", True):
                        await ws_manager.broadcast_to_channel_members(channel_id, {
                            "type": "typing", "data": {
                                "channel_id": channel_id, "user_id": user_id,
                                "display_name": user.get("display_name"), "typing": True,
                            },
                        }, exclude={user_id})
                elif t == "voice.join":
                    room_id = msg.get("roomId", "lobby")
                    await voice_rooms.join(user_id, room_id)
                    peers = await voice_rooms.peers_with_names(user_id)
                    await ws.send_text(json.dumps({"type": "voice.peers", "data": {"peers": peers}}))
                    await voice_rooms.broadcast(user_id, {"type": "voice.peer_joined", "data": {"user_id": user_id, "display_name": user.get("display_name")}})
                elif t == "voice.leave":
                    await voice_rooms.leave(user_id)
                    await voice_rooms.broadcast(user_id, {"type": "voice.peer_left", "data": {"user_id": user_id, "display_name": user.get("display_name")}})
                elif t == "voice.offer":
                    target = msg.get("to")
                    if target:
                        await ws_manager.send_to_user(target, {"type": "voice.offer", "data": {"from": user_id, "from_display_name": user.get("display_name"), "sdp": msg.get("sdp")}})
                elif t == "voice.answer":
                    target = msg.get("to")
                    if target:
                        await ws_manager.send_to_user(target, {"type": "voice.answer", "data": {"from": user_id, "from_display_name": user.get("display_name"), "sdp": msg.get("sdp")}})
                elif t == "voice.ice":
                    target = msg.get("to")
                    if target:
                        await ws_manager.send_to_user(target, {"type": "voice.ice", "data": {"from": user_id, "from_display_name": user.get("display_name"), "candidate": msg.get("candidate")}})
                elif t == "voice.hangup":
                    await voice_rooms.leave(user_id)
                    await voice_rooms.broadcast(user_id, {"type": "voice.hangup", "data": {"user_id": user_id}})
                elif t == "voice.peer_volume":
                    target = msg.get("to")
                    volume = msg.get("volume")
                    if target and volume is not None:
                        await ws_manager.send_to_user(target, {"type": "voice.peer_volume", "data": {"from": user_id, "volume": volume}})
                elif t == "voice.peer_local_mute":
                    target = msg.get("to")
                    muted = msg.get("muted")
                    if target and muted is not None:
                        await ws_manager.send_to_user(target, {"type": "voice.peer_local_mute", "data": {"from": user_id, "muted": muted}})
                elif t == "voice.peer_kick":
                    target = msg.get("target_user_id")
                    channel_id_for_voice = msg.get("channel_id")
                    if target and channel_id_for_voice:
                        with db() as conn:
                            caller_role = conn.execute("SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?", (channel_id_for_voice, user_id)).fetchone()
                        if caller_role and caller_role["role"] == "admin":
                            await ws_manager.send_to_user(target, {"type": "voice.kicked", "data": {"by": user_id, "by_display_name": user.get("display_name"), "channel_id": channel_id_for_voice}})
                            await voice_rooms.leave(target)
                            await voice_rooms.broadcast(target, {"type": "voice.peer_left", "data": {"user_id": target, "display_name": ""}})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        became_offline = await ws_manager.disconnect(user_id, ws)
        if became_offline:
            await ws_manager.broadcast_online(user_id, False)
        await voice_rooms.leave(user_id)
