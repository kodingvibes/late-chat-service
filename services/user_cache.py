"""Display-name cache for message authors and voice-room rosters.

The chat-bridge used to JOIN against a local users table. That table
moved to late-auth-service. To keep message rendering fast (no
network round-trip per message), we cache user metadata in-process
and refresh from late-auth on miss.

Sync API because chat-bridge is sync. The cache is per-process and
has a short TTL so a rename in late-auth propagates within minutes
without us implementing a pub/sub channel.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

from core.config import LATE_AUTH_SECRET, LATE_AUTH_URL

_TTL_SECONDS = 300
_CACHE: dict[int, tuple[dict, float]] = {}


def _now() -> float:
    return time.time()


def _is_fresh(entry: tuple[dict, float]) -> bool:
    return (_now() - entry[1]) < _TTL_SECONDS


def get_cached(user_id: int) -> Optional[dict]:
    entry = _CACHE.get(user_id)
    if not entry:
        return None
    if not _is_fresh(entry):
        _CACHE.pop(user_id, None)
        return None
    return entry[0]


def _store(user_id: int, payload: dict) -> None:
    _CACHE[user_id] = (payload, _now())


def _empty() -> dict:
    return {"display_name": "", "email": ""}


def fetch_user(user_id: int) -> dict:
    """Return {display_name, email} for `user_id`. Sync.

    On a miss, hits late-auth /api/auth/users/{id} and stores the
    result. Empty strings on any failure (auth service down, user
    deleted, etc.) so the caller can always render a row.
    """
    cached = get_cached(user_id)
    if cached is not None:
        return cached
    try:
        with httpx.Client() as client:
            r = client.get(
                f"{LATE_AUTH_URL}/api/auth/users/{user_id}",
                headers={"Authorization": f"Bearer {LATE_AUTH_SECRET}"},
                timeout=2.0,
            )
        if r.status_code == 200:
            data = r.json()
            payload = {
                "display_name": data.get("display_name", "") or "",
                "email": data.get("email", "") or "",
            }
            _store(user_id, payload)
            return payload
    except Exception:
        pass
    payload = _empty()
    _store(user_id, payload)
    return payload


def fetch_users(user_ids: list[int]) -> dict[int, dict]:
    """Return {user_id: {display_name, email}} for the given ids.

    Cached entries are returned synchronously; misses go to late-auth
    in a single batched call. Empty strings on any failure.
    """
    out: dict[int, dict] = {}
    misses: list[int] = []
    for uid in user_ids:
        cached = get_cached(uid)
        if cached is not None:
            out[uid] = cached
        else:
            misses.append(uid)
    if misses:
        try:
            with httpx.Client() as client:
                r = client.get(
                    f"{LATE_AUTH_URL}/api/auth/users/batch",
                    params=[("id", i) for i in misses],
                    headers={"Authorization": f"Bearer {LATE_AUTH_SECRET}"},
                    timeout=2.0,
                )
            if r.status_code == 200:
                by_id = {row["id"]: row for row in r.json().get("users", [])}
                for uid in misses:
                    row = by_id.get(uid)
                    if row:
                        payload = {
                            "display_name": row.get("display_name", "") or "",
                            "email": row.get("email", "") or "",
                        }
                    else:
                        payload = _empty()
                    _store(uid, payload)
                    out[uid] = payload
            else:
                raise RuntimeError(f"batch status {r.status_code}")
        except Exception:
            for uid in misses:
                payload = _empty()
                _store(uid, payload)
                out[uid] = payload
    return out


def invalidate(user_id: int) -> None:
    _CACHE.pop(user_id, None)


def prime(user_id: int, display_name: str = "", email: str = "") -> None:
    """Inject a known-good entry into the cache.

    Useful for tests and for the common case where the requester
    is the message author: their session already has the values, so
    skip the round-trip.

    If a fresh entry already exists for this user, it is left
    alone (the source of truth is the validated session, not a
    speculative call from a router that didn't actually see the
    session). To force a refresh, call `invalidate` first.
    """
    if get_cached(user_id) is not None:
        return
    _store(user_id, {"display_name": display_name, "email": email})
