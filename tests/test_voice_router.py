import os
import pytest
from pathlib import Path


class TestUploadVoiceNote:
    async def test_upload_no_file(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post("/api/chat/voice-notes", data={"channel_id": ch["id"], "duration_ms": 1000, "amount": 50}, headers=headers)
        assert r.status_code in (400, 422)

    async def test_upload_not_member(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/voice-notes", data={"channel_id": 99999, "duration_ms": 1000, "amount": 50}, files={"file": ("voice.webm", b"audio-data", "audio/webm")}, headers=headers)
        assert r.status_code == 403

    async def test_upload_success(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post("/api/chat/voice-notes", data={"channel_id": ch["id"], "duration_ms": 1000, "amount": 50}, files={"file": ("voice.webm", b"audio-data", "audio/webm")}, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["duration_ms"] == 1000
        assert data["user_id"] == user["id"]
        assert "id" in data

    async def test_upload_too_large(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("MAX_VOICE_NOTE_BYTES", "10")
        import importlib
        from core import config
        importlib.reload(config)
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post("/api/chat/voice-notes", data={"channel_id": ch["id"], "duration_ms": 1000, "amount": 50}, files={"file": ("voice.webm", b"x" * 100, "audio/webm")}, headers=headers)
        assert r.status_code == 413


class TestDownloadVoiceNote:
    async def test_download_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/voice-notes/nonexistent", headers=headers)
        assert r.status_code == 404

    async def test_download_success(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        upload = (await client.post("/api/chat/voice-notes", data={"channel_id": ch["id"], "duration_ms": 1000, "amount": 50}, files={"file": ("voice.webm", b"audio-data", "audio/webm")}, headers=headers)).json()
        r = await client.get(f"/api/chat/voice-notes/{upload['id']}", headers=headers)
        assert r.status_code == 200


class TestVoiceNoteMeta:
    async def test_meta_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/voice-notes/nonexistent/meta", headers=headers)
        assert r.status_code == 404

    async def test_meta_success(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        upload = (await client.post("/api/chat/voice-notes", data={"channel_id": ch["id"], "duration_ms": 1000, "amount": 50}, files={"file": ("voice.webm", b"audio-data", "audio/webm")}, headers=headers)).json()
        r = await client.get(f"/api/chat/voice-notes/{upload['id']}/meta", headers=headers)
        assert r.status_code == 200
        assert r.json()["duration_ms"] == 1000
