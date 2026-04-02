"""
Constraint filter skeleton for the ranking refactor.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

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

_MATERIAL_GROUPS = {
    "steel": ("钢", "镀锌", "无缝钢", "焊接钢"),
    "stainless_steel": ("不锈钢",),
    "plastic": ("pvc", "upvc", "ppr", "pe", "hdpe", "塑料"),
    "copper": ("铜",),
    "cast_iron": ("铸铁",),
    "aluminum": ("铝",),
}


def _as_item_dict(query_item: Any) -> dict[str, Any]:
    if isinstance(query_item, dict):
        return dict(query_item)
    if query_item is None:
        return {}
    data = getattr(query_item, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    return {"value": query_item}


def _parse_numeric_params(payload: dict[str, Any]) -> dict[str, Any]:
    params = dict(payload.get("params") or {})
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


def _normalize_material(payload: dict[str, Any]) -> str:
    canonical_features = dict(
        payload.get("candidate_canonical_features")
        or payload.get("canonical_features")
        or {}
    )
    return str(
        payload.get("material")
        or canonical_features.get("material")
        or ""
    ).strip().lower()


def _material_group(value: str) -> str:
    material = str(value or "").strip().lower()
    if not material:
        return ""
    for group, markers in _MATERIAL_GROUPS.items():
        if any(marker in material for marker in markers):
            return group
    return material


class ConstraintFilter:
    """Applies configurable hard and soft ranking constraints."""

    def __init__(
        self,
        *,
        hard_main_param_rel_threshold: float = 0.50,
        soft_main_param_rel_threshold: float = 0.10,
        max_penalty: float = 0.50,
    ):
        self.hard_main_param_rel_threshold = max(safe_float(hard_main_param_rel_threshold, 0.50), 0.0)
        self.soft_main_param_rel_threshold = max(safe_float(soft_main_param_rel_threshold, 0.10), 0.0)
        self.max_penalty = max(safe_float(max_penalty, 0.50), 0.0)

    def filter(
        self,
        query_item: Any,
        scored_candidates: list[dict[str, Any]],
        *,
        top_k: int = 5,
    ) -> dict[str, Any]:
        item = _as_item_dict(query_item)
        limit = max(int(top_k or 0), 1)
        survivors: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for candidate in scored_candidates or []:
            candidate_copy = dict(candidate)
            hard_violations = self._check_hard_constraints(item, candidate_copy)
            if hard_violations:
                candidate_copy["rejected"] = True
                candidate_copy["rejection_reason"] = hard_violations
                candidate_copy["constraint_explanation"] = {
                    "status": "rejected",
                    "hard_violations": hard_violations,
                    "soft_violations": [],
                    "penalty": 0.0,
                }
                rejected.append(candidate_copy)
                continue

            soft_violations = self._check_soft_constraints(item, candidate_copy)
            penalty = self._compute_penalty(soft_violations)
            candidate_copy["soft_violations"] = soft_violations
            candidate_copy["penalty"] = penalty
            candidate_copy["filtered_score"] = float(candidate_copy.get("unified_score", 0.0) or 0.0) * (1.0 - penalty)
            candidate_copy["constraint_explanation"] = {
                "status": "accepted_with_penalty" if soft_violations else "accepted",
                "hard_violations": [],
                "soft_violations": soft_violations,
                "penalty": penalty,
            }
            survivors.append(candidate_copy)

        survivors.sort(key=lambda candidate: float(candidate.get("filtered_score", 0.0) or 0.0), reverse=True)
        return {
            "candidates": survivors[:limit],
            "rejected": rejected,
            "meta": self._build_meta(limit, survivors, rejected),
        }

    def _check_hard_constraints(self, item: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []

        item_specialty = str(item.get("specialty") or "").strip()
        candidate_specialty = str(candidate.get("specialty") or "").strip()
        if item_specialty and candidate_specialty and item_specialty != candidate_specialty:
            violations.append(
                {
                    "type": "specialty_mismatch",
                    "message": f"specialty mismatch: {item_specialty} vs {candidate_specialty}",
                }
            )

        item_unit = str(item.get("unit") or "").strip().lower()
        candidate_unit = str(candidate.get("unit") or "").strip().lower()
        if item_unit and candidate_unit and item_unit != candidate_unit:
            violations.append(
                {
                    "type": "unit_incompatible",
                    "message": f"unit incompatible: {item_unit} vs {candidate_unit}",
                }
            )

        rel_dist = self._main_param_rel_dist(item, candidate)
        if rel_dist is not None and rel_dist > self.hard_main_param_rel_threshold:
            violations.append(
                {
                    "type": "main_param_deviation",
                    "message": f"main param deviation: {rel_dist:.0%}",
                    "relative_distance": rel_dist,
                }
            )

        item_material = _normalize_material(item)
        candidate_material = _normalize_material(candidate)
        if item_material and candidate_material:
            item_group = _material_group(item_material)
            candidate_group = _material_group(candidate_material)
            if item_group and candidate_group and item_group != candidate_group:
                violations.append(
                    {
                        "type": "material_conflict",
                        "message": f"material conflict: {item_material} vs {candidate_material}",
                    }
                )

        return violations

    def _check_soft_constraints(self, item: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []

        rel_dist = self._main_param_rel_dist(item, candidate)
        if rel_dist is not None and self.soft_main_param_rel_threshold < rel_dist <= self.hard_main_param_rel_threshold:
            violations.append(
                {
                    "type": "main_param_minor_deviation",
                    "severity": min(rel_dist * 0.30, 0.20),
                    "message": f"main param deviation: {rel_dist:.0%}",
                    "relative_distance": rel_dist,
                }
            )

        item_material = _normalize_material(item)
        candidate_material = _normalize_material(candidate)
        if item_material and candidate_material and item_material != candidate_material:
            if _material_group(item_material) == _material_group(candidate_material):
                violations.append(
                    {
                        "type": "material_inexact",
                        "severity": 0.08,
                        "message": f"material not exact: {item_material} vs {candidate_material}",
                    }
                )

        if bool(candidate.get("candidate_scope_conflict", False)):
            violations.append(
                {
                    "type": "scope_conflict",
                    "severity": 0.06,
                    "message": "candidate scope conflict",
                }
            )

        return violations

    def _main_param_rel_dist(self, item: dict[str, Any], candidate: dict[str, Any]) -> float | None:
        item_value = _main_param_value(item)
        candidate_value = _main_param_value(candidate)
        if item_value is None or candidate_value is None:
            return None
        denominator = max(abs(item_value), 1e-9)
        return abs(item_value - candidate_value) / denominator

    def _compute_penalty(self, violations: list[dict[str, Any]]) -> float:
        total = sum(float(violation.get("severity", 0.0) or 0.0) for violation in (violations or []))
        return min(max(total, 0.0), self.max_penalty)

    def _build_meta(
        self,
        limit: int,
        survivors: list[dict[str, Any]],
        rejected: list[dict[str, Any]],
    ) -> dict[str, Any]:
        hard_counter = Counter()
        soft_counter = Counter()
        for candidate in rejected:
            for violation in candidate.get("rejection_reason") or []:
                hard_counter[str(violation.get("type") or "")] += 1
        for candidate in survivors:
            for violation in candidate.get("soft_violations") or []:
                soft_counter[str(violation.get("type") or "")] += 1
        return {
            "requested_top_k": limit,
            "survivor_count": len(survivors),
            "rejected_count": len(rejected),
            "hard_violation_counts": dict(hard_counter),
            "soft_violation_counts": dict(soft_counter),
            "top_filtered_quota_id": str(((survivors or [{}])[0]).get("quota_id", "") or "") if survivors else "",
        }

