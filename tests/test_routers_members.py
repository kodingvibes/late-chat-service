import pytest


class TestListMembers:
    async def test_list(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.get(f"/api/chat/channels/{ch['id']}/members", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert any(m["id"] == user["id"] for m in data)

    async def test_list_not_member(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/channels/99999/members", headers=headers)
        assert r.status_code == 404


class TestChangeRole:
    async def test_change_role(self, client, consume_admin_slot, make_session, mock_late_auth):
        from core.auth import generate_session_id
        from tests.conftest import _create_test_user  # type: ignore
        admin = _create_test_user(sub="__admin_consumer__", email="admin-consumer@example.com", name="Admin Consumer", user_id=consume_admin_slot)
        # The change_role route checks global_role on the session
        # (via late-auth). Re-register the admin with super_admin.
        admin_session = {**admin, "global_role": "super_admin"}
        admin_token = generate_session_id()
        mock_late_auth.register(admin_token, admin_session)
        headers = {"Authorization": f"Bearer {admin_token}"}
        _, target = make_session("sub-role-target", "target@example.com", "Target")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{target['id']}/role", json={"role": "mod"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["role"] == "mod"

    async def test_change_role_self(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{user['id']}/role", json={"role": "mod"}, headers=headers)
        assert r.status_code == 400

    async def test_change_role_not_admin(self, client, consume_admin_slot, make_session):
        make_session("sub-consumer-role", "consumer@example.com", "Consumer")
        make_session("sub-consumer-role2", "consumer2@example.com", "Consumer2")
        make_session("sub-consumer-role3", "consumer3@example.com", "Consumer3")
        session1, user1 = make_session("sub-not-admin-1", "alice@example.com", "Alice")
        session2, user2 = make_session("sub-not-admin-2", "bob@example.com", "Bob")
        ch = (await client.get("/api/chat/channels", headers={"Authorization": f"Bearer {session1}"})).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{user2['id']}/role", json={"role": "mod"}, headers={"Authorization": f"Bearer {session1}"})
        assert r.status_code == 403

    async def test_change_role_invalid(self, client, consume_admin_slot, make_session, mock_late_auth):
        from core.auth import generate_session_id
        from tests.conftest import _create_test_user  # type: ignore
        admin = _create_test_user(sub="__admin_consumer__", email="admin-consumer@example.com", name="Admin Consumer", user_id=consume_admin_slot)
        admin_session = {**admin, "global_role": "super_admin"}
        admin_token = generate_session_id()
        mock_late_auth.register(admin_token, admin_session)
        headers = {"Authorization": f"Bearer {admin_token}"}
        _, target = make_session("sub-role-inv", "inv@example.com", "Inv")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{target['id']}/role", json={"role": "invalid"}, headers=headers)
        assert r.status_code == 400


class TestChangeMute:
    async def test_mute(self, client, consume_admin_slot, make_session, mock_late_auth):
        from core.auth import generate_session_id
        from tests.conftest import _create_test_user  # type: ignore
        admin = _create_test_user(sub="__admin_consumer__", email="admin-consumer@example.com", name="Admin Consumer", user_id=consume_admin_slot)
        admin_session = {**admin, "global_role": "super_admin"}
        admin_token = generate_session_id()
        mock_late_auth.register(admin_token, admin_session)
        headers = {"Authorization": f"Bearer {admin_token}"}
        _, target = make_session("sub-mute-target", "mute@example.com", "Mute")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{target['id']}/mute", json={"muted": True}, headers=headers)
        assert r.status_code == 200
        assert r.json()["muted"] is True

    async def test_mute_self(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{user['id']}/mute", json={"muted": True}, headers=headers)
        assert r.status_code == 400

    async def test_mute_not_admin(self, client, consume_admin_slot, make_session):
        session1, user1 = make_session("sub-mute-1", "alice@example.com", "Alice")
        session2, user2 = make_session("sub-mute-2", "bob@example.com", "Bob")
        ch = (await client.get("/api/chat/channels", headers={"Authorization": f"Bearer {session1}"})).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}/members/{user2['id']}/mute", json={"muted": True}, headers={"Authorization": f"Bearer {session1}"})
        assert r.status_code == 403
