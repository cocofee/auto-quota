from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from .contracts import EvalCase, LifecycleStage, ProviderResult, ProviderStatus


VALID_STATUSES = {ProviderStatus.OK, ProviderStatus.TRACE_INCOMPLETE}
RECALL_K_VALUES = (5, 10, 25, 80)
REQUIRED_CANDIDATE_FIELDS = (
    "quota_id",
    "name",
    "unit",
    "province",
    "provider",
    "source",
    "stage",
    "rank",
    "scores",
    "family",
    "book",
    "param_match",
    "hard_conflicts",
    "drop_reason",
    "raw_stage",
)
PROVIDER_FAILURE_EXCLUSIONS = {
    "missing_result",
    "duplicate_result",
    ProviderStatus.PROVIDER_ERROR.value,
}


def _oracle_completion_rank(case: EvalCase, result: ProviderResult) -> int | None:
    positions: dict[str, int] = {}
    for rank, quota_id in enumerate(result.retrieved_ids, start=1):
        positions.setdefault(quota_id, rank)
    if case.oracle_semantics.value == "all":
        if not case.oracle_set or not case.oracle_set <= positions.keys():
            return None
        return max(positions[quota_id] for quota_id in case.oracle_set)
    ranks = [positions[quota_id] for quota_id in case.oracle_set if quota_id in positions]
    return min(ranks) if ranks else None


def _is_top1_correct(case: EvalCase, quota_id: str) -> bool | None:
    if not case.top1_evaluable:
        return None
    return bool(quota_id and quota_id in case.oracle_set)


def _is_output_correct(case: EvalCase, result: ProviderResult) -> bool:
    return case.output_matches(result.final_quota_ids)


def _is_required_output_correct(case: EvalCase, result: ProviderResult) -> bool:
    return case.required_output_matches(result.final_quota_ids)


def _ranked_top_ids(result: ProviderResult, limit: int = 3) -> tuple[str, ...]:
    return result.ranked_quota_ids[:limit]


def _stage_flips(case: EvalCase, result: ProviderResult) -> dict[str, tuple[int, int]]:
    if not case.top1_evaluable:
        return {}
    flips: dict[str, tuple[int, int]] = {}
    previous_correct: bool | None = None
    for decision in result.decisions:
        current_correct = bool(_is_top1_correct(case, decision.top1_id))
        good = int(previous_correct is False and current_correct is True)
        bad = int(previous_correct is True and current_correct is False)
        flips[decision.name] = (good, bad)
        previous_correct = current_correct
    return flips


def _confidence_ece(rows: list[tuple[float, float]]) -> float:
    if not rows:
        return 0.0
    ece = 0.0
    for bin_index in range(10):
        lower = bin_index / 10.0
        upper = (bin_index + 1) / 10.0
        bucket = [
            row
            for row in rows
            if lower <= row[0] < upper or (bin_index == 9 and row[0] == 1.0)
        ]
        if not bucket:
            continue
        average_confidence = sum(row[0] for row in bucket) / len(bucket)
        average_accuracy = sum(row[1] for row in bucket) / len(bucket)
        ece += len(bucket) / len(rows) * abs(average_confidence - average_accuracy)
    return round(ece, 6)


