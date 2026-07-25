import pytest


class TestListChannels:
    async def test_list(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/channels", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 4
        names = [c["name"] for c in data]
        assert "#lobby" in names

    async def test_voice_participants_reflects_live_room(self, client, auth_headers):
        from services.voice_rooms import voice_rooms
        headers, user = auth_headers
        channels = (await client.get("/api/chat/channels", headers=headers)).json()
        voice_ch = next(c for c in channels if c["channel_type"] == "voice")
        assert voice_ch["voice_participants"] == 0

        voice_rooms.rooms.clear()
        voice_rooms.user_room.clear()
        try:
            await voice_rooms.join(user["id"], str(voice_ch["id"]))
            channels = (await client.get("/api/chat/channels", headers=headers)).json()
            voice_ch = next(c for c in channels if c["channel_type"] == "voice" and c["id"] == voice_ch["id"])
            assert voice_ch["voice_participants"] == 1
        finally:
            voice_rooms.rooms.clear()
            voice_rooms.user_room.clear()


class TestCreateChannel:
    async def test_create(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/channels", json={"name": "test-channel"}, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "#test-channel"

    async def test_create_with_hash(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/channels", json={"name": "#explicit"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["name"] == "#explicit"

    async def test_create_invalid_name(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/channels", json={"name": "bad name!"}, headers=headers)
        assert r.status_code == 400

    async def test_create_too_long(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/channels", json={"name": "a" * 41}, headers=headers)
        assert r.status_code == 400

    async def test_create_duplicate(self, client, auth_headers):
        headers, user = auth_headers
        await client.post("/api/chat/channels", json={"name": "dup"}, headers=headers)
        r = await client.post("/api/chat/channels", json={"name": "dup"}, headers=headers)
        assert r.status_code == 409


class TestUpdateChannel:
    async def test_update(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.patch(f"/api/chat/channels/{ch['id']}", json={"position": 5}, headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestJoinLeave:
    async def test_join(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/join", headers=headers)
        assert r.status_code == 200

    async def test_leave(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/leave", headers=headers)
        assert r.status_code == 200


class TestSearchUsers:
    async def test_search(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/users?q=test", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1

    async def test_search_empty(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/users?q=", headers=headers)
        assert r.status_code == 200
        assert r.json() == []


class TestInvite:
    async def test_invite(self, client, consume_admin_slot, auth_headers, make_session):
        headers, user = auth_headers
        _, target = make_session("sub-invite", "target@example.com", "Target")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/invite", json={"email": "target@example.com"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_invite_invalid_email(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/invite", json={"email": "notanemail"}, headers=headers)
        assert r.status_code == 400

    async def test_invite_not_found(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/invite", json={"email": "nobody@example.com"}, headers=headers)
        assert r.status_code == 404


class TestMarkRead:
    async def test_mark_read(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/read?message_id=1", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestDeleteChannel:
    async def test_delete_as_admin(self, client, consume_admin_slot, auth_headers, make_session):
        from core.db import db
        headers, user = auth_headers
        # Create a throwaway channel as a non-member user; the test user
        # becomes admin via user_id=1 → 'admin' bootstrap.
        r = await client.post("/api/chat/channels", json={"name": "to-delete"}, headers=headers)
        ch = r.json()
        r = await client.delete(f"/api/chat/channels/{ch['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        with db() as conn:
            assert conn.execute("SELECT id FROM channels WHERE id = ?", (ch["id"],)).fetchone() is None

    async def test_delete_as_super_admin_without_membership(self, client, consume_admin_slot, auth_headers, make_session, mock_late_auth):
        headers, user = auth_headers
        # Promote the test user to super_admin via the mock so the
        # chat-bridge code path returns admin for every channel.
        admin_user = {**user, "global_role": "super_admin"}
        mock_late_auth.register(headers["Authorization"].split(" ", 1)[1], admin_user)
        other_session, _ = make_session("other3", "other3@example.com", "Other3")
        r = await client.post("/api/chat/channels", json={"name": "other-channel"}, headers={"Authorization": f"Bearer {other_session}"})
        other_ch = r.json()
        r = await client.delete(f"/api/chat/channels/{other_ch['id']}", headers=headers)
        assert r.status_code == 200

    async def test_delete_as_plain_user_forbidden(self, client, consume_admin_slot, auth_headers, make_session):
        headers, _ = auth_headers
        # A second user is not admin and not in #lobby; can't delete it.
        attacker_session, _ = make_session("attacker", "attacker@example.com", "Attacker")
        h2 = {"Authorization": f"Bearer {attacker_session}"}
        lobby_id = (await client.get("/api/chat/channels", headers=headers)).json()[0]["id"]
        r = await client.delete(f"/api/chat/channels/{lobby_id}", headers=h2)
        assert r.status_code == 403
