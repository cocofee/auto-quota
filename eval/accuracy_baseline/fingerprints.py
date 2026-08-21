from __future__ import annotations

from typing import Any


def normalize_fingerprint_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def query_fingerprint(query_text: Any) -> str:
    return normalize_fingerprint_text(query_text)


def province_query_fingerprint(province: Any, query_text: Any) -> str:
    normalized_province = normalize_fingerprint_text(province)
    normalized_query = query_fingerprint(query_text)
    if not normalized_province or not normalized_query:
        return ""
    return f"{normalized_province}|{normalized_query}"


__all__ = [
    "normalize_fingerprint_text",
    "province_query_fingerprint",
    "query_fingerprint",
]
