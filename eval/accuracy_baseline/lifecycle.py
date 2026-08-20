from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .contracts import (
    CandidateSnapshot,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)


STAGE_ORDER = tuple(LifecycleStage)
DECISION_FIELDS = (
    ("pre_ltr_seed", "pre_ltr_top1_id"),
    ("ltr", "post_ltr_top1_id"),
    ("post_ltr_structural_ranker", "post_ltr_structural_top1_id"),
    ("cgr_ranker", "post_cgr_top1_id"),
    ("candidate_arbiter", "post_arbiter_top1_id"),
    ("explicit_picker", "post_explicit_top1_id"),
    ("anchor", "post_anchor_top1_id"),
    ("final", "post_final_top1_id"),
)
HARD_CONFLICT_FIELDS = (
    "family_gate_hard_conflict",
    "feature_alignment_hard_conflict",
    "logic_hard_conflict",
    "context_alignment_hard_conflict",
    "param_hard_fail",
)
SCORE_FIELDS = (
    "hybrid_score",
    "rerank_score",
    "manual_structured_score",
    "ltr_score",
    "param_score",
    "feature_alignment_score",
    "family_gate_score",
)


def _candidate_map(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("quota_id") or row.get("id") or "").strip(): row
        for row in rows
        if str(row.get("quota_id") or row.get("id") or "").strip()
    }


def _scores(row: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key in SCORE_FIELDS:
        if row.get(key) is None:
            continue
        try:
            result[key] = float(row[key])
        except (TypeError, ValueError):
            continue
    return result


def _snapshot(
    *,
    case: EvalCase,
    provider: str,
    stage: LifecycleStage,
    quota_id: str,
    rank: int | None,
    row: dict[str, Any] | None = None,
    lifecycle_row: dict[str, Any] | None = None,
) -> CandidateSnapshot:
    row = dict(row or {})
    lifecycle_row = dict(lifecycle_row or {})
    hard_conflicts = tuple(field for field in HARD_CONFLICT_FIELDS if bool(row.get(field)))
    lost_reason = str(lifecycle_row.get("lost_reason") or "")
    if lost_reason in HARD_CONFLICT_FIELDS and lost_reason not in hard_conflicts:
        hard_conflicts += (lost_reason,)
    return CandidateSnapshot(
        quota_id=quota_id,
        name=str(row.get("name") or row.get("quota_name") or ""),
        unit=str(row.get("unit") or ""),
        province=str(row.get("_source_province") or case.province),
        provider=provider,
        source=str(lifecycle_row.get("source") or row.get("match_source") or ""),
        stage=stage,
        rank=rank,
        scores=_scores(row),
        family=str((row.get("candidate_canonical_features") or {}).get("family") or ""),
        book=str(row.get("book") or row.get("quota_book") or ""),
        param_match=(None if row.get("param_match") is None else bool(row.get("param_match"))),
        hard_conflicts=hard_conflicts,
        drop_reason=lost_reason,
        raw_stage=str(lifecycle_row.get("first_seen_stage") or row.get("rank_stage") or ""),
        raw={**row, **lifecycle_row},
    )


def _empty_stage(stage: LifecycleStage) -> StageSnapshot:
    return StageSnapshot(stage=stage, emitted=False)


def normalize_production_detail(case: EvalCase, detail: dict[str, Any]) -> ProviderResult:
    candidate_rows = _candidate_map(detail.get("candidate_snapshots") or [])
    lifecycle_rows = _candidate_map(detail.get("candidate_lifecycle_trace") or [])
    recall_ids = tuple(
        str(value).strip()
        for value in (detail.get("recall_topk_ids") or detail.get("all_candidate_ids") or [])
        if str(value).strip()
    )
    retrieved = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.RETRIEVED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(recall_ids, start=1)
    )

    router = dict(detail.get("router") or {})
    classification = dict(router.get("classification") or router)
    route_filter = dict(classification.get("route_scope_filter") or {})
    route_dropped_ids = {
        str(value).strip()
        for value in route_filter.get("dropped_quota_ids") or []
        if str(value).strip()
    }
    route_remaining_ids = [quota_id for quota_id in recall_ids if quota_id not in route_dropped_ids]
    route_filtered = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.ROUTE_FILTERED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(route_remaining_ids, start=1)
    )

    validated_ids = [
        quota_id
        for quota_id in recall_ids
        if str((lifecycle_rows.get(quota_id) or {}).get("filter_state") or "")
        not in {"filtered_hard_param_fail", "filtered_or_gated"}
    ]
    validated = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.VALIDATED,
            quota_id=quota_id,
            rank=rank,
            row=candidate_rows.get(quota_id),
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, quota_id in enumerate(validated_ids, start=1)
    )

    final_ids = tuple(
        str(value).strip()
        for value in (detail.get("final_quota_ids") or [])
        if str(value).strip()
    )
    final_id = str(detail.get("post_final_top1_id") or detail.get("algo_id") or "")
    if not final_ids and final_id:
        final_ids = (final_id,)
    selected_candidate = tuple(candidate for candidate in validated if candidate.quota_id == final_id)
    reranked = tuple(
        _snapshot(
            case=case,
            provider="production",
            stage=LifecycleStage.RERANKED,
            quota_id=quota_id,
            rank=rank,
            row=row,
            lifecycle_row=lifecycle_rows.get(quota_id),
        )
        for rank, (quota_id, row) in enumerate(candidate_rows.items(), start=1)
    )
    stages = {
        LifecycleStage.RETRIEVED: StageSnapshot(
            stage=LifecycleStage.RETRIEVED,
            emitted=bool(recall_ids),
            candidates=retrieved,
            top1_id=recall_ids[0] if recall_ids else "",
            raw_stage_names=("recall_topk_ids",),
        ),
        LifecycleStage.ROUTE_FILTERED: StageSnapshot(
            stage=LifecycleStage.ROUTE_FILTERED,
            emitted=bool(route_filter.get("applied")),
            candidates=route_filtered,
            top1_id=route_remaining_ids[0] if route_remaining_ids else "",
            raw_stage_names=("route_scope_filter",),
        ),
        LifecycleStage.RERANKED: StageSnapshot(
            stage=LifecycleStage.RERANKED,
            emitted=bool(candidate_rows),
            candidates=reranked,
            top1_id=str(detail.get("pre_ltr_top1_id") or ""),
            raw_stage_names=("candidate_snapshots",),
        ),
        LifecycleStage.VALIDATED: StageSnapshot(
            stage=LifecycleStage.VALIDATED,
            emitted=bool(lifecycle_rows),
            candidates=validated,
            top1_id=validated_ids[0] if validated_ids else "",
            raw_stage_names=("candidate_lifecycle_trace",),
        ),
        LifecycleStage.SELECTED: StageSnapshot(
            stage=LifecycleStage.SELECTED,
            emitted=bool(final_id),
            candidates=selected_candidate,
            top1_id=final_id,
            raw_stage_names=("selected_top1_id",),
        ),
        LifecycleStage.POSTPROCESSED: StageSnapshot(
            stage=LifecycleStage.POSTPROCESSED,
            emitted=bool(final_id),
            candidates=selected_candidate,
            top1_id=final_id,
            raw_stage_names=("post_final_top1_id",),
        ),
    }
    decisions = tuple(
        DecisionSnapshot(name=name, top1_id=str(detail.get(field) or ""))
        for name, field in DECISION_FIELDS
        if str(detail.get(field) or "")
    )
    status = ProviderStatus.OK
    if not case.oracle_quota_ids:
        status = ProviderStatus.MISSING_ORACLE
    elif str(detail.get("oracle_status") or "ok") != "ok":
        status = ProviderStatus.ORACLE_NOT_IN_LOCAL_DB
    elif not recall_ids or not lifecycle_rows:
        status = ProviderStatus.TRACE_INCOMPLETE
    return ProviderResult(
        case_id=case.case_id,
        provider_name="production",
        status=status,
        final_quota_ids=final_ids,
        confidence=float(detail.get("confidence") or 0.0),
        lifecycle=tuple(stages[stage] for stage in STAGE_ORDER),
        decisions=decisions,
        raw_trace=detail,
    )


