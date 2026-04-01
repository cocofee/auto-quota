from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import config
from src.text_parser import parser as text_parser
from src.utils import safe_float


PARAM_SPECS: dict[str, tuple[str, ...]] = {
    "dn": ("dn", "conduit_dn"),
    "cross_section": ("cross_section", "cable_section"),
    "power": ("power", "kw"),
    "length": ("length",),
    "thickness": ("thickness",),
    "capacity": ("capacity", "kva"),
    "current": ("current", "ampere"),
    "circuit_count": ("circuit_count", "circuits"),
    "count_band": ("count_band", "port_count", "count"),
}

STRUCT_FIELDS = (
    "family",
    "entity",
    "canonical_name",
    "material",
    "install_method",
    "connection",
    "system",
)

GENERICITY_DEFAULTS = {
    "retrieval_hits": 0.0,
    "positive_hits": 0.0,
    "success_ratio": -1.0,
    "genericity_index": -1.0,
    "retrieval_popularity": -1.0,
    "specificity_score": -1.0,
}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _candidate_features(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        return {}
    return candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}


def _item_features(item: dict, context: dict | None = None) -> dict:
    if isinstance(item.get("canonical_features"), dict) and item.get("canonical_features"):
        return item.get("canonical_features") or {}
    if isinstance(context, dict) and isinstance(context.get("canonical_features"), dict):
        return context.get("canonical_features") or {}
    return {}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _tokenize(text: str) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,4}", text)


def _token_set(text: str) -> set[str]:
    return {token for token in _tokenize(text) if token}