def aggregate_provider_metrics(
    cases: Sequence[EvalCase],
    results: Sequence[ProviderResult],
    *,
    min_slice_size: int = 20,
) -> dict[str, Any]:
    case_counts = Counter(case.case_id for case in cases)
    duplicate_case_ids = sorted(case_id for case_id, count in case_counts.items() if count > 1)
    if duplicate_case_ids:
        raise ValueError(f"duplicate evaluation case ids: {','.join(duplicate_case_ids)}")
    case_by_id = {case.case_id: case for case in cases}
    result_buckets: dict[str, list[ProviderResult]] = defaultdict(list)
    exclusions: Counter[str] = Counter()
    for result in results:
        if result.case_id not in case_by_id:
            exclusions["unknown_case"] += 1
            continue
        result_buckets[result.case_id].append(result)

    valid: list[tuple[EvalCase, ProviderResult]] = []
    for case in cases:
        bucket = result_buckets.get(case.case_id, [])
        if not bucket:
            exclusions["missing_result"] += 1
            continue
        if len(bucket) > 1:
            exclusions["duplicate_result"] += 1
            continue
        result = bucket[0]
        if result.status not in VALID_STATUSES:
            exclusions[result.status.value] += 1
            continue
        valid.append((case, result))

    valid_by_id = {case.case_id: result for case, result in valid}
    rank_by_id = {
        case.case_id: _oracle_completion_rank(case, result)
        for case, result in valid
    }
    ranks = [rank_by_id.get(case.case_id) for case in cases]
    recalled = [rank for rank in ranks if rank is not None]
    final_correct = [
        bool(_is_top1_correct(case, result.final_top1_id))
        for case, result in valid
        if rank_by_id.get(case.case_id) is not None and case.top1_evaluable
    ]
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    lifecycle_valid = [
        (case, result) for case, result in valid if result.status == ProviderStatus.OK
    ]
    for case, result in lifecycle_valid:
        for stage_name, (good, bad) in _stage_flips(case, result).items():
            stage_counts[stage_name]["good_flip"] += good
            stage_counts[stage_name]["bad_flip"] += bad

    slices: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[EvalCase]] = defaultdict(list)
    for case in cases:
        grouped[f"province={case.province}"].append(case)
        grouped[f"source_family={case.source_family or '<empty>'}"].append(case)
        grouped[f"project={case.project_id or '<empty>'}"].append(case)
    for key, rows in sorted(grouped.items()):
        output_correct = sum(
            _is_output_correct(case, valid_by_id[case.case_id])
            for case in rows
            if case.case_id in valid_by_id
        )
        top1_rows = [case for case in rows if case.top1_evaluable]
        top1_correct = sum(
            bool(_is_top1_correct(case, valid_by_id[case.case_id].final_top1_id))
            for case in top1_rows
            if case.case_id in valid_by_id
        )
        slices[key] = {
            "count": len(rows),
            "valid_count": sum(case.case_id in valid_by_id for case in rows),
            "correct": output_correct,
            "final_output_accuracy": (
                round(output_correct / len(rows), 6) if len(rows) >= min_slice_size else None
            ),
            "top1": (
                round(top1_correct / len(top1_rows), 6)
                if len(top1_rows) >= min_slice_size
                else None
            ),
        }

    taxonomy_false_veto_keys: set[tuple[str, str]] = set()
    param_false_hard_fail_keys: set[tuple[str, str]] = set()
    route_loss_count = 0
    route_evaluable_count = 0
    candidate_field_present: Counter[str] = Counter()
    candidate_total = 0
    for case, result in lifecycle_valid:
        retrieved_stage = next(
            (
                stage
                for stage in result.lifecycle
                if stage.stage == LifecycleStage.RETRIEVED and stage.emitted
            ),
            None,
        )
        route_stage = next(
            (
                stage
                for stage in result.lifecycle
                if stage.stage == LifecycleStage.ROUTE_FILTERED and stage.emitted
            ),
            None,
        )
        if retrieved_stage and route_stage:
            retrieved_ids = {candidate.quota_id for candidate in retrieved_stage.candidates}
            route_ids = {candidate.quota_id for candidate in route_stage.candidates}
            if case.oracle_covered_by(retrieved_ids):
                route_evaluable_count += 1
                route_loss_count += int(not case.oracle_covered_by(route_ids))
        for stage in result.lifecycle:
            for candidate in stage.candidates:
                candidate_total += 1
                for field_name in REQUIRED_CANDIDATE_FIELDS:
                    value = getattr(candidate, field_name)
                    if value not in (None, "", (), {}):
                        candidate_field_present[field_name] += 1
                if candidate.quota_id not in case.oracle_set:
                    continue
                if "family_gate_hard_conflict" in candidate.hard_conflicts:
                    taxonomy_false_veto_keys.add((case.case_id, candidate.quota_id))
                if (
                    "param_hard_fail" in candidate.hard_conflicts
                    or candidate.drop_reason == "hard_param_fail"
                ):
                    param_false_hard_fail_keys.add((case.case_id, candidate.quota_id))

    denominator = len(cases)
    provider_failure_count = sum(
        exclusions[reason] for reason in PROVIDER_FAILURE_EXCLUSIONS
    )
    final_top1_evaluable = sum(case.top1_evaluable for case in cases)
    final_top1_hits = sum(
        bool(_is_top1_correct(case, result.final_top1_id))
        for case, result in valid
        if case.top1_evaluable
    )
    final_output_hits = sum(_is_output_correct(case, result) for case, result in valid)
    final_required_hits = sum(
        _is_required_output_correct(case, result) for case, result in valid
    )
    final_top3_hits = sum(
        bool(set(_ranked_top_ids(result, 3)) & case.oracle_set)
        for case, result in valid
        if case.top1_evaluable
    )
    refusal_count = sum(not result.final_top1_id for _, result in valid)
    calibration_rows = [
        (
            max(
                0.0,
                min(
                    1.0,
                    result.confidence / 100.0
                    if result.confidence > 1.0
                    else result.confidence,
                ),
            ),
            float(_is_output_correct(case, result)),
        )
        for case, result in valid
    ]
    return {
        "total_cases": len(cases),
        "valid_cases": len(valid),
        "system_denominator": denominator,
        "provider_failure_count": provider_failure_count,
        "provider_failure_rate": (
            round(provider_failure_count / denominator, 6) if denominator else 0.0
        ),
        "exclusions": dict(sorted(exclusions.items())),
        "recall_at": {
            str(k): (
                round(sum(rank is not None and rank <= k for rank in ranks) / denominator, 6)
                if denominator
                else 0.0
            )
            for k in RECALL_K_VALUES
        },
        "conditional_top1": (
            round(sum(final_correct) / len(final_correct), 6) if final_correct else 0.0
        ),
        "final_top1_evaluable_cases": final_top1_evaluable,
        "final_top1": (
            round(final_top1_hits / final_top1_evaluable, 6)
            if final_top1_evaluable
            else 0.0
        ),
        "final_top3_evaluable_cases": final_top1_evaluable,
        "final_top3": (
            round(final_top3_hits / final_top1_evaluable, 6)
            if final_top1_evaluable
            else 0.0
        ),
        "final_output_accuracy": (
            round(final_output_hits / denominator, 6) if denominator else 0.0
        ),
        "final_required_output_accuracy": (
            round(final_required_hits / denominator, 6) if denominator else 0.0
        ),
        "refusal_rate": round(refusal_count / denominator, 6) if denominator else 0.0,
        "confidence_ece": _confidence_ece(calibration_rows),
        "mrr": (
            round(sum(1.0 / rank for rank in recalled) / denominator, 6)
            if denominator
            else 0.0
        ),
        "stage_flips": {
            name: {
                "good_flip": counts["good_flip"],
                "bad_flip": counts["bad_flip"],
                "net_gain": counts["good_flip"] - counts["bad_flip"],
            }
            for name, counts in sorted(stage_counts.items())
        },
        "taxonomy_false_veto_count": len(taxonomy_false_veto_keys),
        "param_false_hard_fail_count": len(param_false_hard_fail_keys),
        "route_filter_oracle_loss_count": route_loss_count,
        "route_filter_oracle_loss_rate": (
            round(route_loss_count / route_evaluable_count, 6)
            if route_evaluable_count
            else None
        ),
        "candidate_contract_coverage": {
            field_name: (
                round(candidate_field_present[field_name] / candidate_total, 6)
                if candidate_total
                else 0.0
            )
            for field_name in REQUIRED_CANDIDATE_FIELDS
        },
        "trace_complete_rate": (
            round(
                sum(result.status == ProviderStatus.OK for _, result in valid) / denominator,
                6,
            )
            if denominator
            else 0.0
        ),
        "slices": slices,
    }


