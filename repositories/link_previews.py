import json
import time
from core.db import db


def get_cached(url: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT url, data, fetched_at FROM link_previews WHERE url = ?", (url,)
        ).fetchone()
    if not row:
        return None
    return {"url": row["url"], "data": json.loads(row["data"]), "fetched_at": row["fetched_at"]}


def set_cached(url: str, data: dict) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO link_previews (url, data, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
            (url, json.dumps(data), int(time.time())),
        )
