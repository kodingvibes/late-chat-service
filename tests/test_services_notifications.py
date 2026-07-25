import json
import hmac
import hashlib
import time
import pytest
from unittest.mock import AsyncMock, patch


class TestSendToKv:
    async def test_noop_when_url_empty(self):
        from services.notifications import send_to_kv
        with patch("services.notifications.KV_WEBHOOK_URL", ""):
            result = await send_to_kv("test.event", {"key": "value"})
            assert result is None

    async def test_sends_with_signature(self):
        from services.notifications import send_to_kv
        with patch("services.notifications.KV_WEBHOOK_URL", "https://kv.example.com/webhook"):
            with patch("services.notifications.KV_WEBHOOK_SECRET", "secret"):
                with patch("services.notifications.httpx.AsyncClient") as mock_client:
                    mock_post = AsyncMock()
                    mock_client.return_value.__aenter__.return_value.post = mock_post
                    mock_post.return_value.status_code = 200
                    await send_to_kv("test.event", {"key": "value"})
                    mock_post.assert_called_once()
                    call_args = mock_post.call_args[0]
                    assert call_args[0] == "https://kv.example.com/webhook"
                    call_kwargs = mock_post.call_args[1]
                    assert "X-Chat-Signature" in call_kwargs["headers"]
                    body = call_kwargs["content"]
                    assert b"test.event" in body

    async def test_logs_warning_on_4xx(self, caplog):
        from services.notifications import send_to_kv
        with patch("services.notifications.KV_WEBHOOK_URL", "https://kv.example.com/webhook"):
            with patch("services.notifications.KV_WEBHOOK_SECRET", "secret"):
                with patch("services.notifications.httpx.AsyncClient") as mock_client:
                    mock_post = AsyncMock()
                    mock_client.return_value.__aenter__.return_value.post = mock_post
                    mock_post.return_value.status_code = 400
                    mock_post.return_value.text = "Bad request"
                    await send_to_kv("test.event", {"key": "value"})
                    assert any("400" in rec.message for rec in caplog.records)

    async def test_logs_error_on_exception(self, caplog):
        from services.notifications import send_to_kv
        with patch("services.notifications.KV_WEBHOOK_URL", "https://kv.example.com/webhook"):
            with patch("services.notifications.KV_WEBHOOK_SECRET", "secret"):
                with patch("services.notifications.httpx.AsyncClient") as mock_client:
                    mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("Network error")
                    await send_to_kv("test.event", {"key": "value"})
                    assert any("Network error" in rec.message for rec in caplog.records)
