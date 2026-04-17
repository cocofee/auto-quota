# -*- coding: utf-8 -*-
"""Context building, classification, and rule pre-match helpers."""

import re
from contextlib import nullcontext

from src.context_builder import build_context_prior
from src.match_core import _append_trace_step, _normalize_classification
from src.query_builder import build_primary_query_profile
from src.query_router import build_query_route_profile, select_search_books
from src.performance_monitor import PerformanceMonitor
from src.policy_engine import PolicyEngine
from src.rule_validator import RuleValidator
from src.specialty_classifier import (
    BORROW_PRIORITY,
    book_matches_province_scope,
    province_uses_standard_route_books,
)
from src.text_parser import normalize_bill_text, parser as text_parser

from .gates import _evaluate_input_gate, _extract_usage_from_section
from .pickers import _check_rule_subtype_conflict

def _api():
    import src.match_pipeline as api

    return api

def _ensure_item_feature_context(item: dict,
                                 performance_monitor: PerformanceMonitor | None = None):
    """Lazily restore feature context for retry/replay callers that bypass preprocessing."""
    if not isinstance(item, dict):
        return

    stage = (
        performance_monitor.measure("文本解析")
        if performance_monitor is not None else nullcontext()
    )
    with stage:
        context_prior = item.get("context_prior")
        if not isinstance(context_prior, dict) or not context_prior:
            context_prior = build_context_prior(item)
            item["context_prior"] = context_prior

        full_text = f"{item.get('name', '')} {item.get('description', '') or ''}".strip()
        parsed_params = text_parser.parse(full_text)
        params = item.get("params")
        if not isinstance(params, dict):
            params = dict(parsed_params)
        else:
            merged_params = dict(params)
            for key, value in (parsed_params or {}).items():
                if key not in merged_params or merged_params.get(key) in (None, "", [], {}):
                    merged_params[key] = value
            params = merged_params
        item["params"] = params

        parsed_canonical = text_parser.parse_canonical(
            full_text,
            specialty=item.get("specialty", ""),
            context_prior=context_prior,
            params=params,
        )
        canonical_features = item.get("canonical_features")
        if isinstance(canonical_features, dict) and canonical_features:
            merged_canonical = dict(parsed_canonical)
            for key, value in canonical_features.items():
                if value not in (None, "", [], {}):
                    merged_canonical[key] = value
            item["canonical_features"] = merged_canonical
            return

        item["canonical_features"] = parsed_canonical


