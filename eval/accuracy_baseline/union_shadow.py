from __future__ import annotations

import math
import time
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    EvalCase,
    ProviderError,
    ProviderResult,
    ProviderStatus,
)
from .lifecycle import normalize_production_detail


@dataclass(frozen=True, slots=True)
class SerializedGoalHit:
    quota_id: str
    name: str
    unit: str
    score: float
    confidence: float
    reasons: tuple[str, ...]
    source_scores: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class UnionMergeDiagnostics:
    production_ids: tuple[str, ...]
    goal_ids: tuple[str, ...]
    raw_union_ids: tuple[str, ...]
    goal_unique_ids: tuple[str, ...]
    materialized_goal_ids: tuple[str, ...]
    missing_local_goal_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnionBudgetDiagnostics:
    policy: str
    requested_limit: int
    production_slots: int
    goal_only_slots: int
    head_production_ids: tuple[str, ...]
    head_goal_only_ids: tuple[str, ...]


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _upper_median(candidates: Sequence[dict[str, Any]], field: str) -> float:
    values = sorted(
        float(candidate.get(field, candidate.get("hybrid_score", 0.0)) or 0.0)
        for candidate in candidates
    )
    return values[len(values) // 2] if values else 0.0


def _candidate_sources(candidate: dict[str, Any], *, include_goal: bool) -> list[str]:
    sources = [
        str(value).strip()
        for value in candidate.get("candidate_sources") or []
        if str(value).strip()
    ]
    if not sources:
        source = str(candidate.get("match_source") or "production").strip()
        if source:
            sources.append(source)
    if include_goal and "goal_shadow" not in sources:
        sources.append("goal_shadow")
    return sources


def _add_goal_diagnostics(candidate: dict[str, Any], hit: SerializedGoalHit) -> dict[str, Any]:
    updated = deepcopy(candidate)
    updated["goal_shadow_score"] = float(hit.score)
    updated["goal_shadow_confidence"] = float(hit.confidence)
    updated["goal_shadow_reasons"] = list(hit.reasons)
    updated["goal_shadow_source_scores"] = dict(hit.source_scores)
    updated["candidate_sources"] = _candidate_sources(updated, include_goal=True)
    return updated


def merge_goal_candidates(
    production_candidates: Sequence[dict[str, Any]],
    goal_hits: Sequence[SerializedGoalHit],
    *,
    materialize: Callable[..., dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], UnionMergeDiagnostics]:
    production = [deepcopy(candidate) for candidate in production_candidates or []]
    production_ids = _ordered_unique(
        [candidate.get("quota_id", "") for candidate in production]
    )
    goal_hits = sorted(
        list(goal_hits or []),
        key=lambda hit: (-float(hit.score), str(hit.quota_id)),
    )
    goal_ids = _ordered_unique([hit.quota_id for hit in goal_hits])
    raw_union_ids = _ordered_unique([*production_ids, *goal_ids])
    hybrid_median = _upper_median(production, "hybrid_score")
    rerank_median = _upper_median(production, "rerank_score")

    by_quota_id: dict[str, int] = {}
    for index, candidate in enumerate(production):
        quota_id = str(candidate.get("quota_id") or "").strip()
        if quota_id and quota_id not in by_quota_id:
            by_quota_id[quota_id] = index

    goal_unique_ids: list[str] = []
    materialized_goal_ids: list[str] = []
    missing_local_goal_ids: list[str] = []
    goal_only_candidates: list[dict[str, Any]] = []
    seen_goal_ids: set[str] = set()
    for hit in goal_hits:
        quota_id = str(hit.quota_id or "").strip()
        if not quota_id or quota_id in seen_goal_ids:
            continue
        seen_goal_ids.add(quota_id)
        existing_index = by_quota_id.get(quota_id)
        if existing_index is not None:
            production[existing_index] = _add_goal_diagnostics(
                production[existing_index],
                hit,
            )
            continue
        goal_unique_ids.append(quota_id)
        materialized = materialize(
            quota_id,
            fallback_name=hit.name,
            fallback_unit=hit.unit,
        )
        if not isinstance(materialized, dict) or not str(materialized.get("name") or "").strip():
            missing_local_goal_ids.append(quota_id)
            continue
        candidate = _add_goal_diagnostics(materialized, hit)
        candidate["quota_id"] = quota_id
        candidate["match_source"] = "goal_shadow_union"
        candidate["candidate_sources"] = ["goal_shadow"]
        candidate["knowledge_prior_sources"] = ["goal_shadow_union"]
        candidate["hybrid_score"] = hybrid_median
        candidate["rerank_score"] = rerank_median
        candidate["active_rerank_score"] = rerank_median
        goal_only_candidates.append(candidate)
        materialized_goal_ids.append(quota_id)

    diagnostics = UnionMergeDiagnostics(
        production_ids=production_ids,
        goal_ids=goal_ids,
        raw_union_ids=raw_union_ids,
        goal_unique_ids=tuple(goal_unique_ids),
        materialized_goal_ids=tuple(materialized_goal_ids),
        missing_local_goal_ids=tuple(missing_local_goal_ids),
    )
    return [*production, *goal_only_candidates], diagnostics


def reorder_union_candidates_for_budget(
    candidates: Sequence[dict[str, Any]],
    *,
    production_ids: Sequence[str],
    limit: int,
    policy: str,
) -> tuple[list[dict[str, Any]], UnionBudgetDiagnostics | None]:
    working = [deepcopy(candidate) for candidate in candidates or []]
    normalized_policy = str(policy or "none").strip().lower()
    if normalized_policy == "none":
        return working, None
    if normalized_policy != "production_40_goal_10":
        raise ValueError(f"unknown candidate budget policy: {policy}")

    requested_limit = max(0, int(limit or 0))
    production_set = set(_ordered_unique(production_ids))
    production = [
        candidate
        for candidate in working
        if str(candidate.get("quota_id") or "").strip() in production_set
    ]
    goal_only = [
        candidate
        for candidate in working
        if str(candidate.get("quota_id") or "").strip() not in production_set
    ]
    production_slots = math.ceil(requested_limit * 0.8)
    goal_only_slots = requested_limit - production_slots
    head_production = production[:production_slots]
    head_goal_only = goal_only[:goal_only_slots]
    head = [*head_production, *head_goal_only]
    remaining = [
        *production[len(head_production) :],
        *goal_only[len(head_goal_only) :],
    ]
    if len(head) < requested_limit:
        fill_count = requested_limit - len(head)
        head.extend(remaining[:fill_count])
        remaining = remaining[fill_count:]

    diagnostics = UnionBudgetDiagnostics(
        policy=normalized_policy,
        requested_limit=requested_limit,
        production_slots=production_slots,
        goal_only_slots=goal_only_slots,
        head_production_ids=_ordered_unique(
            [
                candidate.get("quota_id", "")
                for candidate in head
                if str(candidate.get("quota_id") or "").strip() in production_set
            ]
        ),
        head_goal_only_ids=_ordered_unique(
            [
                candidate.get("quota_id", "")
                for candidate in head
                if str(candidate.get("quota_id") or "").strip() not in production_set
            ]
        ),
    )
    return [*head, *remaining], diagnostics


def _merge_diagnostics(
    previous: UnionMergeDiagnostics | None,
    current: UnionMergeDiagnostics,
) -> UnionMergeDiagnostics:
    if previous is None:
        return current
    production_ids = _ordered_unique([*previous.production_ids, *current.production_ids])
    goal_ids = _ordered_unique([*previous.goal_ids, *current.goal_ids])
    return UnionMergeDiagnostics(
        production_ids=production_ids,
        goal_ids=goal_ids,
        raw_union_ids=_ordered_unique([*previous.raw_union_ids, *current.raw_union_ids]),
        goal_unique_ids=tuple(quota_id for quota_id in goal_ids if quota_id not in production_ids),
        materialized_goal_ids=_ordered_unique(
            [*previous.materialized_goal_ids, *current.materialized_goal_ids]
        ),
        missing_local_goal_ids=_ordered_unique(
            [*previous.missing_local_goal_ids, *current.missing_local_goal_ids]
        ),
    )


def _merge_frozen_production_candidates(
    current_candidates: Sequence[dict[str, Any]],
    frozen_candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [deepcopy(candidate) for candidate in current_candidates or []]
    seen_ids = {
        str(candidate.get("quota_id") or "").strip()
        for candidate in merged
        if str(candidate.get("quota_id") or "").strip()
    }
    hybrid_median = _upper_median(merged, "hybrid_score")
    rerank_median = _upper_median(merged, "rerank_score")
    for frozen in frozen_candidates or []:
        quota_id = str(frozen.get("quota_id") or "").strip()
        if not quota_id or quota_id in seen_ids:
            continue
        candidate = deepcopy(frozen)
        candidate["quota_id"] = quota_id
        candidate["match_source"] = "production_shadow_replay"
        candidate["candidate_sources"] = _candidate_sources(
            candidate,
            include_goal=False,
        )
        candidate["knowledge_prior_sources"] = ["production_shadow_replay"]
        candidate["hybrid_score"] = float(
            candidate.get("hybrid_score", hybrid_median) or hybrid_median
        )
        candidate["rerank_score"] = float(
            candidate.get("rerank_score", rerank_median) or rerank_median
        )
        candidate["active_rerank_score"] = float(
            candidate.get("active_rerank_score", candidate["rerank_score"])
            or candidate["rerank_score"]
        )
        merged.append(candidate)
        seen_ids.add(quota_id)
    return merged


def _freeze_production_candidates(
    result: Mapping[str, Any],
    *,
    materialize: Callable[..., dict[str, Any] | None],
) -> tuple[dict[str, Any], ...]:
    candidate_snapshots = {
        str(candidate.get("quota_id") or "").strip(): candidate
        for candidate in result.get("candidate_snapshots") or []
        if isinstance(candidate, dict) and str(candidate.get("quota_id") or "").strip()
    }
    recalled_ids = _ordered_unique(
        result.get("recall_topk_ids")
        or result.get("all_candidate_ids")
        or candidate_snapshots.keys()
    )
    frozen: list[dict[str, Any]] = []
    for quota_id in recalled_ids:
        materialized = materialize(quota_id)
        candidate = deepcopy(materialized) if isinstance(materialized, dict) else {}
        candidate.update(deepcopy(candidate_snapshots.get(quota_id, {})))
        if not str(candidate.get("name") or "").strip():
            continue
        candidate["quota_id"] = quota_id
        frozen.append(candidate)
    return tuple(frozen)


class GoalUnionSearcherProxy:
    def __init__(
        self,
        wrapped_searcher: Any,
        goal_hits_by_case: Mapping[str, Sequence[SerializedGoalHit]],
        frozen_production_by_case: Mapping[str, Sequence[dict[str, Any]]] | None = None,
        candidate_budget_policy: str = "none",
    ) -> None:
        self._wrapped_searcher = wrapped_searcher
        self._goal_hits_by_case = {
            str(case_id): tuple(hits)
            for case_id, hits in goal_hits_by_case.items()
        }
        self._frozen_production_by_case = {
            str(case_id): tuple(deepcopy(candidates))
            for case_id, candidates in (frozen_production_by_case or {}).items()
        }
        self._candidate_budget_policy = str(candidate_budget_policy or "none")
        self.diagnostics: dict[str, UnionMergeDiagnostics] = {}
        self.budget_diagnostics: dict[str, list[UnionBudgetDiagnostics]] = defaultdict(list)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped_searcher, name)

    def search(self, query: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        production_candidates = self._wrapped_searcher.search(query, *args, **kwargs)
        item = kwargs.get("item")
        case_id = str((item or {}).get("_accuracy_case_id") or "") if isinstance(item, dict) else ""
        goal_hits = self._goal_hits_by_case.get(case_id)
        if not case_id or goal_hits is None:
            return production_candidates
        production_candidates = _merge_frozen_production_candidates(
            production_candidates,
            self._frozen_production_by_case.get(case_id, ()),
        )
        merged, current = merge_goal_candidates(
            production_candidates,
            goal_hits,
            materialize=self._wrapped_searcher._materialize_quota_candidate,
        )
        self.diagnostics[case_id] = _merge_diagnostics(
            self.diagnostics.get(case_id),
            current,
        )
        limit = kwargs.get("top_k")
        if limit is None and args and isinstance(args[0], int):
            limit = args[0]
        reordered, budget_diagnostics = reorder_union_candidates_for_budget(
            merged,
            production_ids=current.production_ids,
            limit=int(limit or 0),
            policy=self._candidate_budget_policy,
        )
        if budget_diagnostics is not None:
            self.budget_diagnostics[case_id].append(budget_diagnostics)
        return reordered


def _serialize_goal_hit(hit: Any) -> SerializedGoalHit:
    if isinstance(hit, SerializedGoalHit):
        return hit
    return SerializedGoalHit(
        quota_id=str(getattr(hit, "quota_id", "") or ""),
        name=str(getattr(hit, "name", "") or ""),
        unit=str(getattr(hit, "unit", "") or ""),
        score=float(getattr(hit, "score", 0.0) or 0.0),
        confidence=float(getattr(hit, "confidence", 0.0) or 0.0),
        reasons=tuple(getattr(hit, "reasons", ()) or ()),
        source_scores=dict(getattr(hit, "source_scores", {}) or {}),
    )


def _diagnostics_payload(
    diagnostics: UnionMergeDiagnostics | None,
    *,
    goal_error: str = "",
    candidate_budget_policy: str = "none",
    budget_diagnostics: Sequence[UnionBudgetDiagnostics] = (),
) -> dict[str, Any]:
    payload = {
        "production_retrieved_ids": [],
        "goal_retrieved_ids": [],
        "raw_union_ids": [],
        "goal_unique_ids": [],
        "materialized_goal_ids": [],
        "missing_local_goal_ids": [],
        "goal_error": goal_error,
        "candidate_budget_policy": str(candidate_budget_policy or "none"),
        "candidate_budget_calls": [asdict(item) for item in budget_diagnostics],
    }
    if diagnostics is not None:
        payload.update(
            {
                "production_retrieved_ids": list(diagnostics.production_ids),
                "goal_retrieved_ids": list(diagnostics.goal_ids),
                "raw_union_ids": list(diagnostics.raw_union_ids),
                "goal_unique_ids": list(diagnostics.goal_unique_ids),
                "materialized_goal_ids": list(diagnostics.materialized_goal_ids),
                "missing_local_goal_ids": list(diagnostics.missing_local_goal_ids),
            }
        )
    return payload


def evaluate_union_province_records(
    province: str,
    records: list[dict[str, Any]],
    *,
    goal_top_k: int = 80,
    candidate_budget_policy: str = "none",
    init_components: Callable[..., Any] | None = None,
    goal_searcher_factory: Callable[[str], Any] | None = None,
    matcher: Callable[..., list[dict[str, Any]]] | None = None,
    summarizer: Callable[[str, list[dict[str, Any]], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if init_components is None:
        from src.match_engine import init_search_components

        init_components = init_search_components
    if goal_searcher_factory is None:
        from src.goal_search import GoalSearcher

        goal_searcher_factory = GoalSearcher
    if matcher is None:
        from src.match_engine import match_search_only

        matcher = match_search_only
    if summarizer is None:
        from tools.run_real_eval import summarize_real_eval_details

        summarizer = summarize_real_eval_details
    from tools.run_real_eval import _bill_item_from_record, _detail_from_result

    base_searcher, validator = init_components(resolved_province=province)
    goal_searcher = goal_searcher_factory(province)
    goal_hits_by_case: dict[str, tuple[SerializedGoalHit, ...]] = {}
    goal_errors: dict[str, str] = {}
    bill_items: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        case_id = str(record.get("sample_id") or record.get("case_id") or index)
        goal_item = dict(record)
        goal_item["goal_no_answer_priors"] = True
        goal_item["goal_excluded_sources"] = {
            "sample_id": ({case_id} if case_id else set()),
            "source_file": (
                {str(record.get("source") or "").strip()}
                if str(record.get("source") or "").strip()
                else set()
            ),
            "project_name": (
                {str(record.get("project_name") or "").strip()}
                if str(record.get("project_name") or "").strip()
                else set()
            ),
        }
        try:
            goal_hits_by_case[case_id] = tuple(
                _serialize_goal_hit(hit)
                for hit in goal_searcher.search(goal_item, top_k=goal_top_k)
            )
        except Exception as exc:
            goal_hits_by_case[case_id] = ()
            goal_errors[case_id] = f"{type(exc).__name__}: {exc}"
        bill_item = _bill_item_from_record(record, index)
        bill_item["_accuracy_case_id"] = case_id
        bill_items.append(bill_item)

    started = time.perf_counter()
    production_results = matcher(
        deepcopy(bill_items),
        base_searcher,
        validator,
        experience_db=None,
        province=province,
    )
    frozen_production_by_case = {
        str(record.get("sample_id") or record.get("case_id") or index): (
            _freeze_production_candidates(
                result,
                materialize=base_searcher._materialize_quota_candidate,
            )
        )
        for index, (record, result) in enumerate(
            zip(records, production_results),
            start=1,
        )
    }
    proxy = GoalUnionSearcherProxy(
        base_searcher,
        goal_hits_by_case,
        frozen_production_by_case,
        candidate_budget_policy=candidate_budget_policy,
    )
    results = matcher(
        deepcopy(bill_items),
        proxy,
        validator,
        experience_db=None,
        province=province,
    )
    elapsed = time.perf_counter() - started
    details: list[dict[str, Any]] = []
    for record, result in zip(records, results):
        case_id = str(record.get("sample_id") or record.get("case_id") or "")
        detail = _detail_from_result(record, result)
        detail["union_shadow_diagnostics"] = _diagnostics_payload(
            proxy.diagnostics.get(case_id),
            goal_error=goal_errors.get(case_id, ""),
            candidate_budget_policy=candidate_budget_policy,
            budget_diagnostics=proxy.budget_diagnostics.get(case_id, ()),
        )
        details.append(detail)
    return summarizer(province, details, elapsed)


def _provider_error_result(
    case: EvalCase,
    status: ProviderStatus,
    exc: Exception,
) -> ProviderResult:
    return ProviderResult(
        case_id=case.case_id,
        provider_name="production_goal_union_shadow",
        status=status,
        errors=(
            ProviderError(
                code=status.value,
                message=str(exc),
                province=case.province,
            ),
        ),
    )


def _rename_provider_result(
    result: ProviderResult,
    runtime_metadata: Mapping[str, Any],
) -> ProviderResult:
    provider_name = "production_goal_union_shadow"
    lifecycle = tuple(
        replace(
            stage,
            candidates=tuple(
                replace(candidate, provider=provider_name)
                for candidate in stage.candidates
            ),
        )
        for stage in result.lifecycle
    )
    return replace(
        result,
        provider_name=provider_name,
        lifecycle=lifecycle,
        runtime_metadata=dict(runtime_metadata),
    )


class GoalUnionShadowProvider:
    name = "production_goal_union_shadow"

    def __init__(
        self,
        *,
        executor: Callable[..., dict[str, Any]] | None = None,
        goal_top_k: int = 80,
        candidate_budget_policy: str = "none",
    ) -> None:
        self._executor = executor or evaluate_union_province_records
        self._goal_top_k = goal_top_k
        self._candidate_budget_policy = str(candidate_budget_policy or "none")

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]:
        grouped: dict[str, list[EvalCase]] = defaultdict(list)
        for case in cases:
            grouped[case.province].append(case)

        results: list[ProviderResult] = []
        for province in sorted(grouped):
            province_cases = sorted(grouped[province], key=lambda case: case.case_id)
            try:
                payload = self._executor(
                    province,
                    [case.to_record() for case in province_cases],
                    goal_top_k=self._goal_top_k,
                    candidate_budget_policy=self._candidate_budget_policy,
                )
            except Exception as exc:
                results.extend(
                    _provider_error_result(
                        case,
                        ProviderStatus.PROVINCE_UNAVAILABLE,
                        exc,
                    )
                    for case in province_cases
                )
                continue
            details = {
                str(detail.get("sample_id") or ""): detail
                for detail in payload.get("details") or []
            }
            for case in province_cases:
                detail = details.get(case.case_id)
                if detail is None:
                    results.append(
                        _provider_error_result(
                            case,
                            ProviderStatus.PROVIDER_ERROR,
                            RuntimeError("union shadow result missing case detail"),
                        )
                    )
                    continue
                diagnostics = dict(detail.get("union_shadow_diagnostics") or {})
                runtime_metadata = {
                    "experiment": "production_goal_candidate_union_shadow_v1",
                    **diagnostics,
                }
                results.append(
                    _rename_provider_result(
                        normalize_production_detail(case, detail),
                        runtime_metadata,
                    )
                )
        return sorted(results, key=lambda result: result.case_id)


def aggregate_union_shadow_metrics(
    cases: Sequence[EvalCase],
    results: Sequence[ProviderResult],
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    result_by_id = {result.case_id: result for result in results}
    valid_statuses = {ProviderStatus.OK, ProviderStatus.TRACE_INCOMPLETE}
    valid_cases = 0
    production_recalled = 0
    goal_recalled = 0
    raw_union_recalled = 0
    rankable_recalled = 0
    goal_unique_gain = 0
    missing_local_count = 0
    rows: list[dict[str, Any]] = []
    exclusions: dict[str, int] = {}
    for case_id, case in sorted(case_by_id.items()):
        result = result_by_id.get(case_id)
        if result is None:
            exclusions["missing_result"] = exclusions.get("missing_result", 0) + 1
            continue
        if result.status not in valid_statuses:
            key = result.status.value
            exclusions[key] = exclusions.get(key, 0) + 1
            continue
        valid_cases += 1
        metadata = dict(result.runtime_metadata or {})
        production_ids = {
            str(value).strip()
            for value in metadata.get("production_retrieved_ids") or []
            if str(value).strip()
        }
        goal_ids = {
            str(value).strip()
            for value in metadata.get("goal_retrieved_ids") or []
            if str(value).strip()
        }
        raw_union_ids = {
            str(value).strip()
            for value in metadata.get("raw_union_ids") or []
            if str(value).strip()
        }
        rankable_ids = set(result.retrieved_ids)
        production_hit = bool(production_ids & case.oracle_set)
        goal_hit = bool(goal_ids & case.oracle_set)
        raw_union_hit = bool(raw_union_ids & case.oracle_set)
        rankable_hit = bool(rankable_ids & case.oracle_set)
        production_recalled += int(production_hit)
        goal_recalled += int(goal_hit)
        raw_union_recalled += int(raw_union_hit)
        rankable_recalled += int(rankable_hit)
        goal_unique_gain += int(goal_hit and not production_hit)
        missing_local_count += len(
            {
                str(value).strip()
                for value in metadata.get("missing_local_goal_ids") or []
                if str(value).strip()
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "production_recalled": production_hit,
                "goal_recalled": goal_hit,
                "raw_union_recalled": raw_union_hit,
                "rankable_recalled": rankable_hit,
                "goal_unique": goal_hit and not production_hit,
            }
        )

    denominator = valid_cases or 1
    return {
        "total_cases": len(cases),
        "valid_cases": valid_cases,
        "exclusions": dict(sorted(exclusions.items())),
        "production_recalled_count": production_recalled,
        "production_recall": round(production_recalled / denominator, 6) if valid_cases else 0.0,
        "goal_recalled_count": goal_recalled,
        "goal_recall": round(goal_recalled / denominator, 6) if valid_cases else 0.0,
        "raw_union_recalled_count": raw_union_recalled,
        "raw_union_recall": round(raw_union_recalled / denominator, 6) if valid_cases else 0.0,
        "rankable_recalled_count": rankable_recalled,
        "rankable_recall": round(rankable_recalled / denominator, 6) if valid_cases else 0.0,
        "goal_unique_recall_gain": goal_unique_gain,
        "missing_local_goal_candidate_count": missing_local_count,
        "cases": rows,
    }
