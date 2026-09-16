"""SQLite-backed key/value persistence, replacing the previous flat
`data/groups/*.json` files (see app/state.py's old implementation and
docs/changelog.md's "長期使用建議改接資料庫" note).

This is a storage-BACKEND swap, not a relational redesign: every domain that
used to be "one JSON file per key" (a group's GroupState, a character index
mirror, a scenario RAG index cache, a group's memory RAG chunks) is still
exactly one JSON blob per key here, just stored as a row in a SQLite table
instead of a file on disk. Callers' existing to_dict()/from_dict() (or
equivalent) contracts are completely unchanged — this module only knows
"json-serializable value in, same value back out", never what's inside it.

Why this over flat files: the old "read whole file, write whole file"
pattern has no atomicity — a crash mid-write, or two near-simultaneous
writes, can corrupt or silently lose data. Each set_json() call here is one
SQLite transaction: it either fully lands or doesn't happen at all. It also
turns "several scattered files per group" into one single file to back up
or move to a new host.

Scenario page images (PNG) are NOT part of this — they stay as plain files
under DATA_DIR (see app/state.py's save_page_image/load_page_image), since
they were never the *.json reliability problem this addresses and BLOB
storage would add complexity for no real benefit at this scale.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import DB_PATH

# Every table this project uses — deliberately a closed, small set (not
# arbitrary caller-supplied strings) since table names get interpolated
# directly into SQL below; sqlite3's parameter binding can't parametrize
# identifiers, only values, and every call site here is our own code, never
# user input.
_TABLES = ("group_states", "characters", "scenario_indexes", "memory_chunks")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """One short-lived connection per call — this project's write volume is
    "once per chat message", nowhere near hot-loop territory, so the
    simplicity of never sharing a connection across threads/coroutines is
    worth more than the (negligible here) cost of reopening one each time.
    WAL mode lets a read and a write overlap without blocking each other,
    which matters once LINE (a threaded ASGI server) and Discord (its own
    asyncio loop) are both touching the same database file from the same
    process.

    Only sets `synchronous` here, not `journal_mode` — WAL is a property
    persisted in the database file's own header (see _ensure_tables, which
    sets it once, on the very first connection this process ever makes), so
    re-asserting it on every single connection is pure overhead: measured at
    roughly the same per-call cost as `synchronous` itself (~0.25ms each on
    this machine), i.e. re-running it here would silently double every
    connection's PRAGMA cost for zero effect. `synchronous` is NOT persisted
    to the file (it's a per-connection setting, defaulting to FULL), so it
    does need setting on every connection to take effect for it."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_tables() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # set once here — see _connect's docstring
        for table in _TABLES:
            conn.execute(_SCHEMA.format(table=table))
        conn.commit()
    finally:
        conn.close()


_ensure_tables()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Public entry point for batching multiple writes into a single
    connection/transaction — same connection lifecycle as the internal
    _connect() (the `synchronous` pragma, commit on success, always closes),
    just exposed under a name callers outside this module are meant to use.
    See set_json_tx below and app/state.py's save_state, which used to call
    set_json once per character on top of once for the group state itself —
    N+1 separate connections (each paying its own PRAGMA overhead) for what
    is logically one atomic save."""
    with _connect() as conn:
        yield conn


def set_json_tx(conn: sqlite3.Connection, table: str, key: str, value: Any) -> None:
    """Same upsert as set_json, but writes through an already-open
    connection (from transaction() above) instead of opening/closing its
    own — for batching several writes into one transaction."""
    assert table in _TABLES, f"unknown table {table!r}"
    payload = json.dumps(value, ensure_ascii=False)
    conn.execute(
        f"INSERT INTO {table} (key, data, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (key, payload),
    )


def get_json(table: str, key: str) -> Any | None:
    """Returns the parsed JSON value stored under `key`, or None if there's
    no row for it yet — callers should treat that exactly like "the file
    didn't exist yet" did before this module existed, not as an error."""
    assert table in _TABLES, f"unknown table {table!r}"
    with _connect() as conn:
        row = conn.execute(f"SELECT data FROM {table} WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def set_json(table: str, key: str, value: Any) -> None:
    """Upserts `value` (anything json.dumps can serialize) under `key`."""
    assert table in _TABLES, f"unknown table {table!r}"
    payload = json.dumps(value, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {table} (key, data, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (key, payload),
        )


def delete_json(table: str, key: str) -> None:
    assert table in _TABLES, f"unknown table {table!r}"
    with _connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE key = ?", (key,))


def list_keys(table: str) -> list[str]:
    assert table in _TABLES, f"unknown table {table!r}"
    with _connect() as conn:
        rows = conn.execute(f"SELECT key FROM {table}").fetchall()
    return [r[0] for r in rows]
