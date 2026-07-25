from fastapi import APIRouter, Depends, HTTPException
from core.auth import get_session_user, get_channel_role, is_global_admin
from schemas.chat import RoleChangeRequest, MuteRequest
from repositories.members import list_members, change_role, change_mute, get_member
from services.broadcaster import ws_manager

router = APIRouter()

@router.get("/api/chat/channels/{channel_id}/members")
async def list_members_route(channel_id: int, session: dict = Depends(get_session_user)):
    from repositories.channels import get_channel, is_member
    ch = get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "Channel not found")
    if not is_global_admin(session) and not is_member(channel_id, session["user_id"]):
        raise HTTPException(403, "Not a member")
    return list_members(channel_id)

@router.patch("/api/chat/channels/{channel_id}/members/{target_user_id}/role")
async def change_role_route(channel_id: int, target_user_id: int, req: RoleChangeRequest, session: dict = Depends(get_session_user)):
    if session["user_id"] == target_user_id:
        raise HTTPException(400, "Cannot change your own role")
    # Ponytail: platform-level super_admin/admin can change roles in any
    # channel even without a channel_members row. Per-channel admins are
    # scoped to their own channel (unchanged).
    if not is_global_admin(session):
        caller_role = get_channel_role(channel_id, session["user_id"])
        if caller_role != "admin":
            raise HTTPException(403, "Only channel admins can change roles")
    target = get_member(channel_id, target_user_id)
    if not target:
        raise HTTPException(404, "Target user is not a member of this channel")
    valid_roles = {"admin", "mod", None}
    if req.role not in valid_roles:
        raise HTTPException(400, f"Invalid role. Valid: {valid_roles}")
    change_role(channel_id, target_user_id, req.role)
    await ws_manager.broadcast_to_channel_members(channel_id, {
        "type": "member_role_changed",
        "data": {"channel_id": channel_id, "user_id": target_user_id, "role": req.role},
    })
    return {"ok": True, "user_id": target_user_id, "role": req.role}

@router.patch("/api/chat/channels/{channel_id}/members/{target_user_id}/mute")
async def change_mute_route(channel_id: int, target_user_id: int, req: MuteRequest, session: dict = Depends(get_session_user)):
    if session["user_id"] == target_user_id:
        raise HTTPException(400, "Cannot change your own mute status")
    from core.auth import require_admin_or_mod
    from core.db import db
    # Ponytail: super_admin / admin can mute in any channel, same
    # rationale as the role endpoint.
    with db() as conn:
        if not is_global_admin(session) and not require_admin_or_mod(channel_id, session["user_id"], conn):
            raise HTTPException(403, "Only admins and mods can mute members")
        target = conn.execute(
            "SELECT cm.role FROM channel_members cm WHERE cm.channel_id = ? AND cm.user_id = ?",
            (channel_id, target_user_id),
        ).fetchone()
        if not target:
            raise HTTPException(404, "Target user is not a member of this channel")
        if target["role"] in ("admin", "mod"):
            raise HTTPException(400, "Cannot mute an admin or moderator")
    change_mute(channel_id, target_user_id, req.muted)
    await ws_manager.broadcast_to_channel_members(channel_id, {
        "type": "member_muted",
        "data": {"channel_id": channel_id, "user_id": target_user_id, "muted": req.muted},
    })
    return {"ok": True, "user_id": target_user_id, "muted": req.muted}
