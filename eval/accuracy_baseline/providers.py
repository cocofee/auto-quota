from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .contracts import EvalCase, ProviderError, ProviderResult, ProviderStatus
from .lifecycle import normalize_goal_hits, normalize_production_detail


class CandidateProvider(Protocol):
    name: str

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]: ...


def _error_result(
    case: EvalCase,
    provider: str,
    status: ProviderStatus,
    exc: Exception,
) -> ProviderResult:
    return ProviderResult(
        case_id=case.case_id,
        provider_name=provider,
        status=status,
        errors=(
            ProviderError(
                code=status.value,
                message=str(exc),
                province=case.province,
            ),
        ),
    )


class ProductionProvider:
    name = "production"

    def __init__(
        self,
        *,
        executor: Callable[..., dict[str, Any]] | None = None,
        with_experience: bool = False,
    ) -> None:
        if executor is None:
            from tools.run_real_eval import evaluate_province_records

            executor = evaluate_province_records
        self._executor = executor
        self._with_experience = with_experience

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]:
        grouped: dict[str, list[EvalCase]] = defaultdict(list)
        for case in cases:
            grouped[case.province].append(case)

        results: list[ProviderResult] = []
        for province in sorted(grouped):
            province_cases = grouped[province]
            try:
                payload = self._executor(
                    province,
                    [case.to_record() for case in province_cases],
                    with_experience=self._with_experience,
                )
            except Exception as exc:
                results.extend(
                    _error_result(
                        case,
                        self.name,
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
                        _error_result(
                            case,
                            self.name,
                            ProviderStatus.PROVIDER_ERROR,
                            RuntimeError("production result missing case detail"),
                        )
                    )
                else:
                    results.append(normalize_production_detail(case, detail))
        return sorted(results, key=lambda result: result.case_id)


class GoalShadowProvider:
    name = "goal_shadow"

    def __init__(
        self,
        *,
        searcher_factory: Callable[[str], Any] | None = None,
        top_k: int = 80,
    ) -> None:
        if searcher_factory is None:
            from src.goal_search import GoalSearcher

            searcher_factory = GoalSearcher
        self._searcher_factory = searcher_factory
        self._top_k = top_k

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]:
        searchers: dict[str, Any] = {}
        results: list[ProviderResult] = []
        for case in sorted(cases, key=lambda value: (value.province, value.case_id)):
            try:
                searcher = searchers.get(case.province)
                if searcher is None:
                    searcher = self._searcher_factory(case.province)
                    searchers[case.province] = searcher
                local_ids = set(getattr(searcher.index, "by_quota_id", {}))
                if case.oracle_set and not (case.oracle_set & local_ids):
                    results.append(
                        ProviderResult(
                            case_id=case.case_id,
                            provider_name=self.name,
                            status=ProviderStatus.ORACLE_NOT_IN_LOCAL_DB,
                        )
                    )
                    continue
                item = case.to_record()
                item["goal_no_answer_priors"] = True
                item["goal_excluded_sources"] = {
                    "sample_id": {case.case_id},
                    "source_file": ({case.source} if case.source else set()),
                    "project_name": ({case.project_id} if case.project_id else set()),
                }
                hits = searcher.search(item, top_k=self._top_k)
                results.append(normalize_goal_hits(case, hits))
            except Exception as exc:
                results.append(
                    _error_result(case, self.name, ProviderStatus.PROVIDER_ERROR, exc)
                )
        return sorted(results, key=lambda result: result.case_id)
