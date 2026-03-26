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


def test_ltr_ranker_manual_stage_prioritizes_non_conflict_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", False)

    candidates = [
        {
            "quota_id": "A",
            "name": "高语义错候选",
            "param_score": 0.95,
            "logic_score": 0.95,
            "feature_alignment_score": 0.95,
            "context_alignment_score": 0.95,
            "rerank_score": 0.99,
            "hybrid_score": 0.99,
            "logic_hard_conflict": True,
            "param_match": True,
        },
        {
            "quota_id": "B",
            "name": "正确结构候选",
            "param_score": 0.78,
            "logic_score": 0.82,
            "feature_alignment_score": 0.84,
            "context_alignment_score": 0.80,
            "rerank_score": 0.62,
            "hybrid_score": 0.62,
            "param_match": True,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "测试清单", "description": "DN25"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert ranked[0]["rank_stage"] == "manual"
    assert meta["primary_stage"] == "manual"
    assert meta["post_manual_top1_id"] == "B"


def test_ltr_ranker_ltr_stage_still_respects_hard_constraints(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.60]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "高分冲突候选",
            "param_score": 0.90,
            "logic_score": 0.90,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.90,
            "rerank_score": 0.95,
            "hybrid_score": 0.95,
            "logic_hard_conflict": True,
            "param_match": True,
        },
        {
            "quota_id": "B",
            "name": "低分可行候选",
            "param_score": 0.72,
            "logic_score": 0.80,
            "feature_alignment_score": 0.82,
            "context_alignment_score": 0.78,
            "rerank_score": 0.58,
            "hybrid_score": 0.58,
            "param_match": True,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "测试清单", "description": "DN25"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert ranked[0]["rank_stage"] == "ltr"
    assert meta["applied"] is True
    assert meta["primary_stage"] == "ltr"
    assert meta["post_ltr_top1_id"] == "B"


def test_ltr_ranker_ltr_stage_does_not_overweight_sparse_family_alignment(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.88, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "正确但弱结构候选",
            "param_score": 0.70,
            "logic_score": 0.70,
            "feature_alignment_score": 0.55,
            "context_alignment_score": 0.60,
            "rerank_score": 0.82,
            "hybrid_score": 0.82,
            "param_match": True,
        },
        {
            "quota_id": "B",
            "name": "错误但有family特征候选",
            "param_score": 0.66,
            "logic_score": 0.66,
            "feature_alignment_score": 0.56,
            "context_alignment_score": 0.58,
            "rerank_score": 0.60,
            "hybrid_score": 0.60,
            "param_match": True,
            "candidate_canonical_features": {"family": "pipe_support", "entity": "pipe"},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "凿（压)槽", "description": "凿（压)槽"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["post_ltr_top1_id"] == "A"


def test_ltr_ranker_cgr_shadow_guard_keeps_valid_ltr_top1(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.92, 0.61]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.0}],
    )
    monkeypatch.setattr(
        "src.ltr_ranker.apply_constrained_gated_ranker",
        lambda item, ranked, context: (
            [
                {
                    **ranked[1],
                    "cgr_feasible": True,
                    "cgr_score": 0.88,
                    "cgr_probability": 0.68,
                    "_rank_score_source": "cgr",
                },
                {
                    **ranked[0],
                    "cgr_feasible": True,
                    "cgr_score": 0.83,
                    "cgr_probability": 0.26,
                    "_rank_score_source": "cgr",
                },
            ],
            {
                "applied": True,
                "empty_feasible_set": False,
                "gate": 0.72,
                "top_quota_id": "B",
            },
        ),
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "valid incumbent",
            "param_match": True,
            "param_score": 0.86,
            "logic_score": 0.84,
            "feature_alignment_score": 0.81,
            "context_alignment_score": 0.80,
            "rerank_score": 0.79,
            "hybrid_score": 0.79,
        },
        {
            "quota_id": "B",
            "name": "cgr challenger",
            "param_match": True,
            "param_score": 0.82,
            "logic_score": 0.82,
            "feature_alignment_score": 0.80,
            "context_alignment_score": 0.79,
            "rerank_score": 0.78,
            "hybrid_score": 0.78,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "test item", "description": "DN25"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["post_cgr_top1_id"] == "A"
    assert meta["cgr"]["suggested_top1_id"] == "B"
    assert meta["cgr"]["override_allowed"] is False
    assert meta["cgr"]["override_reason"] == "incumbent_protected"


def test_ltr_ranker_cgr_shadow_guard_allows_invalid_ltr_top1_override(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.60]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.0}],
    )
    monkeypatch.setattr(
        "src.ltr_ranker.apply_constrained_gated_ranker",
        lambda item, ranked, context: (
            [
                {
                    **ranked[1],
                    "cgr_feasible": True,
                    "cgr_score": 0.89,
                    "cgr_probability": 0.74,
                    "_rank_score_source": "cgr",
                },
                {
                    **ranked[0],
                    "cgr_feasible": False,
                    "cgr_high_conf_wrong_book": True,
                    "cgr_score": -1.0,
                    "cgr_probability": 0.0,
                    "_rank_score_source": "cgr",
                },
            ],
            {
                "applied": True,
                "empty_feasible_set": False,
                "gate": 0.31,
                "top_quota_id": "B",
            },
        ),
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "bad incumbent",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.87,
            "feature_alignment_score": 0.85,
            "context_alignment_score": 0.84,
            "rerank_score": 0.83,
            "hybrid_score": 0.83,
        },
        {
            "quota_id": "B",
            "name": "valid challenger",
            "param_match": True,
            "param_score": 0.76,
            "logic_score": 0.78,
            "feature_alignment_score": 0.77,
            "context_alignment_score": 0.76,
            "rerank_score": 0.71,
            "hybrid_score": 0.71,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "test item", "description": "DN25"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["post_cgr_top1_id"] == "B"
    assert meta["cgr"]["suggested_top1_id"] == "B"
    assert meta["cgr"]["override_allowed"] is True
    assert meta["cgr"]["override_reason"] == "incumbent_high_conf_wrong_book"


def test_ltr_ranker_cgr_shadow_guard_does_not_override_param_mismatch_alone(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.94, 0.63]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.0}],
    )
    monkeypatch.setattr(
        "src.ltr_ranker.apply_constrained_gated_ranker",
        lambda item, ranked, context: (
            [
                {
                    **ranked[1],
                    "cgr_feasible": True,
                    "cgr_score": 0.91,
                    "cgr_probability": 0.72,
                    "_rank_score_source": "cgr",
                },
                {
                    **ranked[0],
                    "cgr_feasible": True,
                    "param_match": False,
                    "cgr_score": 0.40,
                    "cgr_probability": 0.18,
                    "_rank_score_source": "cgr",
                },
            ],
            {
                "applied": True,
                "empty_feasible_set": False,
                "gate": 0.25,
                "top_quota_id": "B",
            },
        ),
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "param mismatch incumbent",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.89,
            "feature_alignment_score": 0.88,
            "context_alignment_score": 0.86,
            "rerank_score": 0.84,
            "hybrid_score": 0.84,
        },
        {
            "quota_id": "B",
            "name": "challenger",
            "param_match": True,
            "param_score": 0.73,
            "logic_score": 0.74,
            "feature_alignment_score": 0.75,
            "context_alignment_score": 0.74,
            "rerank_score": 0.71,
            "hybrid_score": 0.71,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {"name": "test item", "description": "DN25"},
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["post_cgr_top1_id"] == "A"
    assert meta["cgr"]["override_allowed"] is False
    assert meta["cgr"]["override_reason"] == "incumbent_protected"
