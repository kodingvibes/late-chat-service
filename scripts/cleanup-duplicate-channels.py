#!/usr/bin/env python3
"""One-time cleanup: collapse duplicate seed channels.

The original chat-bridge schema didn't have UNIQUE on channels.name,
and the _seed_channels() helper ran on every container restart. Each
restart added 6 more copies of #lobby, #random, #dev, #infra,
General, and Music. Production has 256 copies each.

This script:
1. Picks the lowest-id row for each duplicate (the original; the
   higher-id ones were created by the seed re-runs).
2. Reassigns FK references from the duplicates to the kept row:
   - channel_members.channel_id
   - messages.channel_id
   - voice_notes.channel_id
   - attachments.channel_id
   - reactions (via message_id) — the kept channel is the one
     whose message the reaction lives in, so no rewrite needed.
   - message_delivered, message_reads — same as reactions.
3. Deletes the duplicate rows.
4. Adds a UNIQUE index on channels.name so this can't happen
   again. The CREATE TABLE in core/db.py already declares the
   column UNIQUE; the index makes it match.

The whole thing runs in a single transaction with a backup.

Usage:  python3 scripts/cleanup-duplicate-channels.py /data/late-chat-service/chat.db
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time


# ponytail: every chat-side table that references channels.id.
# Each (table, channel_column) tuple lists a column we'd need to
# remap if a duplicate channel row gets deleted. The kept channel
# keeps the original id; duplicates get reassigned to it.
CHANNEL_FK_TABLES = (
    ("channel_members", "channel_id"),
    ("messages", "channel_id"),
    ("voice_notes", "channel_id"),
    ("attachments", "channel_id"),
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("db", help="path to chat.db")
    p.add_argument("--no-backup", action="store_true")
    args = p.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    backup = db_path + f".pre-cleanup-dupes-{int(time.time())}.bak"
    if not args.no_backup:
        shutil.copy2(db_path, backup)
        for ext in ("-wal", "-shm"):
            src = db_path + ext
            if os.path.exists(src):
                shutil.copy2(src, backup + ext)
        print(f"backup: {backup}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")

        # Find duplicate groups: (name, kept_id)
        cur = conn.execute("""
            SELECT name, MIN(id) AS kept_id
            FROM channels
            GROUP BY name
            HAVING COUNT(*) > 1
        """)
        groups = cur.fetchall()
        if not groups:
            print("nothing to do; no duplicate channels")
            return 0

        total_dupes = 0
        for name, kept_id in groups:
            dup_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM channels WHERE name = ? AND id != ?",
                    (name, kept_id),
                ).fetchall()
            ]
            n = len(dup_ids)
            total_dupes += n
            print(f"  {name}: keep id={kept_id}, drop {n} duplicates {dup_ids[:5]}{'...' if n > 5 else ''}")

            placeholders = ",".join("?" for _ in dup_ids)
            # ponytail: kept_id binds to the SET col in UPDATE
            # statements, but DELETE doesn't need a SET binding —
            # only the IN list. Two separate param tuples keeps
            # SQLite happy and reads more clearly than counting
            # placeholders by hand.
            update_params = [kept_id] + dup_ids
            delete_params = list(dup_ids)

            for table, col in CHANNEL_FK_TABLES:
                cur = conn.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if not cur.fetchone():
                    continue
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} IN ({placeholders})",
                    update_params,
                )

            # Reactions: keys include message_id which already lives
            # under the kept channel, so no channel rewrite. But if a
            # reaction was somehow attached via the wrong table, leave
            # it — reactions only live under messages, never channels.
            conn.execute(
                f"DELETE FROM channels WHERE id IN ({placeholders})",
                delete_params,
            )

        # Add the missing UNIQUE index so this can't happen again.
        # The CREATE TABLE in core/db.py already declares name
        # UNIQUE NOT NULL, but the table predates that schema
        # version, so the index has to be added explicitly.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_name ON channels(name)")
        print(f"added idx_channels_name (UNIQUE)")

        conn.commit()
        print(f"collapsed {total_dupes} duplicate channel rows")
    except Exception as e:
        conn.rollback()
        print(f"cleanup failed: {e}", file=sys.stderr)
        if not args.no_backup:
            print("restoring from backup", file=sys.stderr)
            shutil.copy2(backup, db_path)
            for ext in ("-wal", "-shm"):
                src = backup + ext
                if os.path.exists(src):
                    shutil.copy2(src, db_path + ext)
        return 1
    finally:
        conn.close()

    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        print(f"final channel count: {n}")
        for row in conn.execute("SELECT name, COUNT(*) FROM channels GROUP BY name HAVING COUNT(*) > 1"):
            print(f"  WARNING: still duplicate: {row}", file=sys.stderr)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
