from dataclasses import replace

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    DatasetKind,
    DecisionSnapshot,
    EvalCase,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)
from eval.accuracy_baseline.metrics import aggregate_provider_metrics, compare_providers


def _case(case_id: str, oracle: str = "Q-2") -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Bill",
        bill_text="Spec",
        unit="set",
        specialty="C10",
        oracle_quota_ids=(oracle,),
        source_family="human",
        project_id="project-a",
    )


def _candidate(
    quota_id: str,
    rank: int,
    *,
    provider: str = "production",
    stage: LifecycleStage = LifecycleStage.RETRIEVED,
    hard_conflicts: tuple[str, ...] = (),
) -> CandidateSnapshot:
    return CandidateSnapshot(
        quota_id=quota_id,
        name=quota_id,
        unit="set",
        province="demo",
        provider=provider,
        source="hybrid" if provider == "production" else "goal_search",
        stage=stage,
        rank=rank,
        hard_conflicts=hard_conflicts,
    )


def _result(
    case_id: str,
    ids: list[str],
    decisions: list[tuple[str, str]],
    final: str,
) -> ProviderResult:
    candidates = tuple(_candidate(quota_id, rank) for rank, quota_id in enumerate(ids, start=1))
    return ProviderResult(
        case_id=case_id,
        provider_name="production",
        status=ProviderStatus.OK,
        final_quota_ids=(final,),
        confidence=70,
        lifecycle=(
            StageSnapshot(
                stage=LifecycleStage.RETRIEVED,
                emitted=True,
                candidates=candidates,
                top1_id=ids[0],
            ),
        ),
        decisions=tuple(DecisionSnapshot(name=name, top1_id=top1) for name, top1 in decisions),
    )


def test_aggregate_metrics_separates_recall_conditional_top1_and_flips():
    case = _case("case-1")
    result = _result(
        "case-1",
        ["Q-1", "Q-2", "Q-3"],
        [("manual", "Q-1"), ("ltr", "Q-2"), ("final", "Q-1")],
        "Q-1",
    )

    report = aggregate_provider_metrics([case], [result], min_slice_size=1)

    assert report["recall_at"] == {"5": 1.0, "10": 1.0, "25": 1.0, "80": 1.0}
    assert report["conditional_top1"] == 0.0
    assert report["final_top1"] == 0.0
    assert report["final_top3"] == 1.0
    assert report["refusal_rate"] == 0.0
    assert report["mrr"] == 0.5
    assert report["stage_flips"]["ltr"] == {"good_flip": 1, "bad_flip": 0, "net_gain": 1}
    assert report["stage_flips"]["final"] == {"good_flip": 0, "bad_flip": 1, "net_gain": -1}


def test_provider_comparison_reports_unique_union_and_excludes_failed_cases():
    cases = [_case("case-1"), _case("case-2")]
    production = _result("case-1", ["Q-1"], [("final", "Q-1")], "Q-1")
    goal_candidate = _candidate("Q-2", 1, provider="goal_shadow")
    goal = replace(
        production,
        provider_name="goal_shadow",
        final_quota_ids=("Q-2",),
        lifecycle=(
            StageSnapshot(
                stage=LifecycleStage.RETRIEVED,
                emitted=True,
                candidates=(goal_candidate,),
                top1_id="Q-2",
            ),
        ),
    )
    failed = ProviderResult(
        case_id="case-2",
        provider_name="production",
        status=ProviderStatus.PROVINCE_UNAVAILABLE,
    )

    comparison = compare_providers(
        cases,
        {"production": [production, failed], "goal_shadow": [goal]},
    )

    assert comparison["comparable_cases"] == 1
    assert comparison["excluded_cases"] == 1
    assert comparison["production_recall"] == 0.0
    assert comparison["goal_shadow_recall"] == 1.0
    assert comparison["union_recall"] == 1.0
    assert comparison["goal_shadow_unique_recall_gain"] == 1


def test_route_and_taxonomy_oracle_losses_are_counted_once_per_case_candidate():
    case = _case("case-1")
    oracle = _candidate(
        "Q-2",
        2,
        hard_conflicts=("family_gate_hard_conflict",),
    )
    retrieved = StageSnapshot(
        stage=LifecycleStage.RETRIEVED,
        emitted=True,
        candidates=(_candidate("Q-1", 1), oracle),
        top1_id="Q-1",
    )
    route_filtered = StageSnapshot(
        stage=LifecycleStage.ROUTE_FILTERED,
        emitted=True,
        candidates=(_candidate("Q-1", 1, stage=LifecycleStage.ROUTE_FILTERED),),
        top1_id="Q-1",
    )
    reranked = StageSnapshot(
        stage=LifecycleStage.RERANKED,
        emitted=True,
        candidates=(replace(oracle, stage=LifecycleStage.RERANKED),),
        top1_id="Q-2",
    )
    result = ProviderResult(
        case_id="case-1",
        provider_name="production",
        status=ProviderStatus.OK,
        final_quota_ids=("Q-1",),
        lifecycle=(retrieved, route_filtered, reranked),
    )

    report = aggregate_provider_metrics([case], [result], min_slice_size=1)

    assert report["route_filter_oracle_loss_count"] == 1
    assert report["route_filter_oracle_loss_rate"] == 1.0
    assert report["taxonomy_false_veto_count"] == 1
