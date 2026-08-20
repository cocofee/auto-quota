from types import SimpleNamespace

from eval.accuracy_baseline.contracts import DatasetKind, EvalCase, LifecycleStage
from eval.accuracy_baseline.lifecycle import (
    normalize_goal_hits,
    normalize_production_detail,
)


def _case() -> EvalCase:
    return EvalCase(
        case_id="case-1",
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Valve",
        bill_text="DN50",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-2",),
        source_family="human",
        project_id="project-a",
    )


def test_normalize_production_detail_keeps_oracle_drop_and_decision_path():
    detail = {
        "recall_topk_ids": ["Q-1", "Q-2", "Q-3"],
        "candidate_snapshots": [
            {"quota_id": "Q-1", "name": "wrong", "rerank_score": 0.9},
            {"quota_id": "Q-2", "name": "correct", "family_gate_hard_conflict": True},
            {"quota_id": "Q-3", "name": "other"},
        ],
        "candidate_lifecycle_trace": [
            {"quota_id": "Q-1", "filter_state": "param_matched", "rank_position": 1},
            {
                "quota_id": "Q-2",
                "filter_state": "filtered_or_gated",
                "lost_reason": "family_gate_hard_conflict",
            },
        ],
        "router": {
            "classification": {
                "route_scope_filter": {
                    "applied": True,
                    "dropped_quota_ids": ["Q-2"],
                    "reason": "strict_route_scope",
                }
            }
        },
        "pre_ltr_top1_id": "Q-1",
        "post_ltr_top1_id": "Q-2",
        "post_ltr_structural_top1_id": "Q-1",
        "post_final_top1_id": "Q-1",
        "algo_id": "Q-1",
        "final_quota_ids": ["Q-1", "Q-3"],
        "oracle_status": "ok",
        "confidence": 70,
    }

    result = normalize_production_detail(_case(), detail)

    assert result.retrieved_ids == ("Q-1", "Q-2", "Q-3")
    route_stage = next(
        stage for stage in result.lifecycle if stage.stage == LifecycleStage.ROUTE_FILTERED
    )
    assert route_stage.emitted is True
    assert [candidate.quota_id for candidate in route_stage.candidates] == ["Q-1", "Q-3"]
    assert [decision.name for decision in result.decisions] == [
        "pre_ltr_seed",
        "ltr",
        "post_ltr_structural_ranker",
        "final",
    ]
    oracle = next(
        candidate
        for stage in result.lifecycle
        for candidate in stage.candidates
        if candidate.quota_id == "Q-2" and candidate.drop_reason
    )
    assert oracle.hard_conflicts == ("family_gate_hard_conflict",)


def test_normalize_goal_hits_emits_only_real_goal_stages():
    hits = [
        SimpleNamespace(
            quota_id="Q-2",
            name="correct",
            unit="set",
            score=1.2,
            confidence=70.0,
            reasons=["bm25:1.00"],
            source_scores={"bm25": 1.0},
        ),
        SimpleNamespace(
            quota_id="Q-1",
            name="wrong",
            unit="set",
            score=1.0,
            confidence=63.0,
            reasons=["bm25:0.80"],
            source_scores={"bm25": 0.8},
        ),
    ]

    result = normalize_goal_hits(_case(), hits)

    emitted = [stage.stage for stage in result.lifecycle if stage.emitted]
    assert emitted == [
        LifecycleStage.RETRIEVED,
        LifecycleStage.RERANKED,
        LifecycleStage.SELECTED,
        LifecycleStage.POSTPROCESSED,
    ]
    assert result.final_top1_id == "Q-2"
