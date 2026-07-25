import pytest
import routers.buzz as buzz_module


class TestBuzz:
    @pytest.fixture(autouse=True)
    def clear_rate_limiter(self):
        buzz_module._last_buzz_at.clear()

    async def test_buzz(self, client, consume_admin_slot, auth_headers, make_session):
        headers, user = auth_headers
        _, target = make_session("sub-buzz-target", "buzz@example.com", "Buzz")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post("/api/chat/buzz", json={"channel_id": ch["id"], "target_user_id": target["id"]}, headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_buzz_self(self, client, auth_headers):
        headers, user = auth_headers
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        r = await client.post("/api/chat/buzz", json={"channel_id": ch["id"], "target_user_id": user["id"]}, headers=headers)
        assert r.status_code == 400

    async def test_buzz_not_member(self, client, consume_admin_slot, auth_headers, make_session):
        # ponytail: every user is in every channel, so a non-existent
        # channel id now goes through the online check and returns 404
        # (target not online / channel not found) rather than 403.
        # The behavior is the same from the user's POV — the buzz
        # can't land — but the status code reflects the new model.
        headers, user = auth_headers
        _, target = make_session("sub-buzz-nomember", "nomember@example.com", "NoMember")
        r = await client.post("/api/chat/buzz", json={"channel_id": 99999, "target_user_id": target["id"]}, headers=headers)
        assert r.status_code == 404

    async def test_buzz_target_not_in_channel(self, client, consume_admin_slot, auth_headers, make_session):
        headers, user = auth_headers
        _, target = make_session("sub-buzz-nochan2", "nochan2@example.com", "NoChan2")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        # Remove target from the channel
        from core.db import db
        with db() as conn:
            conn.execute("DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?", (ch["id"], target["id"]))
        r = await client.post("/api/chat/buzz", json={"channel_id": ch["id"], "target_user_id": target["id"]}, headers=headers)
        assert r.status_code == 404

    async def test_buzz_rate_limit(self, client, consume_admin_slot, auth_headers, make_session):
        headers, user = auth_headers
        _, target = make_session("sub-buzz-rate", "rate@example.com", "Rate")
        ch = (await client.get("/api/chat/channels", headers=headers)).json()[0]
        await client.post("/api/chat/buzz", json={"channel_id": ch["id"], "target_user_id": target["id"]}, headers=headers)
        r = await client.post("/api/chat/buzz", json={"channel_id": ch["id"], "target_user_id": target["id"]}, headers=headers)
        assert r.status_code == 429
