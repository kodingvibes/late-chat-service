import os
import secrets
import subprocess
import time
import mimetypes
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from core.auth import get_session_user
from core.config import ATTACHMENT_DIR, MAX_ATTACHMENT_BYTES, ATTACHMENT_TTL_DAYS
from repositories.channels import is_member
from repositories.attachments import create_attachment, get_attachment, get_attachment_meta
from core.db import db

router = APIRouter()


def _probe_dimensions(path: str) -> tuple[int | None, int | None]:
    """Run ffprobe on `path` and return (width, height) of the
    first video stream, or (None, None) when ffprobe isn't
    available, the file isn't a recognized media, or the stream
    has no width/height (e.g. audio-only). Used to populate
    attachment.width/height so the chat client can reserve a
    placeholder of the exact size. """
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                path,
            ],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        return (None, None)
    if out.returncode != 0:
        return (None, None)
    try:
        data = json.loads(out.stdout.decode("utf-8", errors="ignore") or "{}")
    except Exception:
        return (None, None)
    streams = data.get("streams") or []
    if not streams:
        return (None, None)
    s = streams[0]
    w = s.get("width")
    h = s.get("height")
    if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
        return (None, None)
    return (w, h)

@router.post("/api/chat/channels/{channel_id}/attachments")
async def upload_attachment(request: Request, channel_id: int, file: UploadFile = File(...), session: dict = Depends(get_session_user)):
    if not file:
        raise HTTPException(400, "No file provided")
    # ponytail: every user is in every channel, so "is_member" is always
    # True and we no longer gate uploads on membership. The channel must
    # still exist though — a typo'd id would otherwise blow up on the
    # FK constraint in the attachments INSERT.
    from repositories.channels import get_channel
    if not get_channel(channel_id):
        raise HTTPException(404, "Channel not found")
    contents = await file.read()
    if len(contents) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, f"File too large. Max {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB")
    filename = file.filename or "untitled"
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if mime.startswith("image/"): kind = "image"
    elif mime.startswith("audio/"): kind = "audio"
    elif mime.startswith("video/"): kind = "video"
    elif mime in ("application/pdf",) or mime.startswith("text/"): kind = "document"
    else: kind = "file"
    fid = secrets.token_urlsafe(12)
    ext = Path(filename).suffix or ""
    storage_filename = f"{fid}{ext}"
    storage_path = os.path.join(ATTACHMENT_DIR, storage_filename)
    now = int(time.time())
    expires_at = now + ATTACHMENT_TTL_DAYS * 86400
    os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    try:
        with open(storage_path, "wb") as f:
            f.write(contents)
    except OSError as e:
        raise HTTPException(500, f"Failed to write file: {e}")
    size_bytes = len(contents)
    width: int | None = None
    height: int | None = None
    if kind == "image":
        # Probe the original first — the user uploaded these
        # pixels, and if ffprobe fails on the source we want to
        # fall back to no dimensions rather than throwing.
        width, height = _probe_dimensions(storage_path)
        webp_path = storage_path + ".webp"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", storage_path, "-c:v", "libwebp", "-quality", "80", "-preset", "picture", webp_path],
                           capture_output=True, timeout=30)
            webp_size = os.path.getsize(webp_path)
            if webp_size < len(contents):
                os.replace(webp_path, storage_path)
                ext = ".webp"
                mime = "image/webp"
                size_bytes = webp_size
            else:
                os.remove(webp_path)
        except Exception:
            if os.path.exists(webp_path):
                os.remove(webp_path)
        # Re-probe after the webp rewrite so the dimensions match
        # the file we're actually going to serve. Falls back to
        # the pre-rewrite values if the second probe fails.
        if width is None or height is None:
            width, height = _probe_dimensions(storage_path)
        else:
            w2, h2 = _probe_dimensions(storage_path)
            if w2 and h2:
                width, height = w2, h2
    elif kind == "video":
        width, height = _probe_dimensions(storage_path)
        compressed_path = storage_path + ".compressed.mp4"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", storage_path,
                "-vf", "scale=min(1280,iw):min(720,ih):force_original_aspect_ratio=decrease,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-b:a", "64k", "-ac", "2",
                "-movflags", "+faststart", "-threads", "0", compressed_path],
                capture_output=True, timeout=180)
            compressed_size = os.path.getsize(compressed_path)
            if compressed_size < len(contents):
                os.replace(compressed_path, storage_path)
                size_bytes = compressed_size
            else:
                os.remove(compressed_path)
        except Exception:
            if os.path.exists(compressed_path):
                os.remove(compressed_path)
    create_attachment(fid, channel_id, session["user_id"], kind, filename, mime, size_bytes, storage_path, expires_at, width, height)
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/api/chat/attachments/{fid}{ext}"
    return {"id": fid, "url": url, "kind": kind, "filename": filename, "mime": mime, "size_bytes": size_bytes, "width": width, "height": height, "created_at": now, "expires_at": expires_at}

@router.get("/api/chat/attachments/{attachment_id}")
async def get_attachment_route(attachment_id: str):
    base_id = attachment_id.split(".")[0]
    row = get_attachment(base_id)
    if not row:
        raise HTTPException(404, "Attachment not found")
    if row["expires_at"] < int(time.time()):
        raise HTTPException(410, "Attachment expired")
    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(path=str(path), media_type=row["mime"], filename=row["filename"],
        headers={"Cache-Control": "private, max-age=3600", "X-Attachment-Expires": str(row["expires_at"])})

@router.get("/api/chat/attachments/{attachment_id}/meta")
async def get_attachment_meta_route(attachment_id: str):
    base_id = attachment_id.split(".")[0]
    row = get_attachment_meta(base_id)
    if not row:
        raise HTTPException(404, "Attachment not found")
    if row["expires_at"] < int(time.time()):
        raise HTTPException(410, "Attachment expired")
    return dict(row)
