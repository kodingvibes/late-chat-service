from core.db import db
from services.broadcaster import ws_manager
import time


def mark_delivered(message_id: int, user_ids: list[int]) -> list[int]:
    """Mark a message as delivered to each user_id (they got it via WS).
    Returns the list of user_ids that were newly marked (so callers can
    broadcast an event only for those). Self-deliveries are ignored by
    the caller; we still record them as no-op here. The (message_id,
    user_id) PK makes INSERT OR IGNORE idempotent. """
    if not user_ids:
        return []
    with db() as conn:
        existing = {
            r["user_id"] for r in conn.execute(
                "SELECT user_id FROM message_delivered WHERE message_id = ?",
                (message_id,),
            ).fetchall()
        }
        new = [u for u in user_ids if u not in existing]
        if new:
            now = int(time.time())
            # ponytail: single INSERT…VALUES with a multi-row VALUES
            # list. N round-trips collapsed into one. (u, message_id, now)
            # is a 3-tuple per user; the executemany-style bind keeps
            # the driver happy without a Python loop on the conn.
            placeholders = ",".join(
                f"(?, {message_id}, ?)" for _ in new
            )
            params: list = []
            for uid in new:
                params.extend([uid, now])
            conn.execute(
                f"INSERT OR IGNORE INTO message_delivered (user_id, message_id, delivered_at) "
                f"VALUES {placeholders}",
                params,
            )
    return new


def mark_read(message_id: int, user_id: int) -> int | None:
    """Mark a message as read by user_id. Returns the read_at timestamp
    if the row was newly created (None if it was already read). """
    now = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO message_reads (message_id, user_id, read_at) VALUES (?, ?, ?) RETURNING read_at",
            (message_id, user_id, now),
        )
        row = cur.fetchone()
    return row["read_at"] if row else None


def receipt_counts(message_ids: list[int]) -> dict[int, dict[str, int]]:
    """For each message_id, return {'delivered': N, 'read': M}. The
    N counts members that received the WS message; M counts members
    that opened the channel up to that point. The sender counts
    toward neither. Used to populate the bubble checkmarks on
    history loads. """
    if not message_ids:
        return {}
    out: dict[int, dict[str, int]] = {mid: {"delivered": 0, "read": 0} for mid in message_ids}
    placeholders = ",".join("?" * len(message_ids))
    with db() as conn:
        for r in conn.execute(
            f"SELECT message_id, COUNT(*) AS c FROM message_delivered WHERE message_id IN ({placeholders}) GROUP BY message_id",
            message_ids,
        ).fetchall():
            out[r["message_id"]]["delivered"] = r["c"]
        for r in conn.execute(
            f"SELECT message_id, COUNT(*) AS c FROM message_reads WHERE message_id IN ({placeholders}) GROUP BY message_id",
            message_ids,
        ).fetchall():
            out[r["message_id"]]["read"] = r["c"]
    return out


def member_count_for_channels(channel_ids: list[int]) -> dict[int, int]:
    """Return {channel_id: member_count} for the given channels. Used
    to compute the "all read" checkmark: read == member_count - 1. """
    if not channel_ids:
        return {}
    placeholders = ",".join("?" * len(channel_ids))
    with db() as conn:
        rows = conn.execute(
            f"SELECT channel_id, COUNT(*) AS c FROM channel_members WHERE channel_id IN ({placeholders}) GROUP BY channel_id",
            channel_ids,
        ).fetchall()
    return {r["channel_id"]: r["c"] for r in rows}
