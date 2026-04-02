from src.constraint_filter import ConstraintFilter
from src.unified_ranking_pipeline import UnifiedRankingPipeline
from src.unified_retrieval import UnifiedRetrieval
from src.unified_scoring_engine import AdaptiveWeightCalculator, FeatureExtractor, UnifiedScoringEngine


class _FakeSearcher:
    def __init__(self, rows):
        self.rows = rows

    def search(self, query_item, top_k=10):
        del query_item
        return list(self.rows)[:top_k]


class _FakeKB:
    def get_hints(self, query_item):
        return [{"hint": str((query_item or {}).get("name") or "")}]


class _FakeHybridSearcher:
    def __init__(self):
        self.search_calls = []
        self.prior_calls = []

    def search(self, query, top_k=10, books=None, item=None, context_prior=None):
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "books": list(books or []),
                "item": dict(item or {}),
                "context_prior": dict(context_prior or {}),
            }
        )
        return [
            {
                "quota_id": "Q-HYBRID",
                "name": "Hybrid Candidate",
                "hybrid_score": 0.81,
                "bm25_rank": 1,
                "vector_rank": 2,
            }
        ]

    def collect_prior_candidates(self, query_text, *, full_query="", books=None, item=None, top_k=8, exact_only=False):
        self.prior_calls.append(
            {
                "query_text": query_text,
                "full_query": full_query,
                "books": list(books or []),
                "item": dict(item or {}),
                "top_k": top_k,
                "exact_only": exact_only,
            }
        )
        return [
            {
                "quota_id": "Q-PRIOR",
                "name": "Prior Candidate",
                "hybrid_score": 0.92,
                "knowledge_prior_sources": ["experience"],
                "knowledge_prior_score": 1.1,
            }
        ]


class _FakeRuleMatcher:
    def match(self, item):
        return [
            {
                "quota_id": "Q-RULE",
                "name": f"Rule for {item['name']}",
                "hybrid_score": 0.5,
            }
        ]


def test_unified_retrieval_collects_candidates_and_marks_sources():
    retrieval = UnifiedRetrieval(
        bm25_searcher=_FakeSearcher(
            [
                {"quota_id": "Q1", "name": "Alpha", "hybrid_score": 0.60},
            ]
        ),
        experience_db=_FakeSearcher(
            [
                {"quota_id": "Q2", "name": "Beta", "hybrid_score": 0.70},
            ]
        ),
        universal_kb=_FakeKB(),
    )

    result = retrieval.retrieve({"name": "demo"}, top_k=10)

    assert result["total_retrieved"] == 2
    assert result["sources"] == ["bm25", "experience", "universal_kb"]
    assert result["candidates"][0]["quota_id"] == "Q2"
    assert result["candidates"][0]["from_experience"] is True
    assert "experience" in result["candidates"][0]["sources"]
    assert result["kb_hints"][0]["hint"] == "demo"


def test_unified_retrieval_supports_real_hybrid_searcher_style_calls():
    hybrid_searcher = _FakeHybridSearcher()
    retrieval = UnifiedRetrieval(
        hybrid_searcher=hybrid_searcher,
        universal_kb=_FakeKB(),
        rule_matcher=_FakeRuleMatcher(),
    )

    result = retrieval.retrieve(
        {
            "name": "demo item",
            "description": "with context",
            "classification": {"search_books": ["C10"]},
            "context_prior": {"system_hint": "给排水"},
            "canonical_query": {"search_query": "demo query", "validation_query": "demo query full"},
        },
        top_k=10,
    )

    assert [candidate["quota_id"] for candidate in result["candidates"][:3]] == ["Q-PRIOR", "Q-HYBRID", "Q-RULE"]
    assert result["sources"] == ["hybrid", "prior", "rule", "universal_kb"]
    assert hybrid_searcher.search_calls[0]["query"] == "demo query"
    assert hybrid_searcher.search_calls[0]["books"] == ["C10"]
    assert hybrid_searcher.prior_calls[0]["full_query"] == "demo item with context demo query full"
    assert result["candidates"][0]["from_experience"] is True
    assert "prior" in result["candidates"][0]["sources"]
    assert "bm25" in result["candidates"][1]["sources"]
    assert "vector" in result["candidates"][1]["sources"]


def test_unified_retrieval_can_use_cascade_search_adapter():
    calls = []

    def _fake_cascade(searcher, search_query, classification, top_k=None, item=None, context_prior=None):
        del searcher
        calls.append(
            {
                "search_query": search_query,
                "classification": dict(classification or {}),
                "top_k": top_k,
                "item": dict(item or {}),
                "context_prior": dict(context_prior or {}),
            }
        )
        return [
            {"quota_id": "Q-CASCADE", "name": "Cascade Candidate", "hybrid_score": 0.66},
        ]

    retrieval = UnifiedRetrieval(
        hybrid_searcher=object(),
        cascade_search_fn=_fake_cascade,
    )

    result = retrieval.retrieve(
        {
            "name": "cascade item",
            "classification": {"search_books": ["C4"], "primary": "C4"},
            "context_prior": {"batch_context": {"batch_size": 3}},
        },
        top_k=5,
        include_prior_candidates=False,
    )

    assert [candidate["quota_id"] for candidate in result["candidates"]] == ["Q-CASCADE"]
    assert result["sources"] == ["hybrid"]
    assert calls[0]["search_query"] == "cascade item"
    assert calls[0]["classification"]["primary"] == "C4"


