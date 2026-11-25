"""Vector cache keyed by (model, exact text): never embed the same text twice.

Across the M5 grid many chunks are identical (a change record is one whole chunk
in every structure size, and the same text is a child in parent_child). On a CPU
doing ~4 chunks/s that saves a lot of time. Vectors are float32 blobs in SQLite.
"""

from __future__ import annotations

import hashlib
import sqlite3
from array import array
from pathlib import Path


def text_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


class EmbeddingCache:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, dims INTEGER, vec BLOB)")

    def get_many(self, keys: list[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for start in range(0, len(keys), 500):  # SQLite parameter limit
            part = keys[start : start + 500]
            rows = self.conn.execute(
                f"SELECT key, vec FROM vectors WHERE key IN ({','.join('?' * len(part))})", part
            ).fetchall()
            for key, blob in rows:
                found[key] = array("f", blob).tolist()
        return found

    def put_many(self, items: dict[str, list[float]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO vectors (key, dims, vec) VALUES (?, ?, ?)",
            [(k, len(v), array("f", v).tobytes()) for k, v in items.items()],
        )
        self.conn.commit()

    def __len__(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
