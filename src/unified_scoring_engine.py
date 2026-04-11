"""
Unified scoring engine skeleton for the ranking refactor.
"""

from __future__ import annotations

import re
from typing import Any

from src.candidate_scoring import (
    compute_candidate_prior_score,
    has_exact_experience_anchor,
    has_exact_universal_kb_anchor,
)
from src.text_parser import parser as text_parser
from src.utils import safe_float

_MAIN_PARAM_ALIASES: tuple[tuple[str, ...], ...] = (
    ("dn", "conduit_dn"),
    ("cable_section", "cross_section"),
    ("kw", "power"),
    ("kva", "capacity"),
    ("ampere", "current"),
    ("circuits", "circuit_count"),
    ("port_count", "count_band"),
)


def _as_item_dict(query_item: Any) -> dict[str, Any]:
    if isinstance(query_item, dict):
        return dict(query_item)
    if query_item is None:
        return {}
    data = getattr(query_item, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    return {"value": query_item}


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _tokenize(text: Any) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,4}", normalized)


def _token_set(text: Any) -> set[str]:
    return {token for token in _tokenize(text) if token}


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


def _clip01(value: Any) -> float:
    return max(0.0, min(safe_float(value, 0.0), 1.0))


def _bigram_jaccard(left_tokens: list[str], right_tokens: list[str]) -> float:
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return 0.0
    left = {f"{left_tokens[index]}::{left_tokens[index + 1]}" for index in range(len(left_tokens) - 1)}
    right = {f"{right_tokens[index]}::{right_tokens[index + 1]}" for index in range(len(right_tokens) - 1)}
    return _jaccard(left, right)


