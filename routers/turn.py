from fastapi import APIRouter
from core.config import TURN_URL, TURN_USERNAME, TURN_CREDENTIAL

router = APIRouter()


@router.get("/api/chat/turn-config")
async def turn_config():
    """Return TURN server credentials for WebRTC.

    The frontend fetches this on first voice-join and merges the
    result into its ICE servers list. If no TURN server is configured
    (env vars empty), the response is an empty list and the client
    falls back to STUN-only.
    """
    if not TURN_URL:
        return {"servers": []}
    return {
        "servers": [
            {
                "urls": TURN_URL,
                "username": TURN_USERNAME,
                "credential": TURN_CREDENTIAL,
            },
        ],
    }
