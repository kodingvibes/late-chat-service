from fastapi import APIRouter, Depends, HTTPException
from core.auth import get_session_user
from schemas.chat import ReactionRequest
from repositories.reactions import toggle_reaction, ALLOWED_EMOJIS
from repositories.channels import is_member
from services.broadcaster import ws_manager
from core.db import db

router = APIRouter()

@router.post("/api/chat/messages/{message_id}/reactions")
async def toggle_reaction_route(message_id: int, req: ReactionRequest, session: dict = Depends(get_session_user)):
    if req.emoji not in ALLOWED_EMOJIS:
        raise HTTPException(400, "unknown emoji")
    with db() as conn:
        msg = conn.execute("SELECT channel_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "message not found")
        channel_id = msg["channel_id"]
        if not is_member(channel_id, session["user_id"]):
            raise HTTPException(403, "not a member")
    action, reactions = toggle_reaction(message_id, session["user_id"], req.emoji)
    await ws_manager.broadcast_to_channel_members(channel_id, {
        "type": "reaction",
        "data": {
            "message_id": message_id, "channel_id": channel_id,
            "reactions": reactions, "action": action,
            "user_id": session["user_id"], "emoji": req.emoji,
            "display_name": session["display_name"],
        },
    })
    return {"ok": True, "action": action, "reactions": reactions}
