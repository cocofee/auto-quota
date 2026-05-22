from src import match_core
from src.match_pipeline import _build_search_result_from_candidates, _run_rank_pipeline
import src.match_pipeline.orchestrator as orchestrator
from tools.classify_retriever_miss import extract_search_books


def test_cascade_search_records_main_resolved_books():
    calls = []
    classification = {
        "primary": "C10",
        "fallbacks": ["C9"],
        "search_books": ["C10", "C9"],
        "hard_book_constraints": ["C10", "C9"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    class FakeSearcher:
        aux_searchers = []
        uses_standard_books = True

        def search(self, query, top_k=None, books=None):
            del query, top_k
            calls.append(list(books) if books is not None else None)
            return []

    match_core.cascade_search(FakeSearcher(), "pipe dn50", classification, top_k=5)

    assert calls == [["C10"], ["C10", "C9"]]
    assert classification["retrieval_resolution"]["calls"] == [
        {
            "target": "main",
            "stage": "primary",
            "source_province": "",
            "requested_books": ["C10"],
            "resolved_books": ["C10"],
            "open_search": False,
            "uses_standard_books": True,
        },
        {
            "target": "main",
            "stage": "expanded",
            "source_province": "",
            "requested_books": ["C10", "C9"],
            "resolved_books": ["C10", "C9"],
            "open_search": False,
            "uses_standard_books": True,
        },
    ]


def test_cascade_search_records_mixed_search_trace_counts():
    class FakeSearcher:
        aux_searchers = []
        uses_standard_books = True

        def search(self, query, top_k=None, books=None):
            del query, top_k, books
            return [
                {
                    "quota_id": "C4-1-1",
                    "hybrid_score": 0.9,
                    "_mixed_search_trace": {
                        "hybrid_search_call_count": 1,
                        "session_cache_hit_count": 0,
                        "session_cache_miss_count": 1,
                        "bm25_call_count": 2,
                        "vector_call_count": 2,
                        "vector_filter_fallback_count": 0,
                        "old_vector_index_fallback_count": 0,
                        "query_variant_count": 2,
                        "bm25_hit_count": 10,
                        "vector_hit_count": 8,
                        "substage_sec": {
                            "bm25_search": 0.02,
                            "vector_search": 0.05,
                        },
                        "slowest_substage": "vector_search",
                    },
                }
            ]

    classification = {
        "primary": "C4",
        "search_books": ["C4"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "lamp", classification, top_k=5)

    totals = classification["retrieval_resolution"]["mixed_search_totals"]
    assert totals["cascade_stage_count"] == 1
    assert totals["hybrid_search_call_count"] == 1
    assert totals["session_cache_miss_count"] == 1
    assert totals["bm25_call_count"] == 2
    assert totals["vector_call_count"] == 2
    assert totals["query_variant_count"] == 2
    assert totals["slowest_substage"] == "vector_search"
    assert classification["retrieval_resolution"]["mixed_search_traces"][0]["stage"] == "primary"


def test_prepare_candidates_materializes_canonical_features_before_rerank(monkeypatch):
    calls = []
    classification = {"search_books": [], "primary": "C4"}

    def fake_cascade_search(*args, **kwargs):
        del args, kwargs
        return [
            {
                "quota_id": "C4-2-1",
                "name": "成套配电箱安装 悬挂嵌入式 半周长1.0m",
                "unit": "台",
                "hybrid_score": 0.2,
            },
            {
                "quota_id": "C4-2-2",
                "name": "已有特征候选",
                "unit": "台",
                "hybrid_score": 0.1,
                "candidate_canonical_features": {
                    "family": "existing_family",
                    "entity": "existing_entity",
                    "canonical_name": "existing_name",
                },
            },
        ]

    def fake_no_store(candidate, specialty=""):
        calls.append((candidate["quota_id"], specialty))
        if candidate["quota_id"] == "C4-2-2":
            return {
                "family": "rebuilt_family",
                "entity": "rebuilt_entity",
                "canonical_name": "rebuilt_name",
                "material": "steel",
                "connection": "bolt",
                "install_method": "surface",
                "numeric_params": {"circuits": 4},
            }
        return {
            "family": "electrical_box",
            "entity": "配电箱",
            "canonical_name": "配电箱",
        }

    class FakeReranker:
        def rerank(self, query, candidates, route_profile=None):
            del query, route_profile
            by_id = {candidate["quota_id"]: candidate for candidate in candidates}
            materialized = by_id["C4-2-1"]
            existing = by_id["C4-2-2"]
            assert materialized["candidate_canonical_features"]["family"] == "electrical_box"
            assert materialized["candidate_feature_source"] == "no_store"
            assert materialized["candidate_feature_materialized"] is True
            assert existing["candidate_canonical_features"]["family"] == "existing_family"
            assert existing["candidate_canonical_features"]["entity"] == "existing_entity"
            assert existing["candidate_canonical_features"]["material"] == "steel"
            assert existing["candidate_canonical_features"]["connection"] == "bolt"
            assert existing["candidate_canonical_features"]["install_method"] == "surface"
            assert existing["candidate_canonical_features"]["numeric_params"]["circuits"] == 4
            assert existing["candidate_feature_source"] == "existing+no_store"
            return candidates

    class FakeValidator:
        def validate_candidates(self, full_query, candidates, **kwargs):
            del full_query, kwargs
            return candidates

    monkeypatch.setattr(match_core, "cascade_search", fake_cascade_search)
    monkeypatch.setattr(match_core, "build_candidate_canonical_features_no_store", fake_no_store)

    result = match_core._prepare_candidates(
        object(),
        FakeReranker(),
        FakeValidator(),
        "配电箱",
        "配电箱",
        classification,
        include_prior_candidates=False,
    )

    assert [candidate["quota_id"] for candidate in result] == ["C4-2-1", "C4-2-2"]
    assert calls == [("C4-2-1", "C4"), ("C4-2-2", "C4")]
    assert classification["retrieval_resolution"]["rankable_pool_feature_materialization"] == {
        "checked_count": 2,
        "existing_count": 1,
        "hydrated_existing_count": 1,
        "no_store_count": 2,
        "empty_count": 0,
        "core_ready_count": 2,
    }
    outer = classification["retrieval_resolution"]["mixed_search_totals"]["outer_substage_sec"]
    assert set(outer) >= {
        "cascade_search",
        "route_scope_filter",
        "effective_guard_scope",
        "rankable_feature_materialization",
    }
    assert classification["retrieval_resolution"]["mixed_search_totals"]["outer_substage_total_sec"] >= 0


def test_cascade_search_records_aux_resolved_books_for_nonstandard_searcher():
    class FakeAux:
        province = "Aux Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "2", 2: "4"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["4"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            assert books == ["4", "2"]
            return []

    class FakeSearcher:
        aux_searchers = [FakeAux()]
        uses_standard_books = True

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, books, item, context_prior
            return []

    classification = {
        "primary": "C2",
        "fallbacks": ["C8"],
        "search_books": ["C2", "C8"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "inverter", classification, top_k=5)

    aux_calls = [
        call
        for call in classification["retrieval_resolution"]["calls"]
        if call["target"] == "aux"
    ]
    assert aux_calls == [
        {
            "target": "aux",
            "stage": "aux",
            "source_province": "Aux Province",
            "requested_books": ["C2", "C8"],
            "resolved_books": ["4", "2"],
            "open_search": False,
            "uses_standard_books": False,
        }
    ]


def test_nonstandard_main_resolution_does_not_expand_outside_requested_projection():
    calls = []

    class FakeSearcher:
        aux_searchers = []
        province = "Numeric Install Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "5", 2: "10", 3: "12", 4: "13"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["10", "13", "12"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(list(books) if books is not None else None)
            if books == ["5"]:
                return [
                    {"quota_id": "5-1"},
                    {"quota_id": "5-2"},
                    {"quota_id": "5-3"},
                    {"quota_id": "5-4"},
                    {"quota_id": "5-5"},
                ]
            return []

    classification = {
        "primary": "C5",
        "search_books": ["C5", "C4", "C13", "C12"],
        "route_mode": "moderate",
        "allow_cross_book_escape": True,
    }

    match_core.cascade_search(FakeSearcher(), "smart meter", classification, top_k=5)

    assert calls == [["5"]]
    assert classification["retrieval_resolution"]["calls"][0] == {
        "target": "main",
        "stage": "primary",
        "source_province": "Numeric Install Province",
        "requested_books": ["C5"],
        "resolved_books": ["5"],
        "open_search": False,
        "uses_standard_books": False,
    }


def test_cascade_search_deep_escape_retains_primary_stage_candidates():
    class FakeSearcher:
        aux_searchers = []
        province = "main-install"
        uses_standard_books = True

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            normalized_books = list(books) if books is not None else None
            if normalized_books == ["C4"]:
                return [
                    {"quota_id": "C4-12-192", "name": "oracle", "hybrid_score": 0.40},
                    {"quota_id": "C4-12-193", "name": "neighbor", "hybrid_score": 0.39},
                ]
            if normalized_books == ["C4", "C5", "C13", "C12"]:
                return [
                    {"quota_id": "C4-12-231", "name": "expanded", "hybrid_score": 0.41},
                ]
            if normalized_books is None:
                return [
                    {"quota_id": "D2-5-29", "name": "escape", "hybrid_score": 0.90},
                ]
            return []

    classification = {
        "primary": "C4",
        "fallbacks": ["C5", "C13", "C12"],
        "search_books": ["C4", "C5", "C13", "C12"],
        "route_mode": "moderate",
        "allow_cross_book_escape": True,
    }

    results = match_core.cascade_search(
        FakeSearcher(),
        "标志、诱导装饰灯具安装 墙壁式",
        classification,
        top_k=10,
        adaptive_strategy="deep",
    )

    assert [row["quota_id"] for row in results] == [
        "D2-5-29",
        "C4-12-231",
        "C4-12-192",
        "C4-12-193",
    ]
    assert [call["stage"] for call in classification["retrieval_resolution"]["calls"]] == [
        "primary",
        "expanded",
        "escape",
    ]


def test_retain_primary_stage_candidates_after_rerank_drop():
    reranked = [
        {"quota_id": "D2-5-29", "name": "escape", "hybrid_score": 0.90},
    ]
    prerank = [
        {
            "quota_id": "C4-12-192",
            "name": "oracle",
            "hybrid_score": 0.40,
            "_cascade_stage": "primary",
            "_cascade_stages": ["primary"],
        },
        {
            "quota_id": "D2-5-29",
            "name": "escape",
            "hybrid_score": 0.90,
            "_cascade_stage": "escape",
            "_cascade_stages": ["escape"],
        },
    ]

    retained = match_core._retain_primary_stage_candidates(reranked, prerank)

    assert [row["quota_id"] for row in retained] == ["D2-5-29", "C4-12-192"]


def test_rank_pipeline_ignores_low_score_lifecycle_advisory(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "wrong",
                "post_cgr_top1_id": "wrong",
                "reason": "test_low_score_advisory",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "wrong",
            "name": "low score advisory",
            "_rank_score_source": "manual",
            "manual_structured_score": 0.20,
            "param_score": 0.82,
            "feature_alignment_score": 0.90,
            "param_match": True,
        },
        {
            "quota_id": "right",
            "name": "strong retained candidate",
            "_rank_score_source": "ltr",
            "ltr_score": 0.78,
            "param_score": 0.80,
            "feature_alignment_score": 0.82,
            "param_match": True,
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {"query_route": {"route": "installation_spec"}},
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "right"
    assert best["quota_id"] == "right"
    assert ranking_meta["post_ltr_top1_id"] == "wrong"
    assert ranking_meta["post_final_top1_id"] == "right"
    assert ranking_meta["ltr"]["lifecycle_guard"]["to_quota_id"] == "right"
    assert ranking_meta["ltr"]["lifecycle_guard"]["advisory_only"] is True
    assert ranking_meta["ltr"]["lifecycle_guard"]["advisory_applied"] is True
    lifecycle_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "ltr_lifecycle_guard"
    )
    assert lifecycle_advisory["accepted_by_final_decider"] is True
    assert lifecycle_advisory["selected_quota_id"] == "right"
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_cgr_lifecycle_guard_keeps_structurally_stronger_candidate(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "weak_ltr_choice",
                "post_cgr_top1_id": "strong_structured_choice",
                "reason": "test_cgr_advisory",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "weak_ltr_choice",
            "name": "similar candidate with stronger model score only",
            "_rank_score_source": "ltr",
            "ltr_score": 0.78,
            "manual_structured_score": 0.69,
            "param_score": 0.68,
            "feature_alignment_score": 0.96,
            "rerank_score": 0.95,
            "name_bonus": 0.38,
            "feature_alignment_exact_anchor_count": 2,
            "param_tier": 1,
            "param_match": True,
        },
        {
            "quota_id": "strong_structured_choice",
            "name": "candidate with better structured evidence",
            "_rank_score_source": "ltr",
            "ltr_score": 0.24,
            "manual_structured_score": 0.73,
            "param_score": 0.76,
            "feature_alignment_score": 0.98,
            "rerank_score": 0.99,
            "name_bonus": 0.46,
            "feature_alignment_exact_anchor_count": 2,
            "param_tier": 1,
            "param_match": True,
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {"query_route": {"route": "installation_spec"}},
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "strong_structured_choice"
    assert best["quota_id"] == "strong_structured_choice"
    assert ranking_meta["post_cgr_top1_id"] == "weak_ltr_choice"
    assert ranking_meta["post_cgr_advisory_top1_id"] == "strong_structured_choice"
    assert ranking_meta["selected_top1_id"] == "strong_structured_choice"
    advisory_by_stage = {item["stage"]: item for item in ranking_meta["decision_advisories"]}
    assert advisory_by_stage["post_cgr"]["accepted_by_final_decider"] is True
    assert advisory_by_stage["post_cgr"]["selected_quota_id"] == "strong_structured_choice"
    assert "cgr_lifecycle_guard" not in ranking_meta["ltr"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_promotes_same_book_family_structural_winner(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "ltr_favored_valve",
                "post_cgr_top1_id": "ltr_favored_valve",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C7-3-88",
            "name": "same family ltr favored",
            "_rank_score_source": "ltr",
            "ltr_score": 0.60,
            "manual_structured_score": 0.66,
            "param_score": 0.61,
            "logic_score": 0.55,
            "feature_alignment_score": 0.88,
            "rerank_score": 0.90,
            "name_bonus": 0.10,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "air_valve",
                "canonical_name": "风阀",
                "material": "plastic",
            },
        },
        {
            "quota_id": "C7-3-53",
            "name": "same family stronger structure",
            "_rank_score_source": "ltr",
            "ltr_score": 0.12,
            "manual_structured_score": 0.78,
            "param_score": 0.75,
            "logic_score": 0.72,
            "feature_alignment_score": 0.96,
            "rerank_score": 0.97,
            "name_bonus": 0.22,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "air_valve",
                "canonical_name": "风阀",
                "material": "steel",
            },
        },
        {
            "quota_id": "C12-3-53",
            "name": "cross book candidate",
            "_rank_score_source": "ltr",
            "ltr_score": 0.21,
            "manual_structured_score": 0.61,
            "param_score": 0.60,
            "logic_score": 0.52,
            "feature_alignment_score": 0.85,
            "rerank_score": 0.88,
            "name_bonus": 0.08,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "air_valve",
                "canonical_name": "风阀",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "air_valve",
                "canonical_name": "风阀",
                "material": "steel",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C7-3-53"
    assert best["quota_id"] == "C7-3-53"
    assert ranking_meta["post_ltr_top1_id"] == "C7-3-88"
    assert ranking_meta["post_ltr_structural_top1_id"] == "C7-3-53"
    assert ranking_meta["ltr"]["post_ltr_structural_ranker"]["to_quota_id"] == "C7-3-53"
    assert ranking_meta["ltr"]["post_ltr_structural_ranker"]["source_stage"] == "post_ltr_structural_comparator"
    assert ranking_meta["ltr"]["post_ltr_structural_ranker"]["legacy_source_stage"] == "family_group_ranker"
    assert ranking_meta["ltr"]["post_ltr_structural_ranker"]["contract"] == "post_ltr_structural_comparator"
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["accepted_by_final_decider"] is True
    assert structural_advisory["selected_quota_id"] == "C7-3-53"
    assert structural_advisory["source_stage"] == "post_ltr_structural_comparator"
    assert ranking_meta["ltr"]["family_group_ranker"]["to_quota_id"] == "C7-3-53"
    assert ranking_meta["ltr"]["family_group_ranker"]["book_key"] == "C7"
    assert ranking_meta["ltr"]["family_group_ranker"]["canonical_advisory_stage"] == "post_ltr_structural_ranker"
    assert ranking_meta["ltr"]["family_group_ranker"]["advisory_only"] is True
    assert all(item["stage"] != "family_group_ranker" for item in ranking_meta["decision_advisories"])
    assert ranking_meta["rank_stage_trace_steps"][0]["overridden"] is False
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_uses_primary_parameter_direction(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "general_sleeve",
                "post_cgr_top1_id": "general_sleeve",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C10-4-65",
            "name": "general_sleeve",
            "_rank_score_source": "ltr",
            "ltr_score": 0.10,
            "manual_structured_score": 0.78,
            "param_score": 0.92,
            "logic_score": 0.86,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.91,
            "name_bonus": 0.10,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "pipe_sleeve",
                "canonical_name": "一般填料套管",
                "entity": "套管",
                "material": "一般填料套管",
                "dn": 80,
                "numeric_params": {"dn": 80},
            },
        },
        {
            "quota_id": "C10-4-20",
            "name": "rigid_waterproof_sleeve",
            "_rank_score_source": "ltr",
            "ltr_score": 0.30,
            "manual_structured_score": 0.76,
            "param_score": 0.92,
            "logic_score": 0.86,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.84,
            "name_bonus": 0.10,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "pipe_sleeve",
                "canonical_name": "刚性防水套管",
                "entity": "套管",
                "material": "刚性防水套管",
                "dn": 80,
                "numeric_params": {"dn": 80},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "pipe_sleeve",
                "canonical_name": "刚性防水套管",
                "entity": "套管",
                "material": "刚性防水套管",
                "dn": 80,
                "numeric_params": {"dn": 80},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C10-4-20"
    assert best["quota_id"] == "C10-4-20"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["to_quota_id"] == "C10-4-20"
    assert family_group_ranker["pool_scan"]["mode"] == "same_book_same_family_prefix_tier_top20"
    assert family_group_ranker["advisory_only"] is True
    assert "primary_param:canonical_name" in family_group_ranker["evidence_edges"]
    assert "primary_param:material" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_uses_same_book_prefix_tier_structural_evidence(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-8-32",
                "post_cgr_top1_id": "C4-8-32",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-8-32",
            "name": "cable trench support tier",
            "_rank_score_source": "ltr",
            "ltr_score": 0.50,
            "manual_structured_score": 0.82,
            "param_score": 0.88,
            "logic_score": 0.86,
            "feature_alignment_score": 0.93,
            "rerank_score": 0.95,
            "name_bonus": 0.16,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "install_method": "trench",
                "material": "aluminum",
                "cable_section": 6,
                "numeric_params": {"cable_section": 6},
            },
        },
        {
            "quota_id": "C4-8-22",
            "name": "cable bridge tray tier",
            "_rank_score_source": "ltr",
            "ltr_score": 0.18,
            "manual_structured_score": 0.74,
            "param_score": 0.86,
            "logic_score": 0.78,
            "feature_alignment_score": 0.88,
            "rerank_score": 0.91,
            "name_bonus": 0.14,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "install_method": "bridge tray",
                "material": "copper",
                "cable_section": 6,
                "numeric_params": {"cable_section": 6},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "install_method": "bridge tray",
                "material": "copper",
                "cable_section": 6,
                "numeric_params": {"cable_section": 6},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-8-22"
    assert best["quota_id"] == "C4-8-22"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["pool_scan"]["mode"] == "same_book_same_prefix_tier_top20"
    assert family_group_ranker["advisory_only"] is True
    assert "primary_param:install_method" in family_group_ranker["evidence_edges"]
    assert "primary_param:material" in family_group_ranker["evidence_edges"]
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["accepted_by_final_decider"] is True
    assert structural_advisory["selected_quota_id"] == "C4-8-22"
    assert all(item["stage"] != "family_group_ranker" for item in ranking_meta["decision_advisories"])
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_same_family_prefix_tier_does_not_promote_on_score_edges_only(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-4-30",
                "post_cgr_top1_id": "C4-4-30",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-4-30",
            "name": "control box wall mounted 4 circuits",
            "_rank_score_source": "ltr",
            "ltr_score": 0.41,
            "manual_structured_score": 0.70,
            "param_score": 0.80,
            "logic_score": 0.80,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.88,
            "name_bonus": 0.12,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "install_method": "surface",
            },
        },
        {
            "quota_id": "C4-4-37",
            "name": "control box half perimeter 2.5m",
            "_rank_score_source": "ltr",
            "ltr_score": 0.40,
            "manual_structured_score": 0.74,
            "param_score": 0.80,
            "logic_score": 0.80,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.92,
            "name_bonus": 0.12,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "install_method": "surface",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "install_method": "surface",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-4-30"
    assert best["quota_id"] == "C4-4-30"
    assert "family_group_ranker" not in ranking_meta["ltr"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_reports_unified_structural_evidence(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C2-4-10",
                "post_cgr_top1_id": "C2-4-10",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C2-4-10",
            "name": "surface junction box",
            "_rank_score_source": "ltr",
            "ltr_score": 0.44,
            "manual_structured_score": 0.78,
            "param_score": 0.80,
            "logic_score": 0.85,
            "feature_alignment_score": 0.92,
            "rerank_score": 0.93,
            "name_bonus": 0.20,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "junction_box",
                "canonical_name": "junction box",
                "entity": "box",
                "material": "plastic",
                "install_method": "surface",
                "connection": "clip",
            },
        },
        {
            "quota_id": "C2-4-11",
            "name": "flush steel threaded junction box",
            "_rank_score_source": "ltr",
            "ltr_score": 0.38,
            "manual_structured_score": 0.70,
            "param_score": 0.74,
            "logic_score": 0.80,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.92,
            "name_bonus": 0.18,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "junction_box",
                "canonical_name": "junction box",
                "entity": "box",
                "material": "steel",
                "install_method": "flush",
                "connection": "threaded",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "junction_box",
                "canonical_name": "junction box",
                "entity": "box",
                "material": "steel",
                "install_method": "flush",
                "connection": "threaded",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C2-4-11"
    assert best["quota_id"] == "C2-4-11"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["to_quota_id"] == "C2-4-11"
    structural = family_group_ranker["structural_ranking"]
    assert structural["entry"] == "unified_same_family_structural_ranker"
    assert structural["evidence_groups"]["material"] == ["material"]
    assert structural["evidence_groups"]["install_method"] == ["install_method"]
    assert structural["evidence_groups"]["connection"] == ["connection"]
    assert "primary_param:material" in family_group_ranker["evidence_edges"]
    assert "primary_param:install_method" in family_group_ranker["evidence_edges"]
    assert "primary_param:connection" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_scans_same_family_top20_pool(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-4-31",
                "post_cgr_top1_id": "C4-4-31",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-4-31",
            "name": "surface plastic control box",
            "_rank_score_source": "ltr",
            "ltr_score": 0.45,
            "manual_structured_score": 0.78,
            "param_score": 0.83,
            "logic_score": 0.82,
            "feature_alignment_score": 0.92,
            "rerank_score": 0.94,
            "name_bonus": 0.16,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "plastic",
                "install_method": "surface",
                "connection": "clip",
                "circuits": 8,
                "numeric_params": {"circuits": 8},
            },
        }
    ]
    for idx in range(2, 14):
        candidates.append({
            "quota_id": f"C4-4-{30 + idx}",
            "name": f"same family filler {idx}",
            "_rank_score_source": "ltr",
            "ltr_score": 0.40 - idx * 0.005,
            "manual_structured_score": 0.74,
            "param_score": 0.78,
            "logic_score": 0.80,
            "feature_alignment_score": 0.88,
            "rerank_score": 0.90,
            "name_bonus": 0.12,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "plastic",
                "install_method": "surface",
                "connection": "clip",
                "circuits": 12 + idx,
                "numeric_params": {"circuits": 12 + idx},
            },
        })
    candidates.append({
        "quota_id": "C4-4-30",
        "name": "flush steel threaded control box",
        "_rank_score_source": "ltr",
        "ltr_score": 0.38,
        "manual_structured_score": 0.72,
        "param_score": 0.78,
        "logic_score": 0.80,
        "feature_alignment_score": 0.90,
        "rerank_score": 0.91,
        "name_bonus": 0.14,
        "param_tier": 1,
        "param_match": True,
        "candidate_canonical_features": {
            "family": "electrical_box",
            "canonical_name": "control box",
            "entity": "box",
            "material": "steel",
            "install_method": "flush",
            "connection": "threaded",
            "circuits": 4,
            "numeric_params": {"circuits": 4},
        },
    })

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "steel",
                "install_method": "flush",
                "connection": "threaded",
                "circuits": 4,
                "numeric_params": {"circuits": 4},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-4-30"
    assert best["quota_id"] == "C4-4-30"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["pool_scan"]["mode"] == "same_book_same_family_prefix_tier_top20"
    assert family_group_ranker["pool_scan"]["selected_original_rank"] == 14
    assert "primary_param:material" in family_group_ranker["evidence_edges"]
    assert "primary_param:install_method" in family_group_ranker["evidence_edges"]
    assert "primary_param:connection" in family_group_ranker["evidence_edges"]
    assert "decisive_primary_param:circuits" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_uses_bill_guided_distribution_box_subject(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "4-13-175",
                "post_cgr_top1_id": "4-13-175",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "4-13-175",
            "name": "接线箱暗装 半周长(mm) ≤1500",
            "_rank_score_source": "ltr",
            "ltr_score": 0.48,
            "manual_structured_score": 0.72,
            "param_score": 0.78,
            "logic_score": 0.78,
            "feature_alignment_score": 0.88,
            "rerank_score": 0.92,
            "name_bonus": 0.14,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "conduit_raceway",
                "canonical_name": "接线盒",
                "entity": "接线盒",
                "install_method": "暗装",
                "half_perimeter": 1.5,
                "numeric_params": {"half_perimeter": 1.5},
            },
        },
        {
            "quota_id": "4-2-76",
            "name": "成套配电箱安装 悬挂、嵌入式(半周长) 1.0m",
            "_rank_score_source": "ltr",
            "ltr_score": 0.18,
            "manual_structured_score": 0.70,
            "param_score": 0.76,
            "logic_score": 0.82,
            "feature_alignment_score": 0.94,
            "rerank_score": 0.96,
            "name_bonus": 0.16,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "配电箱",
                "entity": "配电箱",
                "install_method": "嵌入",
                "box_mount_mode": "悬挂/嵌入式",
                "half_perimeter": 1.0,
                "numeric_params": {"half_perimeter": 1.0},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_name": "配电箱（半周长 1.0m以内）",
            "bill_text": "配电箱（半周长 1.0m以内） 安装方式：悬挂嵌入式综合",
            "specialty": "C4",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "cable_family",
                "canonical_name": "电缆",
                "entity": "电缆",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "4-2-76"
    assert best["quota_id"] == "4-2-76"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["pool_scan"]["mode"] == "same_book_bill_guided_family_top20"
    assert "primary_param:box_mount_mode" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_post_ltr_structural_scan_consumes_top20_rankable_contract(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-9-99",
                "post_cgr_top1_id": "C4-9-99",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-9-99",
            "name": "wrong cable incumbent",
            "_rank_score_source": "ltr",
            "ltr_score": 0.52,
            "manual_structured_score": 0.58,
            "param_score": 0.62,
            "logic_score": 0.64,
            "feature_alignment_score": 0.70,
            "rerank_score": 0.80,
            "name_bonus": 0.04,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "power_cable",
                "canonical_name": "cable",
                "entity": "cable",
                "material": "aluminum",
                "numeric_params": {"cable_section": 16},
                "cable_section": 16,
            },
        },
        {
            "quota_id": "C4-8-22",
            "name": "correct bridge tray cable",
            "_rank_score_source": "ltr",
            "ltr_score": 0.18,
            "manual_structured_score": 0.80,
            "param_score": 0.78,
            "logic_score": 0.82,
            "feature_alignment_score": 0.94,
            "rerank_score": 0.91,
            "name_bonus": 0.16,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "bridge_raceway",
                "canonical_name": "bridge tray",
                "entity": "bridge tray",
                "material": "copper",
                "install_method": "bridge tray",
                "laying_method": "bridge tray",
                "bridge_type": "tray",
                "numeric_params": {"cable_section": 6},
                "cable_section": 6,
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "bridge_raceway",
                "canonical_name": "bridge tray",
                "entity": "bridge tray",
                "material": "copper",
                "install_method": "bridge tray",
                "laying_method": "bridge tray",
                "bridge_type": "tray",
                "numeric_params": {"cable_section": 6},
                "cable_section": 6,
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-8-22"
    assert best["quota_id"] == "C4-8-22"
    structural_ranker = ranking_meta["ltr"]["post_ltr_structural_ranker"]
    assert structural_ranker["legacy_source_stage"] == "rankable_contract_top20_structural_scan"
    assert structural_ranker["comparator_version"] == "v36_sys_r4"
    assert structural_ranker["pool_scan"]["mode"] == "top20_rankable_contract_bill_guided_same_book"
    assert structural_ranker["rankable_candidate_contract"]["selected"]["quota_id"] == "C4-8-22"
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["accepted_by_final_decider"] is True
    assert structural_advisory["selected_quota_id"] == "C4-8-22"
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_final_decider_rejects_score_only_family_group_advisory(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-4-31",
                "post_cgr_top1_id": "C4-4-31",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-4-31",
            "name": "generic high confidence rank head",
            "_rank_score_source": "ltr",
            "ltr_score": 0.98,
            "manual_structured_score": 0.62,
            "param_score": 0.70,
            "logic_score": 0.86,
            "feature_alignment_score": 0.72,
            "rerank_score": 0.88,
            "name_bonus": 0.05,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "generic_box",
                "canonical_name": "generic box",
                "entity": "box",
            },
        },
        {
            "quota_id": "C4-4-30",
            "name": "score-only family candidate",
            "_rank_score_source": "ltr",
            "ltr_score": 0.21,
            "manual_structured_score": 0.88,
            "param_score": 0.90,
            "logic_score": 0.95,
            "feature_alignment_score": 0.98,
            "rerank_score": 0.99,
            "name_bonus": 0.20,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "generic box",
                "entity": "box",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_name": "控制箱",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-4-31"
    assert best["quota_id"] == "C4-4-31"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["to_quota_id"] == "C4-4-30"
    assert family_group_ranker["structural_ranking"]["evidence_groups"] == {
        "score": [
            "manual_structured_score",
            "param_score",
            "feature_alignment_score",
            "rerank_score",
            "name_bonus",
        ]
    }
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["accepted_by_final_decider"] is False
    assert structural_advisory["rejected_by_final_decider"] is True
    assert structural_advisory["final_decider_reason"] == "score_only_family_group_advisory_rejected"
    assert all(item["stage"] != "family_group_ranker" for item in ranking_meta["decision_advisories"])
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_hydrates_bill_text_primary_fuel(monkeypatch):
    class FakeParser:
        bill_parse_calls = 0
        bill_canonical_calls = 0

        @classmethod
        def parse(cls, text):
            if "bill fuel" in text:
                cls.bill_parse_calls += 1
                return {"circuits": 4}
            return {}

        @classmethod
        def parse_canonical(cls, text, specialty="", params=None):
            if "bill fuel" not in text:
                return {}
            cls.bill_canonical_calls += 1
            assert specialty == "C4"
            assert params == {"circuits": 4}
            return {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "steel",
                "install_method": "flush",
                "connection": "threaded",
                "circuits": 4,
                "numeric_params": {"circuits": 4},
            }

    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-4-31",
                "post_cgr_top1_id": "C4-4-31",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "text_parser", FakeParser)
    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-4-31",
            "name": "surface plastic control box",
            "_rank_score_source": "ltr",
            "ltr_score": 0.46,
            "manual_structured_score": 0.80,
            "param_score": 0.86,
            "logic_score": 0.83,
            "feature_alignment_score": 0.93,
            "rerank_score": 0.95,
            "name_bonus": 0.18,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "plastic",
                "install_method": "surface",
                "connection": "clip",
                "circuits": 8,
                "numeric_params": {"circuits": 8},
            },
        },
        {
            "quota_id": "C4-4-30",
            "name": "flush steel threaded control box",
            "_rank_score_source": "ltr",
            "ltr_score": 0.42,
            "manual_structured_score": 0.72,
            "param_score": 0.78,
            "logic_score": 0.80,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.92,
            "name_bonus": 0.14,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "canonical_name": "control box",
                "entity": "box",
                "material": "steel",
                "install_method": "flush",
                "connection": "threaded",
                "circuits": 4,
                "numeric_params": {"circuits": 4},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_text": "bill fuel only, no canonical_features",
            "specialty": "C4",
            "query_route": {"route": "installation_spec"},
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-4-30"
    assert best["quota_id"] == "C4-4-30"
    assert FakeParser.bill_parse_calls == 1
    assert FakeParser.bill_canonical_calls == 1
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert "primary_param:material" in family_group_ranker["evidence_edges"]
    assert "primary_param:install_method" in family_group_ranker["evidence_edges"]
    assert "primary_param:connection" in family_group_ranker["evidence_edges"]
    assert "decisive_primary_param:circuits" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_blocks_primary_parameter_conflict(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "sensor_flush_fixture",
                "post_cgr_top1_id": "sensor_flush_fixture",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "2-10-6-39",
            "name": "sensor_flush_fixture",
            "_rank_score_source": "ltr",
            "ltr_score": 0.05,
            "manual_structured_score": 0.62,
            "param_score": 0.76,
            "logic_score": 0.72,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.62,
            "name_bonus": 0.08,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "sanitary_fixture",
                "canonical_name": "蹲便器",
                "entity": "蹲便器",
                "sanitary_flush_mode": "感应冲洗",
            },
        },
        {
            "quota_id": "2-10-6-35",
            "name": "manual_flush_fixture",
            "_rank_score_source": "ltr",
            "ltr_score": 0.25,
            "manual_structured_score": 0.82,
            "param_score": 0.90,
            "logic_score": 0.86,
            "feature_alignment_score": 0.98,
            "rerank_score": 0.90,
            "name_bonus": 0.20,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "sanitary_fixture",
                "canonical_name": "蹲便器",
                "entity": "蹲便器",
                "sanitary_flush_mode": "普通冲洗",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "sanitary_fixture",
                "canonical_name": "蹲便器",
                "entity": "蹲便器",
                "sanitary_flush_mode": "感应冲洗",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "2-10-6-39"
    assert best["quota_id"] == "2-10-6-39"
    assert "family_group_ranker" not in ranking_meta["ltr"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_consumes_hydrated_primary_bin_direction(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-11-250",
                "post_cgr_top1_id": "C4-11-250",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    stale_bridge_features = {
        "canonical_name": "钢制桥架",
        "entity": "桥架",
        "family": "bridge_raceway",
        "material": "钢制",
        "bridge_type": "槽式",
        "numeric_params": {},
        "bridge_wh_sum": None,
    }
    candidates = [
        {
            "quota_id": "C4-11-250",
            "name": "钢制槽式桥架(宽+高)(mm以下) 600",
            "_rank_score_source": "ltr",
            "ltr_score": 0.40,
            "manual_structured_score": 0.75,
            "param_score": 0.80,
            "logic_score": 0.50,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.91,
            "name_bonus": 0.10,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                **stale_bridge_features,
                "raw_text": "钢制槽式桥架(宽+高)(mm以下) 600",
            },
        },
        {
            "quota_id": "C4-11-249",
            "name": "钢制槽式桥架(宽+高)(mm以下) 400",
            "_rank_score_source": "ltr",
            "ltr_score": 0.39,
            "manual_structured_score": 0.75,
            "param_score": 0.80,
            "logic_score": 0.50,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.90,
            "name_bonus": 0.10,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                **stale_bridge_features,
                "raw_text": "钢制槽式桥架(宽+高)(mm以下) 400",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "bridge_raceway",
                "canonical_name": "钢制桥架",
                "entity": "桥架",
                "material": "钢制",
                "bridge_type": "槽式",
                "bridge_wh_sum": 200,
                "numeric_params": {"bridge_wh_sum": 200},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-11-249"
    assert best["quota_id"] == "C4-11-249"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["to_quota_id"] == "C4-11-249"
    assert "primary_param:bridge_wh_sum" in family_group_ranker["evidence_edges"]
    assert "decisive_primary_param:bridge_wh_sum" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_blocks_numeric_only_air_valve_direction(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C7-3-22",
                "post_cgr_top1_id": "C7-3-22",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C7-3-22",
            "name": "碳钢 调节阀安装 对开多叶调节阀 周长(mm以内) 2800",
            "_rank_score_source": "ltr",
            "ltr_score": 0.54,
            "manual_structured_score": 0.73,
            "param_score": 0.60,
            "logic_score": 0.92,
            "feature_alignment_score": 0.42,
            "rerank_score": 0.99,
            "name_bonus": 0.22,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "raw_text": "碳钢 调节阀安装 对开多叶调节阀 周长(mm以内) 2800",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "family": "air_valve",
                "numeric_params": {},
            },
        },
        {
            "quota_id": "C7-3-29",
            "name": "碳钢 调节阀安装 风管防火阀 周长(mm以内) 2200",
            "_rank_score_source": "ltr",
            "ltr_score": 0.53,
            "manual_structured_score": 0.72,
            "param_score": 0.60,
            "logic_score": 0.96,
            "feature_alignment_score": 0.42,
            "rerank_score": 0.98,
            "name_bonus": 0.22,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "raw_text": "碳钢 调节阀安装 风管防火阀 周长(mm以内) 2200",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "family": "air_valve",
                "numeric_params": {},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "air_valve",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "perimeter": 2200,
                "numeric_params": {"perimeter": 2200},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C7-3-22"
    assert best["quota_id"] == "C7-3-22"
    assert "family_group_ranker" not in ranking_meta["ltr"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_family_group_ranker_uses_secondary_type_over_weaker_numeric_bin(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C7-3-29",
                "post_cgr_top1_id": "C7-3-29",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C7-3-29",
            "name": "碳钢 调节阀安装 风管防火阀 周长(mm以内) 2200",
            "_rank_score_source": "ltr",
            "ltr_score": 0.54,
            "manual_structured_score": 0.73,
            "param_score": 0.60,
            "logic_score": 0.96,
            "feature_alignment_score": 0.42,
            "rerank_score": 0.98,
            "name_bonus": 0.22,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "raw_text": "碳钢 调节阀安装 风管防火阀 周长(mm以内) 2200",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "family": "air_valve",
                "numeric_params": {"perimeter": 2200},
            },
        },
        {
            "quota_id": "C7-3-22",
            "name": "碳钢 调节阀安装 对开多叶调节阀 周长(mm以内) 2800",
            "_rank_score_source": "ltr",
            "ltr_score": 0.53,
            "manual_structured_score": 0.73,
            "param_score": 0.60,
            "logic_score": 0.92,
            "feature_alignment_score": 0.42,
            "rerank_score": 0.99,
            "name_bonus": 0.22,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "raw_text": "碳钢 调节阀安装 对开多叶调节阀 周长(mm以内) 2800",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "family": "air_valve",
                "numeric_params": {"perimeter": 2800},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "name": "碳钢阀门",
            "description": "名称：FVD-电动对开式多叶调节阀-超高",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "air_valve",
                "canonical_name": "碳钢风阀",
                "entity": "风阀",
                "perimeter": 2200,
                "numeric_params": {"perimeter": 2200},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C7-3-22"
    assert best["quota_id"] == "C7-3-22"
    family_group_ranker = ranking_meta["ltr"]["family_group_ranker"]
    assert family_group_ranker["to_quota_id"] == "C7-3-22"
    assert "secondary_type:air_valve:opposed_multi_leaf" in family_group_ranker["evidence_edges"]
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_final_decider_rejects_low_confidence_post_cgr_advisory(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "strong_ltr_choice",
                "post_cgr_top1_id": "weak_cgr_advisory",
                "reason": "test_cgr_advisory",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "strong_ltr_choice",
            "name": "strong incumbent",
            "_rank_score_source": "ltr",
            "ltr_score": 0.88,
            "manual_structured_score": 0.72,
            "param_score": 0.86,
            "feature_alignment_score": 0.96,
            "rerank_score": 0.97,
            "param_tier": 1,
            "param_match": True,
            "exact_experience_anchor": True,
        },
        {
            "quota_id": "weak_cgr_advisory",
            "name": "weak advisory",
            "_rank_score_source": "ltr",
            "ltr_score": 0.10,
            "manual_structured_score": 0.45,
            "param_score": 0.78,
            "feature_alignment_score": 0.58,
            "rerank_score": 0.60,
            "param_tier": 1,
            "param_match": True,
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {"query_route": {"route": "installation_spec"}},
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "strong_ltr_choice"
    assert best["quota_id"] == "strong_ltr_choice"
    assert ranking_meta["selected_top1_id"] == "strong_ltr_choice"
    assert ranking_meta["decision_advisories"][0]["stage"] == "post_cgr"
    assert ranking_meta["decision_advisories"][0]["accepted_by_final_decider"] is False
    assert ranking_meta["decision_advisories"][0]["rejected_by_final_decider"] is True
    assert ranking_meta["decision_advisories"][0]["final_decider_reason"] == (
        "incumbent_structural_advantage"
    )
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_final_decider_rejects_post_cgr_when_ltr_and_rerank_support_incumbent(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "ltr_incumbent",
                "post_cgr_top1_id": "post_cgr_advisory",
                "reason": "test_post_cgr_advisory",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "ltr_incumbent",
            "name": "ltr and rerank supported candidate",
            "_rank_score_source": "ltr",
            "ltr_score": 0.55,
            "manual_structured_score": 0.73,
            "param_score": 0.60,
            "feature_alignment_score": 0.42,
            "rerank_score": 0.99,
            "param_match": True,
        },
        {
            "quota_id": "post_cgr_advisory",
            "name": "structured but low ltr advisory",
            "_rank_score_source": "ltr",
            "ltr_score": -0.78,
            "manual_structured_score": 0.80,
            "param_score": 0.79,
            "feature_alignment_score": 0.58,
            "rerank_score": 0.97,
            "param_match": True,
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {"query_route": {"route": "installation_spec"}},
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "ltr_incumbent"
    assert best["quota_id"] == "ltr_incumbent"
    assert ranking_meta["decision_advisories"][0]["accepted_by_final_decider"] is False
    assert ranking_meta["decision_advisories"][0]["final_decider_reason"] == (
        "incumbent_ltr_and_rerank_over_low_confidence_post_cgr"
    )
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_category_safe_lifecycle_guard_can_restore_stronger_rank_head(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "rank_head",
                "post_cgr_top1_id": "rank_head",
                "reason": "test_category_safe_advisory",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, route_profile
            return ordered, {"applied": False, "reason": "not_applied"}

    def pick_category_safe_candidate(item, ordered):
        del item
        return next(row for row in ordered if row["quota_id"] == "category_safe_advisory")

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", pick_category_safe_candidate)

    candidates = [
        {
            "quota_id": "rank_head",
            "name": "rank head with stronger lifecycle score",
            "_rank_score_source": "ltr",
            "ltr_score": 0.79,
            "manual_structured_score": 0.66,
            "param_score": 0.65,
            "feature_alignment_score": 0.85,
            "rerank_score": 0.94,
            "param_tier": 1,
            "param_match": True,
        },
        {
            "quota_id": "category_safe_advisory",
            "name": "category safe but weak model score",
            "_rank_score_source": "ltr",
            "ltr_score": -0.97,
            "manual_structured_score": 0.50,
            "param_score": 0.82,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.47,
            "param_tier": 1,
            "param_match": True,
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {"query_route": {"route": "installation_spec"}},
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "rank_head"
    assert best["quota_id"] == "rank_head"
    assert ranking_meta["post_final_top1_id"] == "rank_head"
    assert ranking_meta["category_safe_lifecycle_guard"]["to_quota_id"] == "rank_head"
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_nonstandard_numeric_main_route_with_broad_a_stays_open_search(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {"Numeric Province": "civil"}.get(province, ""),
    )

    class FakeSearcher:
        aux_searchers = []
        province = "Numeric Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "8", 2: "9", 3: "14"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                del top_k
                return ["14", "9", "8"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(books)
            return []

    classification = {
        "primary": "A",
        "search_books": ["A"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "wall finish", classification, top_k=5)

    assert calls == [None]
    assert classification["retrieval_resolution"]["calls"] == [
        {
            "target": "main",
            "stage": "primary",
            "source_province": "Numeric Province",
            "requested_books": ["A"],
            "resolved_books": [],
            "open_search": True,
            "uses_standard_books": False,
        }
    ]


def test_nonstandard_prefixed_a_group_expands_without_open_search(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.match_core.detect_db_type",
        lambda province: {"Prefixed Province": "civil"}.get(province, ""),
    )

    class FakeSearcher:
        aux_searchers = []
        province = "Prefixed Province"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "A1", 2: "A2", 3: "A14"}

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            del query, top_k, item, context_prior
            calls.append(books)
            return []

    classification = {
        "primary": "A",
        "search_books": ["A"],
        "route_mode": "strict",
        "allow_cross_book_escape": False,
    }

    match_core.cascade_search(FakeSearcher(), "civil item", classification, top_k=5)

    assert calls == [["A1", "A14", "A2"]]
    assert classification["retrieval_resolution"]["calls"][0]["resolved_books"] == ["A1", "A14", "A2"]


def test_build_search_result_trace_includes_retrieval_resolution():
    result = _build_search_result_from_candidates(
        {
            "name": "support",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "fallbacks": ["C10"],
                "candidate_books": ["C4", "C10"],
                "search_books": ["C4", "C10"],
                "hard_book_constraints": ["C4"],
                "route_mode": "strict",
                "retrieval_resolution": {
                    "calls": [
                        {
                            "target": "main",
                            "stage": "primary",
                            "requested_books": ["C4"],
                            "resolved_books": ["C4"],
                            "open_search": False,
                        }
                    ]
                },
            },
        },
        [
            {
                "quota_id": "C4-1-1",
                "name": "support install",
                "unit": "m",
                "param_match": True,
                "param_score": 0.95,
                "param_detail": "ok",
                "rerank_score": 0.88,
            }
        ],
    )

    trace_step = next(
        step for step in result["trace"]["steps"]
        if step.get("stage") == "search_select"
    )
    assert trace_step["retriever"]["search_resolution"]["calls"][0]["resolved_books"] == ["C4"]


def test_build_search_result_includes_candidate_lifecycle_trace():
    result = _build_search_result_from_candidates(
        {
            "name": "support",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "search_books": ["C4"],
                "retrieval_resolution": {"calls": []},
            },
        },
        [
            {
                "quota_id": "C4-1-1",
                "name": "support install",
                "unit": "m",
                "param_match": True,
                "param_score": 0.95,
                "param_detail": "ok",
                "rerank_score": 0.88,
                "_cascade_stage": "primary",
                "_cascade_stages": ["primary"],
            },
            {
                "quota_id": "C4-1-2",
                "name": "wrong size support install",
                "unit": "m",
                "param_match": False,
                "param_score": 0.10,
                "param_detail": "hard fail",
                "param_hard_fail": True,
                "param_validation_tier": "hard_fail",
                "param_tier": 0,
                "rerank_score": 0.99,
                "_cascade_stage": "expanded",
                "_cascade_stages": ["expanded"],
            },
        ],
    )

    lifecycle = {
        row["quota_id"]: row
        for row in result["candidate_lifecycle_trace"]
    }
    assert lifecycle["C4-1-1"]["source"] == "main"
    assert lifecycle["C4-1-1"]["first_seen_stage"] == "primary"
    assert lifecycle["C4-1-1"]["retained_for_ranking"] is True
    assert lifecycle["C4-1-1"]["final_state"] == "selected"
    assert lifecycle["C4-1-2"]["filter_state"] == "filtered_hard_param_fail"
    assert lifecycle["C4-1-2"]["final_state"] == "filtered"
    assert lifecycle["C4-1-2"]["hard_param_resolution"] == "unknown_reject"
    assert "recall_position_gt_20" in lifecycle["C4-1-2"]["hard_param_resolution_reason_codes"]

    assert lifecycle["C4-1-2"]["lost_reason"] == "hard_param_fail"


def test_search_result_uses_light_snapshots_unless_diagnostic_payload_enabled():
    candidates = [
        {
            "quota_id": f"C4-1-{index}",
            "name": f"support install {index}",
            "unit": "m",
            "param_match": True,
            "param_score": 0.90 - index * 0.01,
            "param_detail": "ok",
            "rerank_score": 0.90 - index * 0.01,
            "candidate_canonical_features": {"family": "support", "entity": "support"},
            "_cascade_stage": "primary",
            "_cascade_stages": ["primary"],
        }
        for index in range(8)
    ]
    base_item = {
        "name": "support",
        "description": "",
        "query_route": {"route": "installation_spec"},
        "classification": {
            "primary": "C4",
            "search_books": ["C4"],
            "retrieval_resolution": {"calls": []},
        },
    }

    default_result = _build_search_result_from_candidates(dict(base_item), candidates)
    assert len(default_result["candidate_snapshots"]) == 5
    assert len(default_result["candidate_lifecycle_trace"]) == 5
    assert "candidate_feature_present_fields" not in default_result["candidate_lifecycle_trace"][0]

    diagnostic_item = dict(base_item)
    diagnostic_item.update({
        "_diagnostic_snapshot_payload_enabled": True,
        "_diagnostic_candidate_snapshot_top_n": 8,
        "_diagnostic_lifecycle_trace_top_n": 8,
    })
    diagnostic_result = _build_search_result_from_candidates(diagnostic_item, candidates)
    assert len(diagnostic_result["candidate_snapshots"]) == 8
    assert len(diagnostic_result["candidate_lifecycle_trace"]) == 8

    feature_item = dict(base_item)
    feature_item.update({
        "_diagnostic_snapshot_payload_enabled": True,
        "_diagnostic_lifecycle_trace_top_n": 50,
    })
    feature_result = _build_search_result_from_candidates(feature_item, candidates)
    assert "candidate_feature_present_fields" in feature_result["candidate_lifecycle_trace"][0]


def test_rankable_pool_contract_retains_bounded_hard_fail_candidate():
    result = _build_search_result_from_candidates(
        {
            "name": "support",
            "description": "",
            "query_route": {"route": "installation_spec"},
            "classification": {
                "primary": "C4",
                "search_books": ["C4"],
                "retrieval_resolution": {"calls": []},
            },
        },
        [
            {
                "quota_id": "C4-1-1",
                "name": "support install",
                "unit": "m",
                "param_match": True,
                "param_score": 0.80,
                "param_detail": "ok",
                "rerank_score": 0.70,
                "_cascade_stage": "primary",
                "_cascade_stages": ["primary"],
            },
            {
                "quota_id": "C4-1-2",
                "name": "support install exact family",
                "unit": "m",
                "param_match": False,
                "param_score": 0.32,
                "param_detail": "hard fail but strong recall evidence",
                "param_hard_fail": True,
                "param_validation_tier": "hard_fail",
                "param_tier": 0,
                "rerank_score": 0.90,
                "feature_alignment_score": 0.91,
                "logic_score": 0.84,
                "manual_structured_score": 0.42,
                "_cascade_stage": "primary",
                "_cascade_stages": ["primary"],
            },
            {
                "quota_id": "C4-1-3",
                "name": "unrelated weak candidate",
                "unit": "m",
                "param_match": False,
                "param_score": 0.05,
                "param_detail": "hard fail",
                "param_hard_fail": True,
                "param_validation_tier": "hard_fail",
                "param_tier": 0,
                "rerank_score": 0.10,
                "_cascade_stage": "expanded",
                "_cascade_stages": ["expanded"],
            },
        ],
    )

    lifecycle = {
        row["quota_id"]: row
        for row in result["candidate_lifecycle_trace"]
    }

    assert result["rankable_pool_contract_recovered_count"] == 1
    assert result["hard_param_fail_rejected_count"] == 1
    assert "C4-1-2" in result["all_candidate_ids"]
    assert "C4-1-3" not in result["all_candidate_ids"]
    assert lifecycle["C4-1-2"]["filter_state"] == "rankable_contract_protected"
    assert lifecycle["C4-1-2"]["retained_for_ranking"] is True
    assert lifecycle["C4-1-2"]["rankable_pool_contract_protected"] is True
    assert lifecycle["C4-1-2"]["hard_param_resolution"] == "soft_conflict_protected"
    assert lifecycle["C4-1-3"]["filter_state"] == "filtered_hard_param_fail"
    assert lifecycle["C4-1-3"]["hard_param_resolution"] == "unknown_reject"
    assert result["hard_param_fail_rejected_candidates"][0]["hard_param_resolution"] == "unknown_reject"


def test_rankable_pool_contract_retains_item_structural_hard_fail_candidate():
    result = _build_search_result_from_candidates(
        {
            "name": "distribution box AL",
            "description": "distribution box AL size 440*380*100 wall mounted",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
                "install_method": "wall",
                "box_mount_mode": "wall",
                "numeric_params": {"half_perimeter": 820},
            },
            "classification": {
                "primary": "4",
                "search_books": ["4"],
                "retrieval_resolution": {"calls": []},
            },
        },
        [
            {
                "quota_id": "4-13-177",
                "name": "generic junction box",
                "unit": "set",
                "param_match": True,
                "param_score": 0.60,
                "param_detail": "ok",
                "rerank_score": 0.70,
                "_cascade_stage": "primary",
                "_cascade_stages": ["primary"],
            },
            {
                "quota_id": "4-2-76",
                "name": "distribution box wall mounted half perimeter 1.0m",
                "unit": "set",
                "param_match": False,
                "param_score": 0.16,
                "param_detail": "hard fail but item structure matches",
                "param_hard_fail": True,
                "param_validation_tier": "hard_fail",
                "param_tier": 0,
                "rerank_score": 0.50,
                "candidate_canonical_features": {
                    "family": "electrical_box",
                    "entity": "distribution box",
                    "canonical_name": "distribution box",
                    "install_method": "wall",
                    "box_mount_mode": "wall",
                    "numeric_params": {"half_perimeter": 1000},
                },
                "_cascade_stage": "primary",
                "_cascade_stages": ["primary"],
            },
        ],
    )

    lifecycle = {
        row["quota_id"]: row
        for row in result["candidate_lifecycle_trace"]
    }

    assert result["rankable_pool_contract_recovered_count"] == 1
    assert lifecycle["4-2-76"]["filter_state"] == "rankable_contract_protected"
    assert lifecycle["4-2-76"]["hard_param_resolution"] == "soft_conflict_protected"
    assert "item_structural_evidence" in lifecycle["4-2-76"]["hard_param_resolution_reason_codes"]
    recovered = result["rankable_pool_contract_recovered_candidates"][0]
    assert recovered["quota_id"] == "4-2-76"


def test_post_ltr_structural_scan_can_accept_protected_soft_param_candidate(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "4-13-177",
                "post_cgr_top1_id": "4-13-177",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, ordered, route_profile
            return [], {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "4-13-177",
            "name": "generic junction box",
            "ltr_score": 0.40,
            "manual_structured_score": 0.45,
            "param_score": 0.40,
            "logic_score": 0.55,
            "feature_alignment_score": 0.55,
            "rerank_score": 0.72,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "conduit_raceway",
                "entity": "junction box",
                "canonical_name": "junction box",
                "install_method": "concealed",
                "box_mount_mode": "concealed",
                "numeric_params": {"half_perimeter": 500},
            },
        },
        {
            "quota_id": "4-2-76",
            "name": "distribution box wall mounted half perimeter 1.0m",
            "ltr_score": 0.36,
            "manual_structured_score": 0.74,
            "param_score": 0.72,
            "logic_score": 0.78,
            "feature_alignment_score": 0.92,
            "rerank_score": 0.84,
            "param_tier": 1,
            "param_match": False,
            "_rankable_pool_contract_protected": True,
            "_rankable_pool_contract_reason": "hard_fail_demoted_to_rankable_with_penalty",
            "candidate_canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
                "install_method": "wall",
                "box_mount_mode": "wall",
                "numeric_params": {"half_perimeter": 1000},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_name": "distribution box AL",
            "bill_text": "distribution box AL size 440*380*100 wall mounted",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
                "install_method": "wall",
                "box_mount_mode": "wall",
                "numeric_params": {"half_perimeter": 820},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "4-2-76"
    assert best["quota_id"] == "4-2-76"
    structural_ranker = ranking_meta["ltr"]["post_ltr_structural_ranker"]
    assert structural_ranker["to_quota_id"] == "4-2-76"
    assert structural_ranker["legacy_source_stage"] == "rankable_contract_top20_structural_scan"
    assert structural_ranker["pool_scan"]["mode"] == "top20_rankable_contract_bill_guided_same_book"
    assert "primary_param:half_perimeter" in structural_ranker["evidence_edges"]
    assert "primary_param:box_mount_mode" in structural_ranker["evidence_edges"]
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["accepted_by_final_decider"] is True
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_post_ltr_structural_scan_rejects_weak_protected_soft_param_candidate(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "C4-1-1",
                "post_cgr_top1_id": "C4-1-1",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, ordered, route_profile
            return [], {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "C4-1-1",
            "name": "rank head",
            "ltr_score": 0.60,
            "manual_structured_score": 0.58,
            "param_score": 0.62,
            "logic_score": 0.64,
            "feature_alignment_score": 0.70,
            "rerank_score": 0.80,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
            },
        },
        {
            "quota_id": "C4-1-2",
            "name": "weak protected same family",
            "ltr_score": 0.55,
            "manual_structured_score": 0.90,
            "param_score": 0.90,
            "logic_score": 0.90,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.95,
            "param_tier": 1,
            "param_match": False,
            "_rankable_pool_contract_protected": True,
            "candidate_canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_name": "distribution box",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "electrical_box",
                "entity": "distribution box",
                "canonical_name": "distribution box",
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "C4-1-1"
    assert best["quota_id"] == "C4-1-1"
    assert ranking_meta["post_ltr_structural_ranker"] == {}
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_final_decider_rejects_protected_soft_param_connection_conflict(monkeypatch):
    class FakeApi:
        @staticmethod
        def rerank_candidates_with_ltr(item, ordered, context):
            del item, context
            return ordered, {
                "post_ltr_top1_id": "9-1-18",
                "post_cgr_top1_id": "9-1-18",
                "primary_stage": "ltr",
            }

        @staticmethod
        def arbitrate_candidates(item, ordered, route_profile=None):
            del item, ordered, route_profile
            return [], {"applied": False, "reason": "not_applied"}

    monkeypatch.setattr(orchestrator, "_api", lambda: FakeApi)
    monkeypatch.setattr(orchestrator, "_pick_category_safe_candidate", lambda item, ordered: None)

    candidates = [
        {
            "quota_id": "9-1-18",
            "name": "sprinkler steel pipe grooved dn100",
            "ltr_score": 0.30,
            "manual_structured_score": 0.86,
            "param_score": 0.91,
            "logic_score": 1.0,
            "feature_alignment_score": 0.79,
            "rerank_score": 0.92,
            "param_tier": 1,
            "param_match": True,
            "candidate_canonical_features": {
                "family": "pipe_run",
                "entity": "pipe",
                "canonical_name": "sprinkler pipe",
                "material": "sprinkler steel",
                "connection": "grooved",
                "numeric_params": {"dn": 100},
            },
        },
        {
            "quota_id": "9-1-27",
            "name": "hydrant galvanized pipe threaded dn100",
            "ltr_score": 0.20,
            "manual_structured_score": 0.96,
            "param_score": 0.91,
            "logic_score": 1.0,
            "feature_alignment_score": 0.95,
            "rerank_score": 0.99,
            "param_tier": 1,
            "param_match": False,
            "_rankable_pool_contract_protected": True,
            "candidate_canonical_features": {
                "family": "pipe_run",
                "entity": "pipe",
                "canonical_name": "galvanized pipe",
                "material": "galvanized steel",
                "connection": "threaded",
                "conduit_type": "G",
                "numeric_params": {"dn": 100},
            },
        },
    ]

    ordered, ranking_meta, arbitration, explicit_override, best = _run_rank_pipeline(
        {
            "bill_name": "hydrant steel pipe",
            "bill_text": "hydrant steel pipe material galvanized steel DN100 connection grooved",
            "query_route": {"route": "installation_spec"},
            "canonical_features": {
                "family": "pipe_run",
                "entity": "pipe",
                "canonical_name": "galvanized pipe",
                "material": "galvanized steel",
                "connection": "grooved",
                "numeric_params": {"dn": 100},
            },
        },
        candidates,
        reservoir=candidates,
        allow_arbiter=True,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "9-1-18"
    assert best["quota_id"] == "9-1-18"
    structural_advisory = next(
        item for item in ranking_meta["decision_advisories"] if item["stage"] == "post_ltr_structural_ranker"
    )
    assert structural_advisory["suggested_top1_id"] == "9-1-27"
    assert structural_advisory["accepted_by_final_decider"] is False
    assert structural_advisory["final_decider_reason"] == "protected_soft_param_connection_conflict_rejected"
    assert arbitration["reason"] == "not_applied"
    assert explicit_override == {}


def test_extract_search_books_uses_main_resolved_books_when_present():
    record = {
        "retriever": {
            "search_resolution": {
                "calls": [
                    {"target": "aux", "resolved_books": ["4"]},
                    {"target": "main", "resolved_books": ["03", "C10"]},
                ]
            }
        },
        "router": {
            "classification": {"search_books": ["C12"]},
            "unified_plan": {"primary_book": "C8"},
        },
    }

    books = extract_search_books(record)

    assert books == ["C3", "C10"]


def test_extract_search_books_treats_empty_main_resolution_as_open_search():
    record = {
        "retriever": {
            "search_resolution": {
                "calls": [
                    {"target": "main", "resolved_books": []},
                ]
            }
        },
        "router": {
            "classification": {"search_books": ["A"]},
            "unified_plan": {"primary_book": "C1"},
        },
    }

    books = extract_search_books(record)

    assert books == []
