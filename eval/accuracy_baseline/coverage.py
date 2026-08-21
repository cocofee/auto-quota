from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .contracts import DatasetKind, EvalCase
from .fingerprints import province_query_fingerprint, query_fingerprint

_MINIMUM_CHECKS = {
    "min_cases": "case_count",
    "min_provinces": "province_count",
    "min_source_families": "source_family_count",
    "min_projects": "project_count",
    "min_specialties": "specialty_count",
    "min_splits": "split_count",
}

_MAXIMUM_CHECKS = {
    "max_dominant_province_share": "dominant_province_share",
    "max_dominant_source_family_share": "dominant_source_family_share",
    "max_dominant_project_share": "dominant_project_share",
    "max_dominant_specialty_share": "dominant_specialty_share",
    "max_cross_split_query_overlap": "cross_split_query_overlap_count",
    "max_cross_split_source_family_overlap": "cross_split_source_family_overlap_count",
    "max_cross_split_project_overlap": "cross_split_project_overlap_count",
    "max_cross_split_province_overlap": "cross_split_province_overlap_count",
}

_REQUIRED_FIELD_CHECKS = {
    "require_nonempty_source_family": "missing_source_family_count",
    "require_nonempty_project": "missing_project_count",
    "require_nonempty_specialty": "missing_specialty_count",
    "require_nonempty_split": "missing_split_count",
}

_CONTRACT_TEXT_REQUIREMENTS = {
    "contract_version",
    "approval_reference",
    "target_surface",
}

_CONTRACT_BOOLEAN_REQUIREMENTS = {
    "approved_for_system_baseline",
}

_SUPPORTED_REQUIREMENTS = {
    *_MINIMUM_CHECKS,
    *_MAXIMUM_CHECKS,
    *_REQUIRED_FIELD_CHECKS,
    *_CONTRACT_TEXT_REQUIREMENTS,
    *_CONTRACT_BOOLEAN_REQUIREMENTS,
}

_SYSTEM_BASELINE_MINIMUMS = {
    "min_cases": 2,
    "min_provinces": 2,
    "min_source_families": 2,
    "min_projects": 2,
    "min_specialties": 2,
    "min_splits": 2,
}

_DOMINANT_SHARE_REQUIREMENTS = {
    "max_dominant_province_share",
    "max_dominant_source_family_share",
    "max_dominant_project_share",
    "max_dominant_specialty_share",
}

_STRICT_OVERLAP_REQUIREMENTS = {
    "max_cross_split_query_overlap",
    "max_cross_split_project_overlap",
}

_STRATIFIED_OVERLAP_REQUIREMENTS = {
    "max_cross_split_source_family_overlap",
    "max_cross_split_province_overlap",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _dominant(counter: Counter[str], total: int) -> tuple[str, float]:
    if not counter or total <= 0:
        return "", 0.0
    value, count = min(counter.items(), key=lambda item: (-item[1], item[0]))
    return value, round(count / total, 6)


def _cross_split_overlap(
    cases: Sequence[EvalCase],
    value_getter: Callable[[EvalCase], str],
) -> tuple[int, list[str]]:
    value_splits: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        value = _clean(value_getter(case))
        split = _clean(case.split)
        if value and split:
            value_splits[value].add(split)
    overlaps = sorted(value for value, splits in value_splits.items() if len(splits) > 1)
    return len(overlaps), overlaps[:20]


def _observed_coverage(cases: Sequence[EvalCase]) -> dict[str, Any]:
    province_counts = Counter(_clean(case.province) for case in cases if _clean(case.province))
    source_family_counts = Counter(
        _clean(case.source_family) for case in cases if _clean(case.source_family)
    )
    project_counts = Counter(_clean(case.project_id) for case in cases if _clean(case.project_id))
    specialty_counts = Counter(_clean(case.specialty) for case in cases if _clean(case.specialty))
    split_counts = Counter(_clean(case.split) for case in cases if _clean(case.split))
    case_count = len(cases)

    dominant_province, dominant_province_share = _dominant(province_counts, case_count)
    dominant_source_family, dominant_source_family_share = _dominant(source_family_counts, case_count)
    dominant_project, dominant_project_share = _dominant(project_counts, case_count)
    dominant_specialty, dominant_specialty_share = _dominant(specialty_counts, case_count)

    query_overlap_count, query_overlap_examples = _cross_split_overlap(
        cases,
        lambda case: query_fingerprint(case.query_text),
    )
    province_query_overlap_count, province_query_overlap_examples = _cross_split_overlap(
        cases,
        lambda case: province_query_fingerprint(case.province, case.query_text),
    )
    source_overlap_count, source_overlap_examples = _cross_split_overlap(
        cases,
        lambda case: case.source_family,
    )
    project_overlap_count, project_overlap_examples = _cross_split_overlap(
        cases,
        lambda case: case.project_id,
    )
    province_overlap_count, province_overlap_examples = _cross_split_overlap(
        cases,
        lambda case: case.province,
    )

    return {
        "case_count": case_count,
        "province_count": len(province_counts),
        "source_family_count": len(source_family_counts),
        "project_count": len(project_counts),
        "specialty_count": len(specialty_counts),
        "split_count": len(split_counts),
        "missing_source_family_count": sum(not _clean(case.source_family) for case in cases),
        "missing_project_count": sum(not _clean(case.project_id) for case in cases),
        "missing_specialty_count": sum(not _clean(case.specialty) for case in cases),
        "missing_split_count": sum(not _clean(case.split) for case in cases),
        "dominant_province": dominant_province,
        "dominant_province_share": dominant_province_share,
        "dominant_source_family": dominant_source_family,
        "dominant_source_family_share": dominant_source_family_share,
        "dominant_project": dominant_project,
        "dominant_project_share": dominant_project_share,
        "dominant_specialty": dominant_specialty,
        "dominant_specialty_share": dominant_specialty_share,
        "cross_split_query_overlap_count": query_overlap_count,
        "cross_split_query_overlap_examples": query_overlap_examples,
        "cross_split_province_query_overlap_count": province_query_overlap_count,
        "cross_split_province_query_overlap_examples": province_query_overlap_examples,
        "cross_split_source_family_overlap_count": source_overlap_count,
        "cross_split_source_family_overlap_examples": source_overlap_examples,
        "cross_split_project_overlap_count": project_overlap_count,
        "cross_split_project_overlap_examples": project_overlap_examples,
        "cross_split_province_overlap_count": province_overlap_count,
        "cross_split_province_overlap_examples": province_overlap_examples,
    }


def _coverage_contract_errors(requirements: Mapping[str, Any]) -> list[str]:
    errors = [
        f"unknown_requirement:{name}"
        for name in sorted(set(requirements) - _SUPPORTED_REQUIREMENTS)
    ]
    errors.extend(
        f"missing_requirement:{name}"
        for name in sorted(_SUPPORTED_REQUIREMENTS - set(requirements))
    )

    for name, minimum in _SYSTEM_BASELINE_MINIMUMS.items():
        if name not in requirements:
            continue
        value = requirements.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            errors.append(f"invalid_requirement:{name}")

    for name in _DOMINANT_SHARE_REQUIREMENTS:
        if name not in requirements:
            continue
        value = requirements.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"invalid_requirement:{name}")
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or not 0.0 <= numeric < 1.0:
            errors.append(f"invalid_requirement:{name}")

    for name in _STRICT_OVERLAP_REQUIREMENTS:
        if name not in requirements:
            continue
        value = requirements.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            errors.append(f"invalid_requirement:{name}")

    for name in _STRATIFIED_OVERLAP_REQUIREMENTS:
        if name not in requirements:
            continue
        value = requirements.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"invalid_requirement:{name}")

    for name in _REQUIRED_FIELD_CHECKS:
        if name not in requirements:
            continue
        if requirements.get(name) is not True:
            errors.append(f"invalid_requirement:{name}")

    for name in _CONTRACT_TEXT_REQUIREMENTS:
        if name not in requirements:
            continue
        if not isinstance(requirements.get(name), str) or not str(requirements[name]).strip():
            errors.append(f"invalid_requirement:{name}")

    for name in _CONTRACT_BOOLEAN_REQUIREMENTS:
        if name not in requirements:
            continue
        if requirements.get(name) is not True:
            errors.append(f"invalid_requirement:{name}")

    return sorted(set(errors))


