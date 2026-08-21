from copy import deepcopy

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    DatasetKind,
    EvalCase,
    LifecycleStage,
    OracleSemantics,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)
from eval.accuracy_baseline.union_shadow import (
    GoalUnionShadowProvider,
    GoalUnionSearcherProxy,
    SerializedGoalHit,
    aggregate_union_shadow_metrics,
    evaluate_union_province_records,
    merge_goal_candidates,
    reorder_union_candidates_for_budget,
)


def _goal_hit(quota_id, *, score=0.9, name="Goal", unit="set"):
    return SerializedGoalHit(
        quota_id=quota_id,
        name=name,
        unit=unit,
        score=score,
        confidence=90.0,
        reasons=("goal",),
        source_scores={"bm25": 0.7},
    )


def test_merge_goal_candidates_preserves_production_fields_and_adds_diagnostics():
    production = [
        {
            "quota_id": "Q-1",
            "name": "Production",
            "hybrid_score": 0.8,
            "rerank_score": 0.7,
            "match_source": "hybrid",
        }
    ]
    original = deepcopy(production)

    merged, diagnostics = merge_goal_candidates(
        production,
        [_goal_hit("Q-1")],
        materialize=lambda *_args, **_kwargs: None,
    )

    assert production == original
    assert merged[0]["name"] == "Production"
    assert merged[0]["hybrid_score"] == 0.8
    assert merged[0]["rerank_score"] == 0.7
    assert merged[0]["goal_shadow_score"] == 0.9
    assert merged[0]["goal_shadow_confidence"] == 90.0
    assert merged[0]["candidate_sources"] == ["hybrid", "goal_shadow"]
    assert diagnostics.production_ids == ("Q-1",)
    assert diagnostics.goal_ids == ("Q-1",)
    assert diagnostics.raw_union_ids == ("Q-1",)
    assert diagnostics.goal_unique_ids == ()


def test_merge_goal_candidates_materializes_goal_only_candidate_with_upper_median_scores():
    production = [
        {"quota_id": "Q-1", "name": "A", "hybrid_score": 0.2, "rerank_score": 0.3},
        {"quota_id": "Q-2", "name": "B", "hybrid_score": 0.8, "rerank_score": 0.9},
    ]

    merged, diagnostics = merge_goal_candidates(
        production,
        [_goal_hit("Q-3", score=0.95)],
        materialize=lambda quota_id, **_kwargs: {
            "quota_id": quota_id,
            "name": "C",
            "unit": "set",
            "candidate_canonical_features": {"family": "valve"},
        },
    )

    goal = next(candidate for candidate in merged if candidate["quota_id"] == "Q-3")
    assert [candidate["quota_id"] for candidate in merged] == ["Q-1", "Q-2", "Q-3"]
    assert goal["hybrid_score"] == 0.8
    assert goal["rerank_score"] == 0.9
    assert goal["knowledge_prior_sources"] == ["goal_shadow_union"]
    assert goal["match_source"] == "goal_shadow_union"
    assert goal["candidate_sources"] == ["goal_shadow"]
    assert diagnostics.goal_unique_ids == ("Q-3",)
    assert diagnostics.materialized_goal_ids == ("Q-3",)
    assert diagnostics.missing_local_goal_ids == ()


def test_merge_goal_candidates_reports_missing_local_goal_candidate():
    merged, diagnostics = merge_goal_candidates(
        [{"quota_id": "Q-1", "name": "A", "hybrid_score": 0.5}],
        [_goal_hit("Q-MISSING")],
        materialize=lambda *_args, **_kwargs: None,
    )

    assert [candidate["quota_id"] for candidate in merged] == ["Q-1"]
    assert diagnostics.raw_union_ids == ("Q-1", "Q-MISSING")
    assert diagnostics.materialized_goal_ids == ()
    assert diagnostics.missing_local_goal_ids == ("Q-MISSING",)


def test_reorder_union_candidates_for_40_10_budget_builds_balanced_head():
    production = [
        {"quota_id": f"P-{index}", "name": f"Production {index}"}
        for index in range(45)
    ]
    goal = [
        {"quota_id": f"G-{index}", "name": f"Goal {index}"}
        for index in range(20)
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        [*production, *goal],
        production_ids=tuple(candidate["quota_id"] for candidate in production),
        limit=50,
        policy="production_40_goal_10",
    )

    assert [candidate["quota_id"] for candidate in reordered[:40]] == [
        f"P-{index}" for index in range(40)
    ]
    assert [candidate["quota_id"] for candidate in reordered[40:50]] == [
        f"G-{index}" for index in range(10)
    ]
    assert diagnostics.production_slots == 40
    assert diagnostics.goal_only_slots == 10


