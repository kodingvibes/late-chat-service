import time
from fastapi import APIRouter, Depends, HTTPException
from core.auth import get_session_user
from schemas.chat import BuzzRequest
from services.broadcaster import ws_manager
from core.db import db

router = APIRouter()
_last_buzz_at: dict[str, float] = {}

@router.post("/api/chat/buzz")
async def buzz(req: BuzzRequest, session: dict = Depends(get_session_user)):
    if req.target_user_id == session["user_id"]:
        raise HTTPException(400, "Cannot buzz yourself")
    with db() as conn:
        # ponytail: every user is in every channel, so the caller row
        # is always present. Kept as a presence sanity-check, but it
        # no longer gates the buzz — the only real check left is the
        # target's online state and the rate limiter.
        target = conn.execute("SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?", (req.channel_id, req.target_user_id)).fetchone()
        if not target:
            raise HTTPException(404, "Target user not in channel")
    if not ws_manager.is_online(req.target_user_id):
        raise HTTPException(404, "Target user is not online")
    rate_key = f"{session['user_id']}:{req.target_user_id}"
    last = _last_buzz_at.get(rate_key, 0.0)
    if time.time() - last < 15:
        raise HTTPException(429, "Wait 15 seconds before buzzing again")
    _last_buzz_at[rate_key] = time.time()
    payload = {
        "type": "buzz", "data": {
            "from_user_id": session["user_id"], "from_display_name": session["display_name"],
            "channel_id": req.channel_id, "timestamp": int(time.time()),
        },
    }
    await ws_manager.send_to_user(req.target_user_id, payload)
    await ws_manager.send_to_user(session["user_id"], payload)
    return {"ok": True}
