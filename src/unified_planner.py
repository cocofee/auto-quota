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
    book_matches_province_scope,
    parse_section_title,
    province_uses_standard_route_books,
)
from src.subject_family_guard import should_suppress_family_hint


from src.utils import dedupe_keep_order


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
    return dedupe_keep_order(_normalize_book_code(value) for value in (values or []))


def _filter_books_by_province_scope(values, province: str) -> list[str]:
    books = _normalize_book_list(values)
    province = str(province or "").strip()
    if not province:
        return books
    if not province_uses_standard_route_books(province):
        return [book for book in books if book not in BORROW_PRIORITY]
    filtered: list[str] = []
    for book in books:
        if book in BORROW_PRIORITY and not book_matches_province_scope(book, province):
            continue
        filtered.append(book)
    return filtered


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

    explicit_books = _filter_books_by_province_scope([
        parse_section_title(section),
        parse_section_title(sheet_name),
    ], province)
    strong_system_books = _filter_books_by_province_scope([
        _book_from_system_hint(detect_system_hint(section)),
        _book_from_system_hint(detect_system_hint(sheet_name)),
        _book_from_system_hint(batch_context.get("section_system_hint")),
        _book_from_system_hint(batch_context.get("sheet_system_hint")),
    ], province)
    item_system_books = _filter_books_by_province_scope([
        _book_from_system_hint(detect_system_hint(name, desc)),
        _book_from_system_hint(detect_system_hint(desc)),
    ], province)

    soft_system_books = _filter_books_by_province_scope([
        _book_from_system_hint(context_prior.get("system_hint")),
        _book_from_system_hint(batch_context.get("neighbor_system_hint")),
        _book_from_system_hint(batch_context.get("project_system_hint")),
        *item_system_books,
    ], province)

    family = str(
        canonical_features.get("family")
        or context_prior.get("prior_family")
        or ""
    ).strip()
    suppress_family_hint = should_suppress_family_hint(family, context_prior)
    if suppress_family_hint:
        family = ""
    family_books = _filter_books_by_province_scope(FAMILY_ALLOWED_BOOKS.get(family, ()), province)

    seed_specialty = _normalize_book_code(item.get("specialty") or context_prior.get("specialty"))
    if (
        seed_specialty
        and province
        and (
            (not province_uses_standard_route_books(province))
            or (not book_matches_province_scope(seed_specialty, province))
        )
    ):
        seed_specialty = ""
    if suppress_family_hint and plugin_hints.get("source") == "generated_benchmark_knowledge":
        plugin_hints = dict(plugin_hints)
        for key in (
            "preferred_books",
            "preferred_specialties",
            "synonym_aliases",
            "preferred_quota_names",
            "avoided_quota_names",
        ):
            plugin_hints[key] = []
        plugin_hints["family_hint_suppressed"] = True

    plugin_books = _filter_books_by_province_scope(plugin_hints.get("preferred_books", []), province)
    plugin_specialties = _filter_books_by_province_scope(plugin_hints.get("preferred_specialties", []), province)
    search_aliases = dedupe_keep_order(plugin_hints.get("synonym_aliases", []))[:3]

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
    borrow_books = _filter_books_by_province_scope(
        BORROW_PRIORITY.get(primary_book, [])[:2] if primary_book else [],
        province,
    )

    preferred_books = _filter_books_by_province_scope(
        explicit_books
        + strong_system_books
        + plugin_books
        + plugin_specialties
        + family_books
        + ([primary_book] if primary_book else [])
        + list(borrow_books)
        + soft_system_books
    , province)[:6]

    hard_books = _filter_books_by_province_scope(explicit_books + strong_system_books, province)

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
    if suppress_family_hint:
        reason_tags.append("primary_subject_guard")

    merged_plugin_hints = dict(plugin_hints)
    if preferred_books:
        merged_plugin_hints["preferred_books"] = _filter_books_by_province_scope(
            list(plugin_hints.get("preferred_books", []) or []) + preferred_books,
            province,
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
