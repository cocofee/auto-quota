from __future__ import annotations

import re
import threading
from pathlib import Path

from loguru import logger

import config
from db.sqlite import connect as _db_connect
from src.specialty_classifier import detect_db_type

_available_books_cache: dict[str, set[str]] = {}
_available_books_lock = threading.Lock()

_INSTALL_ROUTE_BOOK_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "安徽省安装工程计价定额(2018)": {
        "C12": ["A11"],
    },
}


def _province_key(province: str | None) -> str:
    return str(province or config.get_current_province() or "").strip()


def _connect_quota_db(db_path: Path):
    return _db_connect(db_path)


def normalize_route_book_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    match = re.match(r"^A0*(\d+)$", raw)
    if match:
        return f"C{int(match.group(1))}"
    match = re.match(r"^C0*(\d+)$", raw)
    if match:
        return f"C{int(match.group(1))}"
    match = re.match(r"^0*(\d+)$", raw)
    if match:
        return f"C{int(match.group(1))}"
    return raw


def normalize_db_book_code(value: object) -> str:
    return str(value or "").strip().upper()


def _build_install_db_book_reverse_overrides() -> dict[str, dict[str, str]]:
    return {
        province: {
            normalize_db_book_code(db_book): normalize_route_book_code(route_book)
            for route_book, db_books in route_overrides.items()
            for db_book in db_books
            if normalize_db_book_code(db_book) and normalize_route_book_code(route_book)
        }
        for province, route_overrides in _INSTALL_ROUTE_BOOK_OVERRIDES.items()
    }


_INSTALL_DB_BOOK_REVERSE_OVERRIDES = _build_install_db_book_reverse_overrides()


def get_available_db_books(province: str | None = None) -> set[str]:
    key = _province_key(province)
    cached = _available_books_cache.get(key)
    if cached is not None:
        return set(cached)

    with _available_books_lock:
        cached = _available_books_cache.get(key)
        if cached is not None:
            return set(cached)

        books: set[str] = set()
        db_path = config.get_quota_db_path(key)
        try:
            conn = _connect_quota_db(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT DISTINCT book FROM quotas "
                    "WHERE book IS NOT NULL AND TRIM(book) != ''"
                )
                books = {
                    normalize_db_book_code(row[0])
                    for row in cursor.fetchall()
                    if normalize_db_book_code(row[0])
                }
            finally:
                conn.close()
        except Exception as exc:
            logger.debug(f"province_book_mapper load books failed: province={key} error={exc}")

        _available_books_cache[key] = set(books)
        return set(books)


def clear_available_db_books_cache(province: str | None = None) -> None:
    key = _province_key(province)
    with _available_books_lock:
        if province is None:
            _available_books_cache.clear()
        else:
            _available_books_cache.pop(key, None)


def map_route_book_to_db_books(
    route_book: object,
    province: str | None = None,
    available_books: set[str] | None = None,
) -> list[str]:
    normalized = normalize_route_book_code(route_book)
    if not normalized:
        return []

    province_name = _province_key(province)
    available = {
        normalize_db_book_code(book)
        for book in (available_books if available_books is not None else get_available_db_books(province_name))
        if normalize_db_book_code(book)
    }
    if normalized in available:
        return [normalized]

    db_type = detect_db_type(province_name)
    if db_type != "install":
        return [normalized]

    province_overrides = _INSTALL_ROUTE_BOOK_OVERRIDES.get(province_name, {})
    override_books = [
        normalize_db_book_code(book)
        for book in province_overrides.get(normalized, [])
        if normalize_db_book_code(book)
    ]
    if override_books:
        if not available:
            return override_books
        filtered_overrides = [book for book in override_books if book in available]
        if filtered_overrides:
            return filtered_overrides

    match = re.match(r"^C(\d+)$", normalized)
    if not match:
        return [normalized]

    ordinal = int(match.group(1))
    candidates = [
        f"A{ordinal}",
        str(ordinal),
        f"{ordinal:02d}",
    ]
    if available:
        filtered = [candidate for candidate in candidates if candidate in available]
        if filtered:
            return filtered
    return [candidates[0]]


def map_db_book_to_route_book(
    db_book: object,
    province: str | None = None,
) -> str:
    normalized = normalize_db_book_code(db_book)
    if not normalized:
        return ""

    province_name = _province_key(province)
    db_type = detect_db_type(province_name)
    if db_type != "install":
        return normalize_route_book_code(normalized)

    reverse_overrides = _INSTALL_DB_BOOK_REVERSE_OVERRIDES.get(province_name, {})
    if normalized in reverse_overrides:
        return reverse_overrides[normalized]

    match = re.match(r"^A0*(\d+)$", normalized)
    if match:
        return f"C{int(match.group(1))}"
    match = re.match(r"^0*(\d+)$", normalized)
    if match:
        return f"C{int(match.group(1))}"
    return normalize_route_book_code(normalized)


def normalize_requested_books_for_search(
    books: list[str] | None,
    province: str | None = None,
    available_books: set[str] | None = None,
) -> list[str] | None:
    requested = [
        str(book or "").strip()
        for book in (books or [])
        if str(book or "").strip()
    ]
    if not requested:
        return None

    resolved: list[str] = []
    saw_broad_group = False
    available = available_books if available_books is not None else get_available_db_books(province)
    available_normalized = {
        normalize_db_book_code(book)
        for book in (available or set())
        if normalize_db_book_code(book)
    }

    for book in requested:
        route_book = normalize_route_book_code(book)
        if len(route_book) == 1 and route_book in {"A", "D", "E"}:
            saw_broad_group = True
            prefixed = sorted(
                candidate
                for candidate in available_normalized
                if candidate == route_book or candidate.startswith(route_book)
            )
            resolved.extend(prefixed)
            continue

        resolved.extend(
            map_route_book_to_db_books(
                route_book,
                province=province,
                available_books=available_normalized,
            )
        )

    resolved = list(dict.fromkeys(book for book in resolved if book))
    if resolved:
        return resolved
    if saw_broad_group:
        return None
    return None
