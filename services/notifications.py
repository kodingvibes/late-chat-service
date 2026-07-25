import json
import hmac
import hashlib
import time
import logging
import httpx
from core.config import KV_WEBHOOK_URL, KV_WEBHOOK_SECRET

log = logging.getLogger("chat-bridge")

async def send_to_kv(event: str, data: dict):
    if not KV_WEBHOOK_URL:
        return
    payload = {"event": event, "data": data, "ts": int(time.time())}
    body = json.dumps(payload).encode()
    sig = hmac.new(KV_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Chat-Signature": sig}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(KV_WEBHOOK_URL, content=body, headers=headers)
            if r.status_code >= 400:
                log.warning(f"kodingvibes webhook returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"Failed to send webhook to kodingvibes: {e}")