def summarize_dataset_coverage(
    cases: Sequence[EvalCase],
    dataset_kind: DatasetKind,
    requirements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observed = _observed_coverage(cases)
    if dataset_kind != DatasetKind.PRIMARY:
        scope = "diagnostic" if dataset_kind == DatasetKind.OSS_DIAGNOSTIC else "regression_slice"
        return {
            "scope": scope,
            "system_baseline_eligible": False,
            "coverage_contract_complete": False,
            "gate_status": "not_applicable",
            "reasons": [f"dataset_kind_{dataset_kind.value}_cannot_be_system_baseline"],
            "requirements": {},
            "checks": [],
            "observed": observed,
        }

    if not requirements:
        return {
            "scope": "slice",
            "system_baseline_eligible": False,
            "coverage_contract_complete": False,
            "gate_status": "not_evaluated",
            "reasons": ["system_coverage_contract_missing"],
            "requirements": {},
            "checks": [],
            "observed": observed,
        }

    contract_errors = _coverage_contract_errors(requirements)
    if contract_errors:
        return {
            "scope": "slice",
            "system_baseline_eligible": False,
            "coverage_contract_complete": False,
            "gate_status": "invalid_contract",
            "reasons": [f"coverage_contract_{error}" for error in contract_errors],
            "requirements": dict(requirements),
            "checks": [],
            "observed": observed,
        }

    checks: list[dict[str, Any]] = []
    for requirement, observed_key in _MINIMUM_CHECKS.items():
        if requirement not in requirements:
            continue
        expected = int(requirements[requirement])
        actual = int(observed[observed_key])
        checks.append({
            "requirement": requirement,
            "expected": expected,
            "actual": actual,
            "passed": actual >= expected,
        })

    for requirement, observed_key in _MAXIMUM_CHECKS.items():
        if requirement not in requirements:
            continue
        expected = float(requirements[requirement])
        actual = float(observed[observed_key])
        checks.append({
            "requirement": requirement,
            "expected": expected,
            "actual": actual,
            "passed": actual <= expected,
        })

    for requirement, observed_key in _REQUIRED_FIELD_CHECKS.items():
        if not bool(requirements.get(requirement, False)):
            continue
        actual = int(observed[observed_key])
        checks.append({
            "requirement": requirement,
            "expected": 0,
            "actual": actual,
            "passed": actual == 0,
        })

    passed = bool(checks) and all(check["passed"] for check in checks)
    failed = [check["requirement"] for check in checks if not check["passed"]]
    return {
        "scope": "system_baseline" if passed else "slice",
        "system_baseline_eligible": passed,
        "coverage_contract_complete": True,
        "gate_status": "passed" if passed else "failed",
        "reasons": [] if passed else [f"coverage_check_failed:{name}" for name in failed],
        "requirements": dict(requirements),
        "checks": checks,
        "observed": observed,
    }


__all__ = ["summarize_dataset_coverage"]