def _build_item_context(item: dict,
                        performance_monitor: PerformanceMonitor | None = None) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    _ensure_item_feature_context(item, performance_monitor=performance_monitor)
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    sheet_name = item.get("sheet_name", "") or ""
    original_name = item.get("original_name", name)
    canonical_features = item.get("canonical_features") or {}
    context_prior = item.get("context_prior") or {}
    input_gate = _evaluate_input_gate(name, desc)
    query_name = input_gate.get("query_name", name)
    with (
        performance_monitor.measure("查询构建")
        if performance_monitor is not None else nullcontext()
    ):
        primary_query_profile = build_primary_query_profile(query_name, desc)
    context_prior = dict(context_prior)
    context_prior["primary_query_profile"] = primary_query_profile
    primary_subject = str(primary_query_profile.get("primary_subject") or "").strip()
    if primary_subject and not context_prior.get("primary_subject"):
        context_prior["primary_subject"] = primary_subject
    decisive_terms = [
        str(value).strip()
        for value in list(primary_query_profile.get("decisive_terms") or [])
        if str(value).strip()
    ]
    if decisive_terms:
        context_prior["decisive_terms"] = decisive_terms[:4]
    noise_marker = str(primary_query_profile.get("noise_marker") or "").strip()
    if noise_marker:
        context_prior["noise_marker"] = noise_marker
    api = _api()
    plugin_hints = api.resolve_plugin_hints(
        province=str(item.get("_resolved_province") or item.get("province") or ""),
        item=item,
        canonical_features=canonical_features,
    )
    unified_plan = api.build_unified_search_plan(
        province=str(item.get("_resolved_province") or item.get("province") or ""),
        item=item,
        context_prior=context_prior,
        canonical_features=canonical_features,
        plugin_hints=plugin_hints,
    )
    if unified_plan and unified_plan.get("plugin_hints"):
        plugin_hints = dict(unified_plan.get("plugin_hints") or {})
    if plugin_hints:
        context_prior = dict(context_prior)
        merged_context_hints = list(context_prior.get("context_hints") or [])
        merged_context_hints.extend(plugin_hints.get("preferred_specialties", []) or [])
        merged_context_hints = list(dict.fromkeys(
            str(value).strip() for value in merged_context_hints if str(value).strip()
        ))[:5]
        if merged_context_hints:
            context_prior["context_hints"] = merged_context_hints
        context_prior["plugin_hints"] = plugin_hints
    if unified_plan:
        context_prior = dict(context_prior)
        context_prior["unified_plan"] = unified_plan
    with (
        performance_monitor.measure("查询构建")
        if performance_monitor is not None else nullcontext()
    ):
        search_query = text_parser.build_quota_query(query_name, desc,
                                                      specialty=item.get("specialty", ""),
                                                      bill_params=item.get("params"),
                                                      section_title=section,
                                                      canonical_features=canonical_features,
                                                      context_prior=context_prior)
    # 线缆类型标签：追加到搜索词，帮助BM25区分电线/电缆/光缆定额
    cable_type = item.get("cable_type", "")
    if cable_type:
        search_query = f"{search_query} {cable_type}"

    raw_query = f"{query_name} {desc}".strip()
    if not raw_query:
        raw_query = f"{name} {desc}".strip()

    # 从分项标题/Sheet名推断用途关键词，注入到full_query中
    # 这样param_validator的介质冲突检查能利用section的方向信息
    # 只在清单文本本身不含这些关键词时才注入，避免重复
    route_query = raw_query
    if section:
        _usage_hint = _extract_usage_from_section(section)
        if _usage_hint and _usage_hint not in route_query:
            route_query = f"{route_query} {_usage_hint}"

    canonical_name = canonical_features.get("canonical_name", "")
    canonical_system = canonical_features.get("system", "")
    if canonical_name and canonical_name not in route_query:
        route_query = f"{route_query} {canonical_name}".strip()
    if canonical_system and canonical_system not in route_query:
        route_query = f"{route_query} {canonical_system}".strip()

    validation_query = route_query.strip()
    canonical_query = {
        "raw_query": raw_query.strip(),
        "route_query": route_query.strip(),
        "validation_query": validation_query,
        "search_query": search_query.strip(),
        "normalized_query": normalize_bill_text(original_name, desc),
        "primary_query_profile": primary_query_profile,
    }

    query_route = build_query_route_profile(
        canonical_query["route_query"],
        item=item,
        specialty=item.get("specialty", ""),
        canonical_features=canonical_features,
        context_prior=context_prior,
    )

    return {
        "name": name,
        "desc": desc,
        "section": section,
        "sheet_name": sheet_name,
        "unit": item.get("unit"),
        "quantity": item.get("quantity"),
        "canonical_query": canonical_query,
        "full_query": canonical_query["validation_query"],
        "normalized_query": canonical_query["normalized_query"],
        "search_query": canonical_query["search_query"],
        "canonical_features": canonical_features,
        "context_prior": context_prior,
        "plugin_hints": plugin_hints,
        "unified_plan": unified_plan,
        "query_route": query_route,
        "item": item,  # L5：供跨省预热读取 _cross_province_hints
        "input_gate": input_gate,
    }


def _has_strong_routing_evidence(classification: dict) -> bool:
    primary = str((classification or {}).get("primary") or "").strip()
    if not primary:
        return False
    reasons = ((classification or {}).get("routing_evidence") or {}).get(primary) or []
    strong_prefixes = (
        "item_override:",
        "section:",
        "sheet:",
        "bill_title:",
        "project_title:",
        "section_system_hint:",
        "sheet_system_hint:",
        "bill_system_hint:",
        "project_title_system_hint:",
    )
    return any(str(reason).startswith(strong_prefixes) for reason in reasons)


def _is_standard_seeded_specialty(seed_primary: str) -> bool:
    seed_primary = str(seed_primary or "").strip().upper()
    return bool(re.fullmatch(r"C\d+", seed_primary))


