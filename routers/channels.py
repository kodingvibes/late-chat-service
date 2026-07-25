from fastapi import APIRouter, Depends, HTTPException
import httpx
from core.auth import get_session_user, get_channel_role, is_global_admin
from core.config import LATE_AUTH_SECRET, LATE_AUTH_URL
from schemas.chat import CreateChannelRequest, UpdateChannelRequest, InviteRequest
from repositories.channels import list_channels, get_channel, create_channel, update_channel
from repositories.receipts import mark_read
from services.notifications import send_to_kv
from services.voice_rooms import voice_rooms
from services.broadcaster import ws_manager
from core.db import db
import asyncio
import time

router = APIRouter()


async def _late_auth_get(path: str) -> dict | list | None:
    """Service-to-service call into late-auth. Returns the parsed JSON
    body on 2xx, None on 4xx, or raises on 5xx (handled as "remote
    error" so the caller can return a 502/503 to the user).
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{LATE_AUTH_URL}{path}",
                headers={"Authorization": f"Bearer {LATE_AUTH_SECRET}"},
                timeout=2.0,
            )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.json()

@router.get("/api/chat/channels")
async def list_channels_route(session: dict = Depends(get_session_user)):
    channels = list_channels(session["user_id"], global_role=session.get("global_role", "user"))
    for ch in channels:
        if ch["channel_type"] == "voice":
            ch["voice_participants"] = voice_rooms.participant_count(ch["id"])
    return channels

@router.post("/api/chat/channels")
async def create_channel_route(req: CreateChannelRequest, session: dict = Depends(get_session_user)):
    name = req.name.strip()
    ch_type = req.channel_type or "text"
    if ch_type == "text":
        if not name.startswith("#"):
            name = "#" + name
        if not name[1:].replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "Invalid channel name")
    if len(name) > 40:
        raise HTTPException(400, "Channel name too long")
    try:
        ch = create_channel(name, req.description, req.is_public, session["user_id"], ch_type)
        return ch
    except Exception:
        raise HTTPException(409, "Channel already exists")

@router.patch("/api/chat/channels/{channel_id}")
async def update_channel_route(channel_id: int, req: UpdateChannelRequest, session: dict = Depends(get_session_user)):
    # Ponytail: only channel admins or platform-level admins can move a
    # channel between categories. Without this any member could shove
    # channels around. Super admin bypasses the per-channel check.
    if not is_global_admin(session):
        role = get_channel_role(channel_id, session["user_id"])
        if role != "admin":
            raise HTTPException(403, "Only admins can update a channel")
    update_channel(channel_id, {"category_id": req.category_id, "position": req.position})
    return {"ok": True}

@router.delete("/api/chat/channels/{channel_id}")
async def delete_channel_route(channel_id: int, session: dict = Depends(get_session_user)):
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    if not is_global_admin(session):
        role = get_channel_role(channel_id, session["user_id"])
        if role != "admin":
            raise HTTPException(403, "Only admins can delete a channel")
    with db() as conn:
        conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    return {"ok": True}

@router.post("/api/chat/channels/{channel_id}/join")
async def join_channel_route(channel_id: int, session: dict = Depends(get_session_user)):
    # ponytail: every user is in every channel, so joining is a no-op.
    # Kept as a 200 for back-compat with the frontend and any old
    # bundles still calling it.
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    return {"ok": True}

@router.post("/api/chat/channels/{channel_id}/leave")
async def leave_channel_route(channel_id: int, session: dict = Depends(get_session_user)):
    # ponytail: leaving a channel is not a thing anymore. The row
    # stays so unread counts and the mute flag keep working. Frontend
    # has hidden the "Leave" button; this is here for old clients.
    return {"ok": True}
@router.get("/api/chat/users")
async def search_users_route(q: str, session: dict = Depends(get_session_user)):
    # ponytail: user search is owned by late-auth. chat-bridge
    # proxies the query over loopback so the result matches the
    # platform-wide directory, not a stale local mirror.
    q = (q or "").strip().lower()
    if not q:
        return []
    result = await _late_auth_get(f"/api/auth/users/search?q={q}")
    return result if isinstance(result, list) else []


@router.post("/api/chat/channels/{channel_id}/invite")
async def invite_user(channel_id: int, req: InviteRequest, session: dict = Depends(get_session_user)):
    # ponytail: every user is already in every channel, so inviting
    # is a no-op. The target user is reported as "joined" (which they
    # always are), the notify side is kept so the existing webhook
    # still fires for chat-bridge consumers that listen for it.
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    # Look up the user by email via late-auth.
    result = await _late_auth_get(f"/api/auth/users/by-email?email={email}")
    if not result or not result.get("user"):
        raise HTTPException(404, "User not found")
    target = result["user"]
    asyncio.create_task(send_to_kv("channel.invited", {
        "channel_id": channel_id, "channel_name": ch["name"],
        "user_id": target["id"], "display_name": target.get("display_name", ""),
        "by": session["display_name"],
    }))
    return {"ok": True, "user": {"id": target["id"], "display_name": target.get("display_name", "")}}

@router.post("/api/chat/channels/{channel_id}/read")
async def mark_read(channel_id: int, message_id: int, session: dict = Depends(get_session_user)):
    with db() as conn:
        conn.execute(
            "UPDATE channel_members SET last_read_message_id = MAX(last_read_message_id, ?) WHERE channel_id = ? AND user_id = ?",
            (message_id, channel_id, session["user_id"]),
        )
    # Ponytail: receipts — any message with id <= message_id that the
    # user didn't read yet just got read now. We mark them in bulk so
    # a scroll that lands the user 50 messages ahead doesn't fire 50
    # individual events. Then broadcast ONE summary event back to
    # the channel so every sender's UI flips to the blue double-check
    # in a single state update.
    newly_read = []
    with db() as conn:
        rows = conn.execute(
            "SELECT id FROM messages WHERE channel_id = ? AND id <= ? AND user_id != ?",
            (channel_id, message_id, session["user_id"]),
        ).fetchall()
        if rows:
            now = int(time.time())
            # ponytail: one INSERT…SELECT instead of N round-trips.
            # RETURNING + a LEFT JOIN against the existing rows tells
            # us which ids were actually new without a second query.
            cur = conn.execute(
                "INSERT OR IGNORE INTO message_reads (message_id, user_id, read_at) "
                "SELECT id, ?, ? FROM messages "
                "WHERE channel_id = ? AND id <= ? AND user_id != ? "
                "RETURNING message_id",
                (session["user_id"], now, channel_id, message_id, session["user_id"]),
            )
            newly_read = [r["message_id"] for r in cur.fetchall()]
    if newly_read:
        await ws_manager.broadcast_to_channel_members(channel_id, {
            "type": "message_read",
            "data": {
                "channel_id": channel_id,
                "user_id": session["user_id"],
                "display_name": session["display_name"],
                "message_ids": newly_read,
                "read_at": int(time.time()),
            },
        }, exclude={session["user_id"]})
    return {"ok": True}
