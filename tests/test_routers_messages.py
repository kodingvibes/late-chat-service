import pytest


class TestListMessages:
    async def test_list(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.get(f"/api/chat/channels/{ch['id']}/messages", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_with_before(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.get(f"/api/chat/channels/{ch['id']}/messages?before=99999", headers=headers)
        assert r.status_code == 200

    async def test_list_not_member(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/channels/99999/messages", headers=headers)
        assert r.status_code == 404


class TestSendMessage:
    async def test_send(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "Hello"}, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["content"] == "Hello"
        assert data["user_id"] == user["id"]

    async def test_send_empty(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "   "}, headers=headers)
        assert r.status_code == 400

    async def test_send_too_long(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "x" * 2_000_001}, headers=headers)
        assert r.status_code == 400

    async def test_send_me_action(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "/me waves", "is_action": True}, headers=headers)
        assert r.status_code == 200
        assert r.json()["is_action"] == 1

    async def test_send_not_member(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/channels/99999/messages", json={"content": "Hi"}, headers=headers)
        assert r.status_code == 404


class TestHideMessage:
    async def test_hide_own(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "Hide me"}, headers=headers)).json()
        r = await client.post(f"/api/chat/messages/{msg['id']}/hide", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_hide_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/messages/99999/hide", headers=headers)
        assert r.status_code == 404


class TestDeleteMessage:
    async def test_delete_own(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "Delete me"}, headers=headers)).json()
        r = await client.delete(f"/api/chat/messages/{msg['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_delete_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.delete("/api/chat/messages/99999", headers=headers)
        assert r.status_code == 404


class TestForwardMessage:
    async def test_forward(self, client, auth_headers):
        headers, user = auth_headers
        chans = (await client.get("/api/chat/channels", headers=headers)).json()
        src = chans[0]
        dst = chans[1]
        msg = (await client.post(f"/api/chat/channels/{src['id']}/messages", json={"content": "Forward me"}, headers=headers)).json()
        r = await client.post(f"/api/chat/messages/{msg['id']}/forward", json={"target_channel_id": dst["id"]}, headers=headers)
        assert r.status_code == 200
        assert r.json()["forwarded_from"] is not None

    async def test_forward_not_found(self, client, auth_headers):
        headers, user = auth_headers
        chans = (await client.get("/api/chat/channels", headers=headers)).json()
        r = await client.post("/api/chat/messages/99999/forward", json={"target_channel_id": chans[0]["id"]}, headers=headers)
        assert r.status_code == 404

    async def test_forward_p2p_not_implemented(self, client, auth_headers):
        headers, user = auth_headers
        chans = (await client.get("/api/chat/channels", headers=headers)).json()
        msg = (await client.post(f"/api/chat/channels/{chans[0]['id']}/messages", json={"content": "P2P"}, headers=headers)).json()
        r = await client.post(f"/api/chat/messages/{msg['id']}/forward", json={"target_channel_id": chans[0]["id"], "target_user_id": 1}, headers=headers)
        assert r.status_code == 501
