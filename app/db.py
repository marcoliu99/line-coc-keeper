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
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.config import BACKUP_DIR, BACKUP_INTERVAL_MINUTES, BACKUP_KEEP_COUNT, DATA_DIR, DB_PATH

_logger = logging.getLogger(__name__)

# Every table this project uses — deliberately a closed, small set (not
# arbitrary caller-supplied strings) since table names get interpolated
# directly into SQL below; sqlite3's parameter binding can't parametrize
# identifiers, only values, and every call site here is our own code, never
# user input.
_TABLES = (
    "group_states", "characters", "scenario_indexes", "memory_chunks", "dictionary",
    "state_checkpoints", "scene_digests",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_TRANSIENT_ROOTS = tuple(Path(path) for path in ("/tmp", "/var/tmp", "/private/tmp"))


def _warn_if_path_looks_transient(label: str, path: Path) -> None:
    resolved = path.expanduser().resolve()
    if any(resolved == root or root in resolved.parents for root in _TRANSIENT_ROOTS):
        _logger.warning(
            "%s (%s) looks transient; configure DB_PATH/DATA_DIR/BACKUP_DIR to a persistent path",
            label, resolved,
        )


def _validate_storage_paths() -> None:
    paths = {"DB_PATH": DB_PATH, "DATA_DIR": DATA_DIR, "BACKUP_DIR": BACKUP_DIR}
    for label, configured_path in paths.items():
        path = Path(configured_path).expanduser().resolve()
        directory = path.parent if label == "DB_PATH" else path
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _warn_if_path_looks_transient(label, path)
        if not os.access(directory, os.W_OK):
            raise RuntimeError(f"{label} is not writable: {path}")
    _logger.info(
        "storage_paths_ready DB_PATH=%s DATA_DIR=%s BACKUP_DIR=%s",
        DB_PATH, DATA_DIR, BACKUP_DIR,
    )


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
    _validate_storage_paths()
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


def list_json(table: str, *, prefix: str | None = None) -> list[tuple[str, Any]]:
    assert table in _TABLES, f"unknown table {table!r}"
    with _connect() as conn:
        if prefix is None:
            rows = conn.execute(f"SELECT key, data FROM {table} ORDER BY updated_at, key").fetchall()
        else:
            rows = conn.execute(
                f"SELECT key, data FROM {table} WHERE key LIKE ? ORDER BY updated_at, key",
                (f"{prefix}%",),
            ).fetchall()
    return [(key, json.loads(data)) for key, data in rows]


def delete_json_tx(conn: sqlite3.Connection, table: str, key: str) -> None:
    assert table in _TABLES, f"unknown table {table!r}"
    conn.execute(f"DELETE FROM {table} WHERE key = ?", (key,))


@contextmanager
def _backup_lock() -> Iterator[bool]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(BACKUP_DIR, 0o700)
    lock_path = BACKUP_DIR / "backup.lock"
    fd: int | None = None
    try:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # A process killed during backup cannot run the context manager's
            # cleanup. Reclaim only a lock whose owner is gone and whose age is
            # beyond two scheduled intervals; a live worker is never touched.
            stale_after = max(300, BACKUP_INTERVAL_MINUTES * 120)
            try:
                age = time.time() - lock_path.stat().st_mtime
                raw = lock_path.read_text(encoding="ascii")
                pid = int(raw.partition("=")[2].strip())
                os.kill(pid, 0)
                owner_alive = True
            except (FileNotFoundError, ValueError, ProcessLookupError):
                owner_alive = False
                age = stale_after
            except PermissionError:
                # The PID may belong to another service user. Treat that as
                # live rather than risking deletion of an active lock.
                owner_alive = True
                age = 0
            if not owner_alive and age >= stale_after:
                lock_path.unlink(missing_ok=True)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            else:
                yield False
                return
        os.write(fd, f"pid={os.getpid()}\n".encode())
        yield True
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def backup_now(reason: str = "scheduled") -> Path | None:
    """Create a consistent SQLite backup, or return None if another worker owns the lock."""
    started = time.monotonic()
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason) or "manual"
    _logger.info("backup_started reason=%s", safe_reason)
    final_path: Path | None = None
    try:
        with _backup_lock() as acquired:
            if not acquired:
                _logger.info("backup_skipped reason=lock_busy")
                return None
            BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            final_path = BACKUP_DIR / f"coc_bot-{stamp}-{uuid4().hex[:8]}-{safe_reason}.db"
            temp_path = BACKUP_DIR / f".{final_path.name}.{os.getpid()}.tmp"
            try:
                source = sqlite3.connect(DB_PATH)
                target = sqlite3.connect(temp_path)
                try:
                    source.backup(target)
                    target.commit()
                finally:
                    target.close()
                    source.close()
                with temp_path.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.chmod(temp_path, 0o600)
                os.replace(temp_path, final_path)
                if reason == "scheduled":
                    scheduled = sorted(BACKUP_DIR.glob("coc_bot-*-scheduled.db"))
                    for old in scheduled[:-BACKUP_KEEP_COUNT]:
                        old.unlink(missing_ok=True)
                size = final_path.stat().st_size
                _logger.info(
                    "backup_success reason=%s path=%s size_bytes=%s duration_ms=%s",
                    safe_reason, final_path, size, int((time.monotonic() - started) * 1000),
                )
                return final_path
            finally:
                temp_path.unlink(missing_ok=True)
    except Exception:
        _logger.exception(
            "backup_failure reason=%s duration_ms=%s",
            safe_reason, int((time.monotonic() - started) * 1000),
        )
        raise