def _parse_numeric_params(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    params = dict(payload.get("params") or {})
    if not params:
        name = " ".join(
            part
            for part in (
                payload.get("name", ""),
                payload.get("description", ""),
            )
            if str(part or "").strip()
        ).strip()
        if name:
            params = text_parser.parse(name)
    numeric_params = dict(payload.get("numeric_params") or {})
    params.update({key: value for key, value in numeric_params.items() if value not in (None, "")})
    return params


def _main_param_value(payload: dict[str, Any]) -> float | None:
    params = _parse_numeric_params(payload)
    direct_value = payload.get("main_param")
    if direct_value not in (None, ""):
        try:
            return float(direct_value)
        except (TypeError, ValueError):
            pass
    for aliases in _MAIN_PARAM_ALIASES:
        for alias in aliases:
            value = params.get(alias)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _canonical_features(payload: dict[str, Any], *, candidate: bool = False) -> dict[str, Any]:
    if candidate:
        features = payload.get("candidate_canonical_features") or payload.get("canonical_features") or {}
    else:
        features = payload.get("canonical_features") or {}
    return dict(features or {})


class FeatureExtractor:
    """Builds a higher-value feature set from existing ranking signals."""

    def extract(self, query_item: Any, candidate: dict[str, Any]) -> dict[str, Any]:
        item = _as_item_dict(query_item)
        candidate = dict(candidate or {})
        item_name = _normalize_text(item.get("name"))
        candidate_name = _normalize_text(candidate.get("name"))
        item_tokens = _tokenize(item_name)
        candidate_tokens = _tokenize(candidate_name)
        item_token_set = set(item_tokens)
        candidate_token_set = set(candidate_tokens)

        item_features = _canonical_features(item, candidate=False)
        candidate_features = _canonical_features(candidate, candidate=True)
        item_main_param = _main_param_value(item)
        candidate_main_param = _main_param_value(candidate)
        ltr_param = dict(candidate.get("_ltr_param") or {})

        query_token_in_candidate_ratio = _coverage(item_token_set, candidate_token_set)
        candidate_token_in_query_ratio = _coverage(candidate_token_set, item_token_set)
        token_jaccard = _jaccard(item_token_set, candidate_token_set)

        retrieval_rank = self._best_rank(candidate)
        retrieval_rank_score = 1.0 / retrieval_rank if retrieval_rank else 0.0

        main_param_exact = _clip01(
            ltr_param.get("param_main_exact", int(item_main_param is not None and candidate_main_param is not None and abs(item_main_param - candidate_main_param) <= 1e-9))
        )
        main_param_rel_score = self._main_param_rel_score(item_main_param, candidate_main_param, ltr_param)

        specialty_match = float(
            bool(
                item.get("specialty")
                and candidate.get("specialty")
                and str(item.get("specialty")).strip() == str(candidate.get("specialty")).strip()
            )
        )
        book_match = float(
            bool(
                item.get("book")
                and candidate.get("book")
                and str(item.get("book")).strip() == str(candidate.get("book")).strip()
            )
        )

        family_match, entity_match, system_match = self._canonical_match_scores(item_features, candidate_features)
        material_match = self._material_match_score(item, item_features, candidate, candidate_features, ltr_param)
        unit_match = self._unit_match_score(item, candidate)
        prior_score = self._normalize_prior_score(candidate)

        features = {
            # Retrieval
            "hybrid_score": _clip01(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0))),
            "rerank_score": _clip01(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0))),
            "active_rerank_score": _clip01(candidate.get("active_rerank_score", candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)))),
            "bm25_score": _clip01(candidate.get("bm25_score")),
            "vector_score": _clip01(candidate.get("vector_score")),
            "retrieval_rank": float(retrieval_rank or 0),
            "retrieval_rank_score": retrieval_rank_score,
            "source_count": float(len(candidate.get("sources") or [])),
            "has_bm25_signal": float(candidate.get("bm25_rank") is not None or candidate.get("bm25_score") is not None),
            "has_vector_signal": float(candidate.get("vector_rank") is not None or candidate.get("vector_score") is not None),
            # Text
            "name_exact_match": float(bool(item_name and candidate_name and item_name == candidate_name)),
            "token_jaccard": token_jaccard,
            "query_token_in_candidate_ratio": query_token_in_candidate_ratio,
            "candidate_token_in_query_ratio": candidate_token_in_query_ratio,
            "core_term_bigram_jaccard": _bigram_jaccard(item_tokens, candidate_tokens),
            "name_bonus": _clip01(candidate.get("name_bonus")),
            # Params
            "param_score": _clip01(candidate.get("param_score")),
            "param_match": float(bool(candidate.get("param_match", False))),
            "param_tier_score": self._param_tier_score(candidate.get("param_tier")),
            "has_main_param": float(item_main_param is not None),
            "main_param_exact": main_param_exact,
            "main_param_rel_score": main_param_rel_score,
            "material_match": material_match,
            "unit_match": unit_match,
            # Classification
            "feature_alignment_score": _clip01(candidate.get("feature_alignment_score", 0.0)),
            "family_match": family_match,
            "entity_match": entity_match,
            "system_match": system_match,
            "specialty_match": specialty_match,
            "book_match": book_match,
            "family_gate_positive": _clip01(max(safe_float(candidate.get("family_gate_score"), 0.0), 0.0) / 1.5),
            "exact_anchor_support": min(int(candidate.get("feature_alignment_exact_anchor_count", 0) or 0), 3) / 3.0,
            # Context / logic
            "context_alignment_score": _clip01(candidate.get("context_alignment_score", 0.0)),
            "logic_score": _clip01(candidate.get("logic_score", 0.0)),
            "scope_match": _clip01(candidate.get("candidate_scope_match", 0.0)),
            "exact_primary_match": float(bool(candidate.get("logic_exact_primary_match", False))),
            "scope_conflict": float(bool(candidate.get("candidate_scope_conflict", False))),
            # Prior knowledge
            "prior_score": prior_score,
            "exact_experience_anchor": float(has_exact_experience_anchor(candidate)),
            "exact_kb_anchor": float(has_exact_universal_kb_anchor(candidate)),
            "from_rule": float(bool(candidate.get("from_rule", False))),
            "from_experience": float(bool(candidate.get("from_experience", False) or "experience" in list(candidate.get("knowledge_prior_sources") or []))),
        }
        return features

    def _best_rank(self, candidate: dict[str, Any]) -> int | None:
        ranks = []
        for key in ("rank", "hybrid_rank", "rrf_rank", "bm25_rank", "vector_rank", "dense_rank"):
            value = candidate.get(key)
            try:
                if value is not None:
                    ranks.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ranks:
            return None
        return max(min(ranks), 1)

    def _main_param_rel_score(
        self,
        item_value: float | None,
        candidate_value: float | None,
        ltr_param: dict[str, Any],
    ) -> float:
        if ltr_param.get("param_main_rel_dist") is not None:
            return _clip01(1.0 - safe_float(ltr_param.get("param_main_rel_dist"), 1.0))
        if item_value is None or candidate_value is None:
            return 0.0
        denominator = max(abs(item_value), 1e-9)
        return _clip01(1.0 - abs(item_value - candidate_value) / denominator)

    def _param_tier_score(self, value: Any) -> float:
        tier = int(safe_float(value, 0.0))
        if tier >= 2:
            return 1.0
        if tier == 1:
            return 0.65
        return 0.35

    def _material_match_score(
        self,
        item: dict[str, Any],
        item_features: dict[str, Any],
        candidate: dict[str, Any],
        candidate_features: dict[str, Any],
        ltr_param: dict[str, Any],
    ) -> float:
        if ltr_param.get("param_material_match") is not None:
            return _clip01(ltr_param.get("param_material_match"))
        item_material = str(
            item.get("material")
            or item_features.get("material")
            or ""
        ).strip().lower()
        candidate_material = str(
            candidate.get("material")
            or candidate_features.get("material")
            or ""
        ).strip().lower()
        if not item_material or not candidate_material:
            return 0.0
        return float(item_material == candidate_material)

    def _unit_match_score(self, item: dict[str, Any], candidate: dict[str, Any]) -> float:
        item_unit = str(item.get("unit") or "").strip().lower()
        candidate_unit = str(candidate.get("unit") or "").strip().lower()
        if not item_unit or not candidate_unit:
            return 0.0
        return float(item_unit == candidate_unit)

    def _normalize_prior_score(self, candidate: dict[str, Any]) -> float:
        raw = compute_candidate_prior_score(candidate)
        if raw <= 0:
            return 0.0
        return _clip01(raw / 0.12)

    def _canonical_match_scores(
        self,
        item_features: dict[str, Any],
        candidate_features: dict[str, Any],
    ) -> tuple[float, float, float]:
        def _match(field: str) -> float:
            left = str(item_features.get(field) or "").strip().lower()
            right = str(candidate_features.get(field) or "").strip().lower()
            if not left or not right:
                return 0.0
            return float(left == right)

        return _match("family"), _match("entity"), _match("system")


