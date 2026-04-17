# -*- coding: utf-8 -*-
"""Province, plugin, and candidate scope helpers."""

import re

from src.candidate_scoring import compute_candidate_rank_score, compute_candidate_sort_key
from src.province_plugins import resolve_plugin_hints
from src.specialty_classifier import book_matches_province_scope, province_uses_standard_route_books

def _quota_book_from_id(quota_id: str) -> str:
    quota_id = str(quota_id or "").strip()
    if len(quota_id) >= 2 and quota_id[0] == "C" and quota_id[1].isalpha():
        letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                      'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                      'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
        return letter_map.get(quota_id[1], "")
    match = re.match(r"(C\d+)-", quota_id)
    if match:
        return match.group(1)
    match = re.match(r"(\d+)-", quota_id)
    if match:
        return f"C{match.group(1)}"
    return ""


def _compute_plugin_candidate_score(item: dict, candidate: dict) -> tuple[float, list[str]]:
    plugin_hints = dict((item or {}).get("plugin_hints") or {})
    if not plugin_hints:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    preferred_books = {str(value or "").strip() for value in plugin_hints.get("preferred_books", []) if str(value or "").strip()}
    preferred_quota_names = [str(value or "").strip() for value in plugin_hints.get("preferred_quota_names", []) if str(value or "").strip()]
    avoided_quota_names = [str(value or "").strip() for value in plugin_hints.get("avoided_quota_names", []) if str(value or "").strip()]

    quota_name = str(candidate.get("name", "") or "")
    quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
    if preferred_books:
        if quota_book in preferred_books:
            score += 0.08
            reasons.append(f"book:{quota_book}")

    if preferred_quota_names and any(name in quota_name for name in preferred_quota_names):
        score += 0.12
        reasons.append("preferred_name")

    if avoided_quota_names and any(name in quota_name for name in avoided_quota_names):
        score -= 0.12
        reasons.append("avoided_name")

    return score, reasons


def _apply_plugin_candidate_biases(item: dict, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []

    biased: list[dict] = []
    has_plugin_signal = False
    for candidate in candidates:
        updated = dict(candidate)
        plugin_score, plugin_reasons = _compute_plugin_candidate_score(item, updated)
        updated["plugin_score"] = plugin_score
        if plugin_reasons:
            updated["plugin_reasons"] = plugin_reasons
            has_plugin_signal = True
        biased.append(updated)

    if not has_plugin_signal:
        return biased

    for candidate in biased:
        candidate["rank_score"] = compute_candidate_rank_score(candidate)
    biased.sort(
        key=compute_candidate_sort_key,
        reverse=True,
    )
    return biased


def _apply_plugin_route_gate(item: dict, candidates: list[dict]) -> tuple[list[dict], dict]:
    plugin_hints = dict((item or {}).get("plugin_hints") or {})
    preferred_books = {
        str(value or "").strip()
        for value in plugin_hints.get("preferred_books", []) or []
        if str(value or "").strip()
    }
    strict_gate = bool(plugin_hints.get("strict_preferred_books"))
    if not candidates or not preferred_books:
        return list(candidates or []), {
            "applied": False,
            "reason": "no_preferred_books",
            "preferred_books": sorted(preferred_books),
        }
    if not strict_gate:
        preferred_count = 0
        routed: list[dict] = []
        for candidate in candidates:
            quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
            updated = dict(candidate)
            updated["plugin_route_book"] = quota_book
            if quota_book in preferred_books:
                preferred_count += 1
            routed.append(updated)
        return routed, {
            "applied": False,
            "reason": "soft_preferred_books_only",
            "preferred_books": sorted(preferred_books),
            "preferred_count": preferred_count,
        }

    routed: list[dict] = []
    preferred_count = 0
    for candidate in candidates:
        quota_book = _quota_book_from_id(candidate.get("quota_id", ""))
        updated = dict(candidate)
        updated["plugin_route_book"] = quota_book
        if quota_book in preferred_books:
            preferred_count += 1
        routed.append(updated)

    return routed, {
        "applied": False,
        "reason": "strict_preferred_books_disabled",
        "preferred_books": sorted(preferred_books),
        "preferred_count": preferred_count,
        "strict_requested": True,
    }


def _top_candidate_id(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    first = candidates[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("quota_id", "") or "")


_TARGET_MAJOR_PREFIXES_BY_DB_TYPE = {
    "install": {"03"},
    "civil": {"01", "02"},
    "municipal": {"04"},
    "landscape": {"05"},
}


def _detect_target_db_type(province: str) -> str:
    province = str(province or "").strip()
    if not province:
        return ""
    if "安装" in province:
        return "install"
    if "市政" in province:
        return "municipal"
    if "园林" in province or "绿化" in province:
        return "landscape"
    if any(
        keyword in province
        for keyword in ("建筑和装饰", "建筑装饰", "装饰工程", "建筑工程", "房屋建筑", "房建")
    ):
        return "civil"
    return ""


def _quota_major_prefix(quota_id: str) -> str:
    quota_id = str(quota_id or "").strip()
    if not quota_id:
        return ""
    prefix = quota_id.split("-", 1)[0].strip()
    if not prefix:
        return ""
    if prefix.isdigit():
        return prefix[:2].zfill(2)
    return ""


def _annotate_candidate_scope_signals(item: dict, candidates: list[dict]) -> list[dict]:
    province = str((item or {}).get("_resolved_province") or (item or {}).get("province") or "").strip()
    target_db_type = _detect_target_db_type(province)
    target_prefixes = _TARGET_MAJOR_PREFIXES_BY_DB_TYPE.get(target_db_type) or set()
    if not candidates or not target_prefixes:
        return [dict(candidate) for candidate in (candidates or [])]

    annotated: list[dict] = []
    for candidate in candidates:
        updated = dict(candidate)
        major_prefix = _quota_major_prefix(updated.get("quota_id", ""))
        updated["candidate_major_prefix"] = major_prefix
        updated["target_db_type"] = target_db_type
        if not major_prefix:
            updated["candidate_scope_match"] = 0.0
            updated["candidate_scope_conflict"] = False
        else:
            updated["candidate_scope_match"] = 1.0 if major_prefix in target_prefixes else 0.0
            updated["candidate_scope_conflict"] = major_prefix not in target_prefixes
        annotated.append(updated)
    return annotated


def _merge_arbiter_annotations(base_candidates: list[dict], arbiter_candidates: list[dict]) -> list[dict]:
    ordered = [dict(candidate) for candidate in (base_candidates or [])]
    if not ordered or not arbiter_candidates:
        return ordered

    arbiter_by_quota_id: dict[str, dict] = {}
    for candidate in arbiter_candidates:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            arbiter_by_quota_id[quota_id] = candidate

    if not arbiter_by_quota_id:
        return ordered

    for candidate in ordered:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        advised = arbiter_by_quota_id.get(quota_id)
        if not advised:
            continue
        if "arbiter_signals" in advised:
            candidate["arbiter_signals"] = list(advised.get("arbiter_signals") or [])
        if "arbiter_recommended" in advised:
            candidate["arbiter_recommended"] = bool(advised.get("arbiter_recommended"))
    return ordered


