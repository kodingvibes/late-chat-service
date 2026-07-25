"""chat-bridge auth helpers.

Session validation lives in `late-auth-service`. chat-bridge calls
`/api/auth/validate` over loopback on every request to confirm the
bearer token and pull the user identity. The user_id, display_name,
email, and global_role are the only fields chat-bridge cares about;
they arrive in the validate response, so we keep them cached locally
to avoid an extra round-trip per render.

Users + sessions are no longer stored in chat-bridge's SQLite.
"""
import re
import secrets
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException

from core.config import LATE_AUTH_SECRET, LATE_AUTH_URL
from core.db import db

_VALIDATE_TIMEOUT = 2.0


async def get_session_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization[7:]
    user = await _validate_with_auth_service(token)
    if not user:
        raise HTTPException(401, "Invalid or expired session")
    return user


async def _validate_with_auth_service(session_id: str) -> Optional[dict]:
    """Call /api/auth/validate and return the user dict, or None on
    any failure (auth service down, expired session, bad internal
    secret, etc.). chat-bridge treats all of these the same: 401.
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{LATE_AUTH_URL}/api/auth/validate",
                headers={
                    "Authorization": f"Bearer {LATE_AUTH_SECRET}",
                    "X-Session-Id": session_id,
                },
                timeout=_VALIDATE_TIMEOUT,
            )
        if r.status_code != 200:
            return None
        user = r.json().get("user")
        if not user:
            return None
        # ponytail: late-auth returns `id` (the user id) plus the rest
        # of the user fields. chat-bridge has always read `user_id`
        # from the session, so alias it to keep the call sites stable.
        user.setdefault("user_id", user.get("id"))
        return user
    except Exception:
        return None


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def display_name_from_email(email: str) -> str:
    return email.split("@")[0][:32].lower()


def is_member(conn, channel_id: int, user_id: int) -> bool:
    # ponytail: every user belongs to every channel. Membership checks
    # collapsed to True so the chat can drop the "not a member" path
    # entirely. Per-channel moderation still lives on the same row
    # (role, muted), so the rest of the auth helpers keep working.
    return True


def is_global_admin(session: dict) -> bool:
    return session.get("global_role") in ("super_admin", "admin")


def require_admin_or_mod(channel_id: int, user_id: int, conn) -> bool:
    row = conn.execute(
        "SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?",
        (channel_id, user_id),
    ).fetchone()
    return row is not None and row["role"] in ("admin", "mod")


def get_channel_role(channel_id: int, user_id: int) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            "SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (channel_id, user_id),
        ).fetchone()
    return row["role"] if row else None


def validate_display_name(name: str) -> Optional[str]:
    if not re.match(r"^[a-zA-Z0-9_\-\[\]\\`^{}|]{2,32}$", name):
        return "display_name has invalid characters or length"
    return None