def normalize_goal_hits(case: EvalCase, hits: Iterable[Any]) -> ProviderResult:
    rows = list(hits)
    candidates = tuple(
        CandidateSnapshot(
            quota_id=str(hit.quota_id),
            name=str(hit.name),
            unit=str(hit.unit),
            province=case.province,
            provider="goal_shadow",
            source="goal_search",
            stage=LifecycleStage.RETRIEVED,
            rank=rank,
            scores={"goal_score": float(hit.score), **dict(hit.source_scores or {})},
            raw_stage="goal_search",
            raw={"reasons": list(hit.reasons or [])},
        )
        for rank, hit in enumerate(rows, start=1)
    )
    final_id = candidates[0].quota_id if candidates else ""
    selected = candidates[:1]
    stages = {
        LifecycleStage.RETRIEVED: StageSnapshot(
            stage=LifecycleStage.RETRIEVED,
            emitted=True,
            candidates=candidates,
            top1_id=final_id,
            raw_stage_names=("goal_candidate_generation",),
        ),
        LifecycleStage.ROUTE_FILTERED: _empty_stage(LifecycleStage.ROUTE_FILTERED),
        LifecycleStage.RERANKED: StageSnapshot(
            stage=LifecycleStage.RERANKED,
            emitted=True,
            candidates=tuple(replace(candidate, stage=LifecycleStage.RERANKED) for candidate in candidates),
            top1_id=final_id,
            raw_stage_names=("goal_score_sort",),
        ),
        LifecycleStage.VALIDATED: _empty_stage(LifecycleStage.VALIDATED),
        LifecycleStage.SELECTED: StageSnapshot(
            stage=LifecycleStage.SELECTED,
            emitted=bool(selected),
            candidates=tuple(replace(candidate, stage=LifecycleStage.SELECTED) for candidate in selected),
            top1_id=final_id,
            raw_stage_names=("goal_top1",),
        ),
        LifecycleStage.POSTPROCESSED: StageSnapshot(
            stage=LifecycleStage.POSTPROCESSED,
            emitted=bool(selected),
            candidates=tuple(replace(candidate, stage=LifecycleStage.POSTPROCESSED) for candidate in selected),
            top1_id=final_id,
            raw_stage_names=("goal_top1",),
        ),
    }
    return ProviderResult(
        case_id=case.case_id,
        provider_name="goal_shadow",
        status=ProviderStatus.OK,
        final_quota_ids=tuple(candidate.quota_id for candidate in candidates[:3]),
        confidence=(float(rows[0].confidence) if rows else 0.0),
        lifecycle=tuple(stages[stage] for stage in STAGE_ORDER),
        decisions=((DecisionSnapshot(name="goal_score", top1_id=final_id),) if final_id else ()),
    )
