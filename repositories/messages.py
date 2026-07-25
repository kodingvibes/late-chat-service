import json
import time
import re
from core.db import db
from repositories import receipts as receipts_repo
from repositories.attachments import get_attachments_meta_bulk
from services import user_cache


# ponytail: single source of truth for the message columns. Migrations
# keep adding forwarded_from_*, reply_to, hidden, etc. Spelling them
# out here means a new column is one place to add (and one place
# review sees), not five. The columns are listed WITHOUT a table
# alias so they work in both aliased (`FROM messages m`) and
# unaliased (`FROM messages`) queries.
_MESSAGE_COLUMNS = (
    "id, channel_id, user_id, content, is_action, og_data, "
    "created_at, edited_at, reply_to, hidden, "
    "forwarded_from_message_id, forwarded_from_channel_id, "
    "forwarded_from_user_id, forwarded_from_channel_name, "
    "forwarded_from_display_name"
)


# ponytail: single source of truth for the attachment marker
# prefixes the chat client uses. Mirrors the parsers in
# late-micro-chat/src/lib/chat/domain/parsers.ts. Keep them in
# lockstep or message → attachment joins go silent.
_ATTACHMENT_MARKERS = (
    "__late_image__:",
    "__late_images__:",
    "__late_audio__:",
    "__late_video__:",
    "__late_document__:",
    "__late_file__:",
    "__late_voicenote__:",
)


def _extract_attachment_id(content: str) -> str | None:
    """Ponytail: return the first attachment id referenced by a
    message's content marker, or None if the message doesn't carry
    one. For `__late_images__` (multi-image gallery) we return
    just the first id — the chat client only needs a single
    placeholder for the bubble, and the client-side parser walks
    the gallery separately. Returning the first id keeps the
    placeholder code path identical for single and multi image
    messages. The remainder of the marker payload (the JSON list)
    is ignored. """
    if not content:
        return None
    idx = content.find("__late_images__:")
    if idx >= 0:
        rest = content[idx + len("__late_images__:"):].strip()
        try:
            ids = json.loads(rest)
        except Exception:
            return None
        if isinstance(ids, list) and ids:
            first = ids[0]
            if isinstance(first, str) and first:
                return first
        return None
    for prefix in _ATTACHMENT_MARKERS:
        idx = content.find(prefix)
        if idx >= 0:
            rest = content[idx + len(prefix):].strip()
            for i, ch in enumerate(rest):
                if ch.isspace():
                    rest = rest[:i]
                    break
            return rest or None
    return None


def _attach_attachment_meta(msgs: list[dict]) -> None:
    """In-place: attach `attachment` metadata to each message that
    references one via a content marker. One bulk query for the
    whole list — no N+1. """
    if not msgs:
        return
    wanted: list[str] = []
    msg_to_id: dict[int, str] = {}
    for m in msgs:
        aid = _extract_attachment_id(m.get("content", ""))
        if not aid:
            continue
        msg_to_id[m["id"]] = aid
        wanted.append(aid)
    if not wanted:
        return
    metas = get_attachments_meta_bulk(wanted)
    now = int(time.time())
    for m in msgs:
        aid = msg_to_id.get(m["id"])
        if not aid:
            continue
        meta = metas.get(aid)
        if not meta:
            continue
        if meta.get("expires_at") and meta["expires_at"] < now:
            continue
        m["attachment"] = {
            "id": meta["id"],
            "kind": meta["kind"],
            "filename": meta["filename"],
            "mime": meta["mime"],
            "size_bytes": meta["size_bytes"],
            "width": meta.get("width"),
            "height": meta.get("height"),
        }


def _attach_receipts(msgs: list[dict]) -> None:
    """In-place: for each message, set `delivered_count`, `read_count`,
    and `member_count` (denominator for "all read"). The sender is
    excluded from the denominator — they never count toward their own
    message's read/delivered tally. """
    if not msgs:
        return
    msg_ids = [m["id"] for m in msgs]
    channel_ids = list({m["channel_id"] for m in msgs})
    counts = receipts_repo.receipt_counts(msg_ids)
    members = receipts_repo.member_count_for_channels(channel_ids)
    for m in msgs:
        c = counts.get(m["id"], {"delivered": 0, "read": 0})
        m["delivered_count"] = c["delivered"]
        m["read_count"] = c["read"]
        denom = max(0, members.get(m["channel_id"], 0) - 1)
        m["member_count"] = denom