def test_budget_duplicate_goal_id_does_not_consume_goal_only_slot():
    candidates = [
        {"quota_id": "P-1", "name": "Production with Goal diagnostics"},
        *(
            {"quota_id": f"G-{index}", "name": f"Goal {index}"}
            for index in range(12)
        ),
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-1",),
        limit=10,
        policy="production_40_goal_10",
    )

    assert diagnostics.head_production_ids == ("P-1",)
    assert diagnostics.head_goal_only_ids == tuple(f"G-{index}" for index in range(9))
    assert len(reordered[:10]) == 10


def test_budget_backfills_unused_production_slots_from_goal_only_candidates():
    candidates = [
        *(
            {"quota_id": f"P-{index}", "name": "Production"}
            for index in range(3)
        ),
        *({"quota_id": f"G-{index}", "name": "Goal"} for index in range(12)),
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-0", "P-1", "P-2"),
        limit=10,
        policy="production_40_goal_10",
    )

    assert len(diagnostics.head_production_ids) == 3
    assert len(diagnostics.head_goal_only_ids) == 7
    assert len(reordered[:10]) == 10


def test_budget_scales_non_50_limit_to_80_20_ratio():
    candidates = [
        *(
            {"quota_id": f"P-{index}", "name": "Production"}
            for index in range(30)
        ),
        *({"quota_id": f"G-{index}", "name": "Goal"} for index in range(10)),
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=tuple(f"P-{index}" for index in range(30)),
        limit=25,
        policy="production_40_goal_10",
    )

    assert diagnostics.production_slots == 20
    assert diagnostics.goal_only_slots == 5
    assert [candidate["quota_id"] for candidate in reordered[20:25]] == [
        f"G-{index}" for index in range(5)
    ]


def test_budget_none_preserves_candidate_order():
    candidates = [
        {"quota_id": "P-1", "name": "Production"},
        {"quota_id": "G-1", "name": "Goal"},
    ]

    reordered, diagnostics = reorder_union_candidates_for_budget(
        candidates,
        production_ids=("P-1",),
        limit=1,
        policy="none",
    )

    assert reordered == candidates
    assert diagnostics is None


class _FakeBaseSearcher:
    province = "demo-province"
    delegated_value = "delegated"

    def __init__(self):
        self.calls = []
        self.production_candidates = [
            {"quota_id": "Q-1", "name": "Production", "hybrid_score": 0.6}
        ]

    def search(self, query, *args, **kwargs):
        self.calls.append((query, args, kwargs))
        return deepcopy(self.production_candidates)

    def _materialize_quota_candidate(self, quota_id, **_kwargs):
        return {"quota_id": quota_id, "name": f"Materialized {quota_id}", "unit": "set"}


def test_proxy_delegates_and_merges_case_scoped_goal_hits():
    base = _FakeBaseSearcher()
    proxy = GoalUnionSearcherProxy(base, {"case-1": (_goal_hit("Q-2"),)})

    result = proxy.search(
        "query",
        top_k=20,
        books=["C10"],
        item={"_accuracy_case_id": "case-1"},
    )

    assert proxy.province == "demo-province"
    assert proxy.delegated_value == "delegated"
    assert [candidate["quota_id"] for candidate in result] == ["Q-1", "Q-2"]
    assert base.calls[0][0] == "query"
    assert base.calls[0][2]["books"] == ["C10"]
    assert proxy.diagnostics["case-1"].raw_union_ids == ("Q-1", "Q-2")


def test_proxy_without_case_lookup_returns_production_candidates_unchanged():
    base = _FakeBaseSearcher()
    proxy = GoalUnionSearcherProxy(base, {"case-1": (_goal_hit("Q-2"),)})

    result = proxy.search("query", top_k=20, item={"_accuracy_case_id": "missing"})

    assert result == base.production_candidates
    assert "missing" not in proxy.diagnostics


