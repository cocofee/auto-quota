from __future__ import annotations

from src.candidate_scoring import compute_candidate_rank_score
from src.ltr_ranker import LTRRanker


def test_candidate_rank_score_prefers_ltr_when_marked():
    candidate = {
        "_rank_score_source": "ltr",
        "ltr_score": 0.91,
        "param_score": 0.1,
        "logic_score": 0.1,
        "feature_alignment_score": 0.1,
        "context_alignment_score": 0.1,
        "rerank_score": 0.1,
    }
    assert compute_candidate_rank_score(candidate) == 0.91


def test_ltr_ranker_falls_back_when_model_missing(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_V2_MODEL_PATH", "output/not_exists_model.txt")
    monkeypatch.setattr("config.LTR_V2_FEATURES_PATH", "output/not_exists_features.json")
    LTRRanker._model = None
    LTRRanker._feature_names = None
    LTRRanker._load_attempted = False
    LTRRanker._load_error = ""

    candidates = [
        {
            "quota_id": "A",
            "name": "钢管 DN25",
            "param_score": 0.8,
            "logic_score": 0.8,
            "feature_alignment_score": 0.8,
            "context_alignment_score": 0.8,
            "rerank_score": 0.8,
            "hybrid_score": 0.8,
        },
        {
            "quota_id": "B",
            "name": "钢管 DN32",
            "param_score": 0.7,
            "logic_score": 0.7,
            "feature_alignment_score": 0.7,
            "context_alignment_score": 0.7,
            "rerank_score": 0.7,
            "hybrid_score": 0.7,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "钢管", "description": "DN25", "params": {"dn": 25}},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["applied"] is False
    assert meta["fallback_reason"].startswith("model_missing")