def _is_seeded_specialty_trustworthy(
    item: dict,
    seed_primary: str,
    section: str,
    sheet_name: str,
    *,
    province: str | None = None,
) -> bool:
    seed_primary = str(seed_primary or "").strip()
    if not seed_primary:
        return False
    if seed_primary not in BORROW_PRIORITY or not _is_standard_seeded_specialty(seed_primary):
        return False
    if province and not province_uses_standard_route_books(province):
        return False
    if province and not book_matches_province_scope(seed_primary, province):
        return False

    item = dict(item or {})
    context_prior = dict(item.get("context_prior") or {})
    batch_context = dict(context_prior.get("batch_context") or {})
    supportive_fields = (
        section,
        sheet_name,
        item.get("section"),
        item.get("sheet_name"),
        item.get("specialty_name"),
        context_prior.get("system_hint"),
        batch_context.get("section_system_hint"),
        batch_context.get("sheet_system_hint"),
        batch_context.get("project_system_hint"),
        batch_context.get("neighbor_system_hint"),
    )
    if any(str(value or "").strip() for value in supportive_fields):
        return True

    fallbacks = [
        str(book).strip()
        for book in (item.get("specialty_fallbacks") or [])
        if str(book).strip()
    ]
    return bool(fallbacks)


def _build_seeded_specialty_classification(primary: str, fallbacks: list[str], *, strict: bool) -> dict:
    candidate_books = [primary] + [book for book in fallbacks if book != primary]
    classification = {
        "primary": primary,
        "fallbacks": list(fallbacks),
        "candidate_books": candidate_books,
        "search_books": list(candidate_books),
        "routing_evidence": {
            primary: ["item_specialty"] if strict else ["soft_item_specialty"]
        },
        "book_scores": {primary: 10.0 if strict else 1.2},
        "confidence": "high" if strict else "medium",
        "reason": "item_specialty" if strict else "soft_item_specialty",
        "route_mode": "strict" if strict else "moderate",
        "allow_cross_book_escape": not strict,
        "hard_book_constraints": [primary] if strict else [],
    }
    return classification