def compare_providers(
    cases: Sequence[EvalCase],
    provider_results: dict[str, Sequence[ProviderResult]],
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    result_maps: dict[str, dict[str, ProviderResult]] = {}
    for provider, results in provider_results.items():
        buckets: dict[str, list[ProviderResult]] = defaultdict(list)
        for result in results:
            buckets[result.case_id].append(result)
        result_maps[provider] = {
            case_id: bucket[0]
            for case_id, bucket in buckets.items()
            if len(bucket) == 1
        }
    production_provider = next(
        (
            name
            for name in ("search_core", "production", "production_e2e")
            if name in result_maps
        ),
        "production",
    )
    production_hits = 0
    goal_hits = 0
    union_hits = 0
    goal_unique = 0
    excluded = 0
    rows: list[dict[str, Any]] = []
    for case_id, case in sorted(case_by_id.items()):
        production = result_maps.get(production_provider, {}).get(case_id)
        goal = result_maps.get("goal_shadow", {}).get(case_id)
        if (
            production is None
            or goal is None
            or production.status not in VALID_STATUSES
            or goal.status not in VALID_STATUSES
        ):
            excluded += 1
            continue
        production_ids = set(production.retrieved_ids)
        goal_ids = set(goal.retrieved_ids)
        production_hit = case.oracle_covered_by(production_ids)
        goal_hit = case.oracle_covered_by(goal_ids)
        union_hit = case.oracle_covered_by(production_ids | goal_ids)
        production_hits += int(production_hit)
        goal_hits += int(goal_hit)
        union_hits += int(union_hit)
        goal_unique += int(goal_hit and not production_hit)
        rows.append(
            {
                "case_id": case_id,
                "production_recalled": production_hit,
                "goal_shadow_recalled": goal_hit,
                "union_recalled": union_hit,
                "goal_shadow_unique": goal_hit and not production_hit,
            }
        )
    total = len(rows)
    return {
        "total_cases": len(cases),
        "production_provider": production_provider,
        "comparable_cases": total,
        "excluded_cases": excluded,
        "production_recall": round(production_hits / total, 6) if total else 0.0,
        "goal_shadow_recall": round(goal_hits / total, 6) if total else 0.0,
        "union_recall": round(union_hits / total, 6) if total else 0.0,
        "goal_shadow_unique_recall_gain": goal_unique,
        "cases": rows,
    }
