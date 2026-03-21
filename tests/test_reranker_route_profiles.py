from src.param_validator import ParamValidator
from src.reranker import Reranker


class _FakeModel:
    def predict(self, pairs):
        query = pairs[0][0]
        if "48" in query:
            return [0.35, 0.92]
        return [0.88, 0.52]


def test_reranker_uses_spec_scores_for_spec_heavy_routes():
    reranker = Reranker(model_name="fake")
    reranker._model = _FakeModel()
    candidates = [
        {"quota_id": "Q1", "name": "成套配电箱 24回路"},
        {"quota_id": "Q2", "name": "成套配电箱 48回路"},
    ]

    ranked = reranker.rerank(
        "成套配电箱 48",
        candidates,
        route_profile={"route": "installation_spec"},
    )

    assert ranked[0]["quota_id"] == "Q2"
    assert ranked[0]["spec_rerank_score"] == 0.92
    assert ranked[0]["semantic_rerank_score"] == 0.52
    assert ranked[0]["active_rerank_score"] == ranked[0]["spec_rerank_score"]


def test_param_validator_final_rank_keeps_best_structured_candidate_first():
    candidates = [
        {
            "quota_id": "Q1",
            "name": "镀锌钢管 DN150",
            "param_match": True,
            "param_tier": 2,
            "param_score": 0.84,
            "logic_score": 0.72,
            "feature_alignment_score": 0.78,
            "context_alignment_score": 0.76,
            "rerank_score": 0.95,
            "candidate_canonical_features": {"entity": "pipe", "family": "pipe_support", "system": "water"},
        },
        {
            "quota_id": "Q2",
            "name": "镀锌钢管 DN100",
            "param_match": True,
            "param_tier": 2,
            "param_score": 0.92,
            "logic_score": 0.95,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.86,
            "rerank_score": 0.72,
            "logic_exact_primary_match": True,
            "candidate_canonical_features": {"entity": "pipe", "family": "pipe_support", "system": "water"},
        },
    ]

    ParamValidator._final_rank(candidates)

    assert candidates[0]["quota_id"] == "Q2"
    assert candidates[0]["rank_score"] >= candidates[1]["rank_score"]
