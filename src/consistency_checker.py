# -*- coding: utf-8 -*-
"""
L3 consistency checker.

Groups similar bill items and detects when the matched quota signatures disagree.
This module is advisory-only: it may flag conflicts or recommend a replacement,
but it must not rewrite the selected quota.
"""

from __future__ import annotations

from loguru import logger

import config


_SOURCE_WEIGHTS = {
    "experience_exact": 5.0,
    "experience_similar": 3.0,
    "experience_exact_confirmed": 3.5,
    "experience_similar_confirmed": 3.0,
    "rule_direct": 4.0,
    "agent": 2.0,
    "agent_fastpath": 1.5,
    "search": 1.0,
}

_LOCATION_PREFIXES = ("室内", "室外", "户内", "户外")
_ACTION_SUFFIXES = ("制作安装", "安装", "敷设", "制作", "铺设")


def _normalize_core_name(name: str) -> str:
    """Strip location and action wrappers from the item name."""
    if not name:
        return ""

    result = str(name).strip()
    for prefix in _LOCATION_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break

    for suffix in sorted(_ACTION_SUFFIXES, key=len, reverse=True):
        if result.endswith(suffix) and len(result) > len(suffix):
            result = result[:-len(suffix)]
            break

    return result.strip()


def _build_fingerprint(item: dict) -> str:
    """Fingerprint similar bill items that should usually share one quota family."""
    name = item.get("name", "")
    core_name = _normalize_core_name(name)
    specialty = item.get("specialty", "") or ""
    params = item.get("params", {}) or {}

    param_parts: list[str] = []
    for key in ("dn", "cable_section", "material", "connection", "kva", "shape"):
        value = params.get(key)
        if value not in (None, ""):
            param_parts.append(f"{key}={value}")

    cable_type = item.get("cable_type", "")
    if cable_type:
        param_parts.append(f"cable_type={cable_type}")

    return f"{core_name}|{specialty}|{'|'.join(sorted(param_parts))}"


def _compute_vote_weight(result: dict) -> float:
    """Compute vote weight from source reliability and confidence."""
    source = result.get("match_source", "search") or "search"
    confidence = result.get("confidence", 50) or 50

    base_weight = 1.0
    for key, weight in sorted(_SOURCE_WEIGHTS.items(), key=lambda pair: len(pair[0]), reverse=True):
        if str(source).startswith(key):
            base_weight = weight
            break

    return base_weight * (float(confidence) / 100.0)


def _quota_signature(result: dict) -> tuple[str, ...]:
    """Signature used to compare whether grouped results picked the same quota."""
    quotas = result.get("quotas") or []
    return tuple(str(quota.get("quota_id", "")).strip() for quota in quotas if quota.get("quota_id"))


def _build_correction_advisory(result: dict, winner_results: list[dict]) -> dict | None:
    """Recommend the majority-vote quota without mutating the current result."""
    template = max(winner_results, key=lambda row: row.get("confidence", 0))
    template_quotas = template.get("quotas", [])
    if not template_quotas:
        return None

    old_quotas = result.get("quotas", [])
    old_main_id = old_quotas[0].get("quota_id", "?") if old_quotas else "?"
    new_main = template_quotas[0]
    return {
        "action": "group_vote_advisory",
        "applied": False,
        "advisory_only": True,
        "quota_id": str(new_main.get("quota_id") or "").strip(),
        "quota_name": str(new_main.get("name") or "").strip(),
        "old_quota_id": str(old_main_id or "").strip(),
        "winner_confidence": template.get("confidence", 0),
    }


def check_and_fix(results: list[dict]) -> list[dict]:
    """
    L3 consistency review.

    The algorithm stays the same:
    1. group similar items
    2. detect inconsistent quota signatures
    3. emit conflict/advisory signals

    It no longer rewrites `quotas`, `confidence`, or `explanation`.
    """
    if not getattr(config, "REFLECTION_ENABLED", True):
        return results

    if not results or len(results) < 2:
        return results

    skip_conf = getattr(config, "REFLECTION_SKIP_HIGH_CONFIDENCE", 90)
    min_ratio = getattr(config, "REFLECTION_MIN_VOTE_RATIO", 1.5)

    for result in results:
        result["reflection_corrected"] = False
        result["reflection_correction"] = {}
        result["reflection_conflict"] = False
        result["reflection_summary"] = {
            "groups_checked": 0,
            "inconsistent_groups": 0,
            "advisories_emitted": 0,
            "conflicts_flagged": 0,
        }

    groups: dict[str, list[tuple[int, dict]]] = {}
    for index, result in enumerate(results):
        item = result.get("bill_item", {})
        if not item:
            continue
        fingerprint = _build_fingerprint(item)
        groups.setdefault(fingerprint, []).append((index, result))

    groups_checked = 0
    inconsistencies_found = 0
    advisories_emitted = 0
    conflicts_flagged = 0

    for members in groups.values():
        if len(members) < 2:
            continue

        groups_checked += 1

        signature_groups: dict[tuple[str, ...] | tuple[str], list[dict]] = {}
        for _index, result in members:
            signature = _quota_signature(result)
            signature_key = signature if signature else ("EMPTY",)
            signature_groups.setdefault(signature_key, []).append(result)

        if len(signature_groups) <= 1:
            continue

        inconsistencies_found += 1
        signature_scores = {
            signature: sum(_compute_vote_weight(row) for row in grouped_results)
            for signature, grouped_results in signature_groups.items()
        }
        ranked_signatures = sorted(signature_scores.items(), key=lambda pair: pair[1], reverse=True)
        winner_signature = ranked_signatures[0][0]
        winner_score = ranked_signatures[0][1]
        runner_up_score = ranked_signatures[1][1] if len(ranked_signatures) > 1 else 0.0

        if runner_up_score > 0 and winner_score / runner_up_score < min_ratio:
            conflicts_flagged += 1
            for grouped_results in signature_groups.values():
                for result in grouped_results:
                    result["reflection_conflict"] = True
            sample_name = members[0][1].get("bill_item", {}).get("name", "?")
            logger.info(
                "L3 consistency conflict detected for [{}]: {} similar items, {} quota signatures, vote ratio below {}",
                sample_name,
                len(members),
                len(signature_groups),
                min_ratio,
            )
            continue

        winner_results = signature_groups[winner_signature]
        for signature, grouped_results in signature_groups.items():
            if signature == winner_signature:
                continue
            for result in grouped_results:
                if result.get("confidence", 0) >= skip_conf:
                    continue
                advisory = _build_correction_advisory(result, winner_results)
                if advisory:
                    result["reflection_correction"] = advisory
                    result["reflection_old_quota"] = advisory.get("old_quota_id", "")
                    advisories_emitted += 1

    summary = {
        "groups_checked": groups_checked,
        "inconsistent_groups": inconsistencies_found,
        "advisories_emitted": advisories_emitted,
        "conflicts_flagged": conflicts_flagged,
    }
    for result in results:
        result["reflection_summary"] = dict(summary)

    if groups_checked > 0:
        logger.info(
            "L3 consistency review finished: groups_checked={}, inconsistent_groups={}, advisories_emitted={}, conflicts_flagged={}",
            groups_checked,
            inconsistencies_found,
            advisories_emitted,
            conflicts_flagged,
        )

    return results
