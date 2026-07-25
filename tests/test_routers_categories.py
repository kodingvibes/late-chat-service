import pytest


class TestListCategories:
    async def test_list(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.get("/api/chat/categories", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 2


class TestCreateCategory:
    async def test_create(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/categories", json={"name": "New Cat"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["name"] == "New Cat"

    async def test_create_empty(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.post("/api/chat/categories", json={"name": ""}, headers=headers)
        assert r.status_code == 400

    async def test_create_duplicate(self, client, auth_headers):
        headers, user = auth_headers
        await client.post("/api/chat/categories", json={"name": "Dup"}, headers=headers)
        r = await client.post("/api/chat/categories", json={"name": "Dup"}, headers=headers)
        assert r.status_code in (200, 409)  # no UNIQUE constraint on name


class TestUpdateCategory:
    async def test_update(self, client, auth_headers):
        headers, user = auth_headers
        cats = (await client.get("/api/chat/categories", headers=headers)).json()
        r = await client.patch(f"/api/chat/categories/{cats[0]['id']}", json={"name": "Updated", "is_collapsed": True}, headers=headers)
        assert r.status_code == 200
        assert r.json()["name"] == "Updated"

    async def test_update_not_found(self, client, auth_headers):
        headers, user = auth_headers
        r = await client.patch("/api/chat/categories/99999", json={"name": "Nope"}, headers=headers)
        assert r.status_code == 404


class TestDeleteCategory:
    async def test_delete(self, client, auth_headers):
        headers, user = auth_headers
        cat = (await client.post("/api/chat/categories", json={"name": "To Delete"}, headers=headers)).json()
        r = await client.delete(f"/api/chat/categories/{cat['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True
