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


def _first_oracle_rank(case: EvalCase, result: ProviderResult) -> int | None:
    for rank, quota_id in enumerate(result.retrieved_ids, start=1):
        if quota_id in case.oracle_set:
            return rank
    return None


def _is_correct(case: EvalCase, quota_id: str) -> bool:
    return bool(quota_id and quota_id in case.oracle_set)


def _ranked_top_ids(result: ProviderResult, limit: int = 3) -> tuple[str, ...]:
    for target_stage in (
        LifecycleStage.VALIDATED,
        LifecycleStage.RERANKED,
        LifecycleStage.RETRIEVED,
    ):
        stage = next(
            (
                value
                for value in result.lifecycle
                if value.stage == target_stage and value.emitted and value.candidates
            ),
            None,
        )
        if stage is not None:
            ordered = sorted(
                stage.candidates,
                key=lambda candidate: (
                    candidate.rank is None,
                    candidate.rank or 10**9,
                    candidate.quota_id,
                ),
            )
            return tuple(candidate.quota_id for candidate in ordered[:limit])
    return result.final_quota_ids[:limit]


def _stage_flips(case: EvalCase, result: ProviderResult) -> dict[str, tuple[int, int]]:
    flips: dict[str, tuple[int, int]] = {}
    previous_correct: bool | None = None
    for decision in result.decisions:
        current_correct = _is_correct(case, decision.top1_id)
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
    case_by_id = {case.case_id: case for case in cases}
    valid: list[tuple[EvalCase, ProviderResult]] = []
    exclusions: Counter[str] = Counter()
    for result in results:
        case = case_by_id.get(result.case_id)
        if case is None:
            exclusions["unknown_case"] += 1
        elif result.status not in VALID_STATUSES:
            exclusions[result.status.value] += 1
        else:
            valid.append((case, result))

    ranks = [_first_oracle_rank(case, result) for case, result in valid]
    recalled = [rank for rank in ranks if rank is not None]
    final_correct = [
        _is_correct(case, result.final_top1_id)
        for case, result in valid
        if _first_oracle_rank(case, result) is not None
    ]
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for case, result in valid:
        for stage_name, (good, bad) in _stage_flips(case, result).items():
            stage_counts[stage_name]["good_flip"] += good
            stage_counts[stage_name]["bad_flip"] += bad

    slices: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[tuple[EvalCase, ProviderResult]]] = defaultdict(list)
    for case, result in valid:
        grouped[f"province={case.province}"].append((case, result))
        grouped[f"source_family={case.source_family or '<empty>'}"].append((case, result))
        grouped[f"project={case.project_id or '<empty>'}"].append((case, result))
    for key, rows in sorted(grouped.items()):
        correct = sum(_is_correct(case, result.final_top1_id) for case, result in rows)
        slices[key] = {
            "count": len(rows),
            "correct": correct,
            "top1": (
                round(correct / len(rows), 6) if len(rows) >= min_slice_size else None
            ),
        }

    taxonomy_false_veto_keys: set[tuple[str, str]] = set()
    param_false_hard_fail_keys: set[tuple[str, str]] = set()
    route_loss_count = 0
    route_evaluable_count = 0
    candidate_field_present: Counter[str] = Counter()
    candidate_total = 0
    for case, result in valid:
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
            if retrieved_ids & case.oracle_set:
                route_evaluable_count += 1
                route_loss_count += int(not bool(route_ids & case.oracle_set))
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

    denominator = len(valid)
    final_top1_hits = sum(_is_correct(case, result.final_top1_id) for case, result in valid)
    final_top3_hits = sum(
        bool(set(_ranked_top_ids(result, 3)) & case.oracle_set)
        for case, result in valid
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
            float(_is_correct(case, result.final_top1_id)),
        )
        for case, result in valid
        if result.final_top1_id
    ]
    return {
        "total_cases": len(cases),
        "valid_cases": denominator,
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
        "final_top1": round(final_top1_hits / denominator, 6) if denominator else 0.0,
        "final_top3": round(final_top3_hits / denominator, 6) if denominator else 0.0,
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
    result_maps = {
        provider: {result.case_id: result for result in results}
        for provider, results in provider_results.items()
    }
    production_hits = 0
    goal_hits = 0
    union_hits = 0
    goal_unique = 0
    excluded = 0
    rows: list[dict[str, Any]] = []
    for case_id, case in sorted(case_by_id.items()):
        production = result_maps.get("production", {}).get(case_id)
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
        production_hit = bool(production_ids & case.oracle_set)
        goal_hit = bool(goal_ids & case.oracle_set)
        union_hit = bool((production_ids | goal_ids) & case.oracle_set)
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
        "comparable_cases": total,
        "excluded_cases": excluded,
        "production_recall": round(production_hits / total, 6) if total else 0.0,
        "goal_shadow_recall": round(goal_hits / total, 6) if total else 0.0,
        "union_recall": round(union_hits / total, 6) if total else 0.0,
        "goal_shadow_unique_recall_gain": goal_unique,
        "cases": rows,
    }
