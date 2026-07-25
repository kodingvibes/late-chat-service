import ipaddress
import socket

import pytest

from repositories.link_previews import get_cached, set_cached
from services import link_preview

PUBLIC_IP = "93.184.216.34"


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """Stand-in for socket.getaddrinfo: literal IPs resolve to themselves
    (so SSRF checks on IP literals still work), any hostname resolves to a
    benign public IP (so fixture hostnames like notfound.example.com, which
    have no real DNS record, don't depend on live network in CI)."""
    try:
        ipaddress.ip_address(host)
        addr = host
    except ValueError:
        addr = PUBLIC_IP
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(link_preview.socket, "getaddrinfo", _fake_getaddrinfo)


class TestFetchOgParsing:
    async def test_parse_from_fixture_html(self, respx_mock):
        html = (
            "<html><head>"
            "<meta property='og:title' content='Fixture Title'>"
            "<meta property='og:description' content='Fixture description'>"
            "<meta property='og:image' content='https://cdn.example.com/img.png'>"
            "<meta property='og:site_name' content='Fixture Site'>"
            "</head></html>"
        )
        respx_mock.get("https://fixture.example.com/page").respond(
            status_code=200, headers={"content-type": "text/html"}, content=html.encode(),
        )
        og = await link_preview.fetch_og("https://fixture.example.com/page")
        assert og["kind"] == "link"
        assert og["title"] == "Fixture Title"
        assert og["description"] == "Fixture description"
        assert og["image"] == "https://cdn.example.com/img.png"
        assert og["site_name"] == "Fixture Site"

    async def test_twitter_fallback(self, respx_mock):
        html = (
            "<html><head>"
            "<meta name='twitter:title' content='Tweet Title'>"
            "<meta name='twitter:image' content='https://cdn.example.com/tw.png'>"
            "</head></html>"
        )
        respx_mock.get("https://twitter.example.com/status").respond(
            status_code=200, headers={"content-type": "text/html"}, content=html.encode(),
        )
        og = await link_preview.fetch_og("https://twitter.example.com/status")
        assert og["kind"] == "link"
        assert og["title"] == "Tweet Title"
        assert og["image"] == "https://cdn.example.com/tw.png"
        assert "description" not in og

    async def test_title_and_meta_description_fallback(self, respx_mock):
        html = (
            "<html><head><title>Plain Title</title>"
            "<meta name='description' content='Plain desc'></head></html>"
        )
        respx_mock.get("https://plain.example.com/").respond(
            status_code=200, headers={"content-type": "text/html"}, content=html.encode(),
        )
        og = await link_preview.fetch_og("https://plain.example.com/")
        assert og["kind"] == "link"
        assert og["title"] == "Plain Title"
        assert og["description"] == "Plain desc"

    async def test_relative_image_resolved(self, respx_mock):
        html = (
            "<html><head><meta property='og:title' content='Rel'>"
            "<meta property='og:image' content='/img/card.png'></head></html>"
        )
        respx_mock.get("https://relimg.example.com/page").respond(
            status_code=200, headers={"content-type": "text/html"}, content=html.encode(),
        )
        og = await link_preview.fetch_og("https://relimg.example.com/page")
        assert og["image"] == "https://relimg.example.com/img/card.png"

    async def test_minimal_card_on_no_metadata(self, respx_mock):
        html = "<html><head></head><body>nothing here</body></html>"
        respx_mock.get("https://bare.example.com/some/path").respond(
            status_code=200, headers={"content-type": "text/html"}, content=html.encode(),
        )
        og = await link_preview.fetch_og("https://bare.example.com/some/path")
        assert og["kind"] == "link"
        assert og["site_name"] == "bare.example.com"
        assert og["title"] == "some/path"

    async def test_non_html_minimal_card(self, mock_httpx_og):
        og = await link_preview.fetch_og("https://binary.example.com")
        assert og["kind"] == "link"
        assert og["site_name"] == "binary.example.com"

    async def test_http_error_is_error_kind(self, mock_httpx_og):
        og = await link_preview.fetch_og("https://notfound.example.com")
        assert og == {"url": "https://notfound.example.com", "kind": "error"}


