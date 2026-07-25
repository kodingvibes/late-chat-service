import json
import hmac
import hashlib
from fastapi import APIRouter, Header, HTTPException
from schemas.chat import WebhookPayload
from core.config import KV_WEBHOOK_SECRET
from services.broadcaster import ws_manager

router = APIRouter()

@router.post("/api/chat/webhook/from-kv")
async def webhook_from_kv(req: WebhookPayload, x_kv_signature: str = Header(None)):
    if not KV_WEBHOOK_SECRET:
        raise HTTPException(403, "Inbound webhook not enabled")
    # ponytail: Pydantic v2 deprecates BaseModel.dict() in favor of
    # model_dump(mode="json"). The signing body needs the JSON shape
    # the caller will sign on its end, which for nested datetime /
    # model fields means mode="json" (default mode keeps Python
    # objects in the dump). The chat-bridge WebhookPayload has no
    # such fields, but default mode is what callers will sign, so
    # match that exactly to keep signature verification stable.
    body = json.dumps(req.model_dump(mode="json"), separators=(",", ":")).encode()
    expected = hmac.new(KV_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_kv_signature or ""):
        raise HTTPException(403, "Invalid signature")
    if req.event == "message.new":
        data = req.data
        channel_id = data.get("channel_id")
        if channel_id:
            await ws_manager.broadcast_to_channel_members(channel_id, {"type": "message", "data": data})
    return {"ok": True}