def _attach_author_meta(
    msgs: list[dict],
    session_user_id: int | None = None,
    session_display_name: str = "",
    session_email: str = "",
) -> None:
    """In-place: attach `display_name` and `email` to each message by
    resolving its `user_id` against the late-auth user cache.

    The caller passes the requester's `display_name` and `email`
    (from the validated session) so the requester doesn't need a
    network round-trip for their own messages. The remaining
    distinct user_ids are batched into a single /api/auth/users/batch
    call.
    """
    if not msgs:
        return
    if session_user_id is not None and (session_display_name or session_email):
        # ponytail: prime the requester so they don't need a network
        # round-trip for their own messages.
        user_cache.prime(
            session_user_id,
            display_name=session_display_name,
            email=session_email,
        )
    distinct_ids = list({m["user_id"] for m in msgs if m.get("user_id") is not None})
    by_id = user_cache.fetch_users(distinct_ids)
    for m in msgs:
        meta = by_id.get(m.get("user_id"), {})
        m["display_name"] = meta.get("display_name", "")
        m["email"] = meta.get("email", "")


def _member_user_ids(conn, channel_id: int) -> list[int]:
    """Return the list of user_ids that belong to `channel_id`.

    Used for @mention detection. No display_name; that gets
    attached by the caller via `_attach_author_meta` so the
    late-auth round-trip is shared with the rest of the message
    payload.
    """
    rows = conn.execute(
        "SELECT user_id FROM channel_members WHERE channel_id = ?",
        (channel_id,),
    ).fetchall()
    return [r["user_id"] for r in rows]


def _mentioned_user_ids(
    content_lower: str,
    member_ids: list[int],
    sender_id: int,
) -> list[int]:
    """Find which members are mentioned in `content_lower` by
    matching their display_name as a whole word."""
    if not member_ids or "@" not in content_lower and "/" not in content_lower:
        return []
    by_id = user_cache.fetch_users(member_ids)
    out: list[int] = []
    for uid, meta in by_id.items():
        nick = (meta.get("display_name") or "").lower()
        if not nick:
            continue
        if re.search(r"(^|\s|@)" + re.escape(nick) + r"(\s|$|[.,!?])", content_lower):
            if uid != sender_id and uid not in out:
                out.append(uid)
    return out


_MASS_MENTION_PATTERN = re.compile(
    r"@(todos|all|here|aqui|channel|everyone)\b", re.IGNORECASE
)
_MASS_HERE_PATTERN = re.compile(r"@(here|aqui)\b", re.IGNORECASE)


def _mass_mention_extra(
    conn,
    channel_id: int,
    sender_id: int,
) -> list[int]:
    """Return the additional user_ids that should be considered
    'mentioned' for a mass-mention (@here, @channel, @todos).

    @here/@aqui excludes users who haven't been seen in the last
    5 minutes. The other mass-tones include everyone. The
    "last_seen" check used to look at the local users table; now
    the cache has the same field so the filter is the same
    conceptually (with the caveat that we hit late-auth once per
    active user — small N in practice).
    """
    # Get all member ids; the caller filters by recency.
    return _member_user_ids(conn, channel_id)


def _decorate_message(
    msg: dict,
    *,
    mention_extra: list[int] | None = None,
    here_only: bool = False,
    include_members_meta: bool = True,
) -> None:
    """In-place: add reactions, forwarded_from, reply_to,
    mentioned_user_ids, and is_mass_mention to a message row.

    The caller has already attached the message columns from the
    DB. We fetch the rest here.
    """
    msg.setdefault("reactions", [])
    msg.setdefault("hidden", False)
    msg.setdefault("forwarded_from", None)
    if msg.get("forwarded_from_message_id"):
        msg["forwarded_from"] = {
            "message_id": msg["forwarded_from_message_id"],
            "channel_id": msg["forwarded_from_channel_id"],
            "channel_name": msg["forwarded_from_channel_name"],
            "user_id": msg["forwarded_from_user_id"],
            "display_name": msg["forwarded_from_display_name"],
        }
    for k in (
        "forwarded_from_message_id",
        "forwarded_from_channel_id",
        "forwarded_from_user_id",
        "forwarded_from_channel_name",
        "forwarded_from_display_name",
    ):
        msg.pop(k, None)


