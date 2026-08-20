from dataclasses import FrozenInstanceError

import pytest

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


def test_eval_case_normalizes_oracles_and_exposes_bill_text():
    case = EvalCase(
        case_id="case-1",
        dataset_kind=DatasetKind.PRIMARY,
        province="demo",
        bill_name="Valve",
        bill_text="DN50 threaded",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-1", "Q-2"),
        source_family="user_correction",
        project_id="project-a",
    )

    assert case.query_text == "Valve DN50 threaded"
    assert case.oracle_set == {"Q-1", "Q-2"}
    with pytest.raises(FrozenInstanceError):
        case.province = "changed"


def test_provider_result_uses_fixed_lifecycle_and_decision_contracts():
    candidate = CandidateSnapshot(
        quota_id="Q-1",
        name="Quota",
        unit="set",
        province="demo",
        provider="production",
        source="hybrid",
        stage=LifecycleStage.RETRIEVED,
        rank=1,
    )
    stage = StageSnapshot(
        stage=LifecycleStage.RETRIEVED,
        emitted=True,
        candidates=(candidate,),
        top1_id="Q-1",
    )
    result = ProviderResult(
        case_id="case-1",
        provider_name="production",
        status=ProviderStatus.OK,
        final_quota_ids=("Q-1",),
        confidence=0.9,
        lifecycle=(stage,),
        decisions=(DecisionSnapshot(name="final", top1_id="Q-1"),),
    )

    assert result.retrieved_ids == ("Q-1",)
    assert result.final_top1_id == "Q-1"