class TestExtractUrls:
    def test_plain_urls_in_order(self):
        assert link_preview.extract_urls("see https://a.com and https://b.com") == [
            "https://a.com",
            "https://b.com",
        ]

    def test_strips_trailing_punctuation(self):
        assert link_preview.extract_urls("go to https://a.com.") == ["https://a.com"]

    def test_ignores_url_after_image_marker(self):
        content = "look https://good.com __late_image__:https://cdn.internal/x.png"
        assert link_preview.extract_urls(content) == ["https://good.com"]

    def test_ignores_images_marker_json_payload(self):
        content = 'cap https://good.com __late_images__:["https://cdn/1.png","https://cdn/2.png"]'
        assert link_preview.extract_urls(content) == ["https://good.com"]

    def test_ignores_attachment_marker_payload(self):
        content = "https://good.com __late_document__:https://cdn/report.pdf"
        assert link_preview.extract_urls(content) == ["https://good.com"]

    def test_no_url_in_caption_returns_empty(self):
        assert link_preview.extract_urls("__late_image__:https://cdn/x.png") == []

    def test_dedup_and_cap_at_three(self):
        content = " ".join(f"https://x.com/{i}" for i in range(5))
        urls = link_preview.extract_urls(content)
        assert urls == ["https://x.com/0", "https://x.com/1", "https://x.com/2"]


class TestSsrf:
    async def test_ssrf_reject_private_ip_literal(self, respx_mock):
        og = await link_preview.fetch_og("http://127.0.0.1/")
        assert og == {"url": "http://127.0.0.1/", "kind": "error"}
        assert len(respx_mock.calls) == 0

    async def test_ssrf_reject_cloud_metadata_ip(self, respx_mock):
        og = await link_preview.fetch_og("http://169.254.169.254/")
        assert og["kind"] == "error"
        assert len(respx_mock.calls) == 0

    async def test_ssrf_reject_dns_rebind_to_private(self, respx_mock, monkeypatch):
        monkeypatch.setattr(
            link_preview.socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))],
        )
        og = await link_preview.fetch_og("http://rebind.example.com/")
        assert og == {"url": "http://rebind.example.com/", "kind": "error"}
        assert len(respx_mock.calls) == 0


class TestLinkPreviewRepository:
    def test_set_and_get_cached_round_trip(self, tmp_db):
        data = {"url": "https://example.com", "kind": "link", "title": "T"}
        set_cached("https://example.com", data)
        cached = get_cached("https://example.com")
        assert cached is not None
        assert cached["data"] == data
        assert cached["fetched_at"] > 0

    def test_get_cached_miss(self, tmp_db):
        assert get_cached("https://nope.example.com") is None

    def test_set_cached_upsert_overwrites(self, tmp_db):
        set_cached("https://example.com", {"url": "https://example.com", "kind": "link"})
        set_cached("https://example.com", {"url": "https://example.com", "kind": "error"})
        cached = get_cached("https://example.com")
        assert cached["data"]["kind"] == "error"


class TestUnfurlEndpoint:
    async def test_auth_required(self, client):
        r = await client.get("/api/chat/unfurl", params={"url": "https://example.com"})
        assert r.status_code == 401

    async def test_ssrf_reject_non_http_scheme(self, client, auth_headers):
        headers, _ = auth_headers
        r = await client.get("/api/chat/unfurl", params={"url": "file:///etc/passwd"}, headers=headers)
        assert r.status_code == 400

    async def test_ssrf_reject_private_ip_via_endpoint(self, client, auth_headers, respx_mock):
        headers, _ = auth_headers
        r = await client.get("/api/chat/unfurl", params={"url": "http://127.0.0.1/"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kind"] == "error"
        assert len(respx_mock.calls) == 0

    async def test_error_result_is_not_cached(self, client, auth_headers, respx_mock):
        headers, _ = auth_headers
        respx_mock.get("https://fails.example.com/").respond(status_code=500)
        r = await client.get(
            "/api/chat/unfurl", params={"url": "https://fails.example.com/"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["kind"] == "error"
        # A transient failure must not be persisted for the whole TTL window.
        assert get_cached("https://fails.example.com/") is None

    async def test_cache_hit_path(self, client, auth_headers, mock_httpx_og):
        headers, _ = auth_headers
        r1 = await client.get("/api/chat/unfurl", params={"url": "https://example.com"}, headers=headers)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["kind"] == "link"
        assert data1["title"] == "OG Title"
        assert len(mock_httpx_og.calls) == 1

        r2 = await client.get("/api/chat/unfurl", params={"url": "https://example.com"}, headers=headers)
        assert r2.status_code == 200
        assert r2.json() == data1
        assert len(mock_httpx_og.calls) == 1