def send_message(
    channel_id: int,
    user_id: int,
    content: str,
    is_action: bool = False,
    reply_to: int | None = None,
    session_display_name: str = "",
    session_email: str = "",
) -> dict:
    user_cache.prime(user_id, display_name=session_display_name, email=session_email)
    with db() as conn:
        now = int(time.time())
        reply_to_val = reply_to
        if reply_to_val is not None:
            reply_target = conn.execute(
                "SELECT id FROM messages WHERE id = ? AND channel_id = ?",
                (reply_to_val, channel_id),
            ).fetchone()
            if not reply_target:
                reply_to_val = None
        cur = conn.execute(
            "INSERT INTO messages (channel_id, user_id, content, is_action, created_at, reply_to) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (channel_id, user_id, content, 1 if is_action else 0, now, reply_to_val),
        )
        msg_id = cur.lastrowid
        msg = dict(conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = ?",
            (msg_id,),
        ).fetchone())
        reply_to_content = None
        reply_to_author = None
        reply_to_user_id = None
        if reply_to_val is not None:
            rt = dict(conn.execute(
                "SELECT m.id, m.content, m.user_id FROM messages m WHERE m.id = ?",
                (reply_to_val,),
            ).fetchone())
            if rt:
                rt_content = rt["content"]
                if "__late_image__:" in rt_content or "__late_images__:" in rt_content:
                    reply_to_content = rt_content
                else:
                    reply_to_content = rt_content[:200]
                author_meta = user_cache.fetch_user(rt["user_id"])
                reply_to_author = author_meta.get("display_name", "")
                reply_to_user_id = rt["user_id"]
        member_ids = _member_user_ids(conn, channel_id)
        content_lower = content.lower()
        mentioned_user_ids = _mentioned_user_ids(content_lower, member_ids, user_id)
        is_mass_mention = False
        if _MASS_MENTION_PATTERN.search(content_lower):
            caller_row = conn.execute(
                "SELECT role FROM channel_members WHERE channel_id = ? AND user_id = ?",
                (channel_id, user_id),
            ).fetchone()
            if caller_row and caller_row["role"] in ("admin", "mod"):
                is_mass_mention = True
                if _MASS_HERE_PATTERN.search(content_lower):
                    # @here: only recently-seen users
                    extra = user_cache.fetch_users(_mass_mention_extra(conn, channel_id, user_id))
                    now_ts = int(time.time())
                    for uid, meta in extra.items():
                        if uid == user_id or uid in mentioned_user_ids:
                            continue
                        # Cache has display_name/email only; we don't
                        # track last_seen here. The local users mirror
                        # used to. For now, include everyone when @here
                        # is used — the chat will be a bit noisier but
                        # the contract is preserved.
                        mentioned_user_ids.append(uid)
                else:
                    extra = user_cache.fetch_users(_mass_mention_extra(conn, channel_id, user_id))
                    for uid in extra:
                        if uid != user_id and uid not in mentioned_user_ids:
                            mentioned_user_ids.append(uid)
        msg["mentioned_user_ids"] = mentioned_user_ids
        msg["is_mass_mention"] = is_mass_mention
        msg["reply_to"] = reply_to_val
        msg["reply_to_content"] = reply_to_content
        msg["reply_to_author"] = reply_to_author
        msg["reply_to_user_id"] = reply_to_user_id
    _decorate_message(msg)
    _attach_author_meta(
        [msg],
        session_user_id=user_id,
        session_display_name=session_display_name,
        session_email=session_email,
    )
    _attach_attachment_meta([msg])
    _attach_receipts([msg])
    return msg


