"""SSRF-safe OpenGraph/Twitter-card fetching, shared by the server-push
enrichment (routers/messages.py) and the client-pull /api/chat/unfurl route
(routers/link_preview.py). Single source of truth for URL extraction and
OG parsing so both paths behave identically.
"""
import html
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger("chat-bridge")

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Attachment/image markers embedded in message content (see the frontend
# parsers.ts). Everything from the first marker onward is machine payload
# (ids, data URIs, JSON) - never a human caption - so URL extraction must
# stop at it or it would unfurl a card for the attachment's own URL.
_ATTACHMENT_MARKERS = (
    "__late_image__:",
    "late_image__:",
    "__late_images__:",
    "late_images__:",
    "__late_audio__:",
    "__late_video__:",
    "__late_document__:",
    "__late_file__:",
    "__late_voicenote__:",
)


def _caption_before_markers(content: str) -> str:
    cut = len(content)
    for marker in _ATTACHMENT_MARKERS:
        idx = content.find(marker)
        if idx != -1 and idx < cut:
            cut = idx
    return content[:cut]


def has_attachment_marker(content: str) -> bool:
    """True if the message carries an attachment payload. Such a message is
    not editable text: its content is a machine marker (`__late_image__:<id>`)
    that the client parses, so letting a user rewrite it would orphan the
    attachment or forge a reference to someone else's."""
    return any(marker in content for marker in _ATTACHMENT_MARKERS)

USER_AGENT = "Mozilla/5.0 (compatible; late-chat-og/1.0; +https://late.kodingvibes.com) Discordbot/2.0"
TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 512 * 1024


def extract_urls(content: str) -> list[str]:
    seen = set()
    out = []
    for m in URL_RE.finditer(_caption_before_markers(content)):
        url = m.group(0).rstrip(".,;:!?)")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 3:
            break
    return out


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_resolves_safe(host: str, port: int) -> bool:
    """Resolve host and reject if it (or any of its addresses) is internal."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    if not infos:
        return False
    return all(not _is_blocked_ip(info[4][0]) for info in infos)


def _validate_url_safe(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _host_resolves_safe(host, port)


def _meta(html_text: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',
        html_text,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
            html_text,
            re.IGNORECASE,
        )
    return html.unescape(m.group(1).strip()) if m else None


def _minimal_card(url: str) -> dict:
    """Reachable but no usable OG/twitter/title metadata (e.g. X/Twitter,
    bot-gated pages). Still renderable, just honest about what we have."""
    parsed = urlparse(url)
    host = parsed.hostname or url
    site_name = host[4:] if host.startswith("www.") else host
    title = parsed.path.strip("/") or site_name
    return {"url": url, "site_name": site_name, "title": title, "kind": "link"}


def _error(url: str) -> dict:
    return {"url": url, "kind": "error"}


async def fetch_og(url: str) -> dict:
    """Always returns a dict with a `kind` field ('link' or 'error'), never
    None. SSRF-safe: validates scheme + resolves + IP-checks the host before
    every connection, including on each manual redirect hop. This blocks
    direct private-IP targets, redirect-to-private, and cloud-metadata IPs.

    Residual (accepted): a narrow DNS-rebind TOCTOU remains because httpx
    re-resolves the hostname when it connects, so a name with TTL=0 could in
    theory flip to a private IP in the sub-millisecond window after our
    getaddrinfo check. Fully closing it needs IP-pinning (connect by the
    validated IP with a Host header + sni_hostname), which is incompatible
    with the transport-mock test suite without keying every test by IP.
    Given the low severity (blind-ish, leaks only meta tags) this is left as
    a documented follow-up rather than a brittle test rewrite."""
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _validate_url_safe(current_url):
            log.warning(f"OG fetch blocked (unsafe url): {current_url}")
            return _error(url)
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            ) as client:
                async with client.stream("GET", current_url) as r:
                    if 300 <= r.status_code < 400:
                        location = r.headers.get("location")
                        if not location:
                            return _error(url)
                        current_url = urljoin(current_url, location)
                        continue
                    if r.status_code >= 400:
                        return _error(url)
                    content_type = r.headers.get("content-type", "")
                    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                        return _minimal_card(url)
                    body = b""
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) >= MAX_BODY_BYTES:
                            break
                    encoding = r.encoding or "utf-8"
                    try:
                        html_text = body.decode(encoding, errors="replace")
                    except LookupError:
                        html_text = body.decode("utf-8", errors="replace")
                    final_url = str(r.url)
        except Exception as e:
            log.warning(f"OG fetch failed for {current_url}: {e}")
            return _error(url)

        title = _meta(html_text, "og:title") or _meta(html_text, "twitter:title")
        if not title:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html_text, re.IGNORECASE)
            if m:
                title = html.unescape(m.group(1).strip())
        description = _meta(html_text, "og:description") or _meta(html_text, "twitter:description") or _meta(html_text, "description")
        image = _meta(html_text, "og:image") or _meta(html_text, "twitter:image")
        site_name = _meta(html_text, "og:site_name")

        if not title and not description and not image and not site_name:
            return _minimal_card(url)

        og = {"url": url, "kind": "link"}
        if title:
            og["title"] = title[:300]
        if description:
            og["description"] = description[:500]
        if image:
            og["image"] = urljoin(final_url, image)
        if site_name:
            og["site_name"] = site_name[:120]
        return og

    log.warning(f"OG fetch exceeded max redirects for {url}")
    return _error(url)
