from __future__ import annotations

import re
from typing import Any

from src.context_builder import detect_system_hint, normalize_system_hint
from src.province_plugins import resolve_plugin_hints
from src.specialty_classifier import (
    BOOKS,
    BORROW_PRIORITY,
    FAMILY_ALLOWED_BOOKS,
    SYSTEM_HINT_TO_BOOK,
    parse_section_title,
)


def _dedupe_keep_order(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_book_code(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in BOOKS:
        return text
    if re.fullmatch(r"C\d+", text):
        return text if text in BOOKS else ""
    if text.isdigit():
        normalized = f"C{int(text)}"
        return normalized if normalized in BOOKS else ""
    return ""


def _normalize_book_list(values) -> list[str]:
    return _dedupe_keep_order(_normalize_book_code(value) for value in (values or []))


def _book_from_system_hint(value: str) -> str:
    text = normalize_system_hint(str(value or "").strip())
    return str(SYSTEM_HINT_TO_BOOK.get(text) or "").strip()


def build_unified_search_plan(
    *,
    province: str = "",
    item: dict[str, Any] | None = None,
    context_prior: dict[str, Any] | None = None,
    canonical_features: dict[str, Any] | None = None,
    plugin_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(item or {})
    context_prior = dict(context_prior or {})
    canonical_features = dict(canonical_features or {})
    plugin_hints = dict(
        plugin_hints
        or resolve_plugin_hints(
            province=province,
            item=item,
            canonical_features=canonical_features,
        )
        or {}
    )

    section = str(item.get("section") or "").strip()
    sheet_name = str(item.get("sheet_name") or "").strip()
    name = str(item.get("name") or "").strip()
    desc = str(item.get("description") or "").strip()
    batch_context = dict(context_prior.get("batch_context") or {})

    explicit_books = _normalize_book_list([
        parse_section_title(section),
        parse_section_title(sheet_name),
    ])
    strong_system_books = _normalize_book_list([
        _book_from_system_hint(detect_system_hint(section)),
        _book_from_system_hint(detect_system_hint(sheet_name)),
        _book_from_system_hint(batch_context.get("section_system_hint")),
        _book_from_system_hint(batch_context.get("sheet_system_hint")),
    ])
    item_system_books = _normalize_book_list([
        _book_from_system_hint(detect_system_hint(name, desc)),
        _book_from_system_hint(detect_system_hint(desc)),
    ])

    soft_system_books = _normalize_book_list([
        _book_from_system_hint(context_prior.get("system_hint")),
        _book_from_system_hint(batch_context.get("neighbor_system_hint")),
        _book_from_system_hint(batch_context.get("project_system_hint")),
        *item_system_books,
    ])

    family = str(
        canonical_features.get("family")
        or context_prior.get("prior_family")
        or ""
    ).strip()
    family_books = _normalize_book_list(FAMILY_ALLOWED_BOOKS.get(family, ()))

    seed_specialty = _normalize_book_code(item.get("specialty") or context_prior.get("specialty"))
    plugin_books = _normalize_book_list(plugin_hints.get("preferred_books", []))
    plugin_specialties = _normalize_book_list(plugin_hints.get("preferred_specialties", []))
    search_aliases = _dedupe_keep_order(plugin_hints.get("synonym_aliases", []))[:3]

    primary_book = next(
        (
            book for book in (
                explicit_books
                + strong_system_books
                + plugin_books
                + plugin_specialties
                + family_books
                + ([seed_specialty] if seed_specialty else [])
                + soft_system_books
            )
            if book
        ),
        "",
    )
    borrow_books = BORROW_PRIORITY.get(primary_book, [])[:2] if primary_book else []

    preferred_books = _normalize_book_list(
        explicit_books
        + strong_system_books
        + plugin_books
        + plugin_specialties
        + family_books
        + ([primary_book] if primary_book else [])
        + list(borrow_books)
        + soft_system_books
    )[:6]

    hard_books = _normalize_book_list(explicit_books + strong_system_books)

    route_mode = "open"
    if hard_books:
        route_mode = "strict"
    elif preferred_books or search_aliases:
        route_mode = "moderate"

    reason_tags = []
    if explicit_books:
        reason_tags.append("explicit_book_anchor")
    if strong_system_books:
        reason_tags.append("strong_system_anchor")
    if family_books:
        reason_tags.append("family_cluster")
    if plugin_books or plugin_specialties or search_aliases:
        reason_tags.append("province_plugin")
    if seed_specialty:
        reason_tags.append("seed_specialty")

    merged_plugin_hints = dict(plugin_hints)
    if preferred_books:
        merged_plugin_hints["preferred_books"] = _dedupe_keep_order(
            list(plugin_hints.get("preferred_books", []) or []) + preferred_books
        )[:6]
    if search_aliases:
        merged_plugin_hints["synonym_aliases"] = search_aliases
    if route_mode == "strict" and hard_books:
        merged_plugin_hints["strict_preferred_books"] = True

    return {
        "province": str(province or "").strip(),
        "primary_book": primary_book,
        "preferred_books": preferred_books,
        "hard_books": hard_books,
        "borrow_books": _normalize_book_list(borrow_books),
        "family": family,
        "family_books": family_books,
        "seed_specialty": seed_specialty,
        "search_aliases": search_aliases,
        "route_mode": route_mode,
        "allow_cross_book_escape": route_mode != "strict",
        "reason_tags": reason_tags,
        "plugin_hints": merged_plugin_hints,
    }