def test_proxy_accumulates_production_ids_across_cascade_calls():
    base = _FakeBaseSearcher()
    proxy = GoalUnionSearcherProxy(base, {"case-1": (_goal_hit("Q-3"),)})

    proxy.search("first", item={"_accuracy_case_id": "case-1"})
    base.production_candidates = [
        {"quota_id": "Q-2", "name": "Second", "hybrid_score": 0.5}
    ]
    proxy.search("second", item={"_accuracy_case_id": "case-1"})

    diagnostics = proxy.diagnostics["case-1"]
    assert diagnostics.production_ids == ("Q-1", "Q-2")
    assert diagnostics.goal_ids == ("Q-3",)
    assert diagnostics.raw_union_ids == ("Q-1", "Q-3", "Q-2")


def test_proxy_applies_40_10_budget_before_cascade_truncation():
    base = _FakeBaseSearcher()
    base.production_candidates = [
        {
            "quota_id": f"P-{index}",
            "name": f"Production {index}",
            "hybrid_score": 1.0 - index / 100,
        }
        for index in range(45)
    ]
    goal_hits = tuple(
        _goal_hit(f"G-{index}", score=1.0 - index / 100)
        for index in range(20)
    )
    proxy = GoalUnionSearcherProxy(
        base,
        {"case-1": goal_hits},
        candidate_budget_policy="production_40_goal_10",
    )

    result = proxy.search(
        "query",
        top_k=50,
        item={"_accuracy_case_id": "case-1"},
    )

    assert [candidate["quota_id"] for candidate in result[:40]] == [
        f"P-{index}" for index in range(40)
    ]
    assert [candidate["quota_id"] for candidate in result[40:50]] == [
        f"G-{index}" for index in range(10)
    ]
    assert {candidate["quota_id"] for candidate in result} == {
        *(f"P-{index}" for index in range(45)),
        *(f"G-{index}" for index in range(20)),
    }
    diagnostics = proxy.budget_diagnostics["case-1"]
    assert len(diagnostics) == 1
    assert diagnostics[0].production_slots == 40
    assert diagnostics[0].goal_only_slots == 10


def _case(case_id="case-1", province="demo-province"):
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province=province,
        bill_name="Valve",
        bill_text="DN50",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-2",),
        source_family="human",
        project_id="project-a",
        source="source-a",
    )


def _detail(case_id):
    return {
        "sample_id": case_id,
        "recall_topk_ids": ["Q-1", "Q-2"],
        "final_quota_ids": ["Q-1"],
        "algo_id": "Q-1",
        "post_final_top1_id": "Q-1",
        "candidate_snapshots": [
            {"quota_id": "Q-1", "name": "A"},
            {"quota_id": "Q-2", "name": "B"},
        ],
        "candidate_lifecycle_trace": [
            {"quota_id": "Q-1", "first_seen_stage": "hybrid"},
            {"quota_id": "Q-2", "first_seen_stage": "goal_shadow_union"},
        ],
        "union_shadow_diagnostics": {
            "production_retrieved_ids": ["Q-1"],
            "goal_retrieved_ids": ["Q-2"],
            "raw_union_ids": ["Q-1", "Q-2"],
            "goal_unique_ids": ["Q-2"],
            "materialized_goal_ids": ["Q-2"],
            "missing_local_goal_ids": [],
        },
    }


def test_union_shadow_provider_groups_provinces_and_exposes_diagnostics():
    calls = []

    def executor(province, records, *, goal_top_k, candidate_budget_policy):
        calls.append(
            (
                province,
                [record["sample_id"] for record in records],
                goal_top_k,
                candidate_budget_policy,
            )
        )
        return {"details": [_detail(record["sample_id"]) for record in records]}

    provider = GoalUnionShadowProvider(
        executor=executor,
        goal_top_k=80,
        candidate_budget_policy="production_40_goal_10",
    )

    results = provider.run(
        [_case("b", "province-b"), _case("a", "province-a")]
    )

    assert calls == [
        ("province-a", ["a"], 80, "production_40_goal_10"),
        ("province-b", ["b"], 80, "production_40_goal_10"),
    ]
    assert [result.case_id for result in results] == ["a", "b"]
    assert all(result.provider_name == "production_goal_union_shadow" for result in results)
    assert all(result.status == ProviderStatus.OK for result in results)
    assert results[0].runtime_metadata["experiment"] == "production_goal_candidate_union_shadow_v1"
    assert results[0].runtime_metadata["goal_unique_ids"] == ["Q-2"]


