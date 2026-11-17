"""Content-addressed cache for model calls, stored in SQLite.

Key = SHA-256 of (namespace, provider, model, request payload). Identical requests
to the same model are free after the first time. This is what makes evaluation and
re-indexing affordable on free-tier quotas, and makes repeated runs reproducible.

Caveat: with temperature > 0 the first sampled answer is "frozen". That is usually
what we want for evaluation; pass use_cache=False when you want fresh samples.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

# Bump to invalidate every existing entry (e.g. after changing response formats).
CACHE_SCHEMA_VERSION = 1


def make_cache_key(namespace: str, provider: str, model: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(
        {"v": CACHE_SCHEMA_VERSION, "ns": namespace, "provider": provider, "model": model, "payload": payload},
        sort_keys=True,  # {"a":1,"b":2} and {"b":2,"a":1} must hash the same
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CallCache:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Calls happen on the event-loop thread; writes are tiny so sync sqlite is fine.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS model_calls (
                   key        TEXT PRIMARY KEY,
                   namespace  TEXT NOT NULL,
                   provider   TEXT NOT NULL,
                   model      TEXT NOT NULL,
                   response   TEXT NOT NULL,
                   created_at REAL NOT NULL
               )"""
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT response FROM model_calls WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, *, namespace: str, provider: str, model: str, response_json: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO model_calls VALUES (?, ?, ?, ?, ?, ?)",
            (key, namespace, provider, model, response_json, time.time()),
        )
        self._conn.commit()

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute("SELECT namespace, COUNT(*) FROM model_calls GROUP BY namespace").fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
