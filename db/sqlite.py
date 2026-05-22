"""Shared SQLite connection helpers.

This module centralizes a small compatibility layer used across the codebase.
Several services import `db.sqlite` for connection setup, row factory toggles,
and path diagnostics. The implementation stays intentionally small so existing
call sites keep working without behavioral refactors.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _normalize_path(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")


def connect(db_path: str | Path, row_factory: bool = False) -> sqlite3.Connection:
    path = _normalize_path(db_path)
    conn = sqlite3.connect(str(path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def connect_init(db_path: str | Path, row_factory: bool = False) -> sqlite3.Connection:
    """Create parent dirs if needed and return a ready-to-use connection."""
    return connect(db_path, row_factory=row_factory)


def describe_db_path(db_path: str | Path) -> str:
    path = Path(db_path)
    if path.exists():
        return str(path.resolve())
    return f"{path.resolve()} (missing)"

