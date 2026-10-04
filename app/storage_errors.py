"""Errors shared by the storage layers, kept apart from ``db`` so that they
keep one identity even when a test reloads ``app.db``."""
from __future__ import annotations


class NestedTransactionError(RuntimeError):
    """A write was attempted on a second connection while a game-state
    transaction holds SQLite's write lock. Waiting would only time out."""