def test_unified_scoring_engine_scores_candidates_without_mutating_input():
    engine = UnifiedScoringEngine(
        feature_extractor=FeatureExtractor(),
        weight_calculator=AdaptiveWeightCalculator(),
    )
    candidates = [
        {"quota_id": "Q1", "name": "管道安装", "param_match": True, "param_score": 0.8, "hybrid_score": 0.7},
        {"quota_id": "Q2", "name": "风管安装", "param_match": False, "param_score": 0.2, "hybrid_score": 0.4},
    ]

    scored = engine.score({"name": "管道安装", "params": {"dn": 100}}, candidates)

    assert [candidate["quota_id"] for candidate in scored] == ["Q1", "Q2"]
    assert "unified_score" in scored[0]
    assert "weights" in scored[0]
    assert "category_scores" in scored[0]
    assert "features" not in candidates[0]


def test_feature_extractor_collects_real_candidate_signals():
    extractor = FeatureExtractor()

    features = extractor.extract(
        {
            "name": "管道安装 DN100",
            "unit": "m",
            "specialty": "C10",
            "book": "C10",
            "params": {"dn": 100},
            "canonical_features": {
                "family": "pipe_run",
                "entity": "pipe",
                "system": "water",
                "material": "镀锌钢管",
            },
        },
        {
            "quota_id": "Q1",
            "name": "管道安装 DN100",
            "unit": "m",
            "specialty": "C10",
            "book": "C10",
            "hybrid_score": 0.82,
            "rerank_score": 0.91,
            "active_rerank_score": 0.93,
            "bm25_score": 0.76,
            "vector_score": 0.74,
            "bm25_rank": 1,
            "vector_rank": 3,
            "param_match": True,
            "param_score": 0.88,
            "param_tier": 2,
            "feature_alignment_score": 0.86,
            "context_alignment_score": 0.80,
            "logic_score": 0.84,
            "logic_exact_primary_match": True,
            "candidate_scope_match": 0.72,
            "family_gate_score": 1.2,
            "feature_alignment_exact_anchor_count": 2,
            "candidate_canonical_features": {
                "family": "pipe_run",
                "entity": "pipe",
                "system": "water",
                "material": "镀锌钢管",
            },
            "_ltr_param": {
                "param_main_exact": 1,
                "param_main_rel_dist": 0.0,
                "param_material_match": 1.0,
            },
            "knowledge_prior_sources": ["experience"],
            "knowledge_prior_score": 1.1,
            "match_source": "experience_injected_exact",
            "sources": ["hybrid", "bm25", "vector", "experience"],
        },
    )

    assert features["name_exact_match"] == 1.0
    assert features["main_param_exact"] == 1.0
    assert features["main_param_rel_score"] == 1.0
    assert features["material_match"] == 1.0
    assert features["unit_match"] == 1.0
    assert features["family_match"] == 1.0
    assert features["entity_match"] == 1.0
    assert features["system_match"] == 1.0
    assert features["specialty_match"] == 1.0
    assert features["book_match"] == 1.0
    assert features["exact_experience_anchor"] == 1.0
    assert features["from_experience"] == 1.0
    assert features["source_count"] == 4.0
    assert features["retrieval_rank_score"] == 1.0


def test_unified_scoring_engine_prefers_candidate_with_stronger_structured_signals():
    engine = UnifiedScoringEngine(
        feature_extractor=FeatureExtractor(),
        weight_calculator=AdaptiveWeightCalculator(),
    )

    scored = engine.score(
        {
            "name": "管道安装 DN100",
            "unit": "m",
            "specialty": "C10",
            "params": {"dn": 100},
            "canonical_features": {"family": "pipe_run", "entity": "pipe", "system": "water"},
        },
        [
            {
                "quota_id": "Q-GOOD",
                "name": "管道安装 DN100",
                "unit": "m",
                "specialty": "C10",
                "hybrid_score": 0.75,
                "rerank_score": 0.86,
                "param_match": True,
                "param_score": 0.92,
                "param_tier": 2,
                "feature_alignment_score": 0.90,
                "context_alignment_score": 0.82,
                "logic_score": 0.88,
                "logic_exact_primary_match": True,
                "candidate_scope_match": 0.70,
                "candidate_canonical_features": {"family": "pipe_run", "entity": "pipe", "system": "water"},
                "_ltr_param": {"param_main_exact": 1, "param_main_rel_dist": 0.0},
                "sources": ["hybrid", "bm25"],
            },
            {
                "quota_id": "Q-WEAK",
                "name": "风管安装 DN80",
                "unit": "m2",
                "specialty": "C6",
                "hybrid_score": 0.78,
                "rerank_score": 0.80,
                "param_match": False,
                "param_score": 0.15,
                "param_tier": 0,
                "feature_alignment_score": 0.20,
                "context_alignment_score": 0.30,
                "logic_score": 0.25,
                "candidate_scope_match": 0.10,
                "candidate_scope_conflict": True,
                "candidate_canonical_features": {"family": "air_pipe", "entity": "duct", "system": "air"},
                "_ltr_param": {"param_main_exact": 0, "param_main_rel_dist": 0.4},
                "sources": ["hybrid", "vector"],
            },
        ],
    )

    assert [candidate["quota_id"] for candidate in scored] == ["Q-GOOD", "Q-WEAK"]
    assert scored[0]["category_scores"]["param_match"] > scored[1]["category_scores"]["param_match"]
    assert scored[0]["category_scores"]["classification"] > scored[1]["category_scores"]["classification"]
    assert scored[0]["category_scores"]["context"] > scored[1]["category_scores"]["context"]


