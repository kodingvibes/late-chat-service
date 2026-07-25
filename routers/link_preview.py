import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from core.auth import get_session_user
from core.config import LINK_PREVIEW_TTL_SECONDS
from repositories.link_previews import get_cached, set_cached
from services.link_preview import fetch_og

router = APIRouter()


@router.get("/api/chat/unfurl")
async def unfurl_route(url: str, session: dict = Depends(get_session_user)):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "Invalid url")
    cached = get_cached(url)
    if cached and cached["fetched_at"] > int(time.time()) - LINK_PREVIEW_TTL_SECONDS:
        return cached["data"]
    data = await fetch_og(url)
    # Don't persist transient failures (timeouts, 5xx, blocked hosts): a later
    # retry should be able to succeed instead of serving a cached error for the
    # whole TTL window.
    if data.get("kind") != "error":
        set_cached(url, data)
    return data