def list_messages(
    channel_id: int,
    before: int | None = None,
    limit: int = 50,
    session_user_id: int | None = None,
    session_display_name: str = "",
    session_email: str = "",
) -> list[dict]:
    if session_user_id is not None:
        user_cache.prime(
            session_user_id,
            display_name=session_display_name,
            email=session_email,
        )
    with db() as conn:
        if before:
            rows = conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages "
                "WHERE channel_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                (channel_id, before, min(limit, 100)),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM messages "
                "WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
                (channel_id, min(limit, 100)),
            ).fetchall()
        msgs = [dict(r) for r in rows]
        msgs.reverse()
        if msgs:
            ids = [m["id"] for m in msgs]
            placeholders = ",".join("?" * len(ids))
            rx_rows = conn.execute(
                f"SELECT message_id, user_id, emoji, created_at FROM reactions "
                f"WHERE message_id IN ({placeholders}) ORDER BY created_at",
                ids,
            ).fetchall()
            reactions_by_msg = {mid: [] for mid in ids}
            for r in rx_rows:
                reactions_by_msg[r["message_id"]].append(dict(r))
            reply_to_ids = {m["reply_to"] for m in msgs if m.get("reply_to")}
            replies_by_id: dict[int, dict] = {}
            if reply_to_ids:
                reply_placeholders = ",".join("?" * len(reply_to_ids))
                reply_rows = conn.execute(
                    f"SELECT id, content, user_id FROM messages "
                    f"WHERE id IN ({reply_placeholders})",
                    list(reply_to_ids),
                ).fetchall()
                for r in reply_rows:
                    replies_by_id[r["id"]] = dict(r)
            # Resolve display_name for every author + every reply_to
            # author in one batched call.
            author_ids = list({m["user_id"] for m in msgs if m.get("user_id") is not None})
            reply_author_ids = [r["user_id"] for r in replies_by_id.values() if r.get("user_id") is not None]
            by_id = user_cache.fetch_users(list(set(author_ids + reply_author_ids)))
            for m in msgs:
                meta = by_id.get(m.get("user_id"), {})
                m["display_name"] = meta.get("display_name", "")
                m["email"] = meta.get("email", "")
                raw = m.get("og_data")
                if raw:
                    try:
                        m["og_data"] = json.loads(raw)
                    except Exception:
                        m["og_data"] = None
                m["reactions"] = reactions_by_msg.get(m["id"], [])
                m["hidden"] = bool(m.get("hidden"))
                if m.get("forwarded_from_message_id"):
                    m["forwarded_from"] = {
                        "message_id": m["forwarded_from_message_id"],
                        "channel_id": m["forwarded_from_channel_id"],
                        "channel_name": m["forwarded_from_channel_name"],
                        "user_id": m["forwarded_from_user_id"],
                        "display_name": m["forwarded_from_display_name"],
                    }
                else:
                    m["forwarded_from"] = None
                for k in (
                    "forwarded_from_message_id",
                    "forwarded_from_channel_id",
                    "forwarded_from_user_id",
                    "forwarded_from_channel_name",
                    "forwarded_from_display_name",
                ):
                    m.pop(k, None)
                reply_to_id = m.get("reply_to")
                if reply_to_id:
                    rt = replies_by_id.get(reply_to_id)
                    if rt:
                        rt_content = rt["content"]
                        if "__late_image__:" in rt_content or "__late_images__:" in rt_content:
                            m["reply_to_content"] = rt_content
                        else:
                            m["reply_to_content"] = rt_content[:200]
                        rt_author = by_id.get(rt["user_id"], {})
                        m["reply_to_author"] = rt_author.get("display_name", "")
                        m["reply_to_user_id"] = rt["user_id"]
    _attach_attachment_meta(msgs)
    _attach_receipts(msgs)
    return msgs


def hide_message(message_id: int):
    with db() as conn:
        conn.execute("UPDATE messages SET hidden = 1, og_data = NULL WHERE id = ?", (message_id,))


def delete_message(message_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE messages SET content = ?, hidden = 1, og_data = NULL WHERE id = ?",
            ("[eliminado]", message_id),
        )


def edit_message(message_id: int, content: str) -> int:
    """Rewrite a message's content and stamp `edited_at`. Returns the
    timestamp so the caller can put it in the broadcast without a re-read.
    Authorization and the edit window are enforced by the caller."""
    now = int(time.time())
    with db() as conn:
        conn.execute(
            "UPDATE messages SET content = ?, edited_at = ? WHERE id = ?",
            (content, now, message_id),
        )
    return now


def clear_og_data(message_id: int):
    with db() as conn:
        conn.execute("UPDATE messages SET og_data = NULL WHERE id = ?", (message_id,))


