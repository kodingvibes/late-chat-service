import os
import pytest
import importlib


@pytest.mark.skip(reason="conflicts with autouse tmp_db fixture that sets env vars")
def test_default_sqlite_path(monkeypatch):
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    from core import config
    importlib.reload(config)
    assert config.SQLITE_PATH == "/data/chat-bridge.db"


@pytest.mark.skip(reason="conflicts with autouse tmp_db fixture")
def test_default_attachment_dir(monkeypatch):
    monkeypatch.delenv("ATTACHMENT_DIR", raising=False)
    from core import config
    importlib.reload(config)
    assert config.ATTACHMENT_DIR == "/var/lib/late-attachments"


@pytest.mark.skip(reason="conflicts with autouse tmp_db fixture")
def test_default_max_attachment_bytes(monkeypatch):
    monkeypatch.delenv("MAX_ATTACHMENT_BYTES", raising=False)
    from core import config
    importlib.reload(config)
    assert config.MAX_ATTACHMENT_BYTES == 50 * 1024 * 1024


def test_default_ttl_days(monkeypatch):
    monkeypatch.delenv("ATTACHMENT_TTL_DAYS", raising=False)
    from core import config
    importlib.reload(config)
    assert config.ATTACHMENT_TTL_DAYS == 7


def test_shared_internal_secret_falls_back_to_sso(monkeypatch):
    monkeypatch.setenv("SSO_BRIDGE_SECRET", "sso-secret")
    monkeypatch.delenv("SHARED_INTERNAL_SECRET", raising=False)
    from core import config
    importlib.reload(config)
    assert config.SHARED_INTERNAL_SECRET == "sso-secret"