class AdaptiveWeightCalculator:
    """Returns a stable, template-like weight map for the skeleton stage."""

    _TEMPLATES = {
        "default": {
            "text_similarity": 0.35,
            "param_match": 0.30,
            "classification": 0.20,
            "prior_knowledge": 0.10,
            "context": 0.05,
        },
        "param_heavy": {
            "text_similarity": 0.25,
            "param_match": 0.45,
            "classification": 0.15,
            "prior_knowledge": 0.10,
            "context": 0.05,
        },
        "prior_heavy": {
            "text_similarity": 0.20,
            "param_match": 0.20,
            "classification": 0.15,
            "prior_knowledge": 0.35,
            "context": 0.10,
        },
        "classification_heavy": {
            "text_similarity": 0.24,
            "param_match": 0.20,
            "classification": 0.32,
            "prior_knowledge": 0.14,
            "context": 0.10,
        },
        "dirty_short_text": {
            "text_similarity": 0.18,
            "param_match": 0.22,
            "classification": 0.28,
            "prior_knowledge": 0.12,
            "context": 0.20,
        },
    }

    def calculate_weight_profile(self, query_item: Any, features: dict[str, Any]) -> tuple[str, dict[str, float]]:
        item = _as_item_dict(query_item)
        if self._has_exact_experience_path(item, features):
            return "prior_heavy", dict(self._TEMPLATES["prior_heavy"])
        if self._has_classification_anchor(features):
            return "classification_heavy", dict(self._TEMPLATES["classification_heavy"])
        if self._is_dirty_or_short_text(item):
            return "dirty_short_text", dict(self._TEMPLATES["dirty_short_text"])
        if self._has_main_param(item):
            return "param_heavy", dict(self._TEMPLATES["param_heavy"])
        return "default", dict(self._TEMPLATES["default"])

    def calculate_weights(self, query_item: Any, features: dict[str, Any]) -> dict[str, float]:
        _, weights = self.calculate_weight_profile(query_item, features)
        return weights

    def _has_main_param(self, item: dict[str, Any]) -> bool:
        return bool(item.get("main_param") or item.get("params"))

    def _has_exact_experience_path(self, item: dict[str, Any], features: dict[str, Any]) -> bool:
        if _clip01(features.get("exact_experience_anchor", 0.0)) >= 1.0:
            return True
        if _clip01(features.get("from_experience", 0.0)) <= 0.0:
            return False
        match_source = str(item.get("match_source") or item.get("_match_source") or "").strip().lower()
        if "experience" in match_source and "exact" in match_source:
            return True
        return _clip01(features.get("prior_score", 0.0)) >= 0.85

    def _has_classification_anchor(self, features: dict[str, Any]) -> bool:
        exact_anchor_support = _clip01(features.get("exact_anchor_support", 0.0))
        if exact_anchor_support >= (1.0 / 3.0):
            return True
        feature_alignment_score = _clip01(features.get("feature_alignment_score", 0.0))
        family_match = _clip01(features.get("family_match", 0.0))
        specialty_match = _clip01(features.get("specialty_match", 0.0))
        book_match = _clip01(features.get("book_match", 0.0))
        return feature_alignment_score >= 0.85 and max(family_match, specialty_match, book_match) >= 1.0

    def _is_dirty_or_short_text(self, item: dict[str, Any]) -> bool:
        input_gate = dict(item.get("_input_gate") or {})
        if input_gate.get("is_dirty_code"):
            return True
        if item.get("_is_ambiguous_short"):
            return True

        name_text = _normalize_text(item.get("name"))
        name_tokens = _tokenize(name_text)
        desc_tokens = _tokenize(item.get("description"))
        has_main_param = self._has_main_param(item)
        if len(name_tokens) <= 1 and len(name_text) <= 2 and not desc_tokens and not has_main_param:
            return True
        reason_tags = {str(tag).strip().lower() for tag in list(input_gate.get("reason_tags") or []) if str(tag).strip()}
        return "weak_text" in reason_tags or str(input_gate.get("primary_reason") or "").strip().lower() == "dirty_input"


