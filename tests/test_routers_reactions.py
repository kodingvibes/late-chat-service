import pytest


class TestToggleReaction:
    async def test_toggle_add(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "React"}, headers=headers)).json()
        r = await client.post(f"/api/chat/messages/{msg['id']}/reactions", json={"emoji": "heart"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["action"] == "added"

    async def test_toggle_remove(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "React"}, headers=headers)).json()
        await client.post(f"/api/chat/messages/{msg['id']}/reactions", json={"emoji": "heart"}, headers=headers)
        r = await client.post(f"/api/chat/messages/{msg['id']}/reactions", json={"emoji": "heart"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["action"] == "removed"

    async def test_unknown_emoji(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        msg = (await client.post(f"/api/chat/channels/{ch['id']}/messages", json={"content": "React"}, headers=headers)).json()
        r = await client.post(f"/api/chat/messages/{msg['id']}/reactions", json={"emoji": "unknown"}, headers=headers)
        assert r.status_code == 400

    async def test_message_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/messages/99999/reactions", json={"emoji": "heart"}, headers=headers)
        assert r.status_code == 404
