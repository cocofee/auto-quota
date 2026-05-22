# -*- coding: utf-8 -*-
"""Thin orchestration layer for the match pipeline package."""

from contextlib import nullcontext
from itertools import count
import re

from loguru import logger

import config

from src.ambiguity_gate import analyze_ambiguity
from src.bill_item_context import BillItemContext
from src.candidate_scoring import (
    compute_candidate_rank_score,
    explain_candidate_rank_score,
    has_exact_experience_anchor,
    has_exact_universal_kb_anchor,
)
from src.context_builder import summarize_batch_context_for_trace
from src.explicit_equipment_family_pickers import _promote_explicit_distribution_box_candidate
from src.match_core import (
    _append_trace_step,
    _is_measure_item,
    _summarize_candidates_for_trace,
    calculate_confidence,
    filter_param_hard_fail_candidates,
    infer_confidence_family_alignment,
    summarize_candidate_reasoning,
)
from src.performance_monitor import PerformanceMonitor
from src.policy_engine import PolicyEngine
from src.quota_search import search_by_id
from src.rule_validator import RuleValidator
from src.reason_taxonomy import merge_reason_tags
from src.text_parser import parser as text_parser

from .classifiers import _build_classification, _build_item_context, _prepare_rule_match
from .gates import (
    _append_item_review_rejection_trace,
    _build_input_gate_abstain_result,
    _evaluate_context_gate,
    _review_check_match_result,
)
from .pickers import _pick_category_safe_candidate
from .reasons import (
    DEFAULT_ALTERNATIVE_COUNT,
    _build_alternatives,
    _build_ranked_candidate_snapshots,
    _build_skip_measure_result,
    _set_result_reason,
)
from .reconcilers import (
    _apply_price_validation,
    _inject_rule_backup_candidate,
    _reconcile_search_and_experience,
)
from .scope import (
    _annotate_candidate_scope_signals,
    _apply_plugin_candidate_biases,
    _apply_plugin_route_gate,
    _merge_arbiter_annotations,
    _top_candidate_id,
)


def _api():
    import src.match_pipeline as api

    return api


_EXPERIENCE_SHADOW_AUDIT_COUNTER = count(1)


def _should_shadow_audit_experience_direct(item: dict, exp_result: dict) -> bool:
    sample_every = int(getattr(config, "EXPERIENCE_SHADOW_AUDIT_EVERY_N", 0) or 0)
    if sample_every <= 0:
        return False
    if not isinstance(item, dict) or not isinstance(exp_result, dict):
        return False
    if str(exp_result.get("match_source") or "").strip() != "experience_exact":
        return False
    sequence = next(_EXPERIENCE_SHADOW_AUDIT_COUNTER)
    sampled = sequence % sample_every == 0
    if sampled:
        item["_experience_shadow_audit"] = {
            "sampled": True,
            "sequence": sequence,
            "sample_every": sample_every,
        }
    return sampled


def _append_experience_shadow_audit_trace(result: dict, exp_backup: dict, item: dict) -> None:
    if not isinstance(result, dict) or not isinstance(exp_backup, dict) or not isinstance(item, dict):
        return
    audit_meta = item.get("_experience_shadow_audit")
    if not isinstance(audit_meta, dict) or not audit_meta.get("sampled"):
        return

    exp_qid = str(((exp_backup.get("quotas") or [{}])[0] or {}).get("quota_id", "") or "").strip()
    final_qid = str(((result.get("quotas") or [{}])[0] or {}).get("quota_id", "") or "").strip()
    exp_conf = int(exp_backup.get("confidence", 0) or 0)
    final_conf = int(result.get("confidence", 0) or 0)
    diverged = bool(exp_qid != final_qid)
    confidence_gap = abs(final_conf - exp_conf)
    alert_gap = int(getattr(config, "EXPERIENCE_SHADOW_ALERT_CONFIDENCE_GAP", 12) or 12)
    should_alert = diverged or confidence_gap >= alert_gap

    _append_trace_step(
        result,
        "experience_shadow_audit",
        sampled=True,
        sequence=int(audit_meta.get("sequence", 0) or 0),
        sample_every=int(audit_meta.get("sample_every", 0) or 0),
        experience_quota_id=exp_qid,
        final_quota_id=final_qid,
        experience_confidence=exp_conf,
        final_confidence=final_conf,
        diverged=diverged,
        confidence_gap=confidence_gap,
        alert=should_alert,
    )

    if should_alert:
        logger.warning(
            "experience shadow audit diverged: "
            f"exp={exp_qid or '<empty>'}/{exp_conf} "
            f"final={final_qid or '<empty>'}/{final_conf}"
        )


def _merge_explicit_annotations(base_candidates: list[dict], explicit_candidates: list[dict]) -> list[dict]:
    ordered = [dict(candidate) for candidate in (base_candidates or [])]
    if not ordered or not explicit_candidates:
        return ordered

    explicit_by_quota_id: dict[str, dict] = {}
    for candidate in explicit_candidates:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            explicit_by_quota_id[quota_id] = candidate

    if not explicit_by_quota_id:
        return ordered

    for candidate in ordered:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        hinted = explicit_by_quota_id.get(quota_id)
        if not hinted:
            continue
        if "explicit_signals" in hinted:
            candidate["explicit_signals"] = list(hinted.get("explicit_signals") or [])
        if "explicit_recommended" in hinted:
            candidate["explicit_recommended"] = bool(hinted.get("explicit_recommended"))
    return ordered


def _promote_candidate_by_quota_id(candidates: list[dict], quota_id: str) -> tuple[list[dict], bool]:
    quota_id = str(quota_id or "").strip()
    ordered = [dict(candidate) for candidate in (candidates or [])]
    if not ordered or not quota_id:
        return ordered, False

    if str(ordered[0].get("quota_id", "") or "").strip() == quota_id:
        return ordered, False

    for index, candidate in enumerate(ordered[1:], start=1):
        if str(candidate.get("quota_id", "") or "").strip() != quota_id:
            continue
        promoted = ordered.pop(index)
        return [promoted] + ordered, True
    return ordered, False


def _has_lifecycle_hard_conflict(candidate: dict) -> bool:
    return any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
            "context_alignment_hard_conflict",
        )
    ) or candidate.get("param_match") is False


def _has_rankable_contract_hard_conflict(candidate: dict) -> bool:
    if not isinstance(candidate, dict):
        return True
    if any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
            "context_alignment_hard_conflict",
        )
    ):
        return True
    if candidate.get("param_match") is False and not candidate.get("_rankable_pool_contract_protected"):
        return True
    return False


def _candidate_lifecycle_score(candidate: dict) -> float:
    try:
        return float(compute_candidate_rank_score(candidate or {}))
    except Exception:
        return 0.0


