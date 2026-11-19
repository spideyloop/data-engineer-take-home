"""SQLite schema and the shared connection for one CLI invocation."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    digest TEXT NOT NULL,
    body BLOB NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (url, status, content_type, digest)
);
CREATE TABLE IF NOT EXISTS extractions (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    posting_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    source TEXT NOT NULL,
    listing_key TEXT NOT NULL,
    role_key TEXT NOT NULL,
    posting_id TEXT NOT NULL REFERENCES jobs(posting_id),
    url TEXT NOT NULL,
    requisition TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    seen_at TEXT NOT NULL,
    PRIMARY KEY (source, listing_key, role_key)
);
CREATE INDEX IF NOT EXISTS listings_job ON listings(posting_id);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    posting_id TEXT NOT NULL REFERENCES jobs(posting_id),
    field TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def save_response(self, response: httpx.Response) -> None:
        now = utc_now()
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        with self.db:
            self.db.execute(
                """INSERT INTO responses VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (url, status, content_type, digest)
                   DO UPDATE SET last_seen = excluded.last_seen""",
                (
                    str(response.url),
                    response.status_code,
                    response.headers.get("content-type", ""),
                    digest,
                    body,
                    now,
                    now,
                ),
            )

    def cached_extraction(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT payload FROM extractions WHERE cache_key = ?", (key,)
        ).fetchone()
        return row["payload"] if row else None

    def save_extraction(self, key: str, payload: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO extractions VALUES (?, ?)", (key, payload)
            )
