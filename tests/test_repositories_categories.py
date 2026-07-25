import pytest
from core.db import db
from repositories.categories import list_categories, create_category, update_category, delete_category


def test_list_categories():
    cats = list_categories()
    names = [c["name"] for c in cats]
    assert "TEXTO" in names
    assert "VOZ" in names


def test_create_category():
    cat = create_category("New Category")
    assert cat["name"] == "New Category"
    assert cat["id"] > 0


def test_create_category_duplicate():
    cat1 = create_category("Dup")
    cat2 = create_category("Dup")
    assert cat1["id"] != cat2["id"]


def test_update_category_name():
    cat = create_category("Rename Me")
    updated = update_category(cat["id"], {"name": "Renamed"})
    assert updated["name"] == "Renamed"


def test_update_category_collapsed():
    cat = create_category("Collapse Test")
    updated = update_category(cat["id"], {"is_collapsed": True})
    assert updated["is_collapsed"] == 1


def test_update_category_not_found():
    result = update_category(99999, {"name": "Nope"})
    assert result is None


def test_delete_category():
    cat = create_category("Delete Me")
    delete_category(cat["id"])
    cats = list_categories()
    assert all(c["id"] != cat["id"] for c in cats)


def test_delete_category_sets_channels_null():
    cat = create_category("With Channels")
    from repositories.channels import create_channel, update_channel, get_channel
    from tests.conftest import _create_test_user  # type: ignore
    user = _create_test_user("sub-cat-del", "cat@example.com", "Cat")
    ch = create_channel("#cat-channel", "In category", True, user["id"])
    update_channel(ch["id"], {"category_id": cat["id"]})
    delete_category(cat["id"])
    updated = get_channel(ch["id"])
    assert updated["category_id"] != cat["id"]