class UnifiedScoringEngine:
    """Scores candidates with richer placeholder categories."""

    def __init__(
        self,
        *,
        feature_extractor: FeatureExtractor | None = None,
        weight_calculator: AdaptiveWeightCalculator | None = None,
    ):
        self.feature_extractor = feature_extractor or FeatureExtractor()
        self.weight_calculator = weight_calculator or AdaptiveWeightCalculator()

    def score(self, query_item: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored_candidates: list[dict[str, Any]] = []
        for candidate in candidates or []:
            candidate_copy = dict(candidate)
            features = self.feature_extractor.extract(query_item, candidate_copy)
            if hasattr(self.weight_calculator, "calculate_weight_profile"):
                weight_template, weights = self.weight_calculator.calculate_weight_profile(query_item, features)
            else:
                weight_template = ""
                weights = self.weight_calculator.calculate_weights(query_item, features)
            category_scores = self._compute_category_scores(features)
            final_score = sum(
                float(category_scores.get(category, 0.0)) * float(weights.get(category, 0.0))
                for category in weights
            )
            candidate_copy["features"] = features
            candidate_copy["weights"] = weights
            candidate_copy["weight_template"] = weight_template
            candidate_copy["category_scores"] = category_scores
            candidate_copy["unified_score"] = final_score
            candidate_copy["confidence_base"] = self._estimate_base_confidence(final_score, features, weight_template)
            candidate_copy["confidence"] = candidate_copy["confidence_base"]
            candidate_copy["explanation"] = self._build_explanation(weights, category_scores)
            scored_candidates.append(candidate_copy)
        scored_candidates.sort(key=lambda candidate: float(candidate.get("unified_score", 0.0) or 0.0), reverse=True)
        return scored_candidates

    def _compute_category_scores(self, features: dict[str, Any]) -> dict[str, float]:
        text_similarity = min(
            1.0,
            features["name_exact_match"] * 0.30
            + features["token_jaccard"] * 0.15
            + features["query_token_in_candidate_ratio"] * 0.15
            + features["candidate_token_in_query_ratio"] * 0.10
            + features["core_term_bigram_jaccard"] * 0.10
            + features["name_bonus"] * 0.05
            + features["active_rerank_score"] * 0.10
            + features["retrieval_rank_score"] * 0.05,
        )
        param_match = min(
            1.0,
            features["param_match"] * 0.20
            + features["param_score"] * 0.20
            + features["param_tier_score"] * 0.10
            + features["main_param_exact"] * 0.20
            + features["main_param_rel_score"] * 0.15
            + features["material_match"] * 0.10
            + features["unit_match"] * 0.05,
        )
        classification = min(
            1.0,
            features["feature_alignment_score"] * 0.25
            + features["family_match"] * 0.20
            + features["entity_match"] * 0.10
            + features["system_match"] * 0.10
            + features["specialty_match"] * 0.10
            + features["book_match"] * 0.10
            + features["family_gate_positive"] * 0.05
            + features["exact_anchor_support"] * 0.10,
        )
        prior_knowledge = min(
            1.0,
            features["prior_score"] * 0.35
            + features["exact_experience_anchor"] * 0.30
            + features["exact_kb_anchor"] * 0.15
            + features["from_experience"] * 0.10
            + features["from_rule"] * 0.10,
        )
        context = min(
            1.0,
            max(
                0.0,
                features["context_alignment_score"] * 0.35
                + features["logic_score"] * 0.25
                + features["scope_match"] * 0.15
                + features["exact_primary_match"] * 0.15
                + features["system_match"] * 0.10
                - features["scope_conflict"] * 0.15,
            ),
        )
        return {
            "text_similarity": text_similarity,
            "param_match": param_match,
            "classification": classification,
            "prior_knowledge": prior_knowledge,
            "context": context,
        }

    def _estimate_base_confidence(
        self,
        final_score: float,
        features: dict[str, Any],
        weight_template: str,
    ) -> float:
        confidence = 0.10 + _clip01(final_score) * 0.70
        confidence += _clip01(features.get("exact_experience_anchor", 0.0)) * 0.10
        confidence += _clip01(features.get("exact_anchor_support", 0.0)) * 0.08
        confidence += _clip01(features.get("main_param_exact", 0.0)) * 0.04
        confidence += _clip01(features.get("exact_primary_match", 0.0)) * 0.03
        if str(weight_template or "").strip().lower() == "dirty_short_text":
            confidence -= 0.15
        if _clip01(features.get("scope_conflict", 0.0)) > 0.0:
            confidence -= 0.08
        return _clip01(confidence)

    def _build_explanation(self, weights: dict[str, float], category_scores: dict[str, Any]) -> dict[str, Any]:
        contributions = []
        for category, weight in weights.items():
            score = float(category_scores.get(category, 0.0) or 0.0)
            contributions.append(
                {
                    "category": category,
                    "score": score,
                    "weight": float(weight or 0.0),
                    "contribution": score * float(weight or 0.0),
                }
            )
        contributions.sort(key=lambda row: row["contribution"], reverse=True)
        return {
            "weights": dict(weights),
            "category_scores": dict(category_scores),
            "contributions": contributions,
            "top_driver": contributions[0]["category"] if contributions else "",
            "summary": "skeleton_unified_scoring",
        }
