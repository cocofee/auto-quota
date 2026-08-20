from types import SimpleNamespace

from eval.accuracy_baseline.contracts import DatasetKind, EvalCase, ProviderStatus
from eval.accuracy_baseline.providers import GoalShadowProvider, ProductionProvider


def _case(case_id: str, province: str) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province=province,
        bill_name="Valve",
        bill_text="DN50",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-1",),
        source_family="human",
        project_id="project-a",
    )


def test_production_provider_isolates_unavailable_province():
    def executor(province, records, with_experience=False):
        if province == "missing":
            raise RuntimeError("index unavailable")
        return {
            "details": [
                {
                    "sample_id": records[0]["sample_id"],
                    "recall_topk_ids": ["Q-1"],
                    "candidate_snapshots": [{"quota_id": "Q-1", "name": "correct"}],
                    "candidate_lifecycle_trace": [
                        {"quota_id": "Q-1", "filter_state": "param_matched", "rank_position": 1}
                    ],
                    "post_final_top1_id": "Q-1",
                    "final_quota_ids": ["Q-1"],
                    "algo_id": "Q-1",
                    "oracle_status": "ok",
                }
            ]
        }

    results = ProductionProvider(executor=executor).run(
        [_case("ok-1", "available"), _case("bad-1", "missing")]
    )

    assert {result.case_id: result.status for result in results} == {
        "bad-1": ProviderStatus.PROVINCE_UNAVAILABLE,
        "ok-1": ProviderStatus.OK,
    }


def test_goal_provider_forces_leakage_safe_priors_and_top80():
    calls = []

    class FakeSearcher:
        index = SimpleNamespace(by_quota_id={"Q-1": object()})

        def search(self, item, top_k):
            calls.append((item, top_k))
            return [
                SimpleNamespace(
                    quota_id="Q-1",
                    name="correct",
                    unit="set",
                    score=1.0,
                    confidence=63.0,
                    reasons=[],
                    source_scores={"bm25": 1.0},
                )
            ]

    results = GoalShadowProvider(searcher_factory=lambda province: FakeSearcher()).run(
        [_case("goal-1", "available")]
    )

    assert results[0].status == ProviderStatus.OK
    assert calls[0][1] == 80
    assert calls[0][0]["goal_no_answer_priors"] is True
    assert calls[0][0]["goal_excluded_sources"]["sample_id"] == {"goal-1"}
