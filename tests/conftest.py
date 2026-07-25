import os
import sys
import json
import time
import sqlite3
import pytest
import respx
import jwt
import httpx
from pathlib import Path
from httpx import AsyncClient, ASGITransport

os.environ["SSO_BRIDGE_SECRET"] = "test-secret-key-for-testing"
os.environ["SQLITE_PATH"] = ":memory:"
os.environ["KV_WEBHOOK_URL"] = ""
os.environ["KV_WEBHOOK_SECRET"] = ""
os.environ["SHARED_INTERNAL_SECRET"] = "test-secret-key-for-testing"
os.environ["ATTACHMENT_DIR"] = "/tmp/late-test-attachments"
os.environ["MAX_ATTACHMENT_BYTES"] = str(5 * 1024 * 1024)
os.environ["ATTACHMENT_TTL_DAYS"] = "7"
os.environ["MAX_VOICE_NOTE_BYTES"] = str(10 * 1024 * 1024)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import SSO_SECRET
from core.db import db, get_db
from app import app


@pytest.fixture(autouse=True)
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("ATTACHMENT_DIR", str(tmp_path / "attachments"))
    os.makedirs(str(tmp_path / "attachments"), exist_ok=True)
    from core import config
    monkeypatch.setattr(config, "SQLITE_PATH", db_path)
    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path / "attachments"))
    import core.db as db_module
    monkeypatch.setattr(db_module, "SQLITE_PATH", db_path)
    with get_db() as conn:
        from notes_store import init_table
        init_table(conn)
    # ponytail: the user cache is a process-global dict so that
    # message rendering can hit it from any code path. Between tests
    # we want a clean slate — otherwise an entry from a previous
    # test leaks in and the late-auth mock returns whatever it had.
    from services.user_cache import _CACHE
    _CACHE.clear()
    yield
    for p in Path(tmp_path / "attachments").iterdir():
        p.unlink(missing_ok=True)


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _create_test_user(sub, email, name, *, user_id=None, role="user"):
    """Create a row in the local users table and return the user dict.

    The local users table still exists for FK targets (channels,
    messages, etc.). The new chat-bridge schema no longer creates
    it, so for tests we create it on demand here. chat-bridge
    doesn't read from it at runtime — that's all late-auth now —
    but the FK constraints on channels/messages/voice_notes
    still point at users and need a real row to land.
    """
    from core.db import db
    display = email.split("@")[0]
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supabase_sub TEXT UNIQUE NOT NULL,
                email TEXT NOT NULL,
                name TEXT,
                display_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                global_role TEXT NOT NULL DEFAULT 'user'
            )
        """)
        existing = conn.execute(
            "SELECT id, supabase_sub, email, name, display_name, global_role "
            "FROM users WHERE supabase_sub = ?",
            (sub,),
        ).fetchone()
        if existing:
            user_id_db = existing["id"]
            conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (int(time.time()), user_id_db))
            display = existing["display_name"]
            role = existing["global_role"] or role
        else:
            cur = conn.execute(
                "INSERT INTO users (supabase_sub, email, name, display_name, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sub, email, name, display, int(time.time()), int(time.time())),
            )
            user_id_db = cur.lastrowid
        # ponytail: chat-bridge membership invariant — every user
        # is in every channel. We materialize it the moment a test
        # user is created so the various "is this user a member?"
        # guards in voice.py / attachments.py / messages.py see
        # the right row.
        if user_id is not None:
            user_id_db = user_id
        for ch in conn.execute("SELECT id FROM channels").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO channel_members (channel_id, user_id, joined_at) VALUES (?, ?, ?)",
                (ch["id"], user_id_db, int(time.time())),
            )
    return {
        "id": user_id_db,
        "supabase_sub": sub,
        "email": email,
        "name": name,
        "display_name": display,
        "global_role": role,
    }


@pytest.fixture
def consume_admin_slot(tmp_db, mock_late_auth):
    """Create a dummy user first so user_id=1 gets admin from migration.
    Subsequent users (id>=2) will NOT have admin role by default.
    Also registers the admin user with the late-auth mock so the
    chat-bridge code path can return their session."""
    admin = _create_test_user(
        "__admin_consumer__",
        "admin-consumer@example.com",
        "Admin Consumer",
        role="super_admin",
    )
    assert admin["id"] == 1, f"consume_admin_slot must run first; got id={admin['id']}"
    admin_session = {**admin, "global_role": "super_admin"}
    mock_late_auth.register("consume-admin-slot-token", admin_session)
    return admin["id"]


@pytest.fixture(autouse=True)
def mock_late_auth():
    """Mock every late-auth /api/auth/* call the chat-bridge makes.

    The mock is dynamic: any session_id is accepted, and the user
    returned comes from a test-managed dict keyed by session_id.
    Tests should call `mock_late_auth.register(session_id, user)` to
    enroll a session, or rely on the auto-register helper from
    `make_session`.
    """
    import respx
    from core.config import LATE_AUTH_URL

    state = {"sessions": {}, "next_id": [1], "users": {}}

    def get_user_for_email(email: str) -> dict | None:
        for u in state["users"].values():
            if u.get("email", "").lower() == email.lower():
                return u
        return None

    with respx.mock(assert_all_called=False) as respx_mock:
        def validate_handler(request):
            sid = request.headers.get("X-Session-Id")
            user = state["sessions"].get(sid)
            if not user:
                return httpx.Response(401, json={"detail": "Invalid or expired session"})
            return httpx.Response(200, json={
                "valid": True,
                "user": {**user, "user_id": user.get("id")},
            })

        def search_handler(request):
            q = (request.url.params.get("q") or "").lower()
            rows = [
                {**u, "id": u["id"], "user_id": u["id"]}
                for u in state["users"].values()
                if q in u.get("display_name", "").lower() or q in u.get("email", "").lower()
            ][: int(request.url.params.get("limit", 10))]
            return httpx.Response(200, json=rows)

        def by_email_handler(request):
            email = request.url.params.get("email", "").lower()
            u = get_user_for_email(email)
            if not u:
                return httpx.Response(404, json={"detail": "user not found"})
            return httpx.Response(200, json={"user": {**u, "id": u["id"]}})

        def by_id_handler(request):
            uid = int(request.url.path.rstrip("/").rsplit("/", 1)[-1])
            u = state["users"].get(uid)
            if not u:
                return httpx.Response(404, json={"detail": "user not found"})
            return httpx.Response(200, json={**u, "id": u["id"]})

        def batch_handler(request):
            ids = [int(x) for x in request.url.params.getlist("id")]
            rows = [state["users"][i] for i in ids if i in state["users"]]
            return httpx.Response(200, json={"users": [{**u, "id": u["id"]} for u in rows]})

        respx_mock.get(f"{LATE_AUTH_URL}/api/auth/validate").mock(side_effect=validate_handler)
        respx_mock.get(f"{LATE_AUTH_URL}/api/auth/users/search").mock(side_effect=search_handler)
        respx_mock.get(f"{LATE_AUTH_URL}/api/auth/users/by-email").mock(side_effect=by_email_handler)
        # The /api/auth/users/{id} route is dynamic; respx supports
        # a regex pattern via `url__regex` in newer versions but to
        # keep the test dep minimal, register the batch endpoint and
        # a wildcard for the per-id path.
        respx_mock.get(url__regex=r".*/api/auth/users/\d+$").mock(side_effect=by_id_handler)
        respx_mock.get(f"{LATE_AUTH_URL}/api/auth/users/batch").mock(side_effect=batch_handler)

        class _Mock:
            def register(self, session_id: str, user: dict):
                state["sessions"][session_id] = user
                # Mirror into the user directory so search/by-email
                # lookups against this user work too.
                if "id" in user:
                    state["users"][user["id"]] = user

            def allocate_id(self) -> int:
                n = state["next_id"][0]
                state["next_id"][0] += 1
                return n

        yield _Mock()