def _bigram_set(tokens: list[str]) -> set[str]:
    if len(tokens) < 2:
        return set()
    return {f"{tokens[i]}::{tokens[i + 1]}" for i in range(len(tokens) - 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _coverage(source: set[str], target: set[str]) -> float:
    if not source:
        return 0.0
    return len(source & target) / len(source)


def _get_param_value(payload: dict | None, aliases: tuple[str, ...]):
    payload = payload or {}
    numeric_params = payload.get("numeric_params") if isinstance(payload.get("numeric_params"), dict) else {}
    for alias in aliases:
        for source in (payload, numeric_params):
            if alias not in source:
                continue
            value = source.get(alias)
            if value is None or value == "":
                continue
            if isinstance(value, (list, tuple)):
                if len(value) == 2:
                    try:
                        return (float(value[0]), float(value[1]))
                    except (TypeError, ValueError):
                        continue
            try:
                return float(value)
            except (TypeError, ValueError):
                text = str(value)
                range_value = _parse_range(text)
                if range_value is not None:
                    return range_value
    return None


def _parse_range(text: str):
    text = str(text or "").strip()
    if not text:
        return None
    between = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", text)
    if between:
        return (float(between.group(1)), float(between.group(2)))
    upper = re.search(r"(?:<=|≤|不大于|小于等于)\s*(\d+(?:\.\d+)?)", text)
    if upper:
        return (float("-inf"), float(upper.group(1)))
    lower = re.search(r"(?:>=|≥|不小于|大于等于)\s*(\d+(?:\.\d+)?)", text)
    if lower:
        return (float(lower.group(1)), float("inf"))
    return None


def _is_in_range(query_value: float | None, candidate_value) -> int:
    if query_value is None or candidate_value is None:
        return 0
    if isinstance(candidate_value, tuple) and len(candidate_value) == 2:
        lower, upper = candidate_value
        return int(lower <= query_value <= upper)
    return int(abs(candidate_value - query_value) <= 1e-9)


def _value_for_gap(candidate_value):
    if isinstance(candidate_value, tuple) and len(candidate_value) == 2:
        lower, upper = candidate_value
        if math.isinf(lower) and not math.isinf(upper):
            return upper
        if math.isinf(upper) and not math.isinf(lower):
            return lower
        return (lower + upper) / 2.0
    return candidate_value


def _encode_missing_pattern(query_value, candidate_value) -> int:
    if query_value is not None and candidate_value is not None:
        return 0
    if query_value is None and candidate_value is not None:
        return 1
    if query_value is not None and candidate_value is None:
        return 2
    return 3


def _compute_zscores(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    if std <= 1e-12:
        return [0.0 for _ in values]
    return [(value - mean) / std for value in values]


@lru_cache(maxsize=4)
def _load_genericity_stats(path_str: str) -> dict[str, dict]:
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("quotas"), dict):
        return payload["quotas"]
    if isinstance(payload, dict):
        return payload
    return {}


def _genericity_for_candidate(candidate: dict) -> dict[str, float]:
    quota_id = str(candidate.get("quota_id", "") or "").strip()
    if not quota_id:
        return dict(GENERICITY_DEFAULTS)
    stats = _load_genericity_stats(str(config.LTR_GENERICITY_STATS_PATH))
    record = stats.get(quota_id) or {}
    merged = dict(GENERICITY_DEFAULTS)
    for key in GENERICITY_DEFAULTS:
        if key in record:
            merged[key] = safe_float(record.get(key), GENERICITY_DEFAULTS[key])
    retrieval_hits = safe_float(record.get("retrieval_hits"), merged["retrieval_hits"])
    positive_hits = safe_float(record.get("positive_hits"), merged["positive_hits"])
    if merged["retrieval_popularity"] < 0:
        merged["retrieval_popularity"] = math.log1p(max(retrieval_hits, 0.0))
    if merged["genericity_index"] < 0 and retrieval_hits > 0:
        merged["genericity_index"] = math.log1p(retrieval_hits) - math.log1p(max(positive_hits, 0.0))
    if merged["success_ratio"] < 0 and retrieval_hits > 0:
        merged["success_ratio"] = positive_hits / retrieval_hits
    if merged["specificity_score"] < 0 and merged["genericity_index"] >= 0:
        merged["specificity_score"] = 1.0 / (1.0 + merged["genericity_index"])
    return merged


def _extract_query_params(item: dict, context: dict | None = None) -> dict:
    params = dict(item.get("params") or {})
    if not params:
        canonical_query = dict((context or {}).get("canonical_query") or {})
        query_text = " ".join(
            part for part in (
                item.get("name", ""),
                item.get("description", ""),
                canonical_query.get("validation_query", ""),
            ) if part
        ).strip()
        params = text_parser.parse(query_text)
    if "conduit_dn" in params and "dn" not in params:
        params["dn"] = params.get("conduit_dn")
    return params


def _extract_candidate_params(candidate: dict) -> dict:
    cached = candidate.get("_ltr_v2_params")
    if isinstance(cached, dict):
        return cached
    params = text_parser.parse(str(candidate.get("name", "") or ""))
    if "conduit_dn" in params and "dn" not in params:
        params["dn"] = params.get("conduit_dn")
    candidate["_ltr_v2_params"] = params
    return params


def _rank_map(values: list[float], *, descending: bool = True) -> list[int]:
    indexed = list(enumerate(values))
    indexed.sort(key=lambda pair: pair[1], reverse=descending)
    ranks = [0] * len(values)
    for rank, (index, _) in enumerate(indexed, start=1):
        ranks[index] = rank
    return ranks


def _family_key(candidate: dict) -> str:
    features = _candidate_features(candidate)
    family = str(features.get("family") or "").strip()
    entity = str(features.get("entity") or "").strip()
    if family and entity:
        return f"{family}:{entity}"
    return family or entity


def _field_confidence(query_value: str, candidate_value: str) -> tuple[int, int, float]:
    q_has = int(bool(query_value))
    d_has = int(bool(candidate_value))
    if q_has and d_has and query_value == candidate_value:
        return 1, 0, 1.0
    if q_has and d_has and query_value != candidate_value:
        return 0, 1, 0.0
    if q_has or d_has:
        return 0, 0, 0.4
    return 0, 0, 0.0


def _query_param_coverage(query_params: dict) -> float:
    present = 0
    for aliases in PARAM_SPECS.values():
        if _get_param_value(query_params, aliases) is not None:
            present += 1
    return present / max(len(PARAM_SPECS), 1)


def extract_group_features(item: dict, candidates: list[dict], context: dict | None = None) -> list[dict]:
    context = context or {}
    item = item or {}
    query_params = _extract_query_params(item, context)
    query_features = _item_features(item, context)
    canonical_query = dict(context.get("canonical_query") or item.get("canonical_query") or {})
    raw_query = _normalize_text(canonical_query.get("raw_query") or f"{item.get('name', '')} {item.get('description', '')}")
    search_query = _normalize_text(canonical_query.get("search_query") or context.get("search_query") or raw_query)
    query_name = _normalize_text(item.get("name", ""))
    raw_query_tokens = _token_set(raw_query)
    raw_query_name_tokens = _token_set(query_name)
    search_query_tokens = _token_set(search_query)
    query_core_tokens = raw_query_tokens | _token_set(str(query_features.get("canonical_name") or ""))
    query_core_bigrams = _bigram_set(list(query_core_tokens))

    hybrid_scores = [safe_float(c.get("hybrid_score", c.get("rerank_score", 0.0)), 0.0) for c in candidates]
    semantic_scores = [safe_float(c.get("semantic_rerank_score", c.get("vector_score", 0.0)), 0.0) for c in candidates]
    spec_scores = [safe_float(c.get("spec_rerank_score", c.get("rerank_score", c.get("hybrid_score", 0.0))), 0.0) for c in candidates]
    param_scores = [safe_float(c.get("param_score"), 0.0) for c in candidates]
    logic_scores = [safe_float(c.get("logic_score"), 0.5) for c in candidates]
    feature_scores = [safe_float(c.get("feature_alignment_score"), 0.5) for c in candidates]

    hybrid_z = _compute_zscores(hybrid_scores)
    semantic_z = _compute_zscores(semantic_scores)
    spec_z = _compute_zscores(spec_scores)
    param_z = _compute_zscores(param_scores)
    logic_z = _compute_zscores(logic_scores)
    feature_z = _compute_zscores(feature_scores)

    bm25_ranks = _rank_map([safe_float(c.get("bm25_score"), 0.0) for c in candidates])
    dense_ranks = _rank_map([safe_float(c.get("vector_score"), 0.0) for c in candidates])
    rrf_ranks = _rank_map([safe_float(c.get("rrf_score", c.get("hybrid_score", 0.0)), 0.0) for c in candidates])
    hybrid_ranks = _rank_map(hybrid_scores)

    best_hybrid = max(hybrid_scores, default=0.0)
    second_hybrid = sorted(hybrid_scores, reverse=True)[1] if len(hybrid_scores) > 1 else 0.0
    best_semantic = max(semantic_scores, default=0.0)
    best_spec = max(spec_scores, default=0.0)
    best_param = max(param_scores, default=0.0)
    family_counts: dict[str, int] = {}
    for candidate in candidates:
        family_key = _family_key(candidate)
        family_counts[family_key] = family_counts.get(family_key, 0) + 1
    group_ambiguity = max(0.0, min(1.0, 1.0 - max(best_hybrid - second_hybrid, 0.0)))

    param_candidate_values = {
        param_name: [_value_for_gap(_get_param_value(_extract_candidate_params(candidate), aliases)) for candidate in candidates]
        for param_name, aliases in PARAM_SPECS.items()
    }

    features_by_candidate: list[dict] = []
    query_struct_values = {field: str(query_features.get(field) or "").strip() for field in STRUCT_FIELDS}

    for index, candidate in enumerate(candidates):
        candidate_params = _extract_candidate_params(candidate)
        candidate_features = _candidate_features(candidate)
        candidate_name = _normalize_text(candidate.get("name", ""))
        candidate_tokens = _token_set(candidate_name)
        candidate_core_tokens = candidate_tokens | _token_set(str(candidate_features.get("canonical_name") or ""))
        genericity = _genericity_for_candidate(candidate)
        row: dict[str, float | int | str] = {
            "quota_id": str(candidate.get("quota_id", "") or ""),
            "province": str(item.get("_resolved_province") or item.get("province") or context.get("province") or ""),
            "candidate_index": index,
            "manual_structured_score": safe_float(candidate.get("manual_structured_score"), 0.0),
            "manual_param_score": safe_float(candidate.get("param_score"), 0.0),
            "manual_logic_score": safe_float(candidate.get("logic_score"), 0.5),
            "manual_feature_alignment_score": safe_float(candidate.get("feature_alignment_score"), 0.5),
            "candidate_genericity_index": genericity["genericity_index"],
            "candidate_success_ratio": genericity["success_ratio"],
            "candidate_retrieval_popularity": genericity["retrieval_popularity"],
            "candidate_specificity_score": genericity["specificity_score"],
            "bm25_rank": bm25_ranks[index],
            "dense_rank": dense_ranks[index],
            "rrf_rank": rrf_ranks[index],
            "hybrid_rank": hybrid_ranks[index],
            "hybrid_zscore": hybrid_z[index],
            "semantic_rerank_zscore": semantic_z[index],
            "spec_rerank_zscore": spec_z[index],
            "param_score_zscore": param_z[index],
            "logic_score_zscore": logic_z[index],
            "feature_alignment_zscore": feature_z[index],
            "delta_to_group_best_hybrid": best_hybrid - hybrid_scores[index],
            "delta_to_group_best_semantic": best_semantic - semantic_scores[index],
            "delta_to_group_best_spec": best_spec - spec_scores[index],
            "delta_to_group_best_param": best_param - param_scores[index],
            "family_competitor_count": max(family_counts.get(_family_key(candidate), 0) - 1, 0),
            "group_ambiguity_score": group_ambiguity,
            "query_param_coverage": _query_param_coverage(query_params),
            "candidate_struct_coverage": sum(
                1 for field in STRUCT_FIELDS if str(candidate_features.get(field) or "").strip()
            ) / max(len(STRUCT_FIELDS), 1),
        }

        for field in STRUCT_FIELDS:
            query_value = query_struct_values[field]
            candidate_value = str(candidate_features.get(field) or "").strip()
            match, conflict, confidence = _field_confidence(query_value, candidate_value)
            row[f"q_has_{field}"] = int(bool(query_value))
            row[f"d_has_{field}"] = int(bool(candidate_value))
            row[f"{field}_match"] = match
            row[f"{field}_conflict"] = conflict
            row[f"{field}_confidence"] = confidence

        candidate_canonical_tokens = _token_set(
            " ".join(
                str(candidate_features.get(key) or "")
                for key in ("canonical_name", "family", "entity", "material", "install_method", "connection", "system")
            )
        )
        query_canonical_tokens = _token_set(
            " ".join(
                str(query_features.get(key) or "")
                for key in ("canonical_name", "family", "entity", "material", "install_method", "connection", "system")
            )
        )
        row["core_term_bigram_jaccard"] = _jaccard(query_core_bigrams, _bigram_set(list(candidate_core_tokens)))
        row["core_term_overlap_count"] = len(query_core_tokens & candidate_core_tokens)
        row["raw_name_token_coverage"] = _coverage(raw_query_name_tokens, candidate_tokens)
        row["query_token_in_candidate_ratio"] = _coverage(search_query_tokens, candidate_tokens)
        row["candidate_token_in_query_ratio"] = _coverage(candidate_tokens, search_query_tokens)
        row["canonical_term_coverage"] = _coverage(query_canonical_tokens, candidate_canonical_tokens)
        row["material_term_overlap"] = _coverage(
            _token_set(str(query_features.get("material") or "")),
            _token_set(str(candidate_features.get("material") or "")),
        )
        row["install_method_term_overlap"] = _coverage(
            _token_set(str(query_features.get("install_method") or "")),
            _token_set(str(candidate_features.get("install_method") or "")),
        )

        for param_name, aliases in PARAM_SPECS.items():
            query_value = _get_param_value(query_params, aliases)
            candidate_value = _get_param_value(candidate_params, aliases)
            candidate_scalar = _value_for_gap(candidate_value)
            if isinstance(query_value, tuple):
                query_scalar = _value_for_gap(query_value)
            else:
                query_scalar = query_value
            missing_pattern = _encode_missing_pattern(query_scalar, candidate_value)
            row[f"q_has_{param_name}"] = int(query_scalar is not None)
            row[f"d_has_{param_name}"] = int(candidate_value is not None)
            row[f"{param_name}_missing_pattern"] = missing_pattern
            row[f"{param_name}_in_range"] = _is_in_range(query_scalar, candidate_value)
            if query_scalar is None or candidate_value is None:
                row[f"{param_name}_abs_gap"] = -1.0
                row[f"{param_name}_rel_gap"] = -1.0
                row[f"{param_name}_exact_match"] = 0
                row[f"{param_name}_direction"] = 2
                row[f"{param_name}_tier_delta"] = -99
                row[f"{param_name}_is_upward_nearest"] = 0
                continue
            abs_gap = abs(candidate_scalar - query_scalar)
            rel_gap = abs_gap / max(abs(query_scalar), 1.0)
            row[f"{param_name}_abs_gap"] = abs_gap
            row[f"{param_name}_rel_gap"] = rel_gap
            row[f"{param_name}_exact_match"] = int(abs_gap <= 1e-9 or row[f"{param_name}_in_range"] == 1)
            if abs_gap <= 1e-9 or row[f"{param_name}_in_range"] == 1:
                row[f"{param_name}_direction"] = 0
            else:
                row[f"{param_name}_direction"] = 1 if candidate_scalar > query_scalar else -1
            available_values = sorted(
                value for value in param_candidate_values.get(param_name, []) if value is not None
            )
            if available_values:
                insertion = 0
                while insertion < len(available_values) and available_values[insertion] < query_scalar:
                    insertion += 1
                candidate_pos = min(
                    range(len(available_values)),
                    key=lambda idx: (abs(available_values[idx] - candidate_scalar), available_values[idx]),
                )
                row[f"{param_name}_tier_delta"] = candidate_pos - insertion
                upward_values = [value for value in available_values if value >= query_scalar]
                row[f"{param_name}_is_upward_nearest"] = int(
                    bool(upward_values) and abs(upward_values[0] - candidate_scalar) <= 1e-9
                )
            else:
                row[f"{param_name}_tier_delta"] = 0
                row[f"{param_name}_is_upward_nearest"] = 0

        same_family_candidates = [
            peer for peer in candidates if _family_key(peer) == _family_key(candidate)
        ]
        same_family_param_scores = [safe_float(peer.get("param_score"), 0.0) for peer in same_family_candidates]
        same_family_semantic_scores = [
            safe_float(peer.get("semantic_rerank_score", peer.get("vector_score", 0.0)), 0.0)
            for peer in same_family_candidates
        ]
        current_param = safe_float(candidate.get("param_score"), 0.0)
        current_semantic = safe_float(candidate.get("semantic_rerank_score", candidate.get("vector_score", 0.0)), 0.0)
        row["delta_to_best_same_family_param"] = max(same_family_param_scores, default=current_param) - current_param
        row["within_family_rank_by_param"] = 1 + sum(score > current_param for score in same_family_param_scores)
        row["within_family_rank_by_semantic"] = 1 + sum(score > current_semantic for score in same_family_semantic_scores)

        features_by_candidate.append(row)
    return features_by_candidate


__all__ = [
    "PARAM_SPECS",
    "extract_group_features",
    "_compute_zscores",
    "_load_genericity_stats",
]
