import json
import hmac
import hashlib
import pytest
from core.config import KV_WEBHOOK_SECRET as _dummy


class TestWebhook:
    @pytest.fixture(autouse=True)
    def enable_webhook(self, monkeypatch):
        monkeypatch.setattr("routers.webhook.KV_WEBHOOK_SECRET", "webhook-secret")

    async def test_webhook_disabled(self, client, monkeypatch):
        monkeypatch.setattr("routers.webhook.KV_WEBHOOK_SECRET", "")
        r = await client.post("/api/chat/webhook/from-kv", json={"event": "message.new", "data": {}})
        assert r.status_code == 403

    async def test_invalid_signature(self, client):
        r = await client.post(
            "/api/chat/webhook/from-kv",
            json={"event": "message.new", "data": {}},
            headers={"x-kv-signature": "invalid"},
        )
        assert r.status_code == 403

    async def test_valid_signature(self, client):
        payload = {"event": "message.new", "data": {"channel_id": 1, "content": "test"}}
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        r = await client.post(
            "/api/chat/webhook/from-kv",
            json=payload,
            headers={"x-kv-signature": sig},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_unknown_event(self, client):
        payload = {"event": "unknown.event", "data": {}}
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        r = await client.post(
            "/api/chat/webhook/from-kv",
            json=payload,
            headers={"x-kv-signature": sig},
        )
        assert r.status_code == 200
