import os
import logging
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from core.auth import get_session_user
from core.db import db, get_db
from services.broadcaster import ws_manager as ws_mgr
from notes_store import insert_note, get_note

log = logging.getLogger("chat-bridge.voice")
router = APIRouter(prefix="/api/chat")


@router.post("/voice-notes")
async def upload_voice_note(
    channel_id: int = Form(...),
    duration_ms: int = Form(...),
    amount: int = Form(50),
    file: UploadFile = File(...),
    session: dict = Depends(get_session_user),
):
    if not file:
        raise HTTPException(400, "No file provided")

    data = await file.read()
    max_bytes = int(os.environ.get("MAX_VOICE_NOTE_BYTES", str(10 * 1024 * 1024)))
    if len(data) > max_bytes:
        raise HTTPException(413, "Voice note too large")

    mime = file.content_type or "audio/webm"

    with get_db() as conn:
        member = conn.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (channel_id, session["user_id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member")

        note = insert_note(conn, session["user_id"], channel_id, duration_ms, amount, data, mime)
        note["display_name"] = session["display_name"]

    await ws_mgr.broadcast_to_channel_members(
        channel_id,
        {"type": "voice_note", "data": note},
        exclude={session["user_id"]},
    )
    return note


@router.get("/voice-notes/{note_id}")
async def download_voice_note(
    note_id: str,
    session: dict = Depends(get_session_user),
):
    with get_db() as conn:
        note = get_note(conn, note_id)
        if not note:
            raise HTTPException(404, "Voice note not found")
        member = conn.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (note["channel_id"], session["user_id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member of the source channel")

    return FileResponse(
        note["storage_path"],
        media_type=note["mime"],
        filename=f"voice-{note_id}.webm",
    )


@router.get("/voice-notes/{note_id}/meta")
async def voice_note_meta(
    note_id: str,
    session: dict = Depends(get_session_user),
):
    with get_db() as conn:
        note = get_note(conn, note_id)
        if not note:
            raise HTTPException(404, "Voice note not found")
        member = conn.execute(
            "SELECT 1 FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (note["channel_id"], session["user_id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Not a member of the source channel")
        author = conn.execute(
            "SELECT display_name FROM users WHERE id = ?",
            (note["user_id"],),
        ).fetchone()

    result = {k: v for k, v in note.items() if k != "storage_path"}
    if author:
        result["display_name"] = author["display_name"]
    return result