@pytest.fixture
def make_session(mock_late_auth, tmp_db):
    created = []

    def _make(sub="test-sub", email="test@example.com", name="Test User", user_id=None):
        from core.auth import generate_session_id
        from services.user_cache import prime
        user = _create_test_user(sub, email, name)
        user_id_eff = user["id"] if user_id is None else user_id
        session_id = generate_session_id()
        mock_late_auth.register(
            session_id,
            {**user, "id": user_id_eff, "global_role": user.get("global_role", "user")},
        )
        # ponytail: prime the in-process user cache so message
        # rendering doesn't need a late-auth round-trip for the
        # requester's own user (the common case in tests).
        prime(user_id_eff, display_name=user.get("display_name", ""), email=user.get("email", ""))
        created.append({**user, "id": user_id_eff})
        return session_id, {**user, "id": user_id_eff}

    yield _make
    with db() as conn:
        for u in created:
            for tbl in ("voice_notes", "reactions", "messages", "attachments", "channel_members"):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (u["id"],))
                except sqlite3.OperationalError:
                    pass
            conn.execute("DELETE FROM channels WHERE created_by = ?", (u["id"],))
            try:
                conn.execute("DELETE FROM users WHERE id = ?", (u["id"],))
            except sqlite3.OperationalError:
                pass


@pytest.fixture
def auth_headers(make_session):
    session_id, user = make_session()
    return {"Authorization": f"Bearer {session_id}"}, user


@pytest.fixture
def make_jwt():
    def _make(sub="test-sub", email="test@example.com", name="Test User", **extra):
        payload = {
            "sub": sub,
            "email": email,
            "name": name,
            "aud": "late.sh",
            "iss": "kodingvibes.com",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            **extra,
        }
        return jwt.encode(payload, SSO_SECRET, algorithm="HS256")

    return _make


@pytest.fixture
def mock_kv_webhook():
    with respx.mock(assert_all_called=False) as respx_mock:
        yield respx_mock


@pytest.fixture
def mock_httpx_og():
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get("https://example.com").respond(
            status_code=200,
            headers={"content-type": "text/html"},
            content=b"<html><head><title>Test</title><meta property='og:title' content='OG Title'></head></html>",
        )
        respx_mock.get("https://notfound.example.com").respond(status_code=404)
        respx_mock.get("https://binary.example.com").respond(
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01\x02",
        )
        yield respx_mock


@pytest.fixture(autouse=True)
def mock_subprocess(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    class MockCompletedProcess:
        returncode = 0
        stdout = b""
        stderr = b""

    def mock_run(*args, **kwargs):
        return MockCompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_run)


@pytest.fixture
def mock_ffmpeg_fail(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
