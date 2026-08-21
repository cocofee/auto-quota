from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .contracts import EvalCase, ProviderError, ProviderResult, ProviderStatus
from .lifecycle import normalize_goal_hits, normalize_production_detail


class CandidateProvider(Protocol):
    name: str

    def run(self, cases: Sequence[EvalCase]) -> list[ProviderResult]: ...


class ProvinceUnavailableError(RuntimeError):
    pass


def bucket_provider_details(payload: Any) -> dict[str, list[Mapping[str, Any]]]:
    if not isinstance(payload, Mapping):
        raise TypeError("provider payload must be an object")
    raw_details = payload.get("details") or []
    if not isinstance(raw_details, Sequence) or isinstance(raw_details, (str, bytes)):
        raise TypeError("provider payload details must be a sequence")
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for detail in raw_details:
        if not isinstance(detail, Mapping):
            raise TypeError("provider detail must be an object")
        buckets[str(detail.get("sample_id") or "")].append(detail)
    return buckets


def provider_status_from_exception(exc: Exception) -> ProviderStatus:
    if isinstance(exc, (ProvinceUnavailableError, FileNotFoundError, NotADirectoryError)):
        return ProviderStatus.PROVINCE_UNAVAILABLE
    return ProviderStatus.PROVIDER_ERROR


def _error_result(
    case: EvalCase,
    provider: str,
    status: ProviderStatus,
    exc: Exception,
) -> ProviderResult:
    execution_mode = "search_core" if provider in {"search_core", "production"} else provider
    return ProviderResult(
        case_id=case.case_id,
        provider_name=provider,
        status=status,
        runtime_metadata={"execution_mode": execution_mode},
        errors=(
            ProviderError(
                code=status.value,
                message=str(exc),
                province=case.province,
            ),
        ),
    )


class SearchCoreProvider:
    name = "search_core"

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
                detail_buckets = bucket_provider_details(payload)
            except Exception as exc:
                results.extend(
                    _error_result(
                        case,
                        self.name,
                        provider_status_from_exception(exc),
                        exc,
                    )
                    for case in province_cases
                )
                continue
            for case in province_cases:
                bucket = detail_buckets.get(case.case_id, [])
                if not bucket:
                    results.append(
                        _error_result(
                            case,
                            self.name,
                            ProviderStatus.PROVIDER_ERROR,
                            RuntimeError("search core result missing case detail"),
                        )
                    )
                elif len(bucket) > 1:
                    results.append(
                        _error_result(
                            case,
                            self.name,
                            ProviderStatus.PROVIDER_ERROR,
                            RuntimeError("search core returned duplicate case details"),
                        )
                    )
                else:
                    try:
                        results.append(
                            normalize_production_detail(
                                case,
                                bucket[0],
                                provider_name=self.name,
                            )
                        )
                    except Exception as exc:
                        results.append(
                            _error_result(
                                case,
                                self.name,
                                ProviderStatus.PROVIDER_ERROR,
                                exc,
                            )
                        )
        return sorted(results, key=lambda result: result.case_id)


class ProductionProvider(SearchCoreProvider):
    """Legacy provider name for historical report compatibility."""

    name = "production"


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
                if case.oracle_set and not case.oracle_covered_by(local_ids):
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