def test_constraint_filter_returns_structured_result():
    result = ConstraintFilter().filter(
        {"name": "demo"},
        [
            {"quota_id": "Q1", "unified_score": 0.9},
            {"quota_id": "Q2", "unified_score": 0.7},
        ],
        top_k=1,
    )

    assert [candidate["quota_id"] for candidate in result["candidates"]] == ["Q1"]
    assert result["meta"]["survivor_count"] == 2
    assert result["meta"]["rejected_count"] == 0


def test_constraint_filter_rejects_hard_conflicts_and_penalizes_soft_violations():
    result = ConstraintFilter().filter(
        {
            "name": "管道安装 DN100",
            "unit": "m",
            "specialty": "C10",
            "params": {"dn": 100},
            "canonical_features": {"material": "镀锌钢管"},
        },
        [
            {
                "quota_id": "Q-KEEP",
                "name": "管道安装 DN120",
                "unit": "m",
                "specialty": "C10",
                "unified_score": 0.90,
                "params": {"dn": 120},
                "candidate_scope_conflict": True,
                "candidate_canonical_features": {"material": "热镀锌钢管"},
            },
            {
                "quota_id": "Q-REJECT",
                "name": "风管安装 DN200",
                "unit": "m2",
                "specialty": "C6",
                "unified_score": 0.95,
                "params": {"dn": 200},
                "candidate_canonical_features": {"material": "PVC"},
            },
        ],
        top_k=5,
    )

    assert [candidate["quota_id"] for candidate in result["candidates"]] == ["Q-KEEP"]
    assert result["candidates"][0]["penalty"] > 0.0
    assert result["candidates"][0]["soft_violations"]
    assert result["rejected"][0]["quota_id"] == "Q-REJECT"
    assert result["rejected"][0]["rejection_reason"][0]["type"] == "specialty_mismatch"
    assert result["meta"]["hard_violation_counts"]["specialty_mismatch"] == 1
    assert result["meta"]["soft_violation_counts"]["main_param_minor_deviation"] == 1


def test_unified_scoring_explanation_contains_contributions():
    engine = UnifiedScoringEngine(
        feature_extractor=FeatureExtractor(),
        weight_calculator=AdaptiveWeightCalculator(),
    )

    scored = engine.score(
        {"name": "管道安装 DN100", "params": {"dn": 100}},
        [{"quota_id": "Q1", "name": "管道安装 DN100", "param_match": True, "param_score": 0.9, "hybrid_score": 0.8}],
    )

    explanation = scored[0]["explanation"]
    assert explanation["contributions"]
    assert explanation["top_driver"] in {"text_similarity", "param_match", "classification", "prior_knowledge", "context"}


def test_unified_ranking_pipeline_runs_end_to_end_with_injected_components():
    pipeline = UnifiedRankingPipeline(
        retrieval=UnifiedRetrieval(
            bm25_searcher=_FakeSearcher(
                [
                    {"quota_id": "Q1", "name": "管道安装", "hybrid_score": 0.8, "param_match": True, "param_score": 0.9},
                    {"quota_id": "Q2", "name": "风管安装", "hybrid_score": 0.4, "param_match": False, "param_score": 0.2},
                ]
            )
        )
    )

    result = pipeline.rank({"name": "管道安装", "params": {"dn": 100}}, top_k=1, retrieval_top_k=10)

    assert [candidate["quota_id"] for candidate in result["candidates"]] == ["Q1"]
    assert result["total_retrieved"] == 2
    assert result["sources_used"] == ["bm25"]
    assert result["meta"]["skeleton"] is True
    assert result["top1_score"] > 0.0
    assert result["top1_confidence"] >= 0.0
    assert result["top1_explanation"]["contributions"]
    assert result["diagnostics"]["retrieval"]["candidate_count"] == 2
    assert result["diagnostics"]["filter"]["candidate_count"] == 1
    assert result["diagnostics"]["selection"]["top_quota_id"] == "Q1"