def test_union_shadow_provider_classifies_algorithm_failure():
    def executor(province, records, *, goal_top_k, candidate_budget_policy):
        if province == "bad-province":
            raise RuntimeError("broken")
        return {"details": [_detail(record["sample_id"]) for record in records]}

    provider = GoalUnionShadowProvider(executor=executor)
    results = provider.run([_case("bad", "bad-province"), _case("good", "good-province")])
    by_id = {result.case_id: result for result in results}

    assert by_id["bad"].status == ProviderStatus.PROVIDER_ERROR
    assert by_id["good"].status == ProviderStatus.OK


class _FakeGoalSearcher:
    def __init__(self):
        self.items = []

    def search(self, item, top_k):
        self.items.append((deepcopy(item), top_k))
        return [_goal_hit("Q-2")]


def test_evaluate_union_records_applies_goal_leakage_exclusions_and_runs_proxy():
    base = _FakeBaseSearcher()
    goal = _FakeGoalSearcher()
    matcher_calls = []

    def init_components(*, resolved_province):
        assert resolved_province == "demo-province"
        return base, object()

    def matcher(bill_items, searcher, validator, *, experience_db, province):
        matcher_calls.append((bill_items, searcher, validator, experience_db, province))
        results = []
        for item in bill_items:
            candidates = searcher.search(item["name"], top_k=50, item=item)
            results.append(
                {
                    "quotas": candidates[:1],
                    "recall_topk_ids": [candidate["quota_id"] for candidate in candidates],
                    "all_candidate_ids": [candidate["quota_id"] for candidate in candidates],
                    "candidate_snapshots": candidates,
                    "candidate_lifecycle_trace": [
                        {
                            "quota_id": candidate["quota_id"],
                            "first_seen_stage": candidate.get("match_source", "hybrid"),
                        }
                        for candidate in candidates
                    ],
                    "post_final_top1_id": candidates[0]["quota_id"],
                }
            )
        return results

    record = _case().to_record()
    payload = evaluate_union_province_records(
        "demo-province",
        [record],
        init_components=init_components,
        goal_searcher_factory=lambda _province: goal,
        matcher=matcher,
        summarizer=lambda _province, details, _elapsed: {"details": details},
        candidate_budget_policy="production_40_goal_10",
    )

    goal_item, top_k = goal.items[0]
    assert top_k == 80
    assert goal_item["goal_no_answer_priors"] is True
    assert goal_item["goal_excluded_sources"] == {
        "sample_id": {"case-1"},
        "source_file": {"source-a"},
        "project_name": {"project-a"},
    }
    assert len(matcher_calls) == 2
    assert matcher_calls[0][1] is base
    assert matcher_calls[1][0][0]["_accuracy_case_id"] == "case-1"
    diagnostics = payload["details"][0]["union_shadow_diagnostics"]
    assert diagnostics["raw_union_ids"] == ["Q-1", "Q-2"]
    assert diagnostics["materialized_goal_ids"] == ["Q-2"]
    assert diagnostics["candidate_budget_policy"] == "production_40_goal_10"
    assert diagnostics["candidate_budget_calls"][0]["requested_limit"] == 50


def test_evaluate_union_records_freezes_standalone_production_recall_before_goal_union():
    base = _FakeBaseSearcher()
    base.production_candidates = [
        {"quota_id": "Q-WIDE", "name": "Wide", "hybrid_score": 0.6}
    ]
    goal = _FakeGoalSearcher()
    matcher_calls = []

    def matcher(bill_items, searcher, _validator, **_kwargs):
        matcher_calls.append(searcher)
        if len(matcher_calls) == 1:
            return [
                {
                    "quotas": [{"quota_id": "Q-WIDE"}],
                    "recall_topk_ids": ["Q-WIDE", "Q-RECOVERED"],
                    "candidate_snapshots": [
                        {"quota_id": "Q-WIDE", "name": "Wide"},
                        {"quota_id": "Q-RECOVERED", "name": "Recovered"},
                    ],
                    "post_final_top1_id": "Q-WIDE",
                }
            ]
        item = bill_items[0]
        candidates = searcher.search(item["name"], item=item)
        candidate_ids = [candidate["quota_id"] for candidate in candidates]
        return [
            {
                "quotas": candidates[:1],
                "recall_topk_ids": candidate_ids,
                "candidate_snapshots": candidates,
                "candidate_lifecycle_trace": [
                    {
                        "quota_id": candidate["quota_id"],
                        "first_seen_stage": candidate.get("match_source", "hybrid"),
                    }
                    for candidate in candidates
                ],
                "post_final_top1_id": candidate_ids[0],
            }
        ]

    payload = evaluate_union_province_records(
        "demo-province",
        [_case().to_record()],
        init_components=lambda **_kwargs: (base, object()),
        goal_searcher_factory=lambda _province: goal,
        matcher=matcher,
        summarizer=lambda _province, details, _elapsed: {"details": details},
    )

    diagnostics = payload["details"][0]["union_shadow_diagnostics"]
    assert diagnostics["production_retrieved_ids"] == ["Q-WIDE", "Q-RECOVERED"]
    assert diagnostics["raw_union_ids"] == ["Q-WIDE", "Q-RECOVERED", "Q-2"]


