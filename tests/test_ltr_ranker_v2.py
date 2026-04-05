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
    assert meta["post_manual_top1_id"] == "B"
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["primary_stage"] == "manual"
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


def test_ltr_ranker_keeps_manual_top1_when_ltr_only_suggests_flip(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.40, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "manual incumbent",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.87,
            "feature_alignment_score": 0.86,
            "context_alignment_score": 0.84,
            "rerank_score": 0.83,
            "hybrid_score": 0.83,
            "experience_layer": "authority",
            "match_source": "experience_injected",
            "knowledge_prior_sources": ["experience"],
            "candidate_canonical_features": {"entity": "截止阀"},
        },
        {
            "quota_id": "B",
            "name": "ltr challenger",
            "param_match": True,
            "param_score": 0.72,
            "logic_score": 0.71,
            "feature_alignment_score": 0.70,
            "context_alignment_score": 0.69,
            "rerank_score": 0.68,
            "hybrid_score": 0.68,
            "candidate_canonical_features": {"entity": "闸阀"},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "截止阀",
            "description": "DN25",
            "params": {},
            "canonical_features": {"entity": "截止阀"},
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "strong_anchor_protected"


def test_ltr_ranker_keeps_manual_top1_when_cgr_only_suggests_flip_without_hard_conflict(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", True)

    class _FakeModel:
        def predict(self, matrix):
            return [0.91, 0.60]

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
                    "cgr_score": 0.93,
                    "cgr_probability": 0.76,
                    "_rank_score_source": "cgr",
                },
                {
                    **ranked[0],
                    "cgr_feasible": True,
                    "cgr_score": 0.89,
                    "cgr_probability": 0.42,
                    "_rank_score_source": "cgr",
                },
            ],
            {
                "applied": True,
                "empty_feasible_set": False,
                "gate": 0.55,
                "top_quota_id": "B",
            },
        ),
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "manual incumbent",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.87,
            "feature_alignment_score": 0.85,
            "context_alignment_score": 0.84,
            "rerank_score": 0.82,
            "hybrid_score": 0.82,
        },
        {
            "quota_id": "B",
            "name": "cgr challenger",
            "param_match": True,
            "param_score": 0.80,
            "logic_score": 0.79,
            "feature_alignment_score": 0.78,
            "context_alignment_score": 0.77,
            "rerank_score": 0.76,
            "hybrid_score": 0.76,
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


def test_ltr_guard_blocks_ltr_override_on_strong_anchor(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.60, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "镀锌钢管 丝接 DN25",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.88,
            "feature_alignment_score": 0.86,
            "context_alignment_score": 0.84,
            "rerank_score": 0.82,
            "hybrid_score": 0.82,
            "candidate_canonical_features": {
                "entity": "钢管",
                "material": "镀锌钢管",
                "connection": "丝接",
            },
        },
        {
            "quota_id": "B",
            "name": "镀锌钢管 丝接 DN32",
            "param_match": True,
            "param_score": 0.76,
            "logic_score": 0.76,
            "feature_alignment_score": 0.76,
            "context_alignment_score": 0.76,
            "rerank_score": 0.75,
            "hybrid_score": 0.75,
            "candidate_canonical_features": {
                "entity": "钢管",
                "material": "镀锌钢管",
                "connection": "丝接",
            },
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "镀锌钢管",
            "description": "丝接 DN25",
            "params": {"dn": 25, "material": "镀锌钢管", "connection": "丝接"},
            "canonical_features": {
                "entity": "钢管",
                "material": "镀锌钢管",
                "connection": "丝接",
            },
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["anchor_score"] >= 9.0
    assert meta["ltr_guard"]["anchor_details"]["spec_field"] == "dn"


def test_ltr_guard_allows_ltr_override_on_weak_anchor(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.61, 0.94]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "普通项目安装",
            "param_match": True,
            "param_score": 0.84,
            "logic_score": 0.82,
            "feature_alignment_score": 0.80,
            "context_alignment_score": 0.80,
            "rerank_score": 0.79,
            "hybrid_score": 0.79,
            "candidate_canonical_features": {},
        },
        {
            "quota_id": "B",
            "name": "截止阀安装 DN50",
            "param_match": True,
            "param_score": 0.75,
            "logic_score": 0.75,
            "feature_alignment_score": 0.75,
            "context_alignment_score": 0.75,
            "rerank_score": 0.74,
            "hybrid_score": 0.74,
            "candidate_canonical_features": {"entity": "截止阀"},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "综合项",
            "description": "",
            "params": {},
            "canonical_features": {},
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "B"
    assert meta["primary_stage"] == "ltr"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["anchor_score"] < 6.0


def test_ltr_guard_allows_indoor_pipe_candidate_over_outdoor_manual_anchor(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.60, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )
    monkeypatch.setattr(
        "src.ltr_ranker.compute_candidate_structured_score",
        lambda candidate: {"A": 0.95, "B": 0.90}[candidate["quota_id"]],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "给排水管道 室外塑料排水管(粘接) 公称外径(mm以内) 50",
            "param_match": True,
            "param_score": 0.94,
            "logic_score": 1.0,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.80,
            "rerank_score": 0.99,
            "hybrid_score": 0.02,
            "candidate_canonical_features": {"entity": "塑料排水管", "material": "塑料"},
        },
        {
            "quota_id": "B",
            "name": "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 50",
            "param_match": True,
            "param_score": 0.94,
            "logic_score": 1.0,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.80,
            "rerank_score": 0.99,
            "hybrid_score": 0.02,
            "candidate_canonical_features": {"entity": "塑料排水管", "material": "塑料"},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "塑料管",
            "description": "材质、规格:UPVC排水DN50 连接形式:承插连接",
            "params": {"dn": 50, "material": "UPVC"},
            "canonical_features": {"entity": "塑料排水管", "material": "UPVC"},
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "B"
    assert meta["primary_stage"] == "ltr"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["reason"] == "challenger_explicit_semantic_advantage"
    assert "indoor_default_vs_outdoor_incumbent" in meta["ltr_guard"]["semantic_guard"]["details"]["signals"]


def test_ltr_guard_allows_plastic_rainwater_candidate_over_cast_iron_manual_anchor(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.60, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )
    monkeypatch.setattr(
        "src.ltr_ranker.compute_candidate_structured_score",
        lambda candidate: {"A": 0.96, "B": 0.88}[candidate["quota_id"]],
    )
    monkeypatch.setattr(
        LTRRanker,
        "_sort_with_stage_priority",
        staticmethod(
            lambda candidates, stage, primary_score_field: [
                {
                    **candidate,
                    "rank_stage": stage,
                    "rank_score": float(candidate.get(primary_score_field, 0.0) or 0.0),
                }
                for candidate in sorted(
                    list(candidates),
                    key=lambda candidate: (
                        {"manual": {"A": 2, "B": 1}, "ltr": {"B": 2, "A": 1}}[stage][candidate["quota_id"]]
                    ),
                    reverse=True,
                )
            ]
        ),
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "给排水管道 室内柔性铸铁雨水管(机械接口) 公称直径(mm以内) 100",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 1.0,
            "feature_alignment_score": 0.76,
            "context_alignment_score": 0.80,
            "rerank_score": 0.99,
            "hybrid_score": 0.02,
            "candidate_canonical_features": {"entity": "雨水管", "material": "铸铁"},
        },
        {
            "quota_id": "B",
            "name": "给排水管道 室内塑料雨水管(粘接) 公称外径(mm以内) 110",
            "param_match": True,
            "param_score": 0.80,
            "logic_score": 0.98,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.80,
            "rerank_score": 0.99,
            "hybrid_score": 0.04,
            "candidate_canonical_features": {"entity": "雨水管", "material": "塑料"},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "塑料管",
            "description": "材质、规格:UPVC雨水管DN100 连接形式:承插连接",
            "params": {"dn": 100, "material": "UPVC"},
            "canonical_features": {"entity": "雨水管", "material": "UPVC"},
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "B"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["reason"] == "challenger_explicit_semantic_advantage"
    signals = meta["ltr_guard"]["semantic_guard"]["details"]["signals"]
    assert "plastic_query_vs_metal_incumbent" in signals


def test_ltr_guard_blocks_override_on_weak_route_when_manual_margin_is_clear(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.61, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "天棚乳胶漆 两底两面",
            "param_match": True,
            "param_score": 0.92,
            "logic_score": 0.91,
            "feature_alignment_score": 0.90,
            "context_alignment_score": 0.88,
            "rerank_score": 0.84,
            "hybrid_score": 0.84,
            "candidate_canonical_features": {},
        },
        {
            "quota_id": "B",
            "name": "天棚乳胶漆 一底两面",
            "param_match": True,
            "param_score": 0.72,
            "logic_score": 0.72,
            "feature_alignment_score": 0.72,
            "context_alignment_score": 0.72,
            "rerank_score": 0.75,
            "hybrid_score": 0.75,
            "candidate_canonical_features": {},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "天棚乳胶漆",
            "description": "喷刷涂料部位:天棚 刮腻子要求:刮柔性腻子两遍 涂料品种、喷刷遍数:环保乳胶漆两底两面",
            "params": {},
            "canonical_features": {},
            "query_route": {"route": "semantic_description"},
        },
        candidates,
        {"query_route": {"route": "semantic_description"}},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "weak_route_manual_margin"
    assert meta["ltr_guard"]["route"] == "semantic_description"
    assert meta["ltr_guard"]["manual_margin"] >= 0.06


def test_ltr_guard_blocks_override_when_manual_scope_match_beats_scope_conflict(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.61, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.0}, {"f1": 1.0}],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "衬微晶板",
            "param_match": True,
            "param_score": 0.896,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.35,
            "rerank_score": 0.63,
            "hybrid_score": 0.03,
            "candidate_scope_match": 1.0,
            "candidate_scope_conflict": False,
            "candidate_canonical_features": {},
        },
        {
            "quota_id": "B",
            "name": "墙饰面 基层 细木工板",
            "param_match": True,
            "param_score": 0.896,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.35,
            "rerank_score": 0.95,
            "hybrid_score": 0.03,
            "candidate_scope_match": 0.0,
            "candidate_scope_conflict": True,
            "candidate_canonical_features": {},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面装饰板",
            "description": "干区WD-201木饰面 12mm厚B1级阻燃多层板",
            "params": {},
            "canonical_features": {},
            "_resolved_province": "上海市安装工程预算定额(2016)",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "scope_match_protected"
    assert meta["ltr_guard"]["scope_guard"]["incumbent_scope_match"] == 1.0
    assert meta["ltr_guard"]["scope_guard"]["challenger_scope_conflict"] is True


def test_ltr_guard_blocks_ltr_override_on_snapshot_anchor_signals(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.61, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [
            {
                "f1": 0.0,
                "entity_match": 1,
                "canonical_name_match": 1,
                "system_match": 1,
                "family_match": 0,
            },
            {
                "f1": 1.0,
                "entity_match": 0,
                "canonical_name_match": 0,
                "entity_conflict": 1,
                "canonical_name_conflict": 1,
                "system_match": 1,
                "family_match": 0,
            },
        ],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "消火栓按钮",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 1.0,
            "rerank_score": 0.84,
            "hybrid_score": 0.84,
            "candidate_canonical_features": {
                "entity": "消火栓",
                "canonical_name": "消火栓",
                "system": "消防",
            },
        },
        {
            "quota_id": "B",
            "name": "报警按钮 有线式报警",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.37,
            "context_alignment_score": 1.0,
            "rerank_score": 0.92,
            "hybrid_score": 0.92,
            "candidate_canonical_features": {
                "entity": "报警按钮",
                "canonical_name": "报警按钮",
                "system": "消防",
            },
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "消火栓按钮",
            "description": "类型：总线制 安装方式：消火栓箱内安装",
            "canonical_features": {
                "entity": "消火栓",
                "canonical_name": "消火栓",
                "system": "消防",
            },
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "challenger_struct_conflict"
    assert meta["ltr_guard"]["snapshot_guard"]["blocked"] is True
    assert meta["ltr_guard"]["snapshot_guard"]["details"]["incumbent_entity_match"] is True


def test_ltr_guard_blocks_ltr_override_on_family_system_anchor(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.64, 0.96]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [
            {
                "f1": 0.0,
                "entity_match": 1,
                "canonical_name_match": 0,
                "system_match": 1,
                "family_match": 1,
            },
            {
                "f1": 1.0,
                "entity_match": 1,
                "canonical_name_match": 0,
                "system_match": 0,
                "family_match": 0,
            },
        ],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "镀锌钢管敷设 暗配 DN20",
            "param_match": True,
            "param_score": 0.89,
            "logic_score": 1.0,
            "feature_alignment_score": 0.72,
            "context_alignment_score": 0.9,
            "rerank_score": 0.95,
            "hybrid_score": 0.95,
            "candidate_canonical_features": {
                "entity": "配管",
                "family": "conduit_raceway",
                "system": "电气",
            },
        },
        {
            "quota_id": "B",
            "name": "套接紧定式镀锌钢导管敷设 暗配 20",
            "param_match": True,
            "param_score": 0.86,
            "logic_score": 1.0,
            "feature_alignment_score": 0.55,
            "context_alignment_score": 0.8,
            "rerank_score": 0.95,
            "hybrid_score": 0.95,
            "candidate_canonical_features": {
                "entity": "配管",
                "family": "",
                "system": "",
            },
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "配管",
            "description": "材质：SC 规格：20 配置形式:暗敷设",
            "canonical_features": {
                "entity": "配管",
                "family": "conduit_raceway",
                "system": "电气",
            },
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "family_system_anchor_dominates"
    assert meta["ltr_guard"]["snapshot_guard"]["blocked"] is True
    assert meta["ltr_guard"]["snapshot_guard"]["details"]["incumbent_family_match"] is True