def _candidate_float(candidate: dict, key: str, default: float = 0.0) -> float:
    try:
        return float((candidate or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _candidate_int(candidate: dict, key: str, default: int = 0) -> int:
    try:
        return int((candidate or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _candidate_rerank_evidence(candidate: dict) -> float:
    if candidate.get("rerank_score") is not None:
        return _candidate_float(candidate, "rerank_score")
    return _candidate_float(candidate, "hybrid_score")


def _is_protected_anchor(candidate: dict) -> bool:
    if not isinstance(candidate, dict):
        return False
    return bool(
        has_exact_experience_anchor(candidate)
        or has_exact_universal_kb_anchor(candidate)
        or candidate.get("exact_experience_anchor")
        or candidate.get("exact_universal_kb_anchor")
    )


def _has_structural_lifecycle_advantage(incumbent: dict, challenger: dict) -> bool:
    incumbent_param_tier = _candidate_int(incumbent, "param_tier", 1)
    challenger_param_tier = _candidate_int(challenger, "param_tier", 1)
    if challenger_param_tier > incumbent_param_tier:
        return False
    if challenger.get("logic_exact_primary_match") and not incumbent.get("logic_exact_primary_match"):
        return False

    incumbent_scope = _candidate_float(incumbent, "candidate_scope_match")
    challenger_scope = _candidate_float(challenger, "candidate_scope_match")
    if challenger_scope >= incumbent_scope + 0.5:
        return False

    incumbent_manual = _candidate_float(incumbent, "manual_structured_score")
    challenger_manual = _candidate_float(challenger, "manual_structured_score")
    incumbent_param = _candidate_float(incumbent, "param_score")
    challenger_param = _candidate_float(challenger, "param_score")
    incumbent_feature = _candidate_float(incumbent, "feature_alignment_score")
    challenger_feature = _candidate_float(challenger, "feature_alignment_score")
    incumbent_rerank = _candidate_rerank_evidence(incumbent)
    challenger_rerank = _candidate_rerank_evidence(challenger)
    incumbent_name = _candidate_float(incumbent, "name_bonus")
    challenger_name = _candidate_float(challenger, "name_bonus")
    incumbent_anchors = _candidate_int(incumbent, "feature_alignment_exact_anchor_count")
    challenger_anchors = _candidate_int(challenger, "feature_alignment_exact_anchor_count")

    evidence_edges = 0
    if incumbent_manual >= 0.65 and incumbent_manual > challenger_manual + 0.015:
        evidence_edges += 1
    if incumbent_param >= 0.70 and incumbent_param > challenger_param + 0.04:
        evidence_edges += 1
    if incumbent_feature >= 0.90 and incumbent_feature > challenger_feature + 0.015:
        evidence_edges += 1
    if incumbent_rerank >= 0.85 and incumbent_rerank > challenger_rerank + 0.02:
        evidence_edges += 1
    if incumbent_name > challenger_name + 0.05:
        evidence_edges += 1
    if incumbent_anchors > challenger_anchors:
        evidence_edges += 1

    return evidence_edges >= 3


def _candidate_book_key(candidate: dict) -> str:
    quota_id = str((candidate or {}).get("quota_id", "") or "").strip()
    if not quota_id:
        return ""
    if quota_id.startswith("C"):
        return quota_id.split("-", 1)[0]
    if "-" in quota_id:
        return quota_id.split("-", 1)[0]
    match = re.match(r"([A-Za-z]+)", quota_id)
    if match:
        return match.group(1)
    return quota_id[:2]


def _candidate_tier_prefix(candidate: dict) -> str:
    quota_id = str((candidate or {}).get("quota_id", "") or "").strip()
    if not quota_id:
        return ""
    parts = [part for part in quota_id.split("-") if part]
    if len(parts) >= 3:
        return "-".join(parts[:-1])
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 2:
        return f"{parts[0]}-{parts[1][:-1]}"
    return ""


def _candidate_canonical_group(candidate: dict) -> tuple[str, str]:
    features = (candidate or {}).get("candidate_canonical_features") or {}
    if not isinstance(features, dict):
        features = {}
    family = str(features.get("family") or "").strip()
    canonical = str(features.get("canonical_name") or features.get("entity") or "").strip()
    return family, canonical


def _same_family_group(incumbent: dict, challenger: dict) -> bool:
    incumbent_book = _candidate_book_key(incumbent)
    challenger_book = _candidate_book_key(challenger)
    if not incumbent_book or incumbent_book != challenger_book:
        return False

    incumbent_family, incumbent_canonical = _candidate_canonical_group(incumbent)
    challenger_family, challenger_canonical = _candidate_canonical_group(challenger)
    if incumbent_family and challenger_family and incumbent_family == challenger_family:
        return True
    return bool(incumbent_canonical and incumbent_canonical == challenger_canonical)


def _same_tier_prefix_group(incumbent: dict, challenger: dict) -> bool:
    incumbent_book = _candidate_book_key(incumbent)
    challenger_book = _candidate_book_key(challenger)
    if not incumbent_book or incumbent_book != challenger_book:
        return False
    incumbent_prefix = _candidate_tier_prefix(incumbent)
    challenger_prefix = _candidate_tier_prefix(challenger)
    return bool(incumbent_prefix and incumbent_prefix == challenger_prefix)


def _looks_like_distribution_box_subject(text: str) -> bool:
    text = re.sub(r"\s+", "", str(text or ""))
    if not text:
        return False
    if "接线盒" in text:
        return False
    return any(token in text for token in ("配电箱", "电箱", "控制箱", "配电柜", "控制柜"))


def _apply_item_subject_family_hint(item: dict, features: dict) -> dict:
    subject_text = " ".join(
        str((item or {}).get(key) or "")
        for key in ("bill_name", "name")
        if str((item or {}).get(key) or "").strip()
    )
    if not _looks_like_distribution_box_subject(subject_text):
        return features
    hydrated = dict(features or {})
    hydrated["family"] = "electrical_box"
    hydrated["entity"] = "配电箱"
    hydrated["canonical_name"] = "配电箱"
    hydrated.setdefault("system", "电气")
    hydrated.setdefault("item_subject_family_hint", "distribution_box")
    return hydrated


def _bill_guided_family_group(item: dict | None, incumbent: dict, challenger: dict) -> bool:
    incumbent_book = _candidate_book_key(incumbent)
    challenger_book = _candidate_book_key(challenger)
    if not incumbent_book or incumbent_book != challenger_book:
        return False

    bill_features = _item_primary_features(item)
    bill_family = str((bill_features or {}).get("family") or "").strip()
    if bill_family != "electrical_box":
        return False

    incumbent_family, _ = _candidate_canonical_group(incumbent)
    challenger_family, _ = _candidate_canonical_group(challenger)
    return challenger_family == bill_family and incumbent_family != bill_family


_PRIMARY_NUMERIC_FIELDS = (
    "dn",
    "conduit_dn",
    "bridge_wh_sum",
    "perimeter",
    "half_perimeter",
    "large_side",
    "cable_section",
    "cable_cores",
    "kw",
    "kva",
    "ampere",
    "circuits",
    "port_count",
    "switch_gangs",
)

_DECISIVE_PRIMARY_NUMERIC_FIELDS = {
    "dn",
    "conduit_dn",
    "bridge_wh_sum",
    "perimeter",
    "half_perimeter",
    "large_side",
    "cable_section",
    "cable_cores",
    "kw",
    "kva",
    "ampere",
    "circuits",
    "port_count",
    "switch_gangs",
}

_PRIMARY_CATEGORICAL_FIELDS = (
    "canonical_name",
    "material",
    "install_method",
    "connection",
    "laying_method",
    "bridge_type",
    "box_mount_mode",
    "valve_connection_family",
    "valve_type",
    "cable_type",
    "cable_head_type",
    "conduit_type",
    "wire_type",
    "support_scope",
    "support_action",
    "sanitary_mount_mode",
    "sanitary_flush_mode",
    "sanitary_water_mode",
    "sanitary_nozzle_mode",
    "sanitary_tank_mode",
    "lamp_type",
    "outlet_grounding",
)

_GENERIC_PRIMARY_TEXT = {
    "安装",
    "制作",
    "制作安装",
    "配电箱",
    "控制箱",
    "桥架",
    "套管",
    "灯具",
    "筒灯",
    "风口",
    "阀门",
    "电缆",
    "电线",
    "管道",
    "蹲便器",
}

_NUMERIC_ONLY_DECISIVE_NEEDS_SECONDARY_FAMILIES = {
    "air_valve",
    "valve_body",
}

_SECONDARY_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("air_valve:opposed_multi_leaf", ("对开多叶", "对开式多叶", "多叶调节阀", "电动对开")),
    ("air_valve:fire_damper", ("防火阀", "排烟防火阀", "防火调节阀")),
    ("air_valve:butterfly", ("蝶阀", "风管蝶阀")),
    ("air_terminal:louver", ("百叶风口", "单层百叶", "双层百叶", "百叶")),
    ("air_terminal:diffuser", ("散流器", "流线形散流器", "圆形散流器")),
    ("air_terminal:grille", ("格栅风口", "格栅")),
    ("air_terminal:rainproof", ("防雨百叶", "防雨")),
    ("cable_head:indoor", ("户内", "室内")),
    ("cable_head:outdoor", ("户外", "室外")),
    ("cable_head:dry_pack", ("干包", "干包式")),
    ("cable_head:heat_shrink", ("热缩", "热缩式")),
    ("cable_head:cold_shrink", ("冷缩", "冷缩式")),
    ("cable_head:armored", ("铠装",)),
    ("cable_head:unarmored", ("非铠装", "不铠装")),
    ("bridge:mesh", ("网式", "网格式")),
    ("bridge:tray", ("槽式",)),
    ("bridge:ladder", ("梯式", "梯架")),
    ("bridge:pan", ("托盘式",)),
)


def _candidate_features(candidate: dict) -> dict:
    features = (candidate or {}).get("candidate_canonical_features") or (candidate or {}).get("canonical_features") or {}
    features = dict(features) if isinstance(features, dict) else {}
    if not candidate or candidate.get("_primary_features_hydrated"):
        return features

    raw_text = str(
        features.get("raw_text")
        or candidate.get("name")
        or candidate.get("description")
        or ""
    ).strip()
    if not raw_text:
        return features

    has_missing_primary = any(
        _primary_raw_value(features, key) in (None, "", [])
        for key in (*_PRIMARY_NUMERIC_FIELDS, *_PRIMARY_CATEGORICAL_FIELDS)
    )
    if not has_missing_primary:
        candidate["_primary_features_hydrated"] = True
        return features

    try:
        specialty = str(candidate.get("specialty") or _candidate_book_key(candidate) or "").strip()
        parsed_params = text_parser.parse(raw_text)
        parsed_features = text_parser.parse_canonical(
            raw_text,
            specialty=specialty,
            params=parsed_params,
        )
    except Exception as exc:  # pragma: no cover - defensive around parser edge cases
        logger.debug("candidate primary feature hydration failed: {}", exc)
        candidate["_primary_features_hydrated"] = True
        return features

    if not isinstance(parsed_features, dict) or not parsed_features:
        candidate["_primary_features_hydrated"] = True
        return features

    merged = dict(parsed_features)
    for key, value in features.items():
        if key == "numeric_params":
            continue
        if value not in (None, "", []):
            merged[key] = value

    merged_numeric = dict(parsed_features.get("numeric_params") or {})
    existing_numeric = features.get("numeric_params") or {}
    if isinstance(existing_numeric, dict):
        for key, value in existing_numeric.items():
            if value not in (None, "", []):
                merged_numeric[key] = value
    if merged_numeric:
        merged["numeric_params"] = merged_numeric

    _hydrate_primary_tier_limits_from_text(raw_text, merged)
    candidate["candidate_canonical_features"] = merged
    candidate["_primary_features_hydrated"] = True
    return merged


def _hydrate_primary_tier_limits_from_text(raw_text: str, features: dict) -> None:
    text = str(raw_text or "")
    if not text or not isinstance(features, dict):
        return
    numeric_params = features.setdefault("numeric_params", {})
    if not isinstance(numeric_params, dict):
        numeric_params = {}
        features["numeric_params"] = numeric_params

    if _primary_raw_value(features, "half_perimeter") in (None, "", []):
        match = re.search(r"\u534a\u5468\u957f[^\d]{0,12}(?:<=|[<\u2264=])\s*(\d+(?:\.\d+)?)", text)
        if match:
            numeric_params["half_perimeter"] = _normalize_primary_length_limit(match.group(1))

    if _primary_raw_value(features, "perimeter") in (None, "", []):
        match = re.search(r"(?<!\u534a)\u5468\u957f[^\d]{0,12}(?:<=|[<\u2264=])\s*(\d+(?:\.\d+)?)", text)
        if match:
            numeric_params["perimeter"] = _normalize_primary_length_limit(match.group(1))


def _normalize_primary_length_limit(value) -> float | None:
    number = _primary_float(value)
    if number is None:
        return None
    return number * 1000 if 0 < number < 20 else number


def _append_secondary_text_parts(parts: list[str], value) -> None:
    if value in (None, "", []):
        return
    if isinstance(value, dict):
        for nested in value.values():
            _append_secondary_text_parts(parts, nested)
        return
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            _append_secondary_text_parts(parts, nested)
        return
    parts.append(str(value))


def _secondary_text_blob(record: dict | None, features: dict | None = None) -> str:
    parts: list[str] = []
    for source in (record or {}, features or {}):
        if not isinstance(source, dict):
            continue
        for key in (
            "name",
            "description",
            "bill_name",
            "bill_text",
            "raw_text",
            "canonical_name",
            "entity",
            "material",
            "valve_type",
            "bridge_type",
            "cable_head_type",
            "lamp_type",
            "traits",
            "specs",
        ):
            _append_secondary_text_parts(parts, source.get(key))
    return re.sub(r"\s+", "", " ".join(parts)).lower()


def _secondary_type_hits(text: str) -> set[str]:
    if not text:
        return set()
    hits: set[str] = set()
    for label, aliases in _SECONDARY_TYPE_RULES:
        if any(_primary_text(alias) in text for alias in aliases):
            hits.add(label)
    return hits


def _secondary_type_bucket(label: str) -> str:
    return str(label or "").split(":", 1)[0]


def _secondary_type_direction(item: dict | None, candidate: dict, incumbent: dict) -> dict:
    bill_features = _item_primary_features(item)
    bill_hits = _secondary_type_hits(_secondary_text_blob(item, bill_features))
    if not bill_hits:
        return {}

    candidate_features = _candidate_features(candidate)
    incumbent_features = _candidate_features(incumbent)
    candidate_hits = _secondary_type_hits(_secondary_text_blob(candidate, candidate_features))
    incumbent_hits = _secondary_type_hits(_secondary_text_blob(incumbent, incumbent_features))

    edges = sorted(bill_hits & candidate_hits - incumbent_hits)
    conflicts = sorted(bill_hits & incumbent_hits - candidate_hits)

    for expected in bill_hits:
        bucket = _secondary_type_bucket(expected)
        expected_in_candidate = expected in candidate_hits
        expected_in_incumbent = expected in incumbent_hits
        if expected_in_candidate and not expected_in_incumbent:
            continue
        if expected_in_incumbent and not expected_in_candidate:
            continue
        candidate_bucket_conflict = sorted(
            hit for hit in candidate_hits
            if _secondary_type_bucket(hit) == bucket and hit != expected
        )
        incumbent_bucket_conflict = sorted(
            hit for hit in incumbent_hits
            if _secondary_type_bucket(hit) == bucket and hit != expected
        )
        if expected_in_candidate and incumbent_bucket_conflict:
            edges.extend(f"{expected}>{hit}" for hit in incumbent_bucket_conflict)
        if expected_in_incumbent and candidate_bucket_conflict:
            conflicts.extend(f"{expected}!{hit}" for hit in candidate_bucket_conflict)

    edges = list(dict.fromkeys(edges))
    conflicts = list(dict.fromkeys(conflicts))
    if not edges and not conflicts:
        return {}
    return {
        "edges": edges,
        "conflicts": conflicts,
        "bonus": min(0.13, 0.080 * len(edges)),
        "penalty": min(0.12, 0.060 * len(conflicts)),
    }


def _item_primary_features(item: dict | None) -> dict:
    if not isinstance(item, dict):
        return {}
    cached = item.get("_item_primary_features_cache")
    if isinstance(cached, dict):
        return dict(cached)

    features = item.get("canonical_features") or item.get("bill_canonical_features") or {}
    merged = dict(features) if isinstance(features, dict) else {}
    has_missing_primary = any(
        _primary_raw_value(merged, key) in (None, "", [])
        for key in (*_PRIMARY_NUMERIC_FIELDS, *_PRIMARY_CATEGORICAL_FIELDS)
    )
    if has_missing_primary:
        parts: list[str] = []
        for key in ("bill_text", "name", "description", "bill_name"):
            _append_secondary_text_parts(parts, item.get(key))
        raw_text = " ".join(parts).strip()
        if raw_text:
            try:
                specialty = str(item.get("specialty") or item.get("book") or "").strip()
                parsed_params = text_parser.parse(raw_text)
                parsed_features = text_parser.parse_canonical(
                    raw_text,
                    specialty=specialty,
                    params=parsed_params,
                )
            except Exception as exc:  # pragma: no cover - parser should not break ranking
                logger.debug("item primary feature hydration failed: {}", exc)
                parsed_features = {}
                parsed_params = {}

            if isinstance(parsed_features, dict) and parsed_features:
                hydrated = dict(parsed_features)
                for key, value in merged.items():
                    if key == "numeric_params":
                        continue
                    if value not in (None, "", []):
                        hydrated[key] = value
                hydrated_numeric = dict(parsed_features.get("numeric_params") or {})
                existing_numeric = merged.get("numeric_params") or {}
                if isinstance(existing_numeric, dict):
                    for key, value in existing_numeric.items():
                        if value not in (None, "", []):
                            hydrated_numeric[key] = value
                if hydrated_numeric:
                    hydrated["numeric_params"] = hydrated_numeric
                merged = hydrated
            elif isinstance(parsed_params, dict) and parsed_params:
                numeric_params = dict(merged.get("numeric_params") or {})
                for key, value in parsed_params.items():
                    if key in _PRIMARY_NUMERIC_FIELDS and value not in (None, "", []):
                        merged.setdefault(key, value)
                        numeric_params.setdefault(key, value)
                if numeric_params:
                    merged["numeric_params"] = numeric_params

    params = item.get("params") or item.get("bill_params") or {}
    if isinstance(params, dict):
        numeric_params = dict(merged.get("numeric_params") or {})
        for key, value in params.items():
            if value in (None, "", []):
                continue
            merged.setdefault(key, value)
            if key in _PRIMARY_NUMERIC_FIELDS:
                numeric_params.setdefault(key, value)
        if numeric_params:
            merged["numeric_params"] = numeric_params
    merged = _apply_item_subject_family_hint(item, merged)
    _hydrate_primary_tier_limits_from_text(raw_text if "raw_text" in locals() else "", merged)
    item["_item_primary_features_cache"] = dict(merged)
    return merged


def _primary_raw_value(features: dict, key: str):
    value = (features or {}).get(key)
    if value not in (None, "", []):
        return value
    numeric_params = (features or {}).get("numeric_params") or {}
    if isinstance(numeric_params, dict):
        value = numeric_params.get(key)
        if value not in (None, "", []):
            return value
    specs = (features or {}).get("specs") or {}
    if isinstance(specs, dict):
        value = specs.get(key)
        if value not in (None, "", []):
            return value
    return None


def _primary_text(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _primary_text_matches(expected, actual) -> bool:
    expected_text = _primary_text(expected)
    actual_text = _primary_text(actual)
    if not expected_text or not actual_text:
        return False
    if expected_text == actual_text:
        return True
    if len(expected_text) >= 2 and len(actual_text) >= 2:
        return expected_text in actual_text or actual_text in expected_text
    return False


def _is_specific_primary_value(key: str, value) -> bool:
    text = _primary_text(value)
    if not text or text in _GENERIC_PRIMARY_TEXT:
        return False
    if key == "canonical_name":
        return len(text) >= 4
    if key in {"support_action"}:
        return False
    return True


def _primary_float(value) -> float | None:
    try:
        if value in (None, "", []):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _primary_numeric_rank(expected, actual) -> tuple[int, float] | None:
    expected_value = _primary_float(expected)
    actual_value = _primary_float(actual)
    if expected_value is None or actual_value is None:
        return None
    if expected_value <= 0:
        return (0, abs(actual_value - expected_value))
    if actual_value >= expected_value:
        return (0, (actual_value - expected_value) / max(expected_value, 1.0))
    return (1, (expected_value - actual_value) / max(expected_value, 1.0))


def _primary_rank_better(left: tuple[int, float] | None, right: tuple[int, float] | None) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    return left[0] < right[0] or (left[0] == right[0] and left[1] + 0.025 < right[1])


def _primary_param_direction(item: dict | None, candidate: dict, incumbent: dict) -> dict:
    bill_features = _item_primary_features(item)
    if not bill_features:
        return {}
    candidate_features = _candidate_features(candidate)
    incumbent_features = _candidate_features(incumbent)
    if not candidate_features or not incumbent_features:
        return {}

    edges: list[str] = []
    conflicts: list[str] = []
    decisive_edges: list[str] = []

    for key in _PRIMARY_CATEGORICAL_FIELDS:
        expected = _primary_raw_value(bill_features, key)
        if expected in (None, "", []):
            continue
        candidate_value = _primary_raw_value(candidate_features, key)
        incumbent_value = _primary_raw_value(incumbent_features, key)
        candidate_match = _primary_text_matches(expected, candidate_value)
        incumbent_match = _primary_text_matches(expected, incumbent_value)
        candidate_has_value = candidate_value not in (None, "", [])
        incumbent_has_value = incumbent_value not in (None, "", [])

        if candidate_match and not incumbent_match and _is_specific_primary_value(key, expected):
            edges.append(key)
        elif incumbent_match and candidate_has_value and not candidate_match:
            conflicts.append(key)
        elif candidate_has_value and incumbent_has_value and not candidate_match and incumbent_match:
            conflicts.append(key)

    for key in _PRIMARY_NUMERIC_FIELDS:
        expected = _primary_raw_value(bill_features, key)
        if expected in (None, "", []):
            continue
        candidate_rank = _primary_numeric_rank(expected, _primary_raw_value(candidate_features, key))
        incumbent_rank = _primary_numeric_rank(expected, _primary_raw_value(incumbent_features, key))
        if _primary_rank_better(candidate_rank, incumbent_rank):
            edges.append(key)
            if (
                key in _DECISIVE_PRIMARY_NUMERIC_FIELDS
                and candidate_rank is not None
                and candidate_rank[0] == 0
                and (
                    incumbent_rank is None
                    or incumbent_rank[0] > candidate_rank[0]
                    or incumbent_rank[1] > candidate_rank[1] + 0.10
                )
            ):
                decisive_edges.append(key)
        elif _primary_rank_better(incumbent_rank, candidate_rank):
            conflicts.append(key)

    edges = list(dict.fromkeys(edges))
    conflicts = list(dict.fromkeys(conflicts))
    decisive_edges = list(dict.fromkeys(decisive_edges))
    if not edges and not conflicts:
        return {}
    strong_edges = [edge for edge in edges if edge in {"canonical_name", "material", "install_method", "connection"}]
    bonus = min(0.12, 0.032 * len(edges) + 0.030 * len(decisive_edges) + 0.012 * len(strong_edges))
    penalty = min(0.10, 0.045 * len(conflicts))
    return {
        "edges": edges,
        "conflicts": conflicts,
        "decisive_edges": decisive_edges,
        "bonus": bonus,
        "penalty": penalty,
    }


def _family_group_rank_score(candidate: dict) -> float:
    ltr_score = _candidate_float(candidate, "ltr_score")
    ltr_component = max(0.0, min(1.0, (ltr_score + 1.0) / 2.0))
    return (
        _candidate_float(candidate, "manual_structured_score") * 0.28
        + _candidate_float(candidate, "param_score") * 0.22
        + _candidate_float(candidate, "logic_score", 0.5) * 0.16
        + _candidate_float(candidate, "feature_alignment_score", 0.5) * 0.14
        + _candidate_rerank_evidence(candidate) * 0.12
        + _candidate_float(candidate, "name_bonus") * 0.04
        + ltr_component * 0.04
    )


def _has_family_group_edge(candidate: dict, incumbent: dict) -> tuple[int, list[str]]:
    edges: list[str] = []
    if _candidate_float(candidate, "manual_structured_score") > _candidate_float(incumbent, "manual_structured_score") + 0.035:
        edges.append("manual_structured_score")
    if _candidate_float(candidate, "param_score") > _candidate_float(incumbent, "param_score") + 0.055:
        edges.append("param_score")
    if _candidate_float(candidate, "logic_score", 0.5) > _candidate_float(incumbent, "logic_score", 0.5) + 0.09:
        edges.append("logic_score")
    if _candidate_float(candidate, "feature_alignment_score", 0.5) > _candidate_float(incumbent, "feature_alignment_score", 0.5) + 0.035:
        edges.append("feature_alignment_score")
    if _candidate_rerank_evidence(candidate) > _candidate_rerank_evidence(incumbent) + 0.035:
        edges.append("rerank_score")
    if _candidate_float(candidate, "name_bonus") > _candidate_float(incumbent, "name_bonus") + 0.07:
        edges.append("name_bonus")
    if candidate.get("logic_exact_primary_match") and not incumbent.get("logic_exact_primary_match"):
        edges.append("logic_exact_primary_match")
    return len(edges), edges


_STRUCTURAL_PRIMARY_GROUPS = {
    "canonical_name": "type",
    "entity": "type",
    "material": "material",
    "install_method": "install_method",
    "connection": "connection",
    "laying_method": "install_method",
    "bridge_type": "type",
    "box_mount_mode": "install_method",
    "valve_connection_family": "connection",
    "valve_type": "type",
    "cable_type": "type",
    "cable_head_type": "type",
    "conduit_type": "type",
    "wire_type": "type",
    "support_scope": "scope",
    "support_action": "scope",
    "sanitary_mount_mode": "install_method",
    "sanitary_flush_mode": "type",
    "sanitary_water_mode": "type",
    "sanitary_nozzle_mode": "type",
    "sanitary_tank_mode": "type",
    "lamp_type": "type",
    "outlet_grounding": "connection",
}

_STRUCTURAL_GROUP_WEIGHTS = {
    "type": 0.070,
    "primary_parameter": 0.045,
    "install_method": 0.060,
    "material": 0.060,
    "connection": 0.055,
    "numeric_bin": 0.050,
    "secondary_type": 0.075,
    "scope": 0.040,
    "score": 0.022,
}

def _structural_primary_group(edge: str) -> str:
    if edge in _PRIMARY_NUMERIC_FIELDS:
        return "numeric_bin"
    return _STRUCTURAL_PRIMARY_GROUPS.get(edge, "primary_parameter")


def _append_structural_group(groups: dict[str, list[str]], group: str, edge: str) -> None:
    bucket = groups.setdefault(group, [])
    if edge not in bucket:
        bucket.append(edge)


def _build_unified_family_structural_evidence(
    item: dict | None,
    candidate: dict,
    incumbent: dict,
) -> dict:
    primary_direction = _primary_param_direction(item, candidate, incumbent)
    secondary_direction = _secondary_type_direction(item, candidate, incumbent)
    score_edge_count, score_edges = _has_family_group_edge(candidate, incumbent)

    evidence_groups: dict[str, list[str]] = {}
    conflict_groups: dict[str, list[str]] = {}
    evidence_edges: list[str] = []
    conflict_edges: list[str] = []

    primary_edges = list(primary_direction.get("edges") or [])
    primary_conflicts = list(primary_direction.get("conflicts") or [])
    decisive_primary_edges = list(primary_direction.get("decisive_edges") or [])
    secondary_edges = list(secondary_direction.get("edges") or [])
    secondary_conflicts = list(secondary_direction.get("conflicts") or [])

    for edge in primary_edges:
        group = _structural_primary_group(edge)
        _append_structural_group(evidence_groups, group, edge)
        evidence_edges.append(f"primary_param:{edge}")
    for edge in decisive_primary_edges:
        _append_structural_group(evidence_groups, "numeric_bin", edge)
        evidence_edges.append(f"decisive_primary_param:{edge}")
    for edge in secondary_edges:
        _append_structural_group(evidence_groups, "secondary_type", edge)
        evidence_edges.append(f"secondary_type:{edge}")
    for edge in score_edges:
        _append_structural_group(evidence_groups, "score", edge)
        evidence_edges.append(edge)

    for edge in primary_conflicts:
        group = _structural_primary_group(edge)
        _append_structural_group(conflict_groups, group, edge)
        conflict_edges.append(f"primary_param_conflict:{edge}")
    for edge in secondary_conflicts:
        _append_structural_group(conflict_groups, "secondary_type", edge)
        conflict_edges.append(f"secondary_type_conflict:{edge}")

    structural_bonus = 0.0
    for group, edges in evidence_groups.items():
        if group == "score":
            continue
        structural_bonus += _STRUCTURAL_GROUP_WEIGHTS.get(group, 0.030) * len(edges)
    structural_bonus += float(primary_direction.get("bonus") or 0.0)
    structural_bonus += float(secondary_direction.get("bonus") or 0.0)

    structural_penalty = 0.0
    for group, edges in conflict_groups.items():
        structural_penalty += min(0.120, _STRUCTURAL_GROUP_WEIGHTS.get(group, 0.035) * len(edges))
    structural_penalty += float(primary_direction.get("penalty") or 0.0)
    structural_penalty += float(secondary_direction.get("penalty") or 0.0)

    evidence_edges = list(dict.fromkeys(evidence_edges))
    conflict_edges = list(dict.fromkeys(conflict_edges))
    evidence_count = sum(len(edges) for edges in evidence_groups.values())

    return {
        "primary_direction": primary_direction,
        "secondary_direction": secondary_direction,
        "evidence_groups": evidence_groups,
        "conflict_groups": conflict_groups,
        "evidence_edges": evidence_edges,
        "conflict_edges": conflict_edges,
        "primary_edges": primary_edges,
        "primary_conflicts": primary_conflicts,
        "decisive_primary_edges": decisive_primary_edges,
        "secondary_edges": secondary_edges,
        "secondary_conflicts": secondary_conflicts,
        "score_edges": score_edges,
        "score_edge_count": score_edge_count,
        "evidence_count": evidence_count,
        "bonus": min(0.240, structural_bonus),
        "penalty": min(0.180, structural_penalty),
    }


def _promote_family_group_stronger_candidate(
    candidates: list[dict],
    *,
    item: dict | None = None,
    min_margin: float = 0.055,
    max_scan: int = 20,
) -> tuple[list[dict], dict]:
    ordered = [dict(candidate) for candidate in (candidates or [])]
    if len(ordered) < 2:
        return ordered, {}

    incumbent = ordered[0]
    if _is_protected_anchor(incumbent):
        return ordered, {}

    incumbent_score = _family_group_rank_score(incumbent)
    incumbent_ltr = _candidate_float(incumbent, "ltr_score")
    incumbent_rerank = _candidate_rerank_evidence(incumbent)
    incumbent_param_tier = _candidate_int(incumbent, "param_tier", 1)

    best_index = -1
    best_score = incumbent_score
    best_edges: list[str] = []
    best_structural_evidence: dict = {}
    best_pool_scan_mode = "same_book_same_family_top20"
    scanned_same_family_count = 0

    for index, candidate in enumerate(ordered[1:max_scan], start=1):
        same_family_group = _same_family_group(incumbent, candidate)
        bill_guided_group = False
        if not same_family_group:
            bill_guided_group = _bill_guided_family_group(item, incumbent, candidate)
        tier_prefix_group = _same_tier_prefix_group(incumbent, candidate)
        if not same_family_group and not bill_guided_group and not tier_prefix_group:
            continue
        scanned_same_family_count += 1
        if _is_protected_anchor(candidate) or _has_lifecycle_hard_conflict(candidate):
            continue
        candidate_param_tier = _candidate_int(candidate, "param_tier", 1)
        if candidate_param_tier > incumbent_param_tier:
            continue

        structural_evidence = _build_unified_family_structural_evidence(item, candidate, incumbent)
        primary_edges = list(structural_evidence.get("primary_edges") or [])
        primary_conflicts = list(structural_evidence.get("primary_conflicts") or [])
        secondary_edges = list(structural_evidence.get("secondary_edges") or [])
        secondary_conflicts = list(structural_evidence.get("secondary_conflicts") or [])
        if primary_conflicts and not primary_edges and not secondary_edges:
            continue
        if secondary_conflicts and not secondary_edges:
            continue

        candidate_score = _family_group_rank_score(candidate)
        candidate_score += float(structural_evidence.get("bonus") or 0.0)
        candidate_score -= float(structural_evidence.get("penalty") or 0.0)
        margin = candidate_score - incumbent_score

        edge_count = int(structural_evidence.get("evidence_count") or 0)
        non_primary_edge_count = int(structural_evidence.get("score_edge_count") or 0)
        edges = list(structural_evidence.get("evidence_edges") or [])
        decisive_primary_edges = list(structural_evidence.get("decisive_primary_edges") or [])
        if primary_conflicts and len(primary_edges) < 2:
            allowed_numeric_conflicts = bool(
                secondary_edges
                and set(primary_conflicts).issubset({"perimeter", "half_perimeter", "large_side"})
            )
            if not allowed_numeric_conflicts:
                continue
        if decisive_primary_edges and non_primary_edge_count == 0:
            candidate_family, _ = _candidate_canonical_group(candidate)
            incumbent_family, _ = _candidate_canonical_group(incumbent)
            if (
                candidate_family in _NUMERIC_ONLY_DECISIVE_NEEDS_SECONDARY_FAMILIES
                or incumbent_family in _NUMERIC_ONLY_DECISIVE_NEEDS_SECONDARY_FAMILIES
            ) and not secondary_edges:
                continue
        required_edge_count = 1 if (decisive_primary_edges or secondary_edges) and not secondary_conflicts else 2
        if bill_guided_group:
            required_edge_count = max(2, required_edge_count)
        if tier_prefix_group and not same_family_group:
            required_edge_count = max(2, required_edge_count)
        if edge_count < required_edge_count:
            continue
        effective_min_margin = 0.015 if (decisive_primary_edges or secondary_edges) and not secondary_conflicts else min_margin
        prefix_has_structural_edge = bool(primary_edges or decisive_primary_edges or secondary_edges)
        if tier_prefix_group and prefix_has_structural_edge and not primary_conflicts and not secondary_conflicts:
            effective_min_margin = min(effective_min_margin, 0.025)
        if index >= 12:
            late_pool_has_strong_structure = bool(decisive_primary_edges or secondary_edges or edge_count >= 3)
            if not late_pool_has_strong_structure:
                continue
            effective_min_margin = max(effective_min_margin, 0.045)
        if margin < effective_min_margin:
            continue

        candidate_ltr = _candidate_float(candidate, "ltr_score")
        candidate_rerank = _candidate_rerank_evidence(candidate)
        if incumbent_ltr > candidate_ltr + 0.70 and (edge_count < 3 or margin < 0.10):
            continue
        if incumbent_rerank > candidate_rerank + 0.08 and (edge_count < 3 or margin < 0.09):
            continue

        if candidate_score > best_score:
            best_index = index
            best_score = candidate_score
            best_edges = edges
            best_structural_evidence = structural_evidence
            best_pool_scan_mode = (
                "same_book_bill_guided_family_top20"
                if bill_guided_group
                else "same_book_same_family_prefix_tier_top20"
                if tier_prefix_group and same_family_group
                else "same_book_same_prefix_tier_top20"
                if tier_prefix_group
                else "same_book_same_family_top20"
            )

    if best_index < 0:
        return ordered, {}

    promoted = ordered.pop(best_index)
    if best_pool_scan_mode == "same_book_bill_guided_family_top20":
        promoted["_bill_guided_family_group_structural_winner"] = True
        promoted["_bill_guided_family_group_margin"] = round(best_score - incumbent_score, 6)
    elif best_pool_scan_mode in {
        "same_book_same_prefix_tier_top20",
        "same_book_same_family_prefix_tier_top20",
    }:
        promoted["_same_prefix_tier_structural_winner"] = True
        promoted["_same_prefix_tier_margin"] = round(best_score - incumbent_score, 6)
    restored = [promoted] + ordered
    return restored, {
        "applied": True,
        "reason": "same_book_family_group_ranker",
        "from_quota_id": str(incumbent.get("quota_id", "") or ""),
        "to_quota_id": str(promoted.get("quota_id", "") or ""),
        "from_score": round(incumbent_score, 6),
        "to_score": round(best_score, 6),
        "margin": round(best_score - incumbent_score, 6),
        "evidence_edges": best_edges,
        "pool_scan": {
            "mode": best_pool_scan_mode,
            "max_scan": max_scan,
            "scanned_same_family_count": scanned_same_family_count,
            "selected_original_rank": best_index + 1,
        },
        "structural_ranking": {
            "entry": "unified_same_family_structural_ranker",
            "evidence_groups": dict(best_structural_evidence.get("evidence_groups") or {}),
            "conflict_groups": dict(best_structural_evidence.get("conflict_groups") or {}),
            "conflict_edges": list(best_structural_evidence.get("conflict_edges") or []),
            "bonus": round(float(best_structural_evidence.get("bonus") or 0.0), 6),
            "penalty": round(float(best_structural_evidence.get("penalty") or 0.0), 6),
        },
        "book_key": _candidate_book_key(promoted),
        "family_group": list(_candidate_canonical_group(promoted)),
    }


def _rankable_candidate_contract(candidate: dict, rank_position: int) -> dict:
    features = _candidate_features(candidate)
    return {
        "quota_id": str(candidate.get("quota_id", "") or ""),
        "name": str(candidate.get("name", "") or ""),
        "book_key": _candidate_book_key(candidate),
        "rank_position": rank_position,
        "family": str(features.get("family") or "").strip(),
        "entity": str(features.get("entity") or "").strip(),
        "canonical_name": str(features.get("canonical_name") or "").strip(),
        "material": str(features.get("material") or "").strip(),
        "connection": str(features.get("connection") or "").strip(),
        "install_method": str(features.get("install_method") or "").strip(),
        "system": str(features.get("system") or "").strip(),
        "primary_params": dict(features.get("numeric_params") or {}),
        "scores": {
            "ltr_score": _candidate_float(candidate, "ltr_score"),
            "rerank_score": _candidate_rerank_evidence(candidate),
            "rank_score": _candidate_lifecycle_score(candidate),
            "manual_structured_score": _candidate_float(candidate, "manual_structured_score"),
            "param_score": _candidate_float(candidate, "param_score"),
            "logic_score": _candidate_float(candidate, "logic_score", 0.5),
            "feature_alignment_score": _candidate_float(candidate, "feature_alignment_score", 0.5),
        },
        "hard_param_flags": {
            "param_match": candidate.get("param_match", True),
            "param_tier": _candidate_int(candidate, "param_tier", 1),
            "family_gate_hard_conflict": bool(candidate.get("family_gate_hard_conflict")),
            "feature_alignment_hard_conflict": bool(candidate.get("feature_alignment_hard_conflict")),
            "logic_hard_conflict": bool(candidate.get("logic_hard_conflict")),
            "context_alignment_hard_conflict": bool(candidate.get("context_alignment_hard_conflict")),
        },
    }


def _rankable_contract_group_key(contract: dict) -> tuple[str, str, str]:
    family = str(contract.get("family") or "").strip()
    canonical = str(contract.get("canonical_name") or contract.get("entity") or "").strip()
    return (
        str(contract.get("book_key") or "").strip(),
        family,
        canonical,
    )


def _contract_matches_bill_identity(item: dict | None, contract: dict) -> bool:
    bill_features = _item_primary_features(item)
    if not bill_features:
        return False
    bill_family = str(bill_features.get("family") or "").strip()
    bill_entity = str(bill_features.get("entity") or "").strip()
    bill_canonical = str(bill_features.get("canonical_name") or "").strip()
    candidate_family = str(contract.get("family") or "").strip()
    candidate_entity = str(contract.get("entity") or "").strip()
    candidate_canonical = str(contract.get("canonical_name") or "").strip()
    if bill_family and candidate_family and bill_family == candidate_family:
        return True
    return bool(
        bill_canonical
        and candidate_canonical
        and bill_canonical == candidate_canonical
    ) or bool(bill_entity and candidate_entity and bill_entity == candidate_entity)


def _contract_scan_mode(
    *,
    item: dict | None,
    incumbent: dict,
    candidate: dict,
    incumbent_contract: dict,
    candidate_contract: dict,
) -> str:
    incumbent_key = _rankable_contract_group_key(incumbent_contract)
    candidate_key = _rankable_contract_group_key(candidate_contract)
    same_book = bool(incumbent_key[0] and incumbent_key[0] == candidate_key[0])
    same_group = bool(
        same_book
        and (
            (incumbent_key[1] and incumbent_key[1] == candidate_key[1])
            or (incumbent_key[2] and incumbent_key[2] == candidate_key[2])
        )
    )
    if same_group:
        return "top20_rankable_contract_same_book_group"
    if _same_tier_prefix_group(incumbent, candidate):
        return "top20_rankable_contract_same_prefix"
    if _contract_matches_bill_identity(item, candidate_contract):
        return (
            "top20_rankable_contract_bill_guided_same_book"
            if same_book
            else "top20_rankable_contract_bill_guided_cross_book"
        )
    return ""


def _promote_rankable_contract_structural_candidate(
    candidates: list[dict],
    *,
    item: dict | None = None,
    min_margin: float = 0.035,
    max_scan: int = 20,
) -> tuple[list[dict], dict]:
    ordered = [dict(candidate) for candidate in (candidates or [])]
    if len(ordered) < 2:
        return ordered, {}

    incumbent = ordered[0]
    if _is_protected_anchor(incumbent):
        return ordered, {}

    scan_window = ordered[:max_scan]
    incumbent_contract = _rankable_candidate_contract(incumbent, 1)
    incumbent_score = _family_group_rank_score(incumbent)
    incumbent_ltr = _candidate_float(incumbent, "ltr_score")
    incumbent_rerank = _candidate_rerank_evidence(incumbent)
    incumbent_param_tier = _candidate_int(incumbent, "param_tier", 1)

    best_index = -1
    best_score = incumbent_score
    best_structural_evidence: dict = {}
    best_contract: dict = {}
    best_mode = ""
    scanned_count = 0
    eligible_count = 0

    for index, candidate in enumerate(scan_window[1:], start=1):
        if _is_protected_anchor(candidate) or _has_rankable_contract_hard_conflict(candidate):
            continue

        candidate_contract = _rankable_candidate_contract(candidate, index + 1)
        mode = _contract_scan_mode(
            item=item,
            incumbent=incumbent,
            candidate=candidate,
            incumbent_contract=incumbent_contract,
            candidate_contract=candidate_contract,
        )
        if not mode:
            continue
        scanned_count += 1

        structural_evidence = _build_unified_family_structural_evidence(item, candidate, incumbent)
        primary_edges = list(structural_evidence.get("primary_edges") or [])
        primary_conflicts = list(structural_evidence.get("primary_conflicts") or [])
        secondary_edges = list(structural_evidence.get("secondary_edges") or [])
        secondary_conflicts = list(structural_evidence.get("secondary_conflicts") or [])
        decisive_primary_edges = list(structural_evidence.get("decisive_primary_edges") or [])
        edge_count = int(structural_evidence.get("evidence_count") or 0)
        score_edge_count = int(structural_evidence.get("score_edge_count") or 0)
        structural_edge_count = edge_count - score_edge_count
        protected_soft_param = bool(
            candidate.get("_rankable_pool_contract_protected")
            and candidate.get("param_match") is False
        )

        if structural_edge_count <= 0:
            continue
        if primary_conflicts and not (primary_edges or secondary_edges):
            continue
        if secondary_conflicts and not secondary_edges:
            continue
        if decisive_primary_edges and not secondary_edges:
            candidate_family, _ = _candidate_canonical_group(candidate)
            incumbent_family, _ = _candidate_canonical_group(incumbent)
            if (
                candidate_family in _NUMERIC_ONLY_DECISIVE_NEEDS_SECONDARY_FAMILIES
                or incumbent_family in _NUMERIC_ONLY_DECISIVE_NEEDS_SECONDARY_FAMILIES
            ):
                continue

        candidate_param_tier = _candidate_int(candidate, "param_tier", 1)
        if candidate_param_tier > incumbent_param_tier:
            strong_structure = bool(decisive_primary_edges or secondary_edges or structural_edge_count >= 3)
            if not strong_structure or candidate_param_tier > incumbent_param_tier + 1:
                continue

        required_structural_edges = 1 if (decisive_primary_edges or secondary_edges) else 2
        if "cross_book" in mode:
            required_structural_edges = max(2, required_structural_edges)
        if structural_edge_count < required_structural_edges:
            continue
        if protected_soft_param:
            protected_has_specific_evidence = bool(
                decisive_primary_edges
                or secondary_edges
                or len(
                    set(primary_edges)
                    & {
                        "material",
                        "install_method",
                        "connection",
                        "laying_method",
                        "box_mount_mode",
                        "valve_type",
                        "cable_type",
                        "wire_type",
                        "conduit_type",
                        "sanitary_flush_mode",
                        "lamp_type",
                    }
                ) >= 1
            )
            if not protected_has_specific_evidence or structural_edge_count < 2:
                continue

        candidate_score = _family_group_rank_score(candidate)
        candidate_score += float(structural_evidence.get("bonus") or 0.0)
        candidate_score -= float(structural_evidence.get("penalty") or 0.0)
        if protected_soft_param and (decisive_primary_edges or secondary_edges):
            candidate_score += 0.035
        margin = candidate_score - incumbent_score
        effective_min_margin = min_margin
        if decisive_primary_edges or secondary_edges:
            effective_min_margin = min(effective_min_margin, 0.020)
        if protected_soft_param:
            effective_min_margin = max(effective_min_margin, 0.045)
        if "cross_book" in mode:
            effective_min_margin = max(effective_min_margin, 0.055)
        if index >= 12:
            effective_min_margin = max(effective_min_margin, 0.045)
        if margin < effective_min_margin:
            continue

        candidate_ltr = _candidate_float(candidate, "ltr_score")
        candidate_rerank = _candidate_rerank_evidence(candidate)
        if incumbent_ltr > candidate_ltr + 0.70 and (structural_edge_count < 3 or margin < 0.10):
            continue
        if incumbent_rerank > candidate_rerank + 0.08 and (structural_edge_count < 3 or margin < 0.09):
            continue

        eligible_count += 1
        if candidate_score > best_score:
            best_index = index
            best_score = candidate_score
            best_structural_evidence = structural_evidence
            best_contract = candidate_contract
            best_mode = mode

    if best_index < 0:
        return ordered, {}

    promoted = ordered.pop(best_index)
    restored = [promoted] + ordered
    return restored, {
        "applied": True,
        "reason": "rankable_contract_top20_structural_scan",
        "from_quota_id": str(incumbent.get("quota_id", "") or ""),
        "to_quota_id": str(promoted.get("quota_id", "") or ""),
        "from_score": round(incumbent_score, 6),
        "to_score": round(best_score, 6),
        "margin": round(best_score - incumbent_score, 6),
        "evidence_edges": list(best_structural_evidence.get("evidence_edges") or []),
        "pool_scan": {
            "mode": best_mode,
            "max_scan": max_scan,
            "scanned_rankable_contract_count": scanned_count,
            "eligible_rankable_contract_count": eligible_count,
            "selected_original_rank": best_index + 1,
        },
        "rankable_candidate_contract": {
            "incumbent": incumbent_contract,
            "selected": best_contract,
        },
        "structural_ranking": {
            "entry": "top20_rankable_contract_structural_scan",
            "evidence_groups": dict(best_structural_evidence.get("evidence_groups") or {}),
            "conflict_groups": dict(best_structural_evidence.get("conflict_groups") or {}),
            "conflict_edges": list(best_structural_evidence.get("conflict_edges") or []),
            "bonus": round(float(best_structural_evidence.get("bonus") or 0.0), 6),
            "penalty": round(float(best_structural_evidence.get("penalty") or 0.0), 6),
        },
        "book_key": _candidate_book_key(promoted),
        "family_group": list(_candidate_canonical_group(promoted)),
    }


def _promote_post_ltr_structural_candidate(
    candidates: list[dict],
    *,
    item: dict | None = None,
    min_margin: float = 0.055,
    max_scan: int = 20,
) -> tuple[list[dict], dict]:
    ordered, legacy_family_ranker = _promote_family_group_stronger_candidate(
        candidates,
        item=item,
        min_margin=min_margin,
        max_scan=max_scan,
    )
    if not legacy_family_ranker:
        ordered, legacy_family_ranker = _promote_rankable_contract_structural_candidate(
            candidates,
            item=item,
            min_margin=min_margin,
            max_scan=max_scan,
        )
        if not legacy_family_ranker:
            return ordered, {}

    structural_ranker = {
        **dict(legacy_family_ranker),
        "reason": "post_ltr_structural_comparator",
        "legacy_reason": str(legacy_family_ranker.get("reason") or ""),
        "source_stage": "post_ltr_structural_comparator",
        "legacy_source_stage": (
            "rankable_contract_top20_structural_scan"
            if legacy_family_ranker.get("reason") == "rankable_contract_top20_structural_scan"
            else "family_group_ranker"
        ),
        "entry": "post_ltr_structural_ranker",
        "contract": "post_ltr_structural_comparator",
        "comparator_version": (
            "v36_sys_r4"
            if legacy_family_ranker.get("reason") == "rankable_contract_top20_structural_scan"
            else "v36_sys_r2"
        ),
        "compared_dimensions": [
            "family",
            "entity",
            "canonical_name",
            "primary_params",
            "secondary_type",
            "install_method",
            "material",
            "connection",
            "ltr_score",
            "rerank_score",
        ],
        "applied": False,
        "advisory_applied": True,
        "advisory_only": True,
        "decision_owner": "final_decider",
    }
    structural_ranker["legacy_family_group_ranker"] = {
        **dict(legacy_family_ranker),
        "canonical_advisory_stage": "post_ltr_structural_ranker",
        "compatibility_alias": True,
        "applied": False,
        "advisory_applied": True,
        "advisory_only": True,
        "decision_owner": "final_decider",
    }
    return ordered, structural_ranker


def _promote_lifecycle_stronger_candidate(
    candidates: list[dict],
    *,
    min_margin: float = 0.24,
    max_scan: int = 12,
    protect_structural_advantage: bool = True,
) -> tuple[list[dict], dict]:
    ordered = [dict(candidate) for candidate in (candidates or [])]
    if len(ordered) < 2:
        return ordered, {}

    current = ordered[0]
    if _is_protected_anchor(current):
        return ordered, {}

    current_score = _candidate_lifecycle_score(current)
    current_param = float(current.get("param_score", 0.0) or 0.0)
    current_feature = float(current.get("feature_alignment_score", 0.0) or 0.0)
    best_index = -1
    best_score = current_score

    for index, candidate in enumerate(ordered[1:max_scan], start=1):
        if _has_lifecycle_hard_conflict(candidate) or _is_protected_anchor(candidate):
            continue
        candidate_score = _candidate_lifecycle_score(candidate)
        if candidate_score - current_score < min_margin:
            continue
        if current.get("_bill_guided_family_group_structural_winner"):
            continue
        if protect_structural_advantage and _has_structural_lifecycle_advantage(current, candidate):
            continue
        candidate_param = float(candidate.get("param_score", 0.0) or 0.0)
        candidate_feature = float(candidate.get("feature_alignment_score", 0.0) or 0.0)
        if candidate_param < current_param - 0.08 and candidate_feature < current_feature - 0.10:
            continue
        if candidate_score > best_score:
            best_index = index
            best_score = candidate_score

    if best_index < 0:
        return ordered, {}

    promoted = ordered.pop(best_index)
    restored = [promoted] + ordered
    return restored, {
        "applied": True,
        "reason": "stronger_lifecycle_score",
        "from_quota_id": str(current.get("quota_id", "") or ""),
        "to_quota_id": str(promoted.get("quota_id", "") or ""),
        "from_score": round(current_score, 6),
        "to_score": round(best_score, 6),
        "margin": round(best_score - current_score, 6),
    }


def _init_ranking_meta() -> dict:
    return {
        "pre_ltr_top1_id": "",
        "post_ltr_top1_id": "",
        "post_ltr_structural_top1_id": "",
        "post_cgr_top1_id": "",
        "post_cgr_advisory_top1_id": "",
        "post_arbiter_top1_id": "",
        "post_explicit_top1_id": "",
        "post_anchor_top1_id": "",
        "selected_top1_id": "",
        "legacy_top1_id": "",
        "post_final_top1_id": "",
        "final_changed_by": "",
        "final_decider_reason": "",
        "decision_advisories": [],
        "post_ltr_structural_ranker": {},
        "rank_stage_trace_steps": [],
        "candidate_count": 0,
        "hard_param_fail_rejected_count": 0,
        "hard_param_fail_rejected_candidates": [],
        "rankable_pool_contract_recovered_count": 0,
        "rankable_pool_contract_recovered_candidates": [],
        "ltr": {},
        "explicit_override": {},
        "category_safe_advisory": {},
        "unified_ranking_enabled": False,
        "unified_ranking_shadow_mode": False,
        "unified_ranking_mode": "disabled",
        "unified_ranking_executed": False,
        "unified_result_used": False,
        "unified_top1_id": "",
        "unified_top1_score": 0.0,
        "unified_top1_confidence": 0.0,
        "unified_top1_matches_selected": False,
        "unified_top1_matches_legacy": False,
        "legacy_top1_unified_score": None,
        "legacy_top1_unified_confidence": None,
        "unified_legacy_score_gap": None,
        "unified_ranking_diagnostics": {},
        "unified_ranking_error": "",
    }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hard_param_fail_reason_codes(candidate: dict) -> list[str]:
    reason_codes = []
    if bool(candidate.get("param_hard_fail")):
        reason_codes.append("param_hard_fail")
    if str(candidate.get("param_validation_tier", "") or "").strip() == "hard_fail":
        reason_codes.append("param_validation_tier_hard_fail")
    if _safe_int(candidate.get("param_tier", 1), default=1) == 0:
        reason_codes.append("param_tier_zero")
    return reason_codes or ["param_hard_fail"]


def _existing_candidate_features(candidate: dict) -> dict:
    features = (
        (candidate or {}).get("candidate_canonical_features")
        or (candidate or {}).get("canonical_features")
        or {}
    )
    return dict(features) if isinstance(features, dict) else {}


def _hard_param_resolution_signal_fields(candidate: dict) -> list[str]:
    features = _existing_candidate_features(candidate)
    fields: list[str] = []
    for key in (
        "family",
        "entity",
        "canonical_name",
        "system",
        "material",
        "connection",
        "install_method",
        "valve_type",
        "lamp_type",
    ):
        if features.get(key) not in (None, "", [], {}):
            fields.append(key)
    numeric_params = features.get("numeric_params")
    if isinstance(numeric_params, dict):
        fields.extend(
            f"primary_param:{key}"
            for key, value in numeric_params.items()
            if value not in (None, "", [], {})
        )
    return list(dict.fromkeys(fields))


def _hard_param_resolution_conflict_codes(candidate: dict) -> list[str]:
    codes: list[str] = []
    for key, code in (
        ("family_gate_hard_conflict", "family_gate"),
        ("feature_alignment_hard_conflict", "feature_alignment"),
        ("logic_hard_conflict", "logic"),
        ("context_alignment_hard_conflict", "context_alignment"),
        ("candidate_scope_conflict", "scope"),
    ):
        if bool((candidate or {}).get(key)):
            codes.append(code)
    detail = str((candidate or {}).get("param_detail", "") or "")
    detail_markers = (
        ("实体冲突", "entity"),
        ("系统冲突", "system"),
        ("材质", "material"),
        ("连接方式", "connection"),
        ("不匹配", "param_mismatch"),
        ("品类硬排斥", "category_veto"),
    )
    for marker, code in detail_markers:
        if marker in detail:
            codes.append(code)
    return list(dict.fromkeys(codes))


def _classify_hard_param_resolution(
    candidate: dict,
    *,
    recall_position: int | None = None,
    item_signal_count: int | None = None,
    item_edges: list[str] | None = None,
    item_conflicts: list[str] | None = None,
    protected: bool = False,
) -> dict:
    """Classify a hard-param candidate for diagnostics without changing ranking."""
    if not isinstance(candidate, dict):
        return {
            "class": "unknown_reject",
            "reason_codes": ["invalid_candidate"],
            "recall_position": recall_position,
            "signal_count": 0,
            "item_signal_count": 0,
            "evidence_fields": [],
            "conflict_codes": [],
            "item_edges": [],
            "item_conflicts": [],
        }

    if recall_position is None:
        recall_position = _candidate_recall_position(candidate, 999)
    signal_count = _rankable_contract_signal_count(candidate)
    item_signal_count = _safe_int(item_signal_count, default=0)
    item_edges = list(item_edges or [])
    item_conflicts = list(item_conflicts or [])
    evidence_fields = _hard_param_resolution_signal_fields(candidate)
    conflict_codes = _hard_param_resolution_conflict_codes(candidate)
    reason_codes = _hard_param_fail_reason_codes(candidate)
    if protected:
        reason_codes.append("already_rankable_contract_protected")

    if not str(candidate.get("quota_id", "") or "").strip() or not str(candidate.get("name", "") or "").strip():
        resolution_class = "unknown_reject"
        reason_codes.append("missing_identity")
    elif recall_position > 20:
        resolution_class = "unknown_reject"
        reason_codes.append("recall_position_gt_20")
    elif len(conflict_codes) >= 3 or "category_veto" in conflict_codes:
        resolution_class = "hard_veto"
        reason_codes.append("multiple_or_category_conflict")
    elif item_conflicts and not item_edges:
        resolution_class = "hard_veto"
        reason_codes.append("item_structural_conflict_without_edge")
    elif item_signal_count >= 3 and len(item_conflicts) <= 1:
        resolution_class = "soft_conflict_protected"
        reason_codes.append("item_structural_evidence")
    elif signal_count >= 4 and len(conflict_codes) <= 1:
        resolution_class = "soft_conflict_protected"
        reason_codes.append("candidate_rank_evidence")
    elif (
        "通用定额降权" in str(candidate.get("param_detail", "") or "")
        and len(conflict_codes) <= 1
        and signal_count >= 2
    ):
        resolution_class = "rank_penalty_only"
        reason_codes.append("generic_quota_penalty_only")
    elif protected:
        resolution_class = "soft_conflict_protected"
    else:
        resolution_class = "unknown_reject"
        reason_codes.append("insufficient_safe_evidence")

    return {
        "class": resolution_class,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "recall_position": recall_position,
        "signal_count": signal_count,
        "item_signal_count": item_signal_count,
        "evidence_fields": evidence_fields,
        "conflict_codes": conflict_codes,
        "item_edges": item_edges,
        "item_conflicts": item_conflicts,
    }


def _candidate_hard_param_resolution(candidate: dict) -> dict:
    existing = (candidate or {}).get("_hard_param_resolution")
    if isinstance(existing, dict) and existing.get("class"):
        return dict(existing)
    return _classify_hard_param_resolution(candidate)


def _candidate_recall_position(candidate: dict, fallback: int) -> int:
    for key in ("recall_rank", "_recall_rank", "candidate_index", "hybrid_rank", "rrf_rank", "bm25_rank"):
        value = candidate.get(key)
        try:
            rank = int(value)
        except (TypeError, ValueError):
            continue
        if rank > 0:
            return rank
    return fallback


def _rankable_contract_signal_count(candidate: dict) -> int:
    signals = 0
    if max(
        _safe_float(candidate.get("rerank_score")),
        _safe_float(candidate.get("semantic_rerank_score")),
        _safe_float(candidate.get("spec_rerank_score")),
    ) >= 0.72:
        signals += 1
    if _safe_float(candidate.get("feature_alignment_score"), 0.5) >= 0.84:
        signals += 1
    if _safe_float(candidate.get("logic_score"), 0.5) >= 0.78:
        signals += 1
    if _safe_float(candidate.get("manual_structured_score")) >= 0.34:
        signals += 1
    if _safe_float(candidate.get("param_score")) >= 0.20:
        signals += 1
    if _safe_float(candidate.get("name_bonus")) >= 0.08:
        signals += 1
    if _safe_float(candidate.get("candidate_scope_match")) >= 0.60:
        signals += 1
    if _safe_float(candidate.get("knowledge_prior_score")) >= 0.35:
        signals += 1
    return signals


def _should_retain_hard_fail_for_rankable_contract(candidate: dict, recall_position: int) -> bool:
    if recall_position > 20:
        return False
    quota_id = str(candidate.get("quota_id", "") or "").strip()
    name = str(candidate.get("name", "") or "").strip()
    if not quota_id or not name:
        return False

    signal_count = _rankable_contract_signal_count(candidate)
    has_fatal_conflict = any(
        bool(candidate.get(flag))
        for flag in (
            "family_gate_hard_conflict",
            "feature_alignment_hard_conflict",
            "logic_hard_conflict",
        )
    )
    has_scope_conflict = bool(candidate.get("candidate_scope_conflict"))
    if has_fatal_conflict or has_scope_conflict:
        return recall_position <= 12 and signal_count >= 4
    return signal_count >= 3


def _item_primary_text_blob(item: dict | None) -> str:
    parts: list[str] = []
    for key in ("bill_text", "description", "name", "bill_name"):
        _append_secondary_text_parts(parts, (item or {}).get(key))
    return " ".join(parts)


def _trusted_item_numeric_source(item: dict | None, key: str) -> bool:
    if key not in {"perimeter", "half_perimeter", "large_side"}:
        return True
    text = _item_primary_text_blob(item)
    has_dimension = bool(re.search(r"\d+(?:\.\d+)?\s*[xX\*\u00d7]\s*\d+(?:\.\d+)?", text))
    has_perimeter_label = "\u5468\u957f" in text
    has_height_label = any(token in text for token in ("\u8ddd\u5730", "\u5e95\u8ddd\u5730", "\u5b89\u88c5\u9ad8\u5ea6"))
    if has_dimension or has_perimeter_label:
        return True
    return not has_height_label


def _rankable_contract_item_signal_count(item: dict | None, candidate: dict) -> tuple[int, list[str], list[str]]:
    bill_features = _item_primary_features(item)
    candidate_features = _candidate_features(candidate)
    if not bill_features or not candidate_features:
        return 0, [], []

    edges: list[str] = []
    conflicts: list[str] = []
    for key in _PRIMARY_CATEGORICAL_FIELDS:
        expected = _primary_raw_value(bill_features, key)
        if expected in (None, "", []) or not _is_specific_primary_value(key, expected):
            continue
        actual = _primary_raw_value(candidate_features, key)
        if _primary_text_matches(expected, actual):
            edges.append(key)
        elif actual not in (None, "", []):
            conflicts.append(key)

    for key in _PRIMARY_NUMERIC_FIELDS:
        expected = _primary_raw_value(bill_features, key)
        if expected in (None, "", []) or not _trusted_item_numeric_source(item, key):
            continue
        actual = _primary_raw_value(candidate_features, key)
        rank = _primary_numeric_rank(expected, actual)
        if rank is None:
            continue
        if rank[0] == 0 and rank[1] <= 0.80:
            edges.append(key)
        elif rank[0] > 0 and rank[1] > 0.10:
            conflicts.append(key)

    edges = list(dict.fromkeys(edges))
    conflicts = list(dict.fromkeys(conflicts))
    score = len(edges)
    if any(edge in _DECISIVE_PRIMARY_NUMERIC_FIELDS for edge in edges):
        score += 1
    if any(edge in {"valve_type", "sanitary_flush_mode", "laying_method", "box_mount_mode"} for edge in edges):
        score += 1
    if len(conflicts) >= 2:
        score -= 1
    return max(0, score), edges, conflicts


def _retain_rankable_contract_candidates(
    candidates: list[dict],
    hard_param_fail_candidates: list[dict],
    *,
    item: dict | None = None,
    max_recovered: int = 4,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Recover bounded hard-fail candidates that have enough evidence to be ranked."""
    if not hard_param_fail_candidates:
        return candidates, hard_param_fail_candidates, []

    recovered: list[dict] = []
    still_rejected: list[dict] = []
    for fallback_rank, candidate in enumerate(list(hard_param_fail_candidates or []), start=1):
        recall_position = _candidate_recall_position(candidate, fallback_rank)
        item_signal_count, item_edges, item_conflicts = _rankable_contract_item_signal_count(item, candidate)
        candidate["_hard_param_resolution"] = _classify_hard_param_resolution(
            candidate,
            recall_position=recall_position,
            item_signal_count=item_signal_count,
            item_edges=item_edges,
            item_conflicts=item_conflicts,
        )
        if (
            len(recovered) < max_recovered
            and (
                _should_retain_hard_fail_for_rankable_contract(candidate, recall_position)
                or (recall_position <= 20 and item_signal_count >= 3)
            )
        ):
            retained = dict(candidate)
            retained["_rankable_pool_contract_protected"] = True
            retained["_rankable_pool_contract_reason"] = "hard_fail_demoted_to_rankable_with_penalty"
            retained["_rankable_pool_contract_recall_position"] = recall_position
            retained["_rankable_pool_contract_signal_count"] = _rankable_contract_signal_count(candidate)
            retained["_rankable_pool_contract_item_signal_count"] = item_signal_count
            retained["_rankable_pool_contract_item_edges"] = item_edges
            retained["_rankable_pool_contract_item_conflicts"] = item_conflicts
            retained["param_match"] = False
            retained["param_tier"] = max(_safe_int(retained.get("param_tier"), 0), 1)
            retained["param_validation_tier"] = "rankable_contract_protected"
            retained["param_hard_fail"] = False
            retained["_hard_param_resolution"] = _classify_hard_param_resolution(
                retained,
                recall_position=recall_position,
                item_signal_count=item_signal_count,
                item_edges=item_edges,
                item_conflicts=item_conflicts,
                protected=True,
            )
            recovered.append(retained)
        else:
            still_rejected.append(candidate)

    if not recovered:
        return candidates, hard_param_fail_candidates, []
    return list(candidates or []) + recovered, still_rejected, recovered



def _build_hard_param_fail_snapshots(
    candidates: list[dict],
    *,
    top_n: int = 20,
    include_diagnostics: bool = False,
) -> list[dict]:
    snapshots = []
    for candidate in list(candidates or [])[:top_n]:
        hard_param_resolution = _candidate_hard_param_resolution(candidate)
        snapshot = {
            "quota_id": str(candidate.get("quota_id", "") or ""),
            "name": str(candidate.get("name", "") or ""),
            "unit": str(candidate.get("unit", "") or ""),
            "reason_codes": _hard_param_fail_reason_codes(candidate),
            "hard_param_resolution": str(hard_param_resolution.get("class", "") or ""),
            "hard_param_resolution_reason_codes": list(hard_param_resolution.get("reason_codes") or []),
            "hard_param_resolution_conflict_codes": list(hard_param_resolution.get("conflict_codes") or []),
            "hard_param_resolution_evidence_fields": list(hard_param_resolution.get("evidence_fields") or []),
            "hard_param_resolution_recall_position": hard_param_resolution.get("recall_position"),
            "hard_param_resolution_signal_count": _safe_int(
                hard_param_resolution.get("signal_count"), default=0
            ),
            "hard_param_resolution_item_signal_count": _safe_int(
                hard_param_resolution.get("item_signal_count"), default=0
            ),
            "param_match": bool(candidate.get("param_match", True)),
            "param_hard_fail": bool(candidate.get("param_hard_fail")),
            "param_tier": _safe_int(candidate.get("param_tier", 1), default=1),
            "param_validation_tier": str(candidate.get("param_validation_tier", "") or ""),
            "param_detail": str(candidate.get("param_detail", "") or ""),
            "param_score": candidate.get("param_score"),
            "hybrid_score": candidate.get("hybrid_score"),
            "rerank_score": candidate.get("rerank_score"),
            "candidate_major_prefix": str(candidate.get("candidate_major_prefix", "") or ""),
            "target_db_type": str(candidate.get("target_db_type", "") or ""),
            "candidate_scope_match": candidate.get("candidate_scope_match"),
            "candidate_scope_conflict": candidate.get("candidate_scope_conflict"),
        }
        if include_diagnostics:
            canonical_features = dict(
                candidate.get("candidate_canonical_features")
                or candidate.get("canonical_features")
                or {}
            )
            present_fields = sorted(
                key for key, value in canonical_features.items()
                if value not in (None, "", [], {})
            )
            snapshot.update({
                "candidate_canonical_features": canonical_features,
                "canonical_feature_present_fields": present_fields,
                "candidate_feature_materialized": bool(candidate.get("candidate_feature_materialized") or canonical_features),
                "candidate_feature_source": str(candidate.get("candidate_feature_source", "") or ""),
                "candidate_feature_missing_fields": list(candidate.get("candidate_feature_missing_fields") or []),
                "manual_structured_score": candidate.get("manual_structured_score"),
                "logic_score": candidate.get("logic_score"),
                "feature_alignment_score": candidate.get("feature_alignment_score"),
                "rank_score": candidate.get("rank_score", compute_candidate_rank_score(candidate)),
                "rank_score_breakdown": explain_candidate_rank_score(candidate),
                "ltr_feature_snapshot": dict(candidate.get("ltr_feature_snapshot") or {}),
            })
        snapshots.append(snapshot)
    return snapshots


def _diagnostic_snapshot_limit(item: dict | None, key: str, default: int = 20) -> int:
    if not isinstance(item, dict):
        return default
    try:
        value = int(item.get(key, default) or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, 100))


def _diagnostic_snapshot_payload_enabled(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(item.get("_diagnostic_snapshot_payload_enabled"))


def _candidate_feature_trace(candidate: dict) -> dict:
    features = dict(
        (candidate or {}).get("candidate_canonical_features")
        or (candidate or {}).get("canonical_features")
        or {}
    )
    present_fields = sorted(
        key for key, value in features.items()
        if value not in (None, "", [], {})
    )
    return {
        "has_candidate_canonical_features": bool(features),
        "candidate_feature_materialized": bool((candidate or {}).get("candidate_feature_materialized") or features),
        "candidate_feature_source": str((candidate or {}).get("candidate_feature_source", "") or ""),
        "candidate_feature_present_fields": present_fields,
        "candidate_feature_missing_fields": list((candidate or {}).get("candidate_feature_missing_fields") or []),
        "candidate_canonical_feature_summary": {
            key: features.get(key)
            for key in (
                "family",
                "entity",
                "canonical_name",
                "material",
                "connection",
                "install_method",
                "system",
                "secondary_type",
                "valve_type",
                "lamp_type",
            )
            if features.get(key) not in (None, "", [], {})
        },
    }


def _build_rank_stage_reason(name: str,
                             *,
                             prev_top1_id: str,
                             top1_id: str,
                             overridden: bool,
                             reason: str) -> str:
    base_reason = str(reason or "").strip() or ("top1_changed" if overridden else "top1_unchanged")
    if overridden:
        return f"{name} override {prev_top1_id}->{top1_id}; reason={base_reason}"
    stable_top1_id = top1_id or prev_top1_id or ""
    return f"{name} keep {stable_top1_id}; reason={base_reason}"


def _queue_rank_stage_trace_step(ranking_meta: dict,
                                 *,
                                 name: str,
                                 top1_id: str,
                                 prev_top1_id: str,
                                 reason: str) -> None:
    if not isinstance(ranking_meta, dict):
        return
    steps = ranking_meta.get("rank_stage_trace_steps")
    if not isinstance(steps, list):
        steps = []
    top1_id = str(top1_id or "")
    prev_top1_id = str(prev_top1_id or "")
    overridden = bool(top1_id and prev_top1_id and top1_id != prev_top1_id)
    steps.append({
        "name": str(name or "").strip(),
        "top1_id": top1_id,
        "prev_top1_id": prev_top1_id,
        "overridden": overridden,
        "override_reason": _build_rank_stage_reason(
            str(name or "").strip(),
            prev_top1_id=prev_top1_id,
            top1_id=top1_id,
            overridden=overridden,
            reason=reason,
        ),
    })
    ranking_meta["rank_stage_trace_steps"] = steps


def _flush_rank_stage_trace_steps(result: dict) -> None:
    if not isinstance(result, dict):
        return
    pending_steps = result.pop("_pending_rank_stage_trace_steps", None)
    if not isinstance(pending_steps, list):
        return
    for step in pending_steps:
        if not isinstance(step, dict):
            continue
        _append_trace_step(
            result,
            "rank_stage",
            name=str(step.get("name", "") or ""),
            top1_id=str(step.get("top1_id", "") or ""),
            prev_top1_id=str(step.get("prev_top1_id", "") or ""),
            overridden=bool(step.get("overridden", False)),
            override_reason=str(step.get("override_reason", "") or ""),
        )


def _resolve_ltr_rank_stage_reason(ltr_meta: dict) -> str:
    family_group_ranker = dict((ltr_meta or {}).get("family_group_ranker") or {})
    if family_group_ranker.get("applied") and not family_group_ranker.get("advisory_only"):
        return "family_group_ranker:same_book_family_group_ranker"
    fallback_reason = str((ltr_meta or {}).get("fallback_reason") or "").strip()
    if fallback_reason:
        return f"ltr_fallback:{fallback_reason}"
    ltr_guard = dict((ltr_meta or {}).get("ltr_guard") or {})
    if str(ltr_guard.get("action") or "").strip() == "blocked":
        snapshot_guard = dict(ltr_guard.get("snapshot_guard") or {})
        guard_reason = str(snapshot_guard.get("reason") or ltr_guard.get("reason") or "blocked").strip()
        return f"ltr_guard_blocked:{guard_reason}"
    primary_stage = str((ltr_meta or {}).get("primary_stage") or "").strip()
    if primary_stage == "ltr":
        return "ltr_model_rerank"
    if primary_stage == "manual":
        return "manual_rank_retained"
    return primary_stage or "ltr_stage_completed"


def _resolve_cgr_rank_stage_reason(ltr_meta: dict) -> str:
    cgr_meta = dict((ltr_meta or {}).get("cgr") or {})
    override_reason = str(cgr_meta.get("override_reason") or cgr_meta.get("reason") or "").strip()
    if override_reason:
        return f"cgr:{override_reason}"
    if cgr_meta:
        return "cgr_ranked_without_override"
    fallback_reason = str((ltr_meta or {}).get("fallback_reason") or "").strip()
    return f"cgr_not_run:{fallback_reason}" if fallback_reason else "cgr_not_run"


def _resolve_unified_ranking_flags() -> dict:
    enabled = bool(getattr(config, "UNIFIED_RANKING_ENABLED", False))
    shadow_mode = bool(getattr(config, "UNIFIED_RANKING_SHADOW_MODE", False))
    if shadow_mode:
        mode = "shadow"
    elif enabled:
        mode = "enabled"
    else:
        mode = "disabled"
    return {
        "enabled": enabled,
        "shadow_mode": shadow_mode,
        "mode": mode,
    }


_UNIFIED_RANKING_PIPELINE = None


def _get_unified_ranking_pipeline():
    global _UNIFIED_RANKING_PIPELINE
    if _UNIFIED_RANKING_PIPELINE is None:
        from src.unified_ranking_pipeline import UnifiedRankingPipeline

        _UNIFIED_RANKING_PIPELINE = UnifiedRankingPipeline()
    return _UNIFIED_RANKING_PIPELINE


def _run_unified_ranking_shadow(item: dict, candidates: list[dict], *, top_k: int = 5) -> dict:
    pipeline = _get_unified_ranking_pipeline()
    return pipeline.rank_candidates(item, candidates, top_k=top_k)


def _build_unified_shadow_comparison(shadow_result: dict, ranking_meta: dict) -> dict:
    legacy_top1_id = str(ranking_meta.get("legacy_top1_id", "") or ranking_meta.get("selected_top1_id", "") or "")
    unified_top1_id = str(ranking_meta.get("unified_top1_id", "") or "")
    top1_score = float(shadow_result.get("top1_score", 0.0) or 0.0)
    legacy_candidate = None
    for candidate in list(shadow_result.get("candidates") or []):
        if str(candidate.get("quota_id", "") or "") == legacy_top1_id:
            legacy_candidate = candidate
            break

    legacy_score = None
    legacy_confidence = None
    score_gap = None
    if legacy_candidate:
        legacy_score = float(legacy_candidate.get("filtered_score", legacy_candidate.get("unified_score", 0.0)) or 0.0)
        legacy_confidence = float(legacy_candidate.get("confidence", 0.0) or 0.0)
        score_gap = top1_score - legacy_score

    return {
        "legacy_top1_id": legacy_top1_id,
        "unified_top1_id": unified_top1_id,
        "matches_legacy": bool(legacy_top1_id and unified_top1_id and legacy_top1_id == unified_top1_id),
        "legacy_candidate_present": legacy_candidate is not None,
        "legacy_top1_unified_score": legacy_score,
        "legacy_top1_unified_confidence": legacy_confidence,
        "score_gap": score_gap,
        "failure_reason": str(ranking_meta.get("unified_ranking_error", "") or ""),
    }


def _apply_unified_ranking_shadow(item: dict, candidates: list[dict], ranking_meta: dict) -> dict:
    if not candidates:
        return {}
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") == "disabled":
        return {}
    top_k = len(candidates)
    try:
        shadow_result = _run_unified_ranking_shadow(item, candidates, top_k=top_k)
    except Exception as exc:  # pragma: no cover
        ranking_meta["unified_ranking_error"] = str(exc)
        ranking_meta["unified_ranking_executed"] = False
        return {}

    top_candidate = (shadow_result.get("candidates") or [None])[0]
    unified_top1_id = str((top_candidate or {}).get("quota_id", "") or "")
    ranking_meta["unified_ranking_executed"] = True
    ranking_meta["unified_result_used"] = False
    ranking_meta["unified_top1_id"] = unified_top1_id
    ranking_meta["unified_top1_score"] = float(shadow_result.get("top1_score", 0.0) or 0.0)
    ranking_meta["unified_top1_confidence"] = float(shadow_result.get("top1_confidence", 0.0) or 0.0)
    comparison = _build_unified_shadow_comparison(shadow_result, ranking_meta)
    ranking_meta["unified_top1_matches_selected"] = bool(
        unified_top1_id and unified_top1_id == str(ranking_meta.get("selected_top1_id", "") or "")
    )
    ranking_meta["unified_top1_matches_legacy"] = bool(comparison.get("matches_legacy"))
    ranking_meta["legacy_top1_unified_score"] = comparison.get("legacy_top1_unified_score")
    ranking_meta["legacy_top1_unified_confidence"] = comparison.get("legacy_top1_unified_confidence")
    ranking_meta["unified_legacy_score_gap"] = comparison.get("score_gap")
    ranking_meta["unified_ranking_diagnostics"] = dict(shadow_result.get("diagnostics") or {})
    ranking_meta["unified_ranking_error"] = ""
    return shadow_result


def _merge_unified_candidate(base_candidate: dict | None, unified_candidate: dict | None) -> dict | None:
    if not isinstance(unified_candidate, dict):
        return dict(base_candidate) if isinstance(base_candidate, dict) else None
    merged = dict(base_candidate or {})
    merged.update(dict(unified_candidate))
    return merged


def _apply_unified_candidate_order(base_candidates: list[dict], unified_candidates: list[dict]) -> list[dict]:
    base_by_quota_id = {
        str(candidate.get("quota_id", "") or "").strip(): candidate
        for candidate in (base_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    ordered: list[dict] = []
    seen: set[str] = set()
    for unified_candidate in unified_candidates or []:
        quota_id = str(unified_candidate.get("quota_id", "") or "").strip()
        if not quota_id or quota_id in seen:
            continue
        ordered_candidate = _merge_unified_candidate(base_by_quota_id.get(quota_id), unified_candidate)
        if ordered_candidate:
            ordered.append(ordered_candidate)
            seen.add(quota_id)
    for candidate in base_candidates or []:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id and quota_id in seen:
            continue
        ordered.append(dict(candidate))
    return ordered


def _collect_unified_candidates_by_id(
    base_candidates: list[dict],
    unified_candidates: list[dict],
    allowed_quota_ids: set[str],
) -> list[dict]:
    base_by_quota_id = {
        str(candidate.get("quota_id", "") or "").strip(): candidate
        for candidate in (base_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    ordered: list[dict] = []
    seen: set[str] = set()
    for unified_candidate in unified_candidates or []:
        quota_id = str(unified_candidate.get("quota_id", "") or "").strip()
        if (
            not quota_id
            or quota_id in seen
            or quota_id not in allowed_quota_ids
        ):
            continue
        ordered_candidate = _merge_unified_candidate(base_by_quota_id.get(quota_id), unified_candidate)
        if ordered_candidate:
            ordered.append(ordered_candidate)
            seen.add(quota_id)
    return ordered


def _format_unified_selection_explanation(unified_result: dict, candidate: dict | None) -> str:
    top_driver = str(((candidate or {}).get("explanation") or {}).get("top_driver") or "")
    score = float(
        (candidate or {}).get("filtered_score", (candidate or {}).get("unified_score", unified_result.get("top1_score", 0.0))) or 0.0
    )
    if top_driver:
        return f"unified_ranking: top_driver={top_driver}; filtered_score={score:.3f}"
    return f"unified_ranking: filtered_score={score:.3f}"


def _apply_unified_enabled_selection(item: dict,
                                     valid_candidates: list[dict],
                                     matched_candidates: list[dict],
                                     ranking_meta: dict,
                                     arbitration: dict,
                                     unified_result: dict,
                                     best: dict | None,
                                     confidence: float,
                                     explanation: str,
                                     reasoning_decision: dict) -> tuple[list[dict], list[dict], dict | None, float, str, dict]:
    if str(ranking_meta.get("unified_ranking_mode") or "disabled") != "enabled":
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    unified_candidates = list((unified_result or {}).get("candidates") or [])
    if not unified_candidates:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    unified_valid_candidates = _apply_unified_candidate_order(valid_candidates, unified_candidates)
    reordered_matched_candidates = list(matched_candidates or [])
    if matched_candidates:
        # Unified primary can reprioritize matched candidates, but it must not
        # reintroduce param-mismatched candidates as the final selected top1
        # when the legacy pipeline already found safe structured matches.
        matched_ids = {
            str(candidate.get("quota_id", "") or "").strip()
            for candidate in matched_candidates
            if str(candidate.get("quota_id", "") or "").strip()
        }
        reordered_matched_candidates = _collect_unified_candidates_by_id(
            matched_candidates,
            unified_candidates,
            matched_ids,
        )
        if reordered_matched_candidates:
            reordered_valid_candidates = list(reordered_matched_candidates)
            reordered_valid_candidates.extend(
                candidate
                for candidate in unified_valid_candidates
                if str(candidate.get("quota_id", "") or "").strip() not in matched_ids
            )
            decision_candidates = reordered_matched_candidates
        else:
            reordered_valid_candidates = unified_valid_candidates
            decision_candidates = reordered_valid_candidates
    else:
        reordered_valid_candidates = unified_valid_candidates
        decision_candidates = reordered_valid_candidates

    unified_best = decision_candidates[0] if decision_candidates else None
    if not unified_best:
        return valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision

    ranking_meta["unified_result_used"] = True
    ranking_meta["final_changed_by"] = "unified_ranking"
    ranking_meta["selected_top1_id"] = str(unified_best.get("quota_id", "") or "")
    ranking_meta["unified_top1_matches_selected"] = bool(
        ranking_meta["selected_top1_id"]
        and ranking_meta["selected_top1_id"] == str(ranking_meta.get("unified_top1_id", "") or "")
    )

    selected_confidence = float(
        unified_best.get("confidence", (unified_result or {}).get("top1_confidence", confidence)) or confidence
    )
    selected_explanation = _format_unified_selection_explanation(unified_result, unified_best)
    selected_reasoning = analyze_ambiguity(
        unified_valid_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return (
        reordered_valid_candidates,
        reordered_matched_candidates,
        unified_best,
        selected_confidence,
        selected_explanation,
        selected_reasoning,
    )

def _build_parser_trace_diagnostics(item: dict) -> dict:
    canonical_query = item.get("canonical_query") or {}
    primary_query_profile = dict(canonical_query.get("primary_query_profile") or {})
    return {
        "search_query": str(canonical_query.get("search_query") or item.get("search_query") or item.get("name") or ""),
        "validation_query": str(canonical_query.get("validation_query") or ""),
        "route_query": str(canonical_query.get("normalized_query") or ""),
        "primary_subject": str(primary_query_profile.get("primary_subject") or ""),
        "decisive_terms": list(primary_query_profile.get("decisive_terms") or []),
        "quota_aliases": list(primary_query_profile.get("quota_aliases") or []),
        "noise_marker": str(primary_query_profile.get("noise_marker") or ""),
        "query_route": dict(item.get("query_route") or {}),
    }


def _build_router_trace_diagnostics(item: dict) -> dict:
    classification = dict(item.get("classification") or {})
    search_books = [
        str(book).strip()
        for book in list(classification.get("search_books") or [])
        if str(book).strip()
    ]
    hard_search_books = [
        str(book).strip()
        for book in list(
            classification.get("hard_search_books")
            or classification.get("hard_book_constraints")
            or []
        )
        if str(book).strip()
    ]
    advisory_search_books = [
        book for book in search_books
        if book not in hard_search_books
    ]
    unified_plan = dict(item.get("unified_plan") or {})
    plugin_hints = dict(item.get("plugin_hints") or {})
    classification_reason = str(classification.get("reason") or "").strip()
    if classification_reason.startswith("unified_plan"):
        effective_owner = "unified_plan"
    elif classification_reason in {"item_specialty", "soft_item_specialty"}:
        effective_owner = "seeded_specialty"
    elif classification.get("primary"):
        effective_owner = "specialty_classifier"
    else:
        effective_owner = "open_search"

    advisory_owner = ""
    if unified_plan and (
        unified_plan.get("preferred_books")
        or unified_plan.get("hard_books")
        or unified_plan.get("search_aliases")
    ):
        advisory_owner = "unified_plan"
    elif plugin_hints and (
        plugin_hints.get("preferred_books")
        or plugin_hints.get("preferred_specialties")
        or plugin_hints.get("synonym_aliases")
    ):
        advisory_owner = "province_plugin"
    elif item.get("specialty"):
        advisory_owner = "seeded_specialty"

    return {
        "query_route": dict(item.get("query_route") or {}),
        "plugin_hints": plugin_hints,
        "unified_plan": unified_plan,
        "advisory_owner": advisory_owner,
        "effective_owner": effective_owner,
        "effective_reason": classification_reason,
        "classification": {
            "primary": str(classification.get("primary") or ""),
            "fallbacks": list(classification.get("fallbacks") or []),
            "candidate_books": list(classification.get("candidate_books") or []),
            "search_books": search_books,
            "hard_book_constraints": list(classification.get("hard_book_constraints") or []),
            "hard_search_books": hard_search_books,
            "advisory_search_books": advisory_search_books,
            "route_mode": str(classification.get("route_mode") or ""),
        },
    }


def _build_retriever_trace_diagnostics(item: dict,
                                       valid_candidates: list[dict],
                                       matched_candidates: list[dict],
                                       router_diagnostics: dict | None = None) -> dict:
    classification = dict(item.get("classification") or {})
    resolution = dict(classification.get("retrieval_resolution") or {})
    calls = list(resolution.get("calls") or [])
    main_calls = [call for call in calls if str(call.get("target") or "").strip() == "main"]
    escape_used = any(str(call.get("stage") or "").strip() == "escape" for call in main_calls)
    open_used = any(
        str(call.get("stage") or "").strip() in {"escape", "open"}
        for call in main_calls
    )
    resolved_main_books = []
    for call in main_calls:
        resolved_books = [
            str(book).strip()
            for book in list(call.get("resolved_books") or [])
            if str(book).strip()
        ]
        if resolved_books:
            resolved_main_books = resolved_books
            break
    router_effective_owner = str((router_diagnostics or {}).get("effective_owner") or "")
    scope_owner = "retriever_main_escape" if escape_used else (router_effective_owner or "router")
    return {
        "candidate_count": len(valid_candidates or []),
        "matched_candidate_count": len(matched_candidates or []),
        "candidate_ids": [
            str(candidate.get("quota_id", "") or "").strip()
            for candidate in (valid_candidates or [])
            if str(candidate.get("quota_id", "") or "").strip()
        ],
        "authority_hit": any(has_exact_experience_anchor(candidate) for candidate in (valid_candidates or [])),
        "kb_hit": any(has_exact_universal_kb_anchor(candidate) for candidate in (valid_candidates or [])),
        "scope_owner": scope_owner,
        "escape_owner": "retriever_main_escape" if escape_used else "",
        "used_open_search": open_used,
        "resolved_main_books": resolved_main_books,
        "route_scope_filter": dict(classification.get("route_scope_filter") or {}),
        "candidate_scope_guard": dict(classification.get("candidate_scope_guard") or {}),
        "search_resolution": resolution,
    }


def _build_ranker_trace_diagnostics(candidates: list[dict], best: dict | None, ranking_meta: dict, arbitration: dict) -> dict:
    ordered = list(candidates or [])
    selected = best or (ordered[0] if ordered else None)
    second = ordered[1] if len(ordered) > 1 else None
    selected_score = compute_candidate_rank_score(selected) if selected else 0.0
    second_score = compute_candidate_rank_score(second) if second else 0.0

    timeline = [
        {"stage": "pre_ltr_seed", "quota_id": str(ranking_meta.get("pre_ltr_top1_id", "") or "")},
        {"stage": "ltr", "quota_id": str(ranking_meta.get("post_ltr_top1_id", "") or "")},
        {
            "stage": "post_ltr_structural_ranker",
            "quota_id": str(
                ranking_meta.get("post_ltr_structural_top1_id")
                or ranking_meta.get("post_ltr_top1_id", "")
                or ""
            ),
        },
        {"stage": "cgr_ranker", "quota_id": str(ranking_meta.get("post_cgr_top1_id", "") or "")},
        {"stage": "candidate_arbiter", "quota_id": str(ranking_meta.get("post_arbiter_top1_id", "") or "")},
        {"stage": "explicit_override", "quota_id": str(ranking_meta.get("post_explicit_top1_id", "") or "")},
        {"stage": "experience_anchor", "quota_id": str(ranking_meta.get("post_anchor_top1_id", "") or "")},
        {
            "stage": "unified_ranking",
            "quota_id": str(ranking_meta.get("unified_top1_id", "") or "") if ranking_meta.get("unified_result_used") else "",
        },
        {"stage": "selected", "quota_id": str(ranking_meta.get("selected_top1_id", "") or "")},
    ]

    rank_timeline_changes = []
    prev_quota_id = ""
    decision_owner = "pre_ltr_seed"
    for entry in timeline:
        quota_id = str(entry.get("quota_id", "") or "")
        if not quota_id:
            continue
        if not prev_quota_id:
            prev_quota_id = quota_id
            continue
        if quota_id != prev_quota_id:
            rank_timeline_changes.append({
                "stage": entry["stage"],
                "from_quota_id": prev_quota_id,
                "to_quota_id": quota_id,
            })
            decision_owner = entry["stage"]
            prev_quota_id = quota_id

    if decision_owner == "selected":
        decision_owner = rank_timeline_changes[-1]["stage"] if rank_timeline_changes else "pre_ltr_seed"

    return {
        "selected_quota": str((selected or {}).get("quota_id", "") or ""),
        "selected_rank_score": selected_score,
        "second_rank_score": second_score,
        "score_gap": max(selected_score - second_score, 0.0),
        "selected_rank_breakdown": explain_candidate_rank_score(selected or {}),
        "second_rank_breakdown": explain_candidate_rank_score(second or {}) if second else {"rank_score": 0.0, "stage_priority": {}},
        "top_candidates": _build_ranked_candidate_snapshots(ordered, top_n=3),
        "decision_owner": decision_owner,
        "top1_flip_count": len(rank_timeline_changes),
        "rank_timeline": timeline,
        "rank_timeline_changes": rank_timeline_changes,
        "decision_advisories": list(ranking_meta.get("decision_advisories") or []),
        "arbitration": dict(arbitration or {}),
        "unified_ranking": {
            "enabled": bool(ranking_meta.get("unified_ranking_enabled")),
            "shadow_mode": bool(ranking_meta.get("unified_ranking_shadow_mode")),
            "mode": str(ranking_meta.get("unified_ranking_mode") or "disabled"),
            "executed": bool(ranking_meta.get("unified_ranking_executed")),
            "legacy_selected_quota": str(ranking_meta.get("legacy_top1_id", "") or ""),
            "selected_quota": str(ranking_meta.get("unified_top1_id", "") or ""),
            "score": float(ranking_meta.get("unified_top1_score", 0.0) or 0.0),
            "confidence": float(ranking_meta.get("unified_top1_confidence", 0.0) or 0.0),
            "matches_selected": bool(ranking_meta.get("unified_top1_matches_selected")),
            "matches_legacy": bool(ranking_meta.get("unified_top1_matches_legacy")),
            "legacy_score": ranking_meta.get("legacy_top1_unified_score"),
            "legacy_confidence": ranking_meta.get("legacy_top1_unified_confidence"),
            "score_gap_vs_legacy": ranking_meta.get("unified_legacy_score_gap"),
            "result_used": bool(ranking_meta.get("unified_result_used")),
            "diagnostics": dict(ranking_meta.get("unified_ranking_diagnostics") or {}),
            "error": str(ranking_meta.get("unified_ranking_error", "") or ""),
        },
    }


def _infer_lifecycle_source(candidate: dict) -> str:
    source_province = str(candidate.get("_source_province", "") or "").strip()
    cascade_stage = str(candidate.get("_cascade_stage", "") or "").strip()
    match_source = str(candidate.get("match_source", "") or "").strip()
    prior_sources = list(candidate.get("knowledge_prior_sources") or [])
    if has_exact_experience_anchor(candidate):
        return "experience"
    if source_province:
        return "aux"
    if prior_sources or match_source in {
        "existing_candidate_neighbor",
        "knowledge_prior",
        "universal_kb",
    }:
        return "prior"
    if cascade_stage == "escape":
        return "escape"
    return "main"


def _is_lifecycle_hard_param_fail(candidate: dict, hard_fail_ids: set[str]) -> bool:
    quota_id = str(candidate.get("quota_id", "") or "").strip()
    if quota_id and quota_id in hard_fail_ids:
        return True
    if bool(candidate.get("param_hard_fail", False)):
        return True
    if str(candidate.get("param_validation_tier", "") or "").strip() == "hard_fail":
        return True
    return _safe_int(candidate.get("param_tier", 1), default=1) == 0


def _build_candidate_lifecycle_trace(raw_candidates: list[dict],
                                     valid_candidates: list[dict],
                                     matched_candidates: list[dict],
                                     best: dict | None,
                                     ranking_meta: dict,
                                     ranker_diagnostics: dict,
                                     *,
                                     top_n: int = 20,
                                     include_feature_trace: bool = False) -> list[dict]:
    valid_ids = {
        str(candidate.get("quota_id", "") or "").strip()
        for candidate in list(valid_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    matched_ids = {
        str(candidate.get("quota_id", "") or "").strip()
        for candidate in list(matched_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    protected_candidates = {
        str(candidate.get("quota_id", "") or "").strip(): candidate
        for candidate in list(valid_candidates or [])
        if str(candidate.get("quota_id", "") or "").strip()
        and bool(candidate.get("_rankable_pool_contract_protected"))
    }
    hard_fail_ids = {
        str(candidate.get("quota_id", "") or "").strip()
        for candidate in list(ranking_meta.get("hard_param_fail_rejected_candidates") or [])
        if str(candidate.get("quota_id", "") or "").strip()
    }
    selected_id = str((best or {}).get("quota_id", "") or "").strip()
    valid_rank = {
        str(candidate.get("quota_id", "") or "").strip(): index
        for index, candidate in enumerate(list(valid_candidates or []), start=1)
        if str(candidate.get("quota_id", "") or "").strip()
    }
    decision_events: dict[str, list[dict]] = {}
    for change in list((ranker_diagnostics or {}).get("rank_timeline_changes") or []):
        stage = str(change.get("stage", "") or "").strip()
        from_id = str(change.get("from_quota_id", "") or "").strip()
        to_id = str(change.get("to_quota_id", "") or "").strip()
        if from_id:
            decision_events.setdefault(from_id, []).append({
                "stage": stage,
                "event": "demoted_from_top1",
                "to_quota_id": to_id,
            })
        if to_id:
            decision_events.setdefault(to_id, []).append({
                "stage": stage,
                "event": "promoted_to_top1",
                "from_quota_id": from_id,
            })

    snapshots: list[dict] = []
    seen_ids: set[str] = set()
    for candidate in list(raw_candidates or []):
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if not quota_id or quota_id in seen_ids:
            continue
        seen_ids.add(quota_id)
        if len(snapshots) >= top_n:
            break

        stages_seen = [
            str(stage or "").strip()
            for stage in list(candidate.get("_cascade_stages") or [])
            if str(stage or "").strip()
        ]
        first_seen_stage = str(candidate.get("_cascade_stage", "") or "").strip()
        if not first_seen_stage and stages_seen:
            first_seen_stage = stages_seen[0]
        if not first_seen_stage:
            first_seen_stage = str(candidate.get("match_source", "") or "unknown").strip()

        protected_candidate = protected_candidates.get(quota_id)
        if protected_candidate:
            filter_state = "rankable_contract_protected"
            final_state = "selected" if quota_id == selected_id else "retained_lost"
            lost_reason = "" if quota_id == selected_id else "ranked_below_selected"
        elif _is_lifecycle_hard_param_fail(candidate, hard_fail_ids):
            filter_state = "filtered_hard_param_fail"
            final_state = "filtered"
            lost_reason = "hard_param_fail"
        elif quota_id not in valid_ids:
            filter_state = "filtered_or_gated"
            final_state = "dropped_before_ranking"
            lost_reason = "not_in_valid_candidates"
        elif quota_id == selected_id:
            filter_state = "param_matched" if quota_id in matched_ids else "rankable_unmatched_param"
            final_state = "selected"
            lost_reason = ""
        else:
            filter_state = "param_matched" if quota_id in matched_ids else "rankable_unmatched_param"
            final_state = "retained_lost"
            lost_reason = "ranked_below_selected"

        resolution_candidate = protected_candidate or candidate
        hard_param_resolution = (
            _candidate_hard_param_resolution(resolution_candidate)
            if filter_state in {"filtered_hard_param_fail", "rankable_contract_protected"}
            else {}
        )
        snapshot = {
            "quota_id": quota_id,
            "name": str(candidate.get("name", "") or ""),
            "source": _infer_lifecycle_source(candidate),
            "source_province": str(candidate.get("_source_province", "") or ""),
            "first_seen_stage": first_seen_stage,
            "stages_seen": stages_seen,
            "retained_for_ranking": quota_id in valid_ids,
            "filter_state": filter_state,
            "final_state": final_state,
            "lost_reason": lost_reason,
            "selected": quota_id == selected_id,
            "rank_position": valid_rank.get(quota_id),
            "rank_stage": str(candidate.get("rank_stage", "") or ""),
            "rank_score_source": str(candidate.get("_rank_score_source", "") or ""),
            "param_match": bool(candidate.get("param_match", True)),
            "param_tier": _safe_int(candidate.get("param_tier", 1), default=1),
            "param_validation_tier": str(candidate.get("param_validation_tier", "") or ""),
            "param_hard_fail": bool(candidate.get("param_hard_fail", False)),
            "hard_param_resolution": str(hard_param_resolution.get("class", "") or ""),
            "hard_param_resolution_reason_codes": list(hard_param_resolution.get("reason_codes") or []),
            "hard_param_resolution_conflict_codes": list(hard_param_resolution.get("conflict_codes") or []),
            "hard_param_resolution_evidence_fields": list(hard_param_resolution.get("evidence_fields") or []),
            "hard_param_resolution_recall_position": hard_param_resolution.get("recall_position"),
            "hard_param_resolution_signal_count": _safe_int(
                hard_param_resolution.get("signal_count"), default=0
            ),
            "hard_param_resolution_item_signal_count": _safe_int(
                hard_param_resolution.get("item_signal_count"), default=0
            ),
            "rankable_pool_contract_protected": bool(protected_candidate),
            "rankable_pool_contract_reason": str(
                resolution_candidate.get("_rankable_pool_contract_reason", "") or ""
            ),
            "rankable_pool_contract_signal_count": _safe_int(
                resolution_candidate.get("_rankable_pool_contract_signal_count"), default=0
            ),
            "decision_events": decision_events.get(quota_id, []),
        }
        if include_feature_trace:
            snapshot.update(_candidate_feature_trace(resolution_candidate))
        snapshots.append(snapshot)
    return snapshots


def _extract_recall_topk_ids(candidates: list[dict] | None) -> list[str]:
    recall_topk_ids: list[str] = []
    for candidate in list(candidates or []):
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        if quota_id:
            recall_topk_ids.append(quota_id)
    return recall_topk_ids


def _book_code_aliases_for_search_item(item: dict) -> set[str]:
    aliases: set[str] = set()
    classification = item.get("classification") if isinstance(item, dict) else {}
    for value in (
        item.get("specialty") if isinstance(item, dict) else "",
        item.get("book") if isinstance(item, dict) else "",
        *((classification or {}).get("search_books") or []),
    ):
        text = str(value or "").strip().upper()
        if not text:
            continue
        aliases.add(text)
        match = re.match(r"^C0*(\d+)$", text)
        if match:
            aliases.add(match.group(1))
        elif re.match(r"^\d+$", text):
            aliases.add(f"C{int(text)}")
    return aliases


def _merge_existing_candidate_neighbors_for_search_mode(
    item: dict,
    candidates: list[dict],
    *,
    materialize_quota_candidate=None,
    top_k: int = 8,
) -> list[dict]:
    if not candidates:
        return []
    allowed_books = _book_code_aliases_for_search_item(item or {})
    if not allowed_books:
        return list(candidates or [])

    existing_ids = {
        str(candidate.get("quota_id", "") or "").strip()
        for candidate in candidates
        if str(candidate.get("quota_id", "") or "").strip()
    }
    neighbors: list[dict] = []
    materializer = materialize_quota_candidate
    for candidate in candidates[:20]:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        match = re.match(r"^(.+-)(\d+)$", quota_id)
        if not match:
            continue
        prefix = match.group(1)
        number = int(match.group(2))
        prefix_book = prefix.rstrip("-").split("-")[0].upper()
        if prefix_book and prefix_book not in allowed_books:
            continue
        for offset in (-2, -1, 1, 2):
            neighbor_id = f"{prefix}{number + offset}"
            if neighbor_id in existing_ids:
                continue

            materialized = None
            if callable(materializer):
                materialized = materializer(neighbor_id)
            else:
                province = str((item or {}).get("province", "") or "").strip() or None
                row = search_by_id(neighbor_id, province=province)
                if row:
                    materialized = {"quota_id": row[0], "name": row[1], "unit": row[2]}
            if isinstance(materialized, (tuple, list)) and len(materialized) >= 3:
                materialized = {
                    "quota_id": materialized[0],
                    "name": materialized[1],
                    "unit": materialized[2],
                }
            if not isinstance(materialized, dict):
                continue
            if str(materialized.get("quota_id", "") or "").strip() != neighbor_id:
                continue
            if not str(materialized.get("name", "") or "").strip():
                continue

            neighbor = dict(materialized)
            neighbor.update({
                "quota_id": neighbor_id,
                "name": str(materialized.get("name", "") or "").strip(),
                "unit": str(materialized.get("unit", "") or "").strip(),
                "match_source": "existing_candidate_neighbor",
                "candidate_neighbor_seed": quota_id,
                "knowledge_prior_sources": ["candidate_neighbor"],
                "knowledge_prior_score": 0.50 - min(abs(offset), 2) * 0.03,
                "hybrid_score": float(candidate.get("hybrid_score", 0.0) or 0.0) * 0.85,
                "rerank_score": float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)) or 0.0) * 0.85,
            })
            neighbors.append(neighbor)
            existing_ids.add(neighbor_id)
            if len(neighbors) >= top_k:
                return list(candidates or []) + neighbors
    return list(candidates or []) + neighbors


def _run_rank_pipeline(item: dict,
                       decision_candidates: list[dict],
                       *,
                       reservoir: list[dict],
                       allow_arbiter: bool,
                       allow_explicit: bool) -> tuple[list[dict], dict, dict, dict, dict | None]:
    ordered = list(decision_candidates or [])
    ranking_meta = _init_ranking_meta()
    ranking_meta["candidate_count"] = len(reservoir or [])
    unified_ranking_flags = _resolve_unified_ranking_flags()
    ranking_meta["unified_ranking_enabled"] = unified_ranking_flags["enabled"]
    ranking_meta["unified_ranking_shadow_mode"] = unified_ranking_flags["shadow_mode"]
    ranking_meta["unified_ranking_mode"] = unified_ranking_flags["mode"]
    arbitration: dict = {}
    explicit_override: dict = {}

    if not ordered:
        return ordered, ranking_meta, arbitration, explicit_override, None

    def _record_decision_advisory(
        *,
        stage: str,
        from_top1_id: str,
        suggested_top1_id: str,
        reason: str,
        score_margin: float | None = None,
        details: dict | None = None,
    ) -> dict:
        advisory = {
            "stage": stage,
            "from_top1_id": str(from_top1_id or ""),
            "suggested_top1_id": str(suggested_top1_id or ""),
            "reason": str(reason or ""),
            "decision_owner": "final_decider",
            "advisory_only": True,
            "accepted_by_final_decider": False,
            "selected_quota_id": "",
        }
        if score_margin is not None:
            advisory["score_margin"] = score_margin
        if details:
            advisory["details"] = dict(details)
        ranking_meta.setdefault("decision_advisories", []).append(advisory)
        return advisory

    def _record_post_ltr_structural_advisory(
        *,
        source_stage: str,
        from_top1_id: str,
        suggested_top1_id: str,
        reason: str,
        score_margin: float | None = None,
        details: dict | None = None,
    ) -> dict:
        structural_details = dict(details or {})
        structural_details["source_stage"] = str(source_stage or "")
        structural_details["contract"] = "post_ltr_structural_advisory"
        advisory = _record_decision_advisory(
            stage="post_ltr_structural_ranker",
            from_top1_id=from_top1_id,
            suggested_top1_id=suggested_top1_id,
            reason=str(reason or source_stage or "post_ltr_structural_ranker"),
            score_margin=score_margin,
            details=structural_details,
        )
        advisory["source_stage"] = str(source_stage or "")
        advisory["advisory_contract"] = "post_ltr_structural_advisory"
        return advisory

    def _find_ordered_candidate(current_ordered: list[dict], quota_id: str) -> dict | None:
        target_id = str(quota_id or "").strip()
        if not target_id:
            return None
        for candidate in current_ordered or []:
            if str(candidate.get("quota_id", "") or "").strip() == target_id:
                return candidate
        return None

    def _mark_advisory_rejected(advisory: dict, reason: str) -> None:
        advisory["accepted_by_final_decider"] = False
        advisory["rejected_by_final_decider"] = True
        advisory["final_decider_reason"] = reason

    def _advisory_has_structural_evidence(advisory: dict) -> bool:
        details = dict(advisory.get("details") or {})
        structural = dict(details.get("structural_ranking") or {})
        groups = dict(structural.get("evidence_groups") or {})
        return any(
            str(group or "") != "score" and bool(edges)
            for group, edges in groups.items()
        )

    def _family_advisory_evidence_groups(advisory: dict) -> set[str]:
        details = dict(advisory.get("details") or {})
        structural = dict(details.get("structural_ranking") or {})
        groups = dict(structural.get("evidence_groups") or {})
        return {
            str(group or "")
            for group, edges in groups.items()
            if str(group or "") and bool(edges)
        }

    def _family_advisory_pool_scan(advisory: dict) -> dict:
        details = dict(advisory.get("details") or {})
        scan = details.get("pool_scan") or {}
        return dict(scan) if isinstance(scan, dict) else {}

    def _family_advisory_margin(advisory: dict) -> float:
        details = dict(advisory.get("details") or {})
        return _safe_float(details.get("margin", advisory.get("score_margin")), 0.0)

    def _weak_family_group_advisory_rejection(
        advisory: dict,
        incumbent: dict,
        challenger: dict,
    ) -> str:
        groups = _family_advisory_evidence_groups(advisory)
        structural_groups = {group for group in groups if group != "score"}
        details = dict(advisory.get("details") or {})
        structural = dict(details.get("structural_ranking") or {})
        conflict_groups = dict(structural.get("conflict_groups") or {})
        contract = dict(details.get("rankable_candidate_contract") or {})
        selected_contract = dict(contract.get("selected") or {})
        selected_flags = dict(selected_contract.get("hard_param_flags") or {})
        scan = _family_advisory_pool_scan(advisory)
        mode = str(scan.get("mode") or "")
        selected_rank = _safe_int(scan.get("selected_original_rank"), 99)
        margin = _family_advisory_margin(advisory)
        same_family = _same_family_group(incumbent, challenger)

        if (
            selected_flags.get("param_match") is False
            and conflict_groups.get("connection")
            and not bool(structural_groups & {"numeric_bin", "secondary_type"})
        ):
            return "protected_soft_param_connection_conflict_rejected"

        if not structural_groups:
            # A3-R8: score-only advisories were wrong 3/4 times. Keep only the
            # narrow historical case where a very near same-family candidate
            # has a real margin; otherwise it may not change the final answer.
            if mode == "same_book_same_family_top20" and selected_rank <= 2 and margin >= 0.070:
                return ""
            return "score_only_family_group_advisory_rejected"

        prefix_only = mode == "same_book_same_prefix_tier_top20" and not same_family
        weak_prefix_groups = (
            structural_groups <= {"numeric_bin"}
            or structural_groups <= {"type"}
            or structural_groups <= {"material"}
            or structural_groups <= {"connection"}
            or structural_groups <= {"install_method"}
        )
        if prefix_only and weak_prefix_groups:
            return "prefix_only_weak_family_group_advisory_rejected"

        material_like = bool(structural_groups & {"material", "connection", "install_method"})
        strong_type_support = bool(structural_groups & {"secondary_type", "type", "numeric_bin"})
        if (
            material_like
            and not same_family
            and mode != "same_book_bill_guided_family_top20"
            and not strong_type_support
            and len(structural_groups & {"material", "connection", "install_method"}) < 2
        ):
            return "material_connection_without_object_match_rejected"

        if structural_groups <= {"material", "connection", "install_method"} and selected_rank > 6:
            return "late_weak_material_connection_family_advisory_rejected"

        if structural_groups == {"numeric_bin"} and selected_rank > 6:
            return "late_numeric_only_family_advisory_rejected"

        return ""

    def _advisory_blocked_by_strong_incumbent(
        current_ordered: list[dict],
        advisory: dict,
        challenger: dict,
        *,
        stage: str,
    ) -> tuple[bool, str]:
        incumbent_id = str(advisory.get("from_top1_id") or "").strip()
        incumbent = _find_ordered_candidate(current_ordered, incumbent_id)
        if incumbent is None:
            incumbent = current_ordered[0] if current_ordered else None
        if not incumbent:
            return False, ""
        incumbent_quota_id = str(incumbent.get("quota_id", "") or "").strip()
        challenger_quota_id = str(challenger.get("quota_id", "") or "").strip()
        if not incumbent_quota_id or incumbent_quota_id == challenger_quota_id:
            return False, ""

        incumbent_rank = _candidate_lifecycle_score(incumbent)
        challenger_rank = _candidate_lifecycle_score(challenger)
        incumbent_ltr = _candidate_float(incumbent, "ltr_score")
        challenger_ltr = _candidate_float(challenger, "ltr_score")

        if stage in {"family_group_ranker", "post_ltr_structural_ranker"}:
            weak_rejection = _weak_family_group_advisory_rejection(advisory, incumbent, challenger)
            if weak_rejection:
                return True, weak_rejection
            if _advisory_has_structural_evidence(advisory):
                return False, ""
            if incumbent_ltr > challenger_ltr + 0.75 and incumbent_rank > challenger_rank + 0.35:
                return True, "score_only_family_group_advisory_over_strong_ltr_rejected"
            return False, ""

        if stage in {"ltr_lifecycle_guard", "category_safe_lifecycle_guard"}:
            return False, ""

        if _has_structural_lifecycle_advantage(incumbent, challenger):
            return True, "incumbent_structural_advantage"

        if stage == "category_safe" and not _has_structural_lifecycle_advantage(challenger, incumbent):
            if incumbent_ltr > challenger_ltr + 0.45 and incumbent_rank > challenger_rank + 0.20:
                return True, "incumbent_post_ltr_contract_over_low_evidence_advisory"

        incumbent_rerank = _candidate_rerank_evidence(incumbent)
        challenger_rerank = _candidate_rerank_evidence(challenger)
        incumbent_ltr = _candidate_float(incumbent, "ltr_score")
        challenger_ltr = _candidate_float(challenger, "ltr_score")

        if (
            stage == "post_cgr"
            and incumbent_ltr > challenger_ltr + 0.60
            and incumbent_rerank >= challenger_rerank - 0.03
        ):
            return True, "incumbent_ltr_and_rerank_over_low_confidence_post_cgr"

        if incumbent_rerank < 0.90 or incumbent_rerank <= challenger_rerank + 0.04:
            return False, ""

        incumbent_manual = _candidate_float(incumbent, "manual_structured_score")
        challenger_manual = _candidate_float(challenger, "manual_structured_score")
        incumbent_param = _candidate_float(incumbent, "param_score")
        challenger_param = _candidate_float(challenger, "param_score")
        incumbent_feature = _candidate_float(incumbent, "feature_alignment_score")
        challenger_feature = _candidate_float(challenger, "feature_alignment_score")

        weak_challenger_signal = (
            challenger_rerank < 0.75
            or challenger_manual < incumbent_manual - 0.10
            or challenger_feature < incumbent_feature - 0.20
            or challenger_param < incumbent_param - 0.04
            or (
                incumbent_rerank >= 0.98
                and incumbent_rerank > challenger_rerank + 0.05
                and challenger_param <= incumbent_param
            )
        )
        if weak_challenger_signal:
            return True, "incumbent_strong_evidence_over_low_confidence_advisory"
        return False, ""

    def _accept_decision_advisory(
        current_ordered: list[dict],
        selected: dict | None,
        *,
        stage: str,
    ) -> tuple[list[dict], dict | None]:
        advisories = [
            advisory
            for advisory in list(ranking_meta.get("decision_advisories") or [])
            if str(advisory.get("stage") or "") == stage
        ]
        if not advisories:
            return current_ordered, selected

        advisory = advisories[-1]
        superseded_by = str(advisory.get("superseded_by") or "").strip()
        if superseded_by:
            _mark_advisory_rejected(advisory, f"superseded_by_{superseded_by}")
            return current_ordered, selected

        suggested_id = str(advisory.get("suggested_top1_id") or "").strip()
        if not suggested_id:
            _mark_advisory_rejected(advisory, "missing_suggested_top1")
            return current_ordered, selected

        candidate = _find_ordered_candidate(current_ordered, suggested_id)
        if candidate is None:
            _mark_advisory_rejected(advisory, "suggested_candidate_not_rankable")
            return current_ordered, selected

        blocked, block_reason = _advisory_blocked_by_strong_incumbent(
            current_ordered,
            advisory,
            candidate,
            stage=stage,
        )
        if blocked:
            _mark_advisory_rejected(advisory, block_reason)
            return current_ordered, selected

        current_head_id = str(((selected or (current_ordered[0] if current_ordered else None)) or {}).get("quota_id", "") or "")
        advisory["accepted_by_final_decider"] = True
        advisory["rejected_by_final_decider"] = False
        advisory["selected_quota_id"] = suggested_id
        advisory["rank_head_quota_id"] = current_head_id
        advisory["final_decider_reason"] = f"{stage}_advisory_accepted_by_final_decider"
        ranking_meta["final_decider_reason"] = advisory["final_decider_reason"]
        if suggested_id != current_head_id and not ranking_meta.get("final_changed_by"):
            ranking_meta["final_changed_by"] = "final_decider"
        if suggested_id != _top_candidate_id(current_ordered):
            current_ordered, _ = _promote_candidate_by_quota_id(current_ordered, suggested_id)
        return current_ordered, candidate

    def _select_final_candidate_from_advisory(
        current_ordered: list[dict],
        *,
        apply_lifecycle_guard: bool,
    ) -> tuple[list[dict], dict | None]:
        rank_head = current_ordered[0] if current_ordered else None
        rank_head_id = str((rank_head or {}).get("quota_id", "") or "")
        selected = rank_head

        current_ordered, selected = _accept_decision_advisory(
            current_ordered,
            selected,
            stage="post_ltr_structural_ranker",
        )
        current_ordered, selected = _accept_decision_advisory(
            current_ordered,
            selected,
            stage="ltr_lifecycle_guard",
        )
        current_ordered, selected = _accept_decision_advisory(
            current_ordered,
            selected,
            stage="post_cgr",
        )
        current_ordered, selected = _accept_decision_advisory(
            current_ordered,
            selected,
            stage="cgr_lifecycle_guard",
        )

        category_base_ordered = list(current_ordered or [])
        selected_id = str((selected or {}).get("quota_id", "") or "")
        if selected_id and selected_id != _top_candidate_id(category_base_ordered):
            category_base_ordered, _ = _promote_candidate_by_quota_id(category_base_ordered, selected_id)
        category_safe_best = _pick_category_safe_candidate(item, category_base_ordered) if category_base_ordered else None
        category_safe_id = str((category_safe_best or {}).get("quota_id", "") or "")
        rank_head_id = str((selected or rank_head or {}).get("quota_id", "") or "")
        reason = "category_safe_no_match"
        applied = False

        if category_safe_best and category_safe_id:
            reason = (
                "category_safe_matches_rank_head"
                if category_safe_id == rank_head_id
                else "category_safe_advisory_available"
            )
            if category_safe_id != rank_head_id:
                _record_decision_advisory(
                    stage="category_safe",
                    from_top1_id=rank_head_id,
                    suggested_top1_id=category_safe_id,
                    reason=reason,
                    details={"candidate_present": True},
                )

            if apply_lifecycle_guard and category_safe_id != rank_head_id:
                category_ordered, category_promoted = _promote_candidate_by_quota_id(category_base_ordered, category_safe_id)
                guarded_category_ordered, category_lifecycle_guard = _promote_lifecycle_stronger_candidate(
                    category_ordered,
                    protect_structural_advantage=False,
                )
                if category_promoted and category_lifecycle_guard:
                    ranking_meta["category_safe_lifecycle_guard"] = {
                        **dict(category_lifecycle_guard),
                        "advisory_only": True,
                        "decision_owner": "final_decider",
                    }
                    _record_decision_advisory(
                        stage="category_safe_lifecycle_guard",
                        from_top1_id=str(category_lifecycle_guard.get("from_quota_id") or category_safe_id),
                        suggested_top1_id=str(
                            category_lifecycle_guard.get("to_quota_id") or _top_candidate_id(guarded_category_ordered)
                        ),
                        reason=str(category_lifecycle_guard.get("reason") or "stronger_lifecycle_score"),
                        score_margin=category_lifecycle_guard.get("margin"),
                        details=dict(category_lifecycle_guard),
                    )

            current_ordered, selected = _accept_decision_advisory(
                current_ordered,
                selected,
                stage="category_safe",
            )
            current_ordered, selected = _accept_decision_advisory(
                current_ordered,
                selected,
                stage="category_safe_lifecycle_guard",
            )
            selected_id = str((selected or {}).get("quota_id", "") or "")
            applied = bool(category_safe_id and selected_id == category_safe_id and category_safe_id != rank_head_id)

        selected_id = str((selected or {}).get("quota_id", "") or "")
        ranking_meta["category_safe_advisory"] = {
            "applied": applied,
            "advisory_applied": bool(category_safe_id),
            "suggested_quota_id": category_safe_id,
            "selected_quota_id": selected_id,
            "rank_head_quota_id": rank_head_id,
            "decision_owner": "final_decider",
            "reason": reason,
        }
        return current_ordered, selected

    ranking_meta["pre_ltr_top1_id"] = _top_candidate_id(ordered)
    if ranking_meta["unified_ranking_mode"] == "enabled":
        seed_top1_id = ranking_meta["pre_ltr_top1_id"]
        ranking_meta["ltr"] = {
            "skipped_by_unified_primary": True,
            "legacy_stage_disabled": True,
        }
        ranking_meta["post_ltr_top1_id"] = seed_top1_id
        ranking_meta["post_ltr_structural_top1_id"] = seed_top1_id
        ranking_meta["post_cgr_top1_id"] = seed_top1_id
        ranking_meta["post_arbiter_top1_id"] = seed_top1_id
        ranking_meta["post_explicit_top1_id"] = seed_top1_id
        ranking_meta["post_anchor_top1_id"] = seed_top1_id
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        explicit_override = {
            "applied": False,
            "advisory_applied": False,
            "reason": "skipped_by_unified_primary",
            "legacy_stage_disabled": True,
        }
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="ltr",
            top1_id=ranking_meta["post_ltr_top1_id"],
            prev_top1_id=ranking_meta["pre_ltr_top1_id"],
            reason="ltr_skipped_by_unified_primary",
        )
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="post_ltr_structural_ranker",
            top1_id=ranking_meta["post_ltr_structural_top1_id"],
            prev_top1_id=ranking_meta["post_ltr_top1_id"],
            reason="post_ltr_structural_ranker_skipped_by_unified_primary",
        )
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="cgr_ranker",
            top1_id=ranking_meta["post_cgr_top1_id"],
            prev_top1_id=ranking_meta["post_ltr_top1_id"],
            reason="cgr_skipped_by_unified_primary",
        )
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="candidate_arbiter",
            top1_id=ranking_meta["post_arbiter_top1_id"],
            prev_top1_id=ranking_meta["post_cgr_top1_id"],
            reason="arbiter_skipped_by_unified_primary",
        )
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="explicit_picker",
            top1_id=ranking_meta["post_explicit_top1_id"],
            prev_top1_id=ranking_meta["post_arbiter_top1_id"],
            reason="explicit_picker_skipped_by_unified_primary",
        )
        ordered, best = _select_final_candidate_from_advisory(
            ordered,
            apply_lifecycle_guard=False,
        )
        selected_id = str((best or {}).get("quota_id", "") or "")
        if best:
            ranking_meta["selected_top1_id"] = selected_id
        ranking_meta["post_final_top1_id"] = str(ranking_meta.get("selected_top1_id", "") or "")
        _queue_rank_stage_trace_step(
            ranking_meta,
            name="category_safe",
            top1_id=ranking_meta["post_final_top1_id"],
            prev_top1_id=ranking_meta["post_explicit_top1_id"],
            reason=str(
                ranking_meta.get("final_decider_reason")
                or ranking_meta["category_safe_advisory"].get("reason")
                or "category_safe_no_match"
            ),
        )
        return ordered, ranking_meta, arbitration, explicit_override, best

    api = _api()
    ordered, ltr_meta = api.rerank_candidates_with_ltr(item, ordered, {"item": item})
    ltr_meta = dict(ltr_meta or {})
    structural_ordered, structural_ranker = _promote_post_ltr_structural_candidate(ordered, item=item)
    if structural_ranker:
        legacy_family_ranker = dict(structural_ranker.get("legacy_family_group_ranker") or {})
        ranking_meta["post_ltr_structural_ranker"] = dict(structural_ranker)
        ranking_meta["post_ltr_structural_top1_id"] = str(
            structural_ranker.get("to_quota_id") or _top_candidate_id(structural_ordered)
        )
        ltr_meta = {
            **dict(ltr_meta or {}),
            "post_ltr_structural_ranker": dict(structural_ranker),
            "family_group_ranker": legacy_family_ranker,
        }
        _record_post_ltr_structural_advisory(
            source_stage="post_ltr_structural_comparator",
            from_top1_id=str(structural_ranker.get("from_quota_id") or _top_candidate_id(ordered)),
            suggested_top1_id=str(structural_ranker.get("to_quota_id") or _top_candidate_id(structural_ordered)),
            reason=str(structural_ranker.get("reason") or "post_ltr_structural_comparator"),
            score_margin=structural_ranker.get("margin"),
            details=dict(structural_ranker),
        )
    else:
        ranking_meta["post_ltr_structural_top1_id"] = _top_candidate_id(ordered)
    lifecycle_ordered, ltr_lifecycle_guard = _promote_lifecycle_stronger_candidate(ordered)
    if ltr_lifecycle_guard:
        ltr_meta = {
            **dict(ltr_meta or {}),
            "lifecycle_guard": {
                **dict(ltr_lifecycle_guard),
                "applied": False,
                "advisory_applied": True,
                "advisory_only": True,
                "decision_owner": "final_decider",
            },
        }
        _record_decision_advisory(
            stage="ltr_lifecycle_guard",
            from_top1_id=str(ltr_lifecycle_guard.get("from_quota_id") or _top_candidate_id(ordered)),
            suggested_top1_id=str(ltr_lifecycle_guard.get("to_quota_id") or _top_candidate_id(lifecycle_ordered)),
            reason=str(ltr_lifecycle_guard.get("reason") or "stronger_lifecycle_score"),
            score_margin=ltr_lifecycle_guard.get("margin"),
            details=dict(ltr_lifecycle_guard),
        )
    ranking_meta["ltr"] = ltr_meta
    ranking_meta["post_ltr_top1_id"] = _top_candidate_id(ordered)
    raw_post_cgr_top1_id = str((ltr_meta.get("post_cgr_top1_id") or ranking_meta["post_ltr_top1_id"]) or "")
    if raw_post_cgr_top1_id and raw_post_cgr_top1_id != ranking_meta["post_ltr_top1_id"]:
        cgr_ordered, cgr_promoted = _promote_candidate_by_quota_id(ordered, raw_post_cgr_top1_id)
        cgr_advisory = _record_decision_advisory(
            stage="post_cgr",
            from_top1_id=ranking_meta["post_ltr_top1_id"],
            suggested_top1_id=raw_post_cgr_top1_id,
            reason=_resolve_cgr_rank_stage_reason(ltr_meta),
            details={"candidate_present": cgr_promoted},
        )
        guarded_cgr_ordered, cgr_lifecycle_guard = _promote_lifecycle_stronger_candidate(cgr_ordered)
        if cgr_promoted and cgr_lifecycle_guard:
            ltr_meta["cgr_lifecycle_guard"] = {
                **dict(cgr_lifecycle_guard),
                "advisory_only": True,
                "decision_owner": "final_decider",
            }
            cgr_advisory["superseded_by"] = "cgr_lifecycle_guard"
            cgr_advisory["final_decider_reason"] = "superseded_by_cgr_lifecycle_guard"
            _record_decision_advisory(
                stage="cgr_lifecycle_guard",
                from_top1_id=str(cgr_lifecycle_guard.get("from_quota_id") or raw_post_cgr_top1_id),
                suggested_top1_id=str(cgr_lifecycle_guard.get("to_quota_id") or _top_candidate_id(guarded_cgr_ordered)),
                reason=str(cgr_lifecycle_guard.get("reason") or "stronger_lifecycle_score"),
                score_margin=cgr_lifecycle_guard.get("margin"),
                details=dict(cgr_lifecycle_guard),
            )
            ranking_meta["post_cgr_advisory_top1_id"] = _top_candidate_id(guarded_cgr_ordered)
        else:
            ranking_meta["post_cgr_advisory_top1_id"] = raw_post_cgr_top1_id if cgr_promoted else _top_candidate_id(cgr_ordered)
        ranking_meta["post_cgr_top1_id"] = ranking_meta["post_ltr_top1_id"]
    else:
        ranking_meta["post_cgr_top1_id"] = ranking_meta["post_ltr_top1_id"]
        ranking_meta["post_cgr_advisory_top1_id"] = ranking_meta["post_ltr_top1_id"]
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="ltr",
        top1_id=ranking_meta["post_ltr_top1_id"],
        prev_top1_id=ranking_meta["pre_ltr_top1_id"],
        reason=_resolve_ltr_rank_stage_reason(ltr_meta),
    )
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="post_ltr_structural_ranker",
        top1_id=ranking_meta["post_ltr_structural_top1_id"] or ranking_meta["post_ltr_top1_id"],
        prev_top1_id=ranking_meta["post_ltr_top1_id"],
        reason=str(
            (ranking_meta.get("post_ltr_structural_ranker") or {}).get("reason")
            or "post_ltr_structural_ranker_no_advisory"
        ),
    )
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="cgr_ranker",
        top1_id=ranking_meta["post_cgr_top1_id"],
        prev_top1_id=ranking_meta["post_ltr_top1_id"],
        reason=_resolve_cgr_rank_stage_reason(ltr_meta),
    )

    if allow_arbiter:
        arbiter_candidates, arbitration = api.arbitrate_candidates(item, ordered, route_profile=item.get("query_route"))
        ordered = _merge_arbiter_annotations(ordered, arbiter_candidates)
        if arbitration.get("applied"):
            arbitration = {
                **dict(arbitration or {}),
                "applied": False,
                "reason": str(arbitration.get("reason") or "structured_candidate_swap_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ordered, restored_cgr_top1 = _promote_candidate_by_quota_id(
            ordered,
            ranking_meta["post_cgr_top1_id"],
        )
        ordered, post_arbiter_lifecycle_guard = _promote_lifecycle_stronger_candidate(ordered)
        if restored_cgr_top1:
            arbitration = {
                **dict(arbitration or {}),
                "cgr_top1_restored_after_advisory": True,
                "restored_cgr_top1_id": ranking_meta["post_cgr_top1_id"],
            }
        if post_arbiter_lifecycle_guard:
            arbitration = {
                **dict(arbitration or {}),
                "lifecycle_guard": post_arbiter_lifecycle_guard,
            }
        ranking_meta["post_arbiter_top1_id"] = _top_candidate_id(ordered)
    else:
        arbitration = {
            "applied": False,
            "advisory_applied": False,
            "route": str((item.get("query_route") or {}).get("route") or ""),
            "reason": "no_param_matched_candidates",
        }
        ranking_meta["post_arbiter_top1_id"] = ranking_meta["post_ltr_top1_id"]
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="candidate_arbiter",
        top1_id=ranking_meta["post_arbiter_top1_id"],
        prev_top1_id=ranking_meta["post_cgr_top1_id"],
        reason=str(arbitration.get("reason") or "arbiter_not_run"),
    )

    if allow_explicit:
        explicit_result = _promote_explicit_distribution_box_candidate(item, ordered)
        if isinstance(explicit_result, tuple) and len(explicit_result) == 2:
            explicit_candidates, explicit_override = explicit_result
        else:
            explicit_candidates = list(explicit_result or [])
            explicit_override = {}
        ordered = _merge_explicit_annotations(ordered, explicit_candidates)
        if explicit_override.get("applied"):
            explicit_override = {
                **dict(explicit_override or {}),
                "applied": False,
                "reason": str(explicit_override.get("reason") or "explicit_advisory"),
                "reorder_ignored_by_pipeline": True,
            }
        ranking_meta["explicit_override"] = explicit_override
        ranking_meta["post_explicit_top1_id"] = _top_candidate_id(ordered)
    else:
        ranking_meta["post_explicit_top1_id"] = ranking_meta["post_arbiter_top1_id"]
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="explicit_picker",
        top1_id=ranking_meta["post_explicit_top1_id"],
        prev_top1_id=ranking_meta["post_arbiter_top1_id"],
        reason=str(
            (explicit_override or {}).get("reason")
            or ("explicit_stage_skipped" if not allow_explicit else "no_explicit_override")
        ),
    )

    ranking_meta["post_anchor_top1_id"] = _top_candidate_id(ordered)

    ordered, best = _select_final_candidate_from_advisory(
        ordered,
        apply_lifecycle_guard=True,
    )
    if best:
        ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
    ranking_meta["post_final_top1_id"] = str(ranking_meta.get("selected_top1_id", "") or "")
    _queue_rank_stage_trace_step(
        ranking_meta,
        name="category_safe",
        top1_id=ranking_meta["post_final_top1_id"],
        prev_top1_id=ranking_meta["post_explicit_top1_id"],
        reason=str(
            ranking_meta.get("final_decider_reason")
            or ranking_meta["category_safe_advisory"].get("reason")
            or "category_safe_no_match"
        ),
    )
    return ordered, ranking_meta, arbitration, explicit_override, best


def _assemble_search_result_payload(item: dict,
                                    *,
                                    candidates: list[dict],
                                    recall_topk_ids: list[str],
                                    valid_candidates: list[dict],
                                    matched_candidates: list[dict],
                                    best: dict | None,
                                    confidence: float,
                                    explanation: str,
                                    arbitration: dict,
                                    explicit_override: dict,
                                    plugin_route_gate: dict,
                                    reasoning_decision: dict,
                                    ranking_meta: dict) -> dict:
    all_candidate_ids = [
        str(candidate.get("quota_id", "")).strip()
        for candidate in valid_candidates
        if str(candidate.get("quota_id", "")).strip()
    ]
    parser_diagnostics = _build_parser_trace_diagnostics(item)
    router_diagnostics = _build_router_trace_diagnostics(item)
    retriever_diagnostics = _build_retriever_trace_diagnostics(
        item,
        valid_candidates,
        matched_candidates if valid_candidates else [],
        router_diagnostics,
    )
    ranker_candidates = valid_candidates if ranking_meta.get("unified_result_used") else (
        matched_candidates if matched_candidates else valid_candidates
    )
    ranker_diagnostics = _build_ranker_trace_diagnostics(ranker_candidates, best, ranking_meta, arbitration)
    diagnostic_payload_enabled = _diagnostic_snapshot_payload_enabled(item)
    lifecycle_trace_top_n = (
        _diagnostic_snapshot_limit(item, "_diagnostic_lifecycle_trace_top_n", 20)
        if diagnostic_payload_enabled
        else 5
    )
    include_lifecycle_feature_trace = bool(diagnostic_payload_enabled and lifecycle_trace_top_n > 20)
    candidate_lifecycle_trace = _build_candidate_lifecycle_trace(
        candidates,
        valid_candidates,
        matched_candidates,
        best,
        ranking_meta,
        ranker_diagnostics,
        top_n=lifecycle_trace_top_n,
        include_feature_trace=include_lifecycle_feature_trace,
    )
    candidate_snapshot_top_n = (
        _diagnostic_snapshot_limit(item, "_diagnostic_candidate_snapshot_top_n", 20)
        if diagnostic_payload_enabled
        else 5
    )

    quotas = [{
        "quota_id": best["quota_id"],
        "name": best["name"],
        "unit": best.get("unit", ""),
        "reason": explanation,
        "reasoning": summarize_candidate_reasoning(best),
        "db_id": best.get("id"),
    }] if best else []
    supplemental_quotas = item.get("_supplemental_quotas") if isinstance(item, dict) else []
    if quotas and isinstance(supplemental_quotas, list):
        seen_ids = {str(quota.get("quota_id", "")).strip() for quota in quotas if str(quota.get("quota_id", "")).strip()}
        for quota in supplemental_quotas:
            quota_id = str((quota or {}).get("quota_id", "")).strip()
            quota_name = str((quota or {}).get("name", "")).strip()
            if not quota_id or not quota_name or quota_id in seen_ids:
                continue
            quotas.append(dict(quota))
            seen_ids.add(quota_id)

    result = {
        "bill_item": item,
        "quotas": quotas,
        "confidence": confidence,
        "explanation": explanation,
        "candidates_count": len(valid_candidates),
        "candidate_count": len(valid_candidates),
        "hard_param_fail_rejected_count": ranking_meta["hard_param_fail_rejected_count"],
        "hard_param_fail_rejected_candidates": list(ranking_meta.get("hard_param_fail_rejected_candidates") or []),
        "rankable_pool_contract_recovered_count": int(
            ranking_meta.get("rankable_pool_contract_recovered_count", 0) or 0
        ),
        "rankable_pool_contract_recovered_candidates": list(
            ranking_meta.get("rankable_pool_contract_recovered_candidates") or []
        ),
        "all_candidate_ids": all_candidate_ids,
        "recall_topk_ids": list(recall_topk_ids or []),
        "candidate_snapshots": _build_ranked_candidate_snapshots(valid_candidates, top_n=candidate_snapshot_top_n),
        "candidate_lifecycle_trace": candidate_lifecycle_trace,
        "diagnostic_candidate_snapshot_top_n": candidate_snapshot_top_n,
        "diagnostic_lifecycle_trace_top_n": lifecycle_trace_top_n,
        "diagnostic_hard_param_snapshot_top_n": (
            _diagnostic_snapshot_limit(item, "_diagnostic_hard_param_snapshot_top_n", 20)
            if diagnostic_payload_enabled
            else 5
        ),
        "diagnostic_lifecycle_feature_trace": include_lifecycle_feature_trace,
        "match_source": "search",
        "arbitration": arbitration,
        "explicit_override": explicit_override,
        "category_safe_advisory": dict(ranking_meta.get("category_safe_advisory") or {}),
        "post_ltr_structural_ranker": dict(ranking_meta.get("post_ltr_structural_ranker") or {}),
        "decision_advisories": list(ranking_meta.get("decision_advisories") or []),
        "plugin_route_gate": plugin_route_gate,
        "reasoning_decision": reasoning_decision,
        "needs_reasoning": bool(reasoning_decision.get("is_ambiguous")),
        "require_final_review": bool(reasoning_decision.get("require_final_review")),
        "pre_ltr_top1_id": ranking_meta["pre_ltr_top1_id"],
        "post_ltr_top1_id": ranking_meta["post_ltr_top1_id"],
        "post_ltr_structural_top1_id": (
            ranking_meta["post_ltr_structural_top1_id"] or ranking_meta["post_ltr_top1_id"]
        ),
        "post_cgr_top1_id": ranking_meta["post_cgr_top1_id"],
        "post_cgr_advisory_top1_id": ranking_meta["post_cgr_advisory_top1_id"],
        "post_arbiter_top1_id": ranking_meta["post_arbiter_top1_id"],
        "post_explicit_top1_id": ranking_meta["post_explicit_top1_id"],
        "post_anchor_top1_id": ranking_meta["post_anchor_top1_id"],
        "selected_top1_id": ranking_meta["selected_top1_id"],
        "legacy_top1_id": ranking_meta["legacy_top1_id"],
        "unified_ranking_enabled": ranking_meta["unified_ranking_enabled"],
        "unified_ranking_shadow_mode": ranking_meta["unified_ranking_shadow_mode"],
        "unified_ranking_mode": ranking_meta["unified_ranking_mode"],
        "unified_ranking_executed": ranking_meta["unified_ranking_executed"],
        "unified_result_used": ranking_meta["unified_result_used"],
        "unified_top1_id": ranking_meta["unified_top1_id"],
        "unified_top1_score": ranking_meta["unified_top1_score"],
        "unified_top1_confidence": ranking_meta["unified_top1_confidence"],
        "unified_top1_matches_selected": ranking_meta["unified_top1_matches_selected"],
        "unified_top1_matches_legacy": ranking_meta["unified_top1_matches_legacy"],
        "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
        "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
        "unified_legacy_score_gap": ranking_meta["unified_legacy_score_gap"],
        "unified_shadow_comparison": {
            "legacy_top1_id": ranking_meta["legacy_top1_id"],
            "unified_top1_id": ranking_meta["unified_top1_id"],
            "matches": ranking_meta["unified_top1_matches_legacy"],
            "legacy_top1_unified_score": ranking_meta["legacy_top1_unified_score"],
            "legacy_top1_unified_confidence": ranking_meta["legacy_top1_unified_confidence"],
            "score_gap": ranking_meta["unified_legacy_score_gap"],
            "failure_reason": ranking_meta["unified_ranking_error"],
        },
        "unified_ranking_diagnostics": ranking_meta["unified_ranking_diagnostics"],
        "unified_ranking_error": ranking_meta["unified_ranking_error"],
        "post_final_top1_id": str((quotas[0].get("quota_id", "") if quotas else "") or ""),
        "final_changed_by": ranking_meta["final_changed_by"],
        "final_decider_reason": ranking_meta["final_decider_reason"],
        "ltr_rerank": ranking_meta["ltr"],
        "rank_decision_owner": ranker_diagnostics.get("decision_owner", ""),
        "rank_top1_flip_count": ranker_diagnostics.get("top1_flip_count", 0),
    }

    _append_trace_step(
        result,
        "search_select",
        selected_quota=best.get("quota_id") if best else "",
        selected_reasoning=summarize_candidate_reasoning(best) if best else {},
        pre_ltr_top1_id=result.get("pre_ltr_top1_id", ""),
        post_ltr_top1_id=result.get("post_ltr_top1_id", ""),
        post_ltr_structural_top1_id=result.get("post_ltr_structural_top1_id", ""),
        post_ltr_structural_ranker=result.get("post_ltr_structural_ranker", {}),
        post_cgr_top1_id=result.get("post_cgr_top1_id", ""),
        post_arbiter_top1_id=result.get("post_arbiter_top1_id", ""),
        post_explicit_top1_id=result.get("post_explicit_top1_id", ""),
        post_anchor_top1_id=result.get("post_anchor_top1_id", ""),
        selected_top1_id=result.get("selected_top1_id", ""),
        arbitration=arbitration,
        explicit_override=explicit_override,
        decision_advisories=result.get("decision_advisories", []),
        plugin_route_gate=plugin_route_gate,
        reasoning_decision=reasoning_decision,
        parser=parser_diagnostics,
        router=router_diagnostics,
        retriever=retriever_diagnostics,
        ranker=ranker_diagnostics,
        query_route=item.get("query_route") or {},
        batch_context=summarize_batch_context_for_trace(item),
        ltr_rerank=result.get("ltr_rerank", {}),
        candidates_count=len(valid_candidates),
        candidates=_summarize_candidates_for_trace(candidates),
        hard_param_fail_rejected_count=result.get("hard_param_fail_rejected_count", 0),
        hard_param_fail_rejected_candidates=result.get("hard_param_fail_rejected_candidates", []),
    )
    result["_pending_rank_stage_trace_steps"] = list(ranking_meta.get("rank_stage_trace_steps") or [])
    return result


def _finalize_search_result_payload(result: dict,
                                    *,
                                    item: dict,
                                    candidates: list[dict],
                                    valid_candidates: list[dict],
                                    best: dict | None,
                                    explanation: str,
                                    reasoning_decision: dict) -> dict:
    input_gate = item.get("_input_gate") or {}
    hard_param_fail_rejected_count = int(result.get("hard_param_fail_rejected_count", 0) or 0)
    if best and valid_candidates and any(candidate.get("param_match", True) for candidate in valid_candidates):
        _set_result_reason(result, "structured_selection", ["retrieved", "validated"], explanation or "selected from structured candidates")
    elif best and valid_candidates:
        _set_result_reason(result, "param_conflict", ["retrieved", "param_conflict", "manual_review"], explanation or "fallback to best candidate")
    elif hard_param_fail_rejected_count > 0 and not valid_candidates:
        _set_result_reason(
            result,
            "param_hard_fail",
            ["retrieved", "param_hard_fail", "manual_review"],
            "all candidates rejected by hard parameter validation",
        )
    elif candidates and not valid_candidates:
        _set_result_reason(result, "candidate_invalid", ["retrieved", "candidate_invalid", "manual_review"], "candidates missing quota_id/name")
    else:
        _set_result_reason(result, "recall_failure", ["recall_failure", "no_candidates"], "search found no candidates")

    if input_gate:
        _set_result_reason(
            result,
            result.get("primary_reason", ""),
            list(input_gate.get("reason_tags") or []),
            result.get("reason_detail", "") or str(input_gate.get("detail") or ""),
        )
    if reasoning_decision.get("is_ambiguous"):
        ambiguity_tags = ["ambiguous_candidates"]
        if reasoning_decision.get("require_final_review"):
            ambiguity_tags.append("manual_review")
        _set_result_reason(result, result.get("primary_reason", ""), ambiguity_tags, result.get("reason_detail", "") or explanation)

    result = _apply_price_validation(result, item, best)

    if best and valid_candidates:
        result["alternatives"] = _build_alternatives(valid_candidates, skip_obj=best, top_n=DEFAULT_ALTERNATIVE_COUNT)
    if not best:
        result["no_match_reason"] = explanation or (
            "all candidates rejected by hard parameter validation"
            if hard_param_fail_rejected_count > 0
            else "搜索无匹配结果"
        )
    return result


def _build_ranked_selection_decision(item: dict,
                                     *,
                                     best: dict | None,
                                     decision_candidates: list[dict],
                                     candidates_count: int,
                                     param_match: bool,
                                     arbitration: dict) -> tuple[float, str, dict]:
    if not best:
        return 0.0, "no safe candidate selected", {}

    if param_match:
        best_composite = compute_candidate_rank_score(best)
        others = [candidate for candidate in decision_candidates if candidate is not best]
        second_composite = max((compute_candidate_rank_score(candidate) for candidate in others), default=0)
        confidence = calculate_confidence(
            best.get("param_score", 0.5),
            param_match=True,
            name_bonus=best.get("name_bonus", 0.0),
            score_gap=best_composite - second_composite,
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = best.get("param_detail", "")
    else:
        confidence = calculate_confidence(
            best.get("param_score", 0.0),
            param_match=False,
            name_bonus=best.get("name_bonus", 0.0),
            rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
            family_aligned=infer_confidence_family_alignment(best),
            family_hard_conflict=bool(best.get("family_gate_hard_conflict", False)),
            candidates_count=candidates_count,
            is_ambiguous_short=item.get("_is_ambiguous_short", False),
        )
        explanation = f"fallback_to_candidate: {best.get('param_detail', '')}"

    reasoning_decision = analyze_ambiguity(
        decision_candidates,
        route_profile=item.get("query_route"),
        arbitration=arbitration,
    ).as_dict()
    return confidence, explanation, reasoning_decision


def _build_search_result_from_candidates_legacy(item: dict, candidates: list[dict]) -> dict:
    return _build_search_result_from_candidates(item, candidates)


def _build_search_result_from_candidates(item: dict,
                                         candidates: list[dict],
                                         *,
                                         recall_topk_ids: list[str] | None = None) -> dict:
    performance_monitor = PerformanceMonitor()
    best = None
    confidence = 0.0
    explanation = ""
    arbitration: dict = {}
    explicit_override: dict = {}
    reasoning_decision: dict = {}
    matched_candidates: list[dict] = []
    ranking_meta = _init_ranking_meta()
    resolved_recall_topk_ids = (
        list(recall_topk_ids)
        if recall_topk_ids is not None
        else _extract_recall_topk_ids(candidates)
    )

    with performance_monitor.measure("search_candidates_validate"):
        valid_candidates = [
            candidate
            for candidate in (candidates or [])
            if str(candidate.get("quota_id", "")).strip() and str(candidate.get("name", "")).strip()
        ]
    with performance_monitor.measure("search_plugin_route_gate"):
        valid_candidates, plugin_route_gate = _apply_plugin_route_gate(item, valid_candidates)
    with performance_monitor.measure("search_plugin_bias"):
        valid_candidates = _apply_plugin_candidate_biases(item, valid_candidates)
    with performance_monitor.measure("search_scope_annotate"):
        valid_candidates = _annotate_candidate_scope_signals(item, valid_candidates)
    valid_candidates, hard_param_fail_candidates = filter_param_hard_fail_candidates(valid_candidates)
    valid_candidates, hard_param_fail_candidates, rankable_contract_candidates = _retain_rankable_contract_candidates(
        valid_candidates,
        hard_param_fail_candidates,
        item=item,
    )
    diagnostic_payload_enabled = _diagnostic_snapshot_payload_enabled(item)
    hard_param_snapshot_top_n = (
        _diagnostic_snapshot_limit(item, "_diagnostic_hard_param_snapshot_top_n", 20)
        if diagnostic_payload_enabled
        else 5
    )
    ranking_meta["hard_param_fail_rejected_count"] = len(hard_param_fail_candidates)
    ranking_meta["hard_param_fail_rejected_candidates"] = _build_hard_param_fail_snapshots(
        hard_param_fail_candidates,
        top_n=hard_param_snapshot_top_n,
        include_diagnostics=diagnostic_payload_enabled and hard_param_snapshot_top_n > 20,
    )
    ranking_meta["rankable_pool_contract_recovered_count"] = len(rankable_contract_candidates)
    ranking_meta["rankable_pool_contract_recovered_candidates"] = _build_ranked_candidate_snapshots(
        rankable_contract_candidates,
        top_n=10,
    )
    ranking_meta["candidate_count"] = len(valid_candidates)
    candidate_filter_meta = {
        "hard_param_fail_rejected_count": ranking_meta["hard_param_fail_rejected_count"],
        "hard_param_fail_rejected_candidates": ranking_meta["hard_param_fail_rejected_candidates"],
        "rankable_pool_contract_recovered_count": ranking_meta["rankable_pool_contract_recovered_count"],
        "rankable_pool_contract_recovered_candidates": ranking_meta["rankable_pool_contract_recovered_candidates"],
        "candidate_count": ranking_meta["candidate_count"],
    }
    if candidates and not valid_candidates:
        if hard_param_fail_candidates:
            logger.warning("candidate list exists but all items were rejected by hard parameter validation")
        else:
            logger.warning("candidate list exists but all items miss quota_id/name; treat as no-match")

    if valid_candidates:
        with performance_monitor.measure("search_param_match_filter"):
            matched_candidates = [
                candidate
                for candidate in valid_candidates
                if candidate.get("param_match", True) or candidate.get("_rankable_pool_contract_protected")
            ]
        decision_candidates = matched_candidates if matched_candidates else valid_candidates
        with performance_monitor.measure("search_rank_pipeline"):
            ranked_candidates, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
                item,
                decision_candidates,
                reservoir=valid_candidates,
                allow_arbiter=bool(matched_candidates),
                allow_explicit=bool(matched_candidates),
            )
            ranking_meta.update(candidate_filter_meta)
        if matched_candidates:
            matched_candidates = ranked_candidates
        else:
            valid_candidates = ranked_candidates

        if best:
            ranking_meta["selected_top1_id"] = str(best.get("quota_id", "") or "")
            with performance_monitor.measure("search_selection_decision"):
                confidence, explanation, reasoning_decision = _build_ranked_selection_decision(
                    item,
                    best=best,
                    decision_candidates=ranked_candidates,
                    candidates_count=len(valid_candidates),
                    param_match=bool(matched_candidates),
                    arbitration=arbitration,
                )
        else:
            explanation = "no safe candidate selected from ranked results"

    ranking_meta["legacy_top1_id"] = str(ranking_meta.get("selected_top1_id", "") or "")
    unified_result = _apply_unified_ranking_shadow(item, valid_candidates, ranking_meta)
    valid_candidates, matched_candidates, best, confidence, explanation, reasoning_decision = _apply_unified_enabled_selection(
        item,
        valid_candidates,
        matched_candidates,
        ranking_meta,
        arbitration,
        unified_result,
        best,
        confidence,
        explanation,
        reasoning_decision,
    )

    with performance_monitor.measure("search_result_payload_assemble"):
        result = _assemble_search_result_payload(
            item,
            candidates=candidates,
            recall_topk_ids=resolved_recall_topk_ids,
            valid_candidates=valid_candidates,
            matched_candidates=matched_candidates,
            best=best,
            confidence=confidence,
            explanation=explanation,
            arbitration=arbitration,
            explicit_override=explicit_override,
            plugin_route_gate=plugin_route_gate,
            reasoning_decision=reasoning_decision,
            ranking_meta=ranking_meta,
        )
    result["search_candidate_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    return _finalize_search_result_payload(
        result,
        item=item,
        candidates=candidates,
        valid_candidates=valid_candidates,
        best=best,
        explanation=explanation,
        reasoning_decision=reasoning_decision,
    )
def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    performance_monitor = PerformanceMonitor()
    active_candidates = _merge_existing_candidate_neighbors_for_search_mode(
        item,
        list(candidates or []),
    )
    raw_recall_topk_ids = _extract_recall_topk_ids(active_candidates)
    injected_rule_qid = ""
    with performance_monitor.measure("search_rule_backup_injection"):
        if rule_backup:
            active_candidates, injected_rule_qid = _inject_rule_backup_candidate(
                item, active_candidates, rule_backup
            )
    with performance_monitor.measure("search_result_build"):
        result = _build_search_result_from_candidates(
            item,
            active_candidates,
            recall_topk_ids=raw_recall_topk_ids,
        )
    built_search_result = result
    _append_item_review_rejection_trace(result, item)
    with performance_monitor.measure("search_experience_reconcile"):
        result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    if (
        isinstance(built_search_result, dict)
        and isinstance(built_search_result.get("_pending_rank_stage_trace_steps"), list)
        and not isinstance(result.get("_pending_rank_stage_trace_steps"), list)
    ):
        result["_pending_rank_stage_trace_steps"] = list(built_search_result.get("_pending_rank_stage_trace_steps") or [])
    if injected_rule_qid:
        selected_qid = str((result.get("quotas") or [{}])[0].get("quota_id", "") or "").strip()
        if selected_qid == injected_rule_qid:
            result["match_source"] = "rule_injected"
            rule_hits += 1
        _append_trace_step(
            result,
            "rule_backup_injected",
            injected_quota_id=injected_rule_qid,
            backup_confidence=rule_backup.get("confidence", 0),
            selected_rule_candidate=bool(selected_qid and selected_qid == injected_rule_qid),
        )
    elif rule_backup:
        _append_trace_step(
            result,
            "rule_backup_rejected",
            backup_confidence=rule_backup.get("confidence", 0),
            current_confidence=result.get("confidence", 0),
        )
    _append_experience_shadow_audit_trace(result, exp_backup, item)
    result["search_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    _append_trace_step(
        result,
        "search_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
        search_stage_performance=result.get("search_stage_performance") or {},
    )
    _flush_rank_stage_trace_steps(result)
    return result, exp_hits, rule_hits


# ============================================================
# 统一前置处理
# ============================================================

def _prepare_item_for_matching(item: dict, experience_db, rule_validator: RuleValidator,
                               province: str = None, exact_exp_direct: bool = False,
                               lightweight_experience: bool = False,
                               lightweight_rule_prematch: bool = False,
                               performance_monitor: PerformanceMonitor | None = None) -> dict:
    """
    三种模式统一的前置处理：
    1) 措施项跳过
    2) 专业分类
    3) 经验库预匹配（可配置精确命中是否直通）
    4) 规则预匹配（高置信直通、低置信备选）
    """
    if province and not item.get("_resolved_province"):
        item["_resolved_province"] = province
    raw_ctx = _build_item_context(item, performance_monitor=performance_monitor)
    if isinstance(raw_ctx, BillItemContext):
        ctx = raw_ctx
    else:
        ctx = BillItemContext.from_legacy_dict(raw_ctx, item=item)
    item["query_route"] = ctx.get("query_route")
    item["plugin_hints"] = ctx.get("plugin_hints") or {}
    item["unified_plan"] = ctx.get("unified_plan") or {}
    item["context_prior"] = ctx.get("context_prior") or item.get("context_prior") or {}
    item["canonical_query"] = ctx.get("canonical_query") or {}
    name = ctx["name"]
    desc = ctx["desc"]
    canonical_query = ctx.get("canonical_query") or {}
    full_query = canonical_query.get("validation_query") or ctx["full_query"]
    search_query = canonical_query.get("search_query") or ctx["search_query"]
    normalized_query = canonical_query.get("normalized_query") or ctx["normalized_query"]
    input_gate = ctx.get("input_gate") or {}

    if _is_measure_item(name, desc, ctx["unit"], ctx["quantity"]):
        return {
            "early_result": _build_skip_measure_result(item),
            "early_type": "skip_measure",
        }

    if input_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(input_gate.get("primary_reason") or "dirty_input"),
                detail=str(input_gate.get("detail") or "输入质量不足，转人工审核"),
                reason_tags=list(input_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if input_gate.get("is_dirty_code"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or input_gate.get("primary_reason", "dirty_input")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            input_gate.get("reason_tags") or [],
        )
        if input_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = input_gate.get("detail", "")
        item["_input_gate"] = current_gate

    with (
        performance_monitor.measure("专业分类")
        if performance_monitor is not None else nullcontext()
    ):
        classification = _build_classification(
            item, name, desc, ctx["section"], ctx.get("sheet_name", ""), province=province
        )
    ctx = ctx.with_updates(classification=classification)
    item["classification"] = classification
    item["_trace_classification"] = dict(classification or {})

    adaptive_meta = dict(item.get("_adaptive_strategy_meta") or item.get("adaptive_strategy_meta") or {})
    if not adaptive_meta:
        adaptive_meta = dict(_api()._ADAPTIVE_STRATEGY.evaluate(item))
    adaptive_strategy = str(adaptive_meta.get("strategy") or item.get("adaptive_strategy") or "standard").strip().lower()
    if adaptive_strategy not in {"fast", "standard", "deep"}:
        adaptive_strategy = "standard"
    adaptive_meta["strategy"] = adaptive_strategy
    if adaptive_strategy == "fast" and experience_db is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "missing_experience_db"
        adaptive_meta["strategy"] = "standard"
        adaptive_strategy = "standard"
    item["adaptive_strategy"] = adaptive_strategy
    item["adaptive_strategy_meta"] = adaptive_meta
    item["_adaptive_strategy_meta"] = adaptive_meta

    context_gate = _evaluate_context_gate(name, desc, ctx["section"], classification)
    if context_gate.get("should_abstain"):
        return {
            "early_result": _build_input_gate_abstain_result(
                item,
                primary_reason=str(context_gate.get("primary_reason") or "context_missing"),
                detail=str(context_gate.get("detail") or "上下文不足，转人工审核"),
                reason_tags=list(context_gate.get("reason_tags") or []),
            ),
            "early_type": "input_gate_abstain",
        }

    if context_gate.get("reason_tags"):
        current_gate = dict(item.get("_input_gate") or {})
        current_gate["primary_reason"] = current_gate.get("primary_reason") or context_gate.get("primary_reason", "")
        current_gate["reason_tags"] = merge_reason_tags(
            current_gate.get("reason_tags") or [],
            context_gate.get("reason_tags") or [],
        )
        if context_gate.get("detail") and not current_gate.get("detail"):
            current_gate["detail"] = context_gate.get("detail", "")
        item["_input_gate"] = current_gate

    if adaptive_strategy == "fast":
        exp_result = _api().try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)
    elif lightweight_experience:
        exp_result = _api().try_experience_exact_match(
            normalized_query,
            item,
            experience_db,
            rule_validator,
            province=province,
            authority_only=True,
        )
    else:
        exp_result = _api().try_experience_match(
            normalized_query, item, experience_db, rule_validator, province=province)

    # 审核规则检查：经验库命中后，用审核规则验证一遍
    # 防止错误数据进入权威层后被无限复制
    if exp_result:
        review_error = _api()._review_check_match_result(exp_result, item)
        if review_error:
            # 在 item 上标记审核拦截（后续统计时从 result.bill_item 中读取）
            item["_review_rejected"] = True
            top_quota = ((exp_result.get("quotas") or [{}])[0] or {})
            item["_experience_review_rejection"] = {
                "type": review_error.get("type"),
                "reason": review_error.get("reason"),
                "match_source": exp_result.get("match_source", ""),
                "quota_id": str(top_quota.get("quota_id", "") or ""),
            }
            bill_name = item.get("name", "")
            logger.warning(
                f"经验库匹配被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(exp_result, "experience_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            exp_result = None  # 丢弃，走搜索兜底

    exp_backup = exp_result if exp_result else None

    if adaptive_strategy == "fast" and exp_result is None:
        adaptive_meta["downgraded_from"] = "fast"
        adaptive_meta["downgrade_reason"] = "experience_miss"
        adaptive_meta["strategy"] = "standard"
        item["adaptive_strategy"] = "standard"
        item["adaptive_strategy_meta"] = adaptive_meta
        item["_adaptive_strategy_meta"] = adaptive_meta
        adaptive_strategy = "standard"

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        if _should_shadow_audit_experience_direct(item, exp_result):
            audit_meta = item.get("_experience_shadow_audit") or {}
            _append_trace_step(
                exp_result,
                "experience_shadow_audit_sampled",
                sequence=int(audit_meta.get("sequence", 0) or 0),
                sample_every=int(audit_meta.get("sample_every", 0) or 0),
            )
        else:
            _append_trace_step(exp_result, "experience_exact_direct_return")
            return {
                "early_result": exp_result,
                "early_type": "experience_exact",
            }

    if lightweight_rule_prematch:
        rule_direct, rule_backup = None, None
    else:
        rule_direct, rule_backup = _prepare_rule_match(
            rule_validator, full_query, item, search_query, classification,
            route_profile=ctx.get("query_route"))
    if rule_direct:
        # 审核规则检查：规则直通也要过安检（与经验库直通一致）
        review_error = _api()._review_check_match_result(rule_direct, item)
        if review_error:
            bill_name = item.get("name", "")
            logger.warning(
                f"规则直通被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(rule_direct, "rule_direct_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            # 已被审核规则判错的规则直通结果不能再回流为备选，
            # 否则后续可能反向覆盖掉更安全的搜索结果。
            rule_backup = None
            rule_direct = None
        else:
            _append_experience_shadow_audit_trace(rule_direct, exp_backup, item)
            _append_trace_step(rule_direct, "rule_direct_return")
            return {
                "early_result": rule_direct,
                "early_type": "rule_direct",
            }

    return {
        "early_result": None,
        "early_type": None,
        "ctx": ctx,
        "classification": classification,
        "exp_backup": exp_backup,
        "rule_backup": rule_backup,
    }
