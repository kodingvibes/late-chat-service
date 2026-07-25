from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from core.auth import get_session_user
from schemas.chat import SendMessageRequest, ForwardRequest, EditMessageRequest
from repositories.messages import send_message, list_messages, hide_message, delete_message, get_message, forward_message, edit_message, clear_og_data
from repositories.channels import is_member
from repositories.receipts import mark_delivered
from services.broadcaster import ws_manager
from services.notifications import send_to_kv
from services.link_preview import extract_urls, fetch_og, has_attachment_marker
from core.config import EDIT_WINDOW_SECONDS
from core.db import db
import asyncio
import json
import logging
import time

log = logging.getLogger("chat-bridge")
router = APIRouter()

async def _enrich_og_and_broadcast(msg_id: int, channel_id: int, url: str):
    og = await fetch_og(url)
    if og.get("kind") == "error":
        return
    with db() as conn:
        # The fetch can take several seconds (5s/hop, up to 3 redirects). In that
        # window the author may have edited the message to drop or change this
        # link - an edit that already cleared og_data. Without this re-check the
        # stale card would be resurrected and never cleared again. Re-validate
        # that `url` is still the message's first URL before writing.
        row = conn.execute("SELECT content FROM messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            return
        current = extract_urls(row["content"])
        if not current or current[0] != url:
            return
        conn.execute("UPDATE messages SET og_data = ? WHERE id = ?", (json.dumps(og), msg_id))
    await ws_manager.broadcast_to_channel_members(channel_id, {"type": "message_og", "data": {"id": msg_id, "og_data": og}})

@router.get("/api/chat/channels/{channel_id}/messages")
async def list_messages_route(channel_id: int, before: Optional[int] = None, limit: int = 50, session: dict = Depends(get_session_user)):
    from repositories.channels import get_channel
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    # ponytail: every user belongs to every channel, so there is no
    # "not a member" failure mode here. The mute check stays in the
    # write path below.
    return list_messages(
        channel_id,
        before,
        limit,
        session_user_id=session["user_id"],
        session_display_name=session.get("display_name", ""),
        session_email=session.get("email", ""),
    )

@router.post("/api/chat/channels/{channel_id}/messages")
async def send_message_route(channel_id: int, req: SendMessageRequest, session: dict = Depends(get_session_user)):
    content = req.content.strip()
    if not content:
        raise HTTPException(400, "Empty message")
    if len(content) > 2_000_000:
        raise HTTPException(400, "Message too long")
    is_action = bool(req.is_action)
    if not is_action and content.startswith("/me ") and len(content) > 4:
        is_action = True
        content = content[4:].strip()
    from repositories.channels import get_channel
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    with db() as conn:
        # ponytail: the row is always present now, but we still read it
        # for the mute flag (and the role, kept for future per-channel
        # moderation logic that gates non-403 paths).
        member = conn.execute(
            "SELECT role, muted FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (channel_id, session["user_id"]),
        ).fetchone()
        if member and member["muted"] and member["role"] not in ("admin", "mod"):
            raise HTTPException(403, "Estás silenciado en este canal")
    msg = send_message(
        channel_id,
        session["user_id"],
        content,
        is_action,
        req.reply_to,
        session_display_name=session.get("display_name", ""),
        session_email=session.get("email", ""),
    )
    payload = {"type": "message", "data": msg}
    await ws_manager.broadcast_to_channel_members(channel_id, payload, exclude={session["user_id"]})
    # Ponytail: receipts — anyone with a live WS at send time counts as
    # "delivered" (the message left our server to their socket). We
    # record the rows here so the checkmark survives a sender reload,
    # and broadcast a message_delivered event back so any other
    # session of the sender (e.g. the web app open in another tab)
    # flips its bubble from "sent" to "delivered" without waiting
    # for a full refetch.
    delivered_to: list[int] = []
    with db() as conn:
        member_ids = [
            r["user_id"] for r in conn.execute(
                "SELECT user_id FROM channel_members WHERE channel_id = ?",
                (channel_id,),
            ).fetchall()
        ]
    online_members = [uid for uid in member_ids if uid != session["user_id"] and ws_manager.is_online(uid)]
    delivered_to = mark_delivered(msg["id"], online_members)
    if delivered_to:
        await ws_manager.broadcast_to_channel_members(channel_id, {
            "type": "message_delivered",
            "data": {
                "message_id": msg["id"],
                "channel_id": channel_id,
                "user_ids": delivered_to,
            },
        }, exclude=set())
    asyncio.create_task(send_to_kv("message.new", {**msg}))
    urls = extract_urls(content)
    if urls and not is_action:
        asyncio.create_task(_enrich_og_and_broadcast(msg["id"], channel_id, urls[0]))
    return msg

@router.patch("/api/chat/messages/{message_id}")
async def edit_message_route(message_id: int, req: EditMessageRequest, session: dict = Depends(get_session_user)):
    """Author-only, time-boxed edit. Deliberately NOT open to admins/mods:
    they already have hide and delete, and rewriting someone else's words
    is a different and worse power than removing them.

    Editing never notifies. Mentions are resolved once, when the message is
    first sent, so adding `@someone` in an edit does not ping them - which
    also means an edit cannot be used to re-notify a channel repeatedly."""
    content = req.content.strip()
    if not content:
        raise HTTPException(400, "Empty message")
    if len(content) > 2_000_000:
        raise HTTPException(400, "Message too long")
    with db() as conn:
        # ponytail: only the columns this route reads. m.* would drag
        # every forwarded_from_* and the full content body just to
        # compare it to a re-extract later; the edit window check only
        # needs created_at, the author check needs user_id, the
        # hidden guard needs hidden, the OG re-resolve compares the
        # new content against the old one.
        msg = conn.execute(
            "SELECT m.user_id, m.content, m.hidden, m.created_at, m.is_action, "
            "c.id AS ch_id "
            "FROM messages m JOIN channels c ON c.id = m.channel_id "
            "WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found")
        if msg["user_id"] != session["user_id"]:
            raise HTTPException(403, "Not allowed")
        if msg["hidden"]:
            raise HTTPException(400, "Cannot edit a deleted message")
        if has_attachment_marker(msg["content"]) or has_attachment_marker(content):
            raise HTTPException(400, "Cannot edit an attachment message")
        if int(time.time()) - msg["created_at"] > EDIT_WINDOW_SECONDS:
            raise HTTPException(400, "Edit window expired")
        channel_id = msg["ch_id"]
        old_content = msg["content"]
    edited_at = edit_message(message_id, content)
    await ws_manager.broadcast_to_channel_members(channel_id, {
        "type": "edit",
        "data": {"message_id": message_id, "channel_id": channel_id, "content": content, "edited_at": edited_at},
    })
    # The card that was attached to the old text is now lying about the new
    # text, so re-resolve it whenever the first link changed. Dropping the
    # link entirely has to clear the card too, hence the explicit null push.
    old_urls = extract_urls(old_content)
    new_urls = extract_urls(content)
    old_first = old_urls[0] if old_urls else None
    new_first = new_urls[0] if new_urls else None
    if old_first != new_first and not msg["is_action"]:
        clear_og_data(message_id)
        await ws_manager.broadcast_to_channel_members(channel_id, {"type": "message_og", "data": {"id": message_id, "og_data": None}})
        if new_first:
            asyncio.create_task(_enrich_og_and_broadcast(message_id, channel_id, new_first))
    return {"ok": True, "content": content, "edited_at": edited_at}

@router.post("/api/chat/messages/{message_id}/hide")
async def hide_message_route(message_id: int, session: dict = Depends(get_session_user)):
    from core.auth import require_admin_or_mod
    with db() as conn:
        # ponytail: narrow select. The route only checks author and
        # channel; the message body, OG data, forwarded_from fields
        # are irrelevant for a hide.
        msg = conn.execute(
            "SELECT m.user_id, c.id AS ch_id "
            "FROM messages m JOIN channels c ON c.id = m.channel_id "
            "WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found")
        channel_id = msg["ch_id"]
        is_admin = require_admin_or_mod(channel_id, session["user_id"], conn)
        is_author = msg["user_id"] == session["user_id"]
        if not is_admin and not is_author:
            raise HTTPException(403, "Not allowed")
        hide_message(message_id)
    await ws_manager.broadcast_to_channel_members(channel_id, {"type": "hide", "data": {"message_id": message_id, "channel_id": channel_id}})
    return {"ok": True}

@router.delete("/api/chat/messages/{message_id}")
async def delete_message_route(message_id: int, session: dict = Depends(get_session_user)):
    from core.auth import require_admin_or_mod
    with db() as conn:
        # ponytail: same shape as hide. Only author + channel.
        msg = conn.execute(
            "SELECT m.user_id, c.id AS ch_id "
            "FROM messages m JOIN channels c ON c.id = m.channel_id "
            "WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found")
        channel_id = msg["ch_id"]
        is_admin = require_admin_or_mod(channel_id, session["user_id"], conn)
        is_author = msg["user_id"] == session["user_id"]
        if not is_admin and not is_author:
            raise HTTPException(403, "Not allowed")
        delete_message(message_id)
    await ws_manager.broadcast_to_channel_members(channel_id, {"type": "delete", "data": {"message_id": message_id, "channel_id": channel_id}})
    return {"ok": True}

@router.post("/api/chat/messages/{message_id}/forward")
async def forward_message_route(message_id: int, req: ForwardRequest, session: dict = Depends(get_session_user)):
    if req.target_user_id is not None:
        raise HTTPException(501, "p2p forwarding not implemented yet")
    from repositories.channels import get_channel
    target_ch = get_channel(req.target_channel_id)
    if not target_ch:
        raise HTTPException(404, "Target channel not found")
    with db() as conn:
        # ponytail: only the columns this gate reads. The full row
        # (content, OG, forwarded_from, reply_to, edited_at) is
        # loaded by repositories.messages.forward_message — the
        # router only needs hidden + channel_id to enforce policy.
        orig = conn.execute(
            "SELECT m.hidden, m.channel_id, m.user_id "
            "FROM messages m WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if not orig:
            raise HTTPException(404, "Original message not found")
        if orig["hidden"]:
            raise HTTPException(400, "Cannot forward a hidden or deleted message")
        # ponytail: every user is in every channel, so the source-
        # channel check below always passes. Kept as a documented
        # no-op to surface a future re-permissioning in code review
        # if is_member ever stops returning True.
        if not is_member(orig["channel_id"], session["user_id"]):
            raise HTTPException(403, "Not a member of the source channel")
        target_member = conn.execute(
            "SELECT muted, role FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (req.target_channel_id, session["user_id"]),
        ).fetchone()
        if not target_member:
            raise HTTPException(403, "Not a member of the target channel")
        if target_member["muted"] and target_member["role"] not in ("admin", "mod"):
            raise HTTPException(403, "Estás silenciado en el canal destino")
    new_msg = forward_message(
        message_id,
        req.target_channel_id,
        session["user_id"],
        session_display_name=session.get("display_name", ""),
        session_email=session.get("email", ""),
    )
    payload = {"type": "message", "data": new_msg}
    await ws_manager.broadcast_to_channel_members(req.target_channel_id, payload, exclude={session["user_id"]})
    with db() as conn:
        member_ids = [
            r["user_id"] for r in conn.execute(
                "SELECT user_id FROM channel_members WHERE channel_id = ?",
                (req.target_channel_id,),
            ).fetchall()
        ]
    online_members = [uid for uid in member_ids if uid != session["user_id"] and ws_manager.is_online(uid)]
    delivered_to = mark_delivered(new_msg["id"], online_members)
    if delivered_to:
        await ws_manager.broadcast_to_channel_members(req.target_channel_id, {
            "type": "message_delivered",
            "data": {
                "message_id": new_msg["id"],
                "channel_id": req.target_channel_id,
                "user_ids": delivered_to,
            },
        }, exclude=set())
    asyncio.create_task(send_to_kv("message.new", {**new_msg}))
    urls = extract_urls(new_msg.get("content", ""))
    if urls:
        asyncio.create_task(_enrich_og_and_broadcast(new_msg["id"], req.target_channel_id, urls[0]))
    return new_msg