def _union_result(case_id, retrieved_ids, metadata):
    candidates = tuple(
        CandidateSnapshot(
            quota_id=quota_id,
            name=quota_id,
            unit="set",
            province="demo-province",
            provider="production_goal_union_shadow",
            source="union",
            stage=LifecycleStage.RETRIEVED,
            rank=rank,
        )
        for rank, quota_id in enumerate(retrieved_ids, start=1)
    )
    return ProviderResult(
        case_id=case_id,
        provider_name="production_goal_union_shadow",
        status=ProviderStatus.OK,
        final_quota_ids=retrieved_ids[:1],
        lifecycle=(
            StageSnapshot(
                stage=LifecycleStage.RETRIEVED,
                emitted=True,
                candidates=candidates,
                top1_id=retrieved_ids[0] if retrieved_ids else "",
            ),
        ),
        runtime_metadata=metadata,
    )


def test_aggregate_union_shadow_metrics_separates_raw_and_rankable_recall():
    cases = [
        _case("case-1"),
        EvalCase(
            case_id="case-2",
            dataset_kind=DatasetKind.PRIMARY,
            province="demo-province",
            bill_name="Pipe",
            bill_text="DN80",
            unit="m",
            specialty="C10",
            oracle_quota_ids=("Q-1",),
            source_family="human",
            project_id="project-b",
        ),
    ]
    results = [
        _union_result(
            "case-1",
            ("Q-1", "Q-2"),
            {
                "production_retrieved_ids": ["Q-1"],
                "goal_retrieved_ids": ["Q-2"],
                "raw_union_ids": ["Q-1", "Q-2"],
                "missing_local_goal_ids": [],
            },
        ),
        _union_result(
            "case-2",
            ("Q-1",),
            {
                "production_retrieved_ids": ["Q-1"],
                "goal_retrieved_ids": ["Q-3"],
                "raw_union_ids": ["Q-1", "Q-3"],
                "missing_local_goal_ids": ["Q-MISSING"],
            },
        ),
    ]

    report = aggregate_union_shadow_metrics(cases, results)

    assert report["valid_cases"] == 2
    assert report["production_recalled_count"] == 1
    assert report["goal_recalled_count"] == 1
    assert report["raw_union_recalled_count"] == 2
    assert report["goal_unique_recall_gain"] == 1
    assert report["rankable_recalled_count"] == 2
    assert report["raw_union_recall"] == 1.0
    assert report["rankable_recall"] == 1.0
    assert report["missing_local_goal_candidate_count"] == 1


def test_union_shadow_metrics_use_system_denominator_and_all_semantics():
    all_case = EvalCase(
        case_id="case-all",
        dataset_kind=DatasetKind.PRIMARY,
        province="demo-province",
        bill_name="Composite",
        bill_text="",
        unit="set",
        specialty="C10",
        oracle_quota_ids=("Q-1", "Q-2"),
        oracle_semantics=OracleSemantics.ALL,
        source_family="human",
        project_id="project-a",
    )
    result = _union_result(
        "case-all",
        retrieved_ids=("Q-1",),
        metadata={
            "production_retrieved_ids": ["Q-1"],
            "goal_retrieved_ids": ["Q-2"],
            "raw_union_ids": ["Q-1", "Q-2"],
        },
    )

    report = aggregate_union_shadow_metrics(
        [all_case, _case("missing-case")],
        [result],
    )

    assert report["system_denominator"] == 2
    assert report["valid_cases"] == 1
    assert report["production_recalled_count"] == 0
    assert report["goal_recalled_count"] == 0
    assert report["raw_union_recalled_count"] == 1
    assert report["raw_union_recall"] == 0.5
    assert report["provider_failure_count"] == 1
    assert report["provider_failure_rate"] == 0.5
