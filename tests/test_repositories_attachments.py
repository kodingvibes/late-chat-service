import time
import pytest
from core.db import db
from repositories.attachments import create_attachment, get_attachment, get_attachment_meta, get_attachments_meta_bulk, delete_expired
from repositories.channels import create_channel
from tests.conftest import _create_test_user  # type: ignore


@pytest.fixture
def user_and_channel():
    user = _create_test_user("att-test-user", "att@example.com", "Att User")
    ch = create_channel("#attachments", "Attachments test", True, user["id"])
    return user, ch


def test_create_and_get_attachment(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("att1", ch["id"], user["id"], "image", "photo.jpg", "image/jpeg", 1024, "/tmp/test.jpg", now + 86400)
    row = get_attachment("att1")
    assert row is not None
    assert row["kind"] == "image"
    assert row["filename"] == "photo.jpg"


def test_get_attachment_with_extension(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("att2", ch["id"], user["id"], "audio", "song.mp3", "audio/mpeg", 2048, "/tmp/song.mp3", now + 86400)
    row = get_attachment("att2.mp3")
    assert row is not None
    assert row["id"] == "att2"


def test_get_attachment_not_found():
    assert get_attachment("nonexistent") is None


def test_get_attachment_meta(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("att3", ch["id"], user["id"], "video", "clip.mp4", "video/mp4", 4096, "/tmp/clip.mp4", now + 86400)
    meta = get_attachment_meta("att3")
    assert meta is not None
    assert "storage_path" not in meta
    assert meta["kind"] == "video"


def test_get_attachment_meta_includes_dimensions(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("att4", ch["id"], user["id"], "image", "wide.png", "image/png", 1024, "/tmp/wide.png", now + 86400, width=1920, height=1080)
    meta = get_attachment_meta("att4")
    assert meta is not None
    assert meta["width"] == 1920
    assert meta["height"] == 1080


def test_create_attachment_with_dimensions(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("att5", ch["id"], user["id"], "image", "tall.jpg", "image/jpeg", 2048, "/tmp/tall.jpg", now + 86400, width=600, height=1200)
    row = get_attachment("att5")
    assert row["width"] == 600
    assert row["height"] == 1200


def test_get_attachments_meta_bulk_empty():
    assert get_attachments_meta_bulk([]) == {}


def test_get_attachments_meta_bulk_returns_match(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("bulk1", ch["id"], user["id"], "image", "a.png", "image/png", 1, "/tmp/a", now + 86400, width=100, height=200)
    create_attachment("bulk2", ch["id"], user["id"], "image", "b.png", "image/png", 1, "/tmp/b", now + 86400, width=300, height=400)
    metas = get_attachments_meta_bulk(["bulk1", "bulk2", "missing"])
    assert set(metas.keys()) == {"bulk1", "bulk2"}
    assert metas["bulk1"]["width"] == 100
    assert metas["bulk1"]["height"] == 200
    assert metas["bulk2"]["width"] == 300
    assert metas["bulk2"]["height"] == 400


def test_get_attachments_meta_bulk_dedupes_and_strips_extension(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("dedup", ch["id"], user["id"], "image", "x.png", "image/png", 1, "/tmp/x", now + 86400, width=10, height=20)
    metas = get_attachments_meta_bulk(["dedup", "dedup.png", "dedup"])
    assert list(metas.keys()) == ["dedup"]
    assert metas["dedup"]["width"] == 10


def test_delete_expired(user_and_channel):
    user, ch = user_and_channel
    now = int(time.time())
    create_attachment("exp1", ch["id"], user["id"], "file", "old.txt", "text/plain", 100, "/tmp/old.txt", now - 100)
    create_attachment("exp2", ch["id"], user["id"], "file", "new.txt", "text/plain", 100, "/tmp/new.txt", now + 86400)
    expired = delete_expired()
    expired_ids = [e["id"] for e in expired]
    assert "exp1" in expired_ids
    assert "exp2" not in expired_ids
    assert get_attachment("exp1") is None
    assert get_attachment("exp2") is not None