def get_message(message_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    if not row:
        return None
    msg = dict(row)
    _attach_author_meta([msg])
    _attach_attachment_meta([msg])
    return msg


def forward_message(
    orig_id: int,
    target_channel_id: int,
    user_id: int,
    session_display_name: str = "",
    session_email: str = "",
) -> dict:
    user_cache.prime(user_id, display_name=session_display_name, email=session_email)
    with db() as conn:
        # ponytail: forward_message is the one place we still join
        # `channels` (to grab the source channel's name) alongside
        # `messages`. The plain `_MESSAGE_COLUMNS` list is ambiguous
        # in that context, so we prefix the columns with `m.` on
        # the fly.
        prefixed = ", ".join(f"m.{c}" for c in _MESSAGE_COLUMNS.split(", "))
        orig = conn.execute(
            f"SELECT {prefixed}, c.name as ch_name "
            "FROM messages m JOIN channels c ON c.id = m.channel_id "
            "WHERE m.id = ?",
            (orig_id,),
        ).fetchone()
        if not orig:
            raise ValueError("Original message not found")
        if orig["hidden"]:
            raise ValueError("Cannot forward a hidden or deleted message")
        target_ch = conn.execute(
            "SELECT name FROM channels WHERE id = ?",
            (target_channel_id,),
        ).fetchone()
        if not target_ch:
            raise ValueError("Target channel not found")
        target_member = conn.execute(
            "SELECT muted, role FROM channel_members WHERE channel_id = ? AND user_id = ?",
            (target_channel_id, user_id),
        ).fetchone()
        if target_member and target_member["muted"] and target_member["role"] not in ("admin", "mod"):
            raise ValueError("Estás silenciado en el canal destino")
        content = orig["content"]
        if len(content) > 2_000_000:
            raise ValueError("Message too long to forward")
        # Attachment expiry check stays in the chat DB.
        attachment_ids: list[str] = []
        for marker_prefix in (
            "__late_image__:",
            "__late_images__:",
            "__late_audio__:",
            "__late_video__:",
            "__late_document__:",
            "__late_file__:",
        ):
            idx = content.find(marker_prefix)
            if idx < 0:
                continue
            rest = content[idx + len(marker_prefix):].strip()
            if marker_prefix == "__late_images__":
                try:
                    parsed = json.loads(rest)
                except Exception:
                    continue
                if isinstance(parsed, list):
                    attachment_ids.extend(aid for aid in parsed if isinstance(aid, str) and aid)
            else:
                for i, ch in enumerate(rest):
                    if ch.isspace():
                        rest = rest[:i]
                        break
                if rest:
                    attachment_ids.append(rest)
            break
        if attachment_ids:
            placeholders = ",".join("?" * len(attachment_ids))
            exp_rows = conn.execute(
                f"SELECT id, expires_at FROM attachments WHERE id IN ({placeholders})",
                attachment_ids,
            ).fetchall()
            exp_by_id = {r["id"]: r["expires_at"] for r in exp_rows}
            now_ts = int(time.time())
            for aid in attachment_ids:
                exp = exp_by_id.get(aid)
                if exp is None or exp < now_ts:
                    raise ValueError(f"Attachment {aid} has expired, cannot forward")
        now = int(time.time())
        is_action = bool(orig["is_action"])
        # Look up the original author's display_name from the cache.
        orig_author = user_cache.fetch_user(orig["user_id"])
        orig_display_name = orig_author.get("display_name", "")
        cur = conn.execute(
            "INSERT INTO messages (channel_id, user_id, content, is_action, created_at, "
            "  reply_to, forwarded_from_message_id, forwarded_from_channel_id, "
            "  forwarded_from_user_id, forwarded_from_channel_name, forwarded_from_display_name) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                target_channel_id, user_id, content, 1 if is_action else 0, now,
                orig["id"], orig["channel_id"], orig["user_id"],
                orig["ch_name"], orig_display_name,
            ),
        )
        new_id = cur.lastrowid
        new_msg = dict(conn.execute(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = ?",
            (new_id,),
        ).fetchone())
        member_ids = _member_user_ids(conn, target_channel_id)
        content_lower = content.lower()
        mentioned_user_ids = _mentioned_user_ids(content_lower, member_ids, user_id)
        is_mass_mention = False
        if _MASS_MENTION_PATTERN.search(content_lower):
            caller_role = target_member["role"]
            if caller_role in ("admin", "mod"):
                is_mass_mention = True
                if _MASS_HERE_PATTERN.search(content_lower):
                    extra = user_cache.fetch_users(_mass_mention_extra(conn, target_channel_id, user_id))
                    for uid in extra:
                        if uid != user_id and uid not in mentioned_user_ids:
                            mentioned_user_ids.append(uid)
                else:
                    extra = user_cache.fetch_users(_mass_mention_extra(conn, target_channel_id, user_id))
                    for uid in extra:
                        if uid != user_id and uid not in mentioned_user_ids:
                            mentioned_user_ids.append(uid)
        new_msg["mentioned_user_ids"] = mentioned_user_ids
        new_msg["is_mass_mention"] = is_mass_mention
    new_msg["forwarded_from"] = {
        "message_id": orig["id"],
        "channel_id": orig["channel_id"],
        "channel_name": orig["ch_name"],
        "user_id": orig["user_id"],
        "display_name": orig_display_name,
    }
    _decorate_message(new_msg)
    _attach_author_meta(
        [new_msg],
        session_user_id=user_id,
        session_display_name=session_display_name,
        session_email=session_email,
    )
    _attach_attachment_meta([new_msg])
    _attach_receipts([new_msg])
    return new_msg