def _should_expand_seeded_c8_accessory_scope(
    primary: str,
    fallbacks: list[str],
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> bool:
    if str(primary or "").strip() != "C8":
        return False
    if "C10" not in [str(book or "").strip() for book in fallbacks or []]:
        return False

    text = " ".join(
        str(value or "").strip()
        for value in (name, desc, section, sheet_name)
        if str(value or "").strip()
    ).replace("\u789f\u9600", "\u8776\u9600")
    hvac_hints = (
        "\u98ce\u9600",
        "\u9632\u706b\u9600",
        "\u6392\u70df",
        "\u98ce\u7ba1",
        "\u591a\u53f6\u8c03\u8282\u9600",
    )
    if any(token in text for token in hvac_hints):
        return False

    accessory_hints = (
        "\u9600",
        "\u9600\u95e8",
        "\u8776\u9600",
        "\u6b62\u56de\u9600",
        "\u7403\u9600",
        "\u622a\u6b62\u9600",
        "\u8fc7\u6ee4\u5668",
        "\u9664\u6c61\u5668",
        "\u8f6f\u63a5\u5934",
    )
    return any(token in text for token in accessory_hints)
def _merge_seeded_classification_scope(classification: dict, inferred: dict) -> dict:
    base = dict(classification or {})
    inferred = dict(inferred or {})
    primary = str(base.get("primary") or "").strip()
    inferred_primary = str(inferred.get("primary") or "").strip()
    inferred_hard = _dedupe_books(inferred.get("hard_book_constraints") or inferred.get("hard_search_books") or [])
    inferred_search = _dedupe_books(inferred.get("search_books") or inferred.get("candidate_books") or [])
    if not inferred_hard and primary == "C8" and "C10" in inferred_search:
        inferred_hard = ["C8", "C10"]
    if not primary or inferred_primary != primary:
        return base
    if len(inferred_hard) <= 1 or primary not in inferred_hard:
        return base

    inferred_search = _dedupe_books(inferred.get("search_books") or inferred.get("candidate_books") or [])
    if not inferred_search:
        inferred_search = [primary] + [book for book in inferred_hard if book != primary]
    if len(inferred_search) <= 1:
        return base

    base["fallbacks"] = [book for book in inferred_search if book != primary]
    base["candidate_books"] = list(inferred.get("candidate_books") or inferred_search)
    base["search_books"] = list(inferred_search)
    base["hard_book_constraints"] = list(inferred_hard)
    base["hard_search_books"] = _dedupe_books(inferred.get("hard_search_books") or inferred_hard)
    base["advisory_search_books"] = _dedupe_books(
        inferred.get("advisory_search_books")
        or [book for book in inferred_search if book not in inferred_hard]
    )
    base["route_mode"] = str(inferred.get("route_mode") or base.get("route_mode") or "")
    base["allow_cross_book_escape"] = bool(
        inferred.get("allow_cross_book_escape", base.get("allow_cross_book_escape", True))
    )
    if inferred.get("routing_evidence"):
        base["routing_evidence"] = dict(inferred.get("routing_evidence") or {})
    if inferred.get("book_scores"):
        base["book_scores"] = dict(inferred.get("book_scores") or {})
    if inferred.get("reason"):
        base["reason"] = str(inferred.get("reason") or base.get("reason") or "")
    if inferred.get("confidence"):
        base["confidence"] = inferred.get("confidence")
    return base
def _should_override_seeded_specialty(seed_primary: str, inferred: dict) -> bool:
    seed_primary = str(seed_primary or "").strip()
    inferred = dict(inferred or {})
    inferred_primary = str(inferred.get("primary") or "").strip()
    if not seed_primary or not inferred_primary or inferred_primary == seed_primary:
        return False
    hard_constraints = [str(book).strip() for book in (inferred.get("hard_book_constraints") or []) if str(book).strip()]
    if seed_primary in hard_constraints:
        return False
    if _has_strong_routing_evidence(inferred):
        return True
    return False


def _drop_incompatible_standard_classification(classification: dict, province: str | None) -> dict:
    classification = dict(classification or {})
    province = str(province or "").strip()
    primary = str(classification.get("primary") or "").strip()
    if not province or not primary or primary not in BORROW_PRIORITY or not _is_standard_seeded_specialty(primary):
        return classification
    if not province_uses_standard_route_books(province):
        return {"primary": None, "fallbacks": []}
    if not book_matches_province_scope(primary, province):
        return {"primary": None, "fallbacks": []}
    return classification


def _dedupe_books(values) -> list[str]:
    books: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        book = str(value or "").strip()
        if not book or book in seen:
            continue
        seen.add(book)
        books.append(book)
    return books


def _filter_books_to_province_scope(values, province: str | None) -> list[str]:
    books = _dedupe_books(values)
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


def _filter_classification_to_province_scope(classification: dict, province: str | None) -> dict:
    base = dict(classification or {})
    province = str(province or "").strip()
    if not province:
        return base

    primary = str(base.get("primary") or "").strip()
    fallbacks = _dedupe_books(base.get("fallbacks", []))
    candidate_books = _dedupe_books(base.get("candidate_books", []))
    search_books = _dedupe_books(base.get("search_books", []))
    hard_book_constraints = _dedupe_books(base.get("hard_book_constraints", []))
    hard_search_books = _dedupe_books(base.get("hard_search_books", []))
    advisory_search_books = _dedupe_books(base.get("advisory_search_books", []))

    ordered_scoped_books = _filter_books_to_province_scope(
        [primary]
        + fallbacks
        + candidate_books
        + search_books
        + hard_book_constraints
        + hard_search_books
        + advisory_search_books,
        province,
    )
    scoped_set = set(ordered_scoped_books)
    if primary and primary not in scoped_set:
        primary = ""
    if not primary and ordered_scoped_books:
        primary = ordered_scoped_books[0]

    fallbacks = [
        book for book in _filter_books_to_province_scope(fallbacks, province)
        if book != primary
    ]
    candidate_books = [
        book for book in _filter_books_to_province_scope(candidate_books, province)
        if book != primary
    ]
    search_books = [
        book for book in _filter_books_to_province_scope(search_books, province)
        if book != primary
    ]
    hard_book_constraints = _filter_books_to_province_scope(hard_book_constraints, province)
    hard_search_books = _filter_books_to_province_scope(hard_search_books, province)
    advisory_search_books = [
        book for book in _filter_books_to_province_scope(advisory_search_books, province)
        if book != primary
    ]

    if primary:
        if primary not in candidate_books:
            candidate_books.insert(0, primary)
        if primary not in search_books:
            search_books.insert(0, primary)
        if (
            primary in _dedupe_books(base.get("hard_book_constraints", []))
            and primary not in hard_book_constraints
        ):
            hard_book_constraints.insert(0, primary)
        if (
            primary in _dedupe_books(base.get("hard_search_books", []))
            and primary not in hard_search_books
        ):
            hard_search_books.insert(0, primary)

    kept_books = {
        primary,
        *fallbacks,
        *candidate_books,
        *search_books,
        *hard_book_constraints,
        *hard_search_books,
        *advisory_search_books,
    } - {""}

    routing_evidence = {}
    for book, reasons in dict(base.get("routing_evidence") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key in kept_books:
            routing_evidence[book_key] = list(reasons or [])

    book_scores = {}
    for book, score in dict(base.get("book_scores") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key in kept_books:
            book_scores[book_key] = score

    base["primary"] = primary or None
    base["fallbacks"] = fallbacks
    base["candidate_books"] = candidate_books
    base["search_books"] = search_books
    base["hard_book_constraints"] = hard_book_constraints
    base["hard_search_books"] = hard_search_books
    base["advisory_search_books"] = advisory_search_books
    base["routing_evidence"] = routing_evidence
    base["book_scores"] = book_scores
    return base


_STRONG_C10_TO_C8_TERMS = (
    "工业管道",
    "工艺管道",
    "蒸汽",
    "高压",
    "中压",
    "化工",
    "石油",
    "炼油",
    "炼化",
    "锅炉",
    "压力容器",
    "介质",
    "无缝钢管",
    "合金钢",
    "不锈钢",
    "酸洗",
    "脱脂",
)

_CONDITIONAL_C10_TO_C8_TERMS = (
    "焊接",
    "法兰",
    "对焊",
)


def _has_c10_industrial_pipe_signal(
    item: dict,
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> bool:
    context_prior = dict((item or {}).get("context_prior") or {})
    canonical_features = dict((item or {}).get("canonical_features") or {})
    text = " ".join(
        str(value or "")
        for value in (
            name,
            desc,
            section,
            sheet_name,
            context_prior.get("primary_subject"),
            context_prior.get("system_hint"),
            canonical_features.get("system"),
            canonical_features.get("entity"),
        )
        if str(value or "").strip()
    )
    if not text:
        return False
    strong_hits = sum(1 for term in _STRONG_C10_TO_C8_TERMS if term in text)
    if "工业管道" in text or "工艺管道" in text:
        return True
    if strong_hits >= 2:
        return True
    return (
        any(term in text for term in _CONDITIONAL_C10_TO_C8_TERMS)
        and strong_hits >= 1
    )


def _suppress_c10_to_c8_borrow(
    classification: dict,
    item: dict,
    name: str,
    desc: str,
    section: str,
    sheet_name: str = "",
) -> dict:
    base = dict(classification or {})
    primary = str(base.get("primary") or "").strip()
    if primary != "C10":
        return base
    if _has_c10_industrial_pipe_signal(item, name, desc, section, sheet_name):
        return base

    for key in (
        "fallbacks",
        "candidate_books",
        "search_books",
        "hard_book_constraints",
        "hard_search_books",
        "advisory_search_books",
    ):
        if key in base:
            base[key] = [
                book for book in list(base.get(key) or [])
                if str(book).strip() != "C8"
            ]

    routing_evidence = {}
    for book, reasons in dict(base.get("routing_evidence") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key != "C8":
            routing_evidence[book_key] = list(reasons or [])
    if routing_evidence:
        base["routing_evidence"] = routing_evidence

    book_scores = {}
    for book, score in dict(base.get("book_scores") or {}).items():
        book_key = str(book or "").strip()
        if book_key and book_key != "C8":
            book_scores[book_key] = score
    if book_scores or "book_scores" in base:
        base["book_scores"] = book_scores

    return base


def _build_unified_plan_fallback_classification(item: dict, province: str | None) -> dict | None:
    unified_plan = dict((item or {}).get("unified_plan") or {})
    if not unified_plan:
        return None
    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    plugin_source = str(plugin_hints.get("source") or "").strip()
    preferred_books = []
    for value in list(unified_plan.get("preferred_books") or []):
        book = str(value or "").strip()
        if book and book not in preferred_books:
            preferred_books.append(book)
    preferred_books = _filter_books_to_province_scope(preferred_books, province)
    if not preferred_books:
        return None
    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]
    if not non_seed_reason_tags:
        return None
    primary = str(unified_plan.get("primary_book") or "").strip()
    if not primary:
        primary = preferred_books[0]
    province = str(province or "").strip()
    if (
        province
        and primary
        and primary in BORROW_PRIORITY
        and _is_standard_seeded_specialty(primary)
        and (
            (not province_uses_standard_route_books(province))
            or (not book_matches_province_scope(primary, province))
        )
    ):
        return None
    fallbacks = [book for book in preferred_books if book != primary]
    route_mode = str(unified_plan.get("route_mode") or "moderate").strip().lower()
    if route_mode not in {"strict", "moderate", "open"}:
        route_mode = "moderate"
    hard_book_constraints = []
    for value in list(unified_plan.get("hard_books") or []):
        book = str(value or "").strip()
        if book and book not in hard_book_constraints:
            hard_book_constraints.append(book)
    hard_book_constraints = _filter_books_to_province_scope(hard_book_constraints, province)
    strong_reason_tags = {
        "explicit_book_anchor",
        "strong_system_anchor",
        "family_cluster",
    }
    if (
        not hard_book_constraints
        and plugin_source == "generated_benchmark_knowledge"
        and not any(tag in strong_reason_tags for tag in non_seed_reason_tags)
    ):
        return None
    routing_reason = "unified_plan"
    if non_seed_reason_tags:
        routing_reason = f"unified_plan:{'+'.join(non_seed_reason_tags[:2])}"
    return _filter_classification_to_province_scope({
        "primary": primary,
        "fallbacks": fallbacks,
        "candidate_books": list(preferred_books),
        "search_books": list(preferred_books),
        "routing_evidence": {
            book: [routing_reason]
            for book in preferred_books
        },
        "book_scores": {
            book: (2.0 if book == primary else 1.0)
            for book in preferred_books
        },
        "confidence": "medium",
        "reason": routing_reason,
        "route_mode": route_mode,
        "allow_cross_book_escape": bool(
            unified_plan.get("allow_cross_book_escape", route_mode != "strict")
        ),
        "hard_book_constraints": hard_book_constraints,
    }, province)


def _build_broad_group_unified_plan_override(item: dict | None, province: str | None) -> dict | None:
    unified_plan = dict((item or {}).get("unified_plan") or {})
    if not unified_plan:
        return None
    preferred_books = []
    for value in list(unified_plan.get("preferred_books") or []):
        book = str(value or "").strip()
        if book and book not in preferred_books:
            preferred_books.append(book)
    preferred_books = _filter_books_to_province_scope(preferred_books, province)
    if not preferred_books:
        return None

    broad_route_books = {"A", "D", "E"}
    if not all(book in broad_route_books for book in preferred_books):
        return None

    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    if str(plugin_hints.get("source") or "").strip() != "generated_benchmark_knowledge":
        return None

    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]
    if "province_plugin" not in non_seed_reason_tags:
        return None

    primary = str(unified_plan.get("primary_book") or "").strip() or preferred_books[0]
    fallbacks = [book for book in preferred_books if book != primary]
    route_mode = str(unified_plan.get("route_mode") or "moderate").strip().lower()
    if route_mode not in {"strict", "moderate", "open"}:
        route_mode = "moderate"
    routing_reason = "unified_plan:province_plugin"
    return _filter_classification_to_province_scope({
        "primary": primary,
        "fallbacks": fallbacks,
        "candidate_books": list(preferred_books),
        "search_books": list(preferred_books),
        "routing_evidence": {
            book: [routing_reason]
            for book in preferred_books
        },
        "book_scores": {
            book: (2.0 if book == primary else 1.0)
            for book in preferred_books
        },
        "confidence": "medium",
        "reason": routing_reason,
        "route_mode": route_mode,
        "allow_cross_book_escape": bool(
            unified_plan.get("allow_cross_book_escape", route_mode != "strict")
        ),
        "hard_book_constraints": [],
    }, province)


def _should_prefer_unified_plan_fallback(
    current: dict | None,
    fallback: dict | None,
    item: dict | None,
) -> bool:
    current = dict(current or {})
    fallback = dict(fallback or {})
    current_primary = str(current.get("primary") or "").strip()
    if not current_primary:
        return True

    current_route_mode = str(current.get("route_mode") or "").strip().lower()
    if current_route_mode == "strict" or list(current.get("hard_book_constraints") or []):
        return False

    fallback_books = [
        str(book).strip()
        for book in (
            list(fallback.get("search_books") or [])
            or list(fallback.get("candidate_books") or [])
        )
        if str(book).strip()
    ]
    if not fallback_books or current_primary in fallback_books:
        return False

    broad_route_books = {"A", "D", "E"}
    broad_fallback_only = all(book in broad_route_books for book in fallback_books)
    current_is_standard_book = (
        current_primary in BORROW_PRIORITY and _is_standard_seeded_specialty(current_primary)
    )
    if not (broad_fallback_only and current_is_standard_book):
        return False

    unified_plan = dict((item or {}).get("unified_plan") or {})
    plugin_hints = dict(unified_plan.get("plugin_hints") or (item or {}).get("plugin_hints") or {})
    plugin_source = str(plugin_hints.get("source") or "").strip()
    reason_tags = [
        str(value).strip()
        for value in list(unified_plan.get("reason_tags") or [])
        if str(value).strip()
    ]
    non_seed_reason_tags = [tag for tag in reason_tags if tag != "seed_specialty"]

    if plugin_source == "generated_benchmark_knowledge":
        return "province_plugin" in non_seed_reason_tags
    return True


def _build_classification(item: dict, name: str, desc: str, section: str,
                          sheet_name: str = "",
                          province: str = None) -> dict:
    """获取并标准化专业分类结果。"""
    primary = str(item.get("specialty") or "").strip()
    fallbacks = [
        str(book).strip()
        for book in (item.get("specialty_fallbacks") or [])
        if str(book).strip()
    ]
    if primary and not fallbacks:
        fallbacks = [book for book in BORROW_PRIORITY.get(primary, []) if book != primary]
        if primary == "C10":
            fallbacks = [book for book in fallbacks if book != "C8"]
        if not fallbacks:
            fallbacks = select_search_books(primary, province, borrow=True)[1:]
    classification = {
        "primary": primary,
        "fallbacks": fallbacks,
    }
    api = _api()
    inferred = api.classify_specialty(
        name, desc, section_title=section, province=province,
        bill_code=item.get("code"),
        context_prior=item.get("context_prior"),
        canonical_features=item.get("canonical_features"),
        sheet_name=sheet_name or item.get("sheet_name"),
    )
    inferred = _drop_incompatible_standard_classification(inferred, province)
    if primary:
        if _is_seeded_specialty_trustworthy(item, primary, section, sheet_name, province=province):
            classification = _build_seeded_specialty_classification(primary, fallbacks, strict=True)
            if _should_expand_seeded_c8_accessory_scope(primary, fallbacks, name, desc, section, sheet_name):
                expanded_hard = ["C8", "C10"]
                classification["search_books"] = _dedupe_books(expanded_hard + list(classification.get("search_books") or []))
                classification["candidate_books"] = _dedupe_books(
                    list(classification.get("candidate_books") or [])
                    + [book for book in fallbacks if str(book).strip()]
                )
                classification["hard_book_constraints"] = list(expanded_hard)
                classification["hard_search_books"] = list(expanded_hard)
                classification["advisory_search_books"] = [
                    book for book in classification["search_books"]
                    if book not in expanded_hard
                ]
        elif (
            primary in BORROW_PRIORITY
            and _is_standard_seeded_specialty(primary)
            and (not province or province_uses_standard_route_books(province))
            and (not province or book_matches_province_scope(primary, province))
        ):
            classification = _build_seeded_specialty_classification(primary, fallbacks, strict=False)
        else:
            classification = {"primary": None, "fallbacks": []}
    classification = _merge_seeded_classification_scope(classification, inferred)
    if not classification["primary"] or _should_override_seeded_specialty(primary, inferred):
        classification = inferred
    unified_plan_fallback = _build_unified_plan_fallback_classification(item, province)
    if not unified_plan_fallback and classification.get("primary"):
        unified_plan_fallback = _build_broad_group_unified_plan_override(item, province)
    if unified_plan_fallback and (
        not classification.get("primary")
        or _should_prefer_unified_plan_fallback(classification, unified_plan_fallback, item)
    ):
        classification = unified_plan_fallback
    classification = _filter_classification_to_province_scope(classification, province)
    classification = _suppress_c10_to_c8_borrow(
        classification,
        item,
        name,
        desc,
        section,
        sheet_name or item.get("sheet_name") or "",
    )
    primary_book = str(classification.get("primary") or "").strip()
    industrial_c8_signal = primary_book == "C10" and _has_c10_industrial_pipe_signal(
        item,
        name,
        desc,
        section,
        sheet_name or item.get("sheet_name") or "",
    )
    inferred_books = _dedupe_books(list(inferred.get("fallbacks") or []) + list(inferred.get("search_books") or []))
    if industrial_c8_signal and "C8" in inferred_books:
        for key in ("fallbacks", "candidate_books", "search_books", "advisory_search_books"):
            values = list(classification.get(key) or [])
            if "C8" not in values:
                insert_at = 1 if values and values[0] == "C9" else len(values)
                values.insert(insert_at, "C8")
                classification[key] = _dedupe_books(values)
    elif (
        primary_book == "C10"
        and str(classification.get("route_mode") or "") == "strict"
        and list(classification.get("hard_book_constraints") or []) == ["C10"]
    ):
        default_fallbacks = ["C9", "C13", "C12"]
        classification["fallbacks"] = list(default_fallbacks)
        classification["candidate_books"] = ["C10", *default_fallbacks]
        classification["search_books"] = ["C10", *default_fallbacks]
        classification["hard_book_constraints"] = ["C10"]
        classification["hard_search_books"] = ["C10"]
        classification["advisory_search_books"] = list(default_fallbacks)
    if (
        primary_book == "C10"
        and not industrial_c8_signal
        and str(classification.get("confidence") or "") == "high"
        and str(classification.get("route_mode") or "") == "strict"
        and len(list(classification.get("fallbacks") or [])) < 3
    ):
        default_fallbacks = ["C9", "C13", "C12"]
        classification["fallbacks"] = list(default_fallbacks)
        classification["candidate_books"] = ["C10", *default_fallbacks]
        classification["search_books"] = ["C10", *default_fallbacks]
        classification["hard_book_constraints"] = ["C10"]
        classification["hard_search_books"] = ["C10"]
        classification["advisory_search_books"] = list(default_fallbacks)
    return _normalize_classification(classification)


def _prepare_rule_match(rule_validator: RuleValidator, full_query: str, item: dict,
                        search_query: str, classification: dict,
                        route_profile=None) -> tuple[dict, dict]:
    """
    规则预匹配统一入口。

    返回:
        (rule_direct, rule_backup)
        - rule_direct: 高置信直通结果
        - rule_backup: 低置信备选结果
    """
    if rule_validator is None:
        return None, None

    def _try_match(books: list[str] | None):
        active_books = [b for b in (books or []) if b]
        result = rule_validator.match_by_rules(
            full_query,
            item,
            clean_query=search_query,
            books=active_books if active_books else None,
        )
        if not result:
            return None, active_books
        result = _check_rule_subtype_conflict(result, full_query)
        if not result:
            return None, active_books
        return result, active_books

    primary_book = classification.get("primary")
    fallback_books = [b for b in classification.get("fallbacks", []) if b]

    rule_result = None
    rule_books: list[str] = []
    if primary_book:
        rule_result, rule_books = _try_match([primary_book])

    if not rule_result:
        expanded_books = [primary_book] + fallback_books if primary_book else fallback_books
        rule_result, rule_books = _try_match(expanded_books)

    if not rule_result:
        return None, None

    # 品类一致性检查：清单明确写了子类型（如"刚性防水套管"），
    # 但规则匹配到的定额不含该子类型（如匹配到"成品防火套管"），
    # 则丢弃规则匹配结果，让搜索来处理（搜索能更精准地按名称匹配）
    _append_trace_step(
        rule_result,
        "rule_precheck",
        books=rule_books,
        confidence=rule_result.get("confidence", 0),
        quota_ids=[q.get("quota_id", "") for q in rule_result.get("quotas", [])],
    )
    allow_direct, threshold = PolicyEngine.should_use_rule_direct(
        rule_result.get("confidence", 0),
        route_profile=route_profile,
    )
    if allow_direct:
        _append_trace_step(rule_result, "rule_direct", threshold=threshold)
        return rule_result, None
    _append_trace_step(rule_result, "rule_backup", threshold=threshold)
    return None, rule_result


# ============================================================
# 结果构建
# ============================================================

