import os
import time
import pytest
from pathlib import Path
from core.db import db


class TestUploadAttachment:
    async def test_upload_no_file(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", headers=headers)
        assert r.status_code in (400, 422)

    async def test_upload_not_member(self, client, auth_headers):
        # ponytail: every user is in every channel, so "not a member"
        # on a non-existent channel id is a plain 404 (channel not
        # found), not a 403. Reusing the same auth_headers against an
        # id that doesn't exist now returns 404.
        headers, user = auth_headers
        r = await client.post("/api/chat/channels/99999/attachments", files={"file": ("test.txt", b"hello")}, headers=headers)
        assert r.status_code == 404

    async def test_upload_too_large(self, client, auth_headers, monkeypatch):
        import routers.attachments as att_router
        monkeypatch.setattr(att_router, "MAX_ATTACHMENT_BYTES", 10)
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("big.txt", b"x" * 100)}, headers=headers)
        assert r.status_code == 413

    async def test_upload_success(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("photo.jpg", b"fake-image-data", "image/jpeg")}, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["kind"] == "image"
        assert data["filename"] == "photo.jpg"
        assert "url" in data

    async def test_upload_audio(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("song.mp3", b"fake-audio", "audio/mpeg")}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kind"] == "audio"

    async def test_upload_video(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("clip.mp4", b"fake-video", "video/mp4")}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kind"] == "video"

    async def test_upload_document(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kind"] == "document"

    async def test_upload_ffmpeg_fallback(self, client, auth_headers, mock_ffmpeg_fail):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("img.png", b"fake-png", "image/png")}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kind"] == "image"


class TestGetAttachment:
    async def test_get_not_found(self, client):
        r = await client.get("/api/chat/attachments/nonexistent")
        assert r.status_code == 404

    async def test_get_success(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        upload = (await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("test.txt", b"hello", "text/plain")}, headers=headers)).json()
        r = await client.get(upload["url"])
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]

    async def test_get_meta(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        upload = (await client.post(f"/api/chat/channels/{ch['id']}/attachments", files={"file": ("meta.txt", b"meta", "text/plain")}, headers=headers)).json()
        att_id = upload["url"].split("/")[-1].split(".")[0]
        r = await client.get(f"/api/chat/attachments/{att_id}/meta")
        assert r.status_code == 200
        assert r.json()["filename"] == "meta.txt"

    async def test_get_meta_not_found(self, client):
        r = await client.get("/api/chat/attachments/nonexistent/meta")
        assert r.status_code == 404
