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


def test_ltr_guard_allows_arrow_candidate_with_explicit_direction_and_length(monkeypatch):
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
    monkeypatch.setattr(
        "src.ltr_ranker.compute_candidate_structured_score",
        lambda candidate: {"A": 0.92, "B": 0.86}[candidate["quota_id"]],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "标记 箭头 直行(9m)热熔型",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.77,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "B",
            "name": "标记 箭头 转弯(6m)热熔型",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "标记",
            "description": "6m转弯箭头，热熔标线",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["reason"] == "challenger_explicit_semantic_advantage"
    signals = meta["ltr_guard"]["semantic_guard"]["details"]["signals"]
    assert "traffic_arrow_spec_alignment" in signals


def test_ltr_guard_rescues_prime_coat_to_semirigid_emulsified_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.80, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.5}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "3-541",
            "name": "镶贴面层 粘贴石材",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-161",
            "name": "透层粒料基层乳化沥青1.2L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.19,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-163",
            "name": "透层半刚性基层乳化沥青1.1L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.12,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "透层、粘层",
            "description": "水泥稳定碎石层上设置乳化沥青透层 喷油量1.0L/m2",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked][:3] == ["2-163", "3-541", "2-161"]
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "bitumen_layer_rescued"
    assert meta["ltr_guard"]["bitumen_layer_rescue"]["blocked"] is True


def test_ltr_guard_rescues_tack_coat_to_emulsified_tack_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.80, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.5}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "4-449",
            "name": "模板衬墙",
            "param_match": True,
            "param_score": 0.97,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.06,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-161",
            "name": "透层粒料基层乳化沥青1.2L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.11,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-165",
            "name": "黏层沥青层乳化沥青0.5L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.14,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "透层、粘层",
            "description": "PC-3乳化沥青粘层，用量0.3L/m2-0.6L/m2",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked][:3] == ["2-165", "4-449", "2-161"]
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "bitumen_layer_rescued"
    assert meta["ltr_guard"]["bitumen_layer_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_generic_bitumen_title_without_emulsified_signal(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.80]

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
            "quota_id": "4-449",
            "name": "模板衬墙",
            "param_match": True,
            "param_score": 0.97,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.06,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-160",
            "name": "透层粒料基层石油沥青1.2L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.11,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "透层、粘层",
            "description": "石油沥青透层 用量1.2L/m2",
        },
        candidates,
        {},
    )

    assert ranked[0]["quota_id"] == "4-449"
    assert meta["ltr_guard"]["bitumen_layer_rescue"]["blocked"] is False


def test_ltr_guard_confirms_prime_coat_ltr_top1_against_weak_route_guard(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.80, 0.95]

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
            "quota_id": "3-541",
            "name": "镶贴面层 粘贴石材",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-161",
            "name": "透层粒料基层乳化沥青1.2L/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.19,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "透层、粘层",
            "description": "材料品种:透油层乳化沥青 喷油量:0.9-1.0L/m2",
        },
        candidates,
        {"query_route": {"route": "material"}},
    )

    assert ranked[0]["quota_id"] == "2-161"
    assert meta["raw_ltr_top1_id"] == "2-161"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "bitumen_layer_confirmed"
    assert meta["ltr_guard"]["bitumen_layer_rescue"]["blocked"] is True


def test_ltr_guard_rescues_water_stabilized_paver_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.6}, {"f1": 0.4}],
    )

    candidates = [
        {
            "quota_id": "2-129",
            "name": "水泥稳定碎石基层 现拌人铺5%水泥稳定碎石基层厚20cm",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.87,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-186",
            "name": "垫层碎石",
            "param_match": True,
            "param_score": 0.89,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.84,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-133",
            "name": "水泥稳定碎石基层 现拌沥青摊铺机摊铺5%水泥稳定碎石基层厚20cm",
            "param_match": True,
            "param_score": 0.89,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.45,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-134",
            "name": "水泥稳定碎石基层 现拌沥青摊铺机摊铺5%水泥稳定碎石基层每减1cm",
            "param_match": True,
            "param_score": 0.89,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.81,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "水泥稳定碎（砾）石 调节层",
            "description": "水泥稳定碎石层 调节层 摊铺方式：采用集中厂拌且摊铺时必须采用专用摊铺机摊铺",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}},
    )

    assert ranked[0]["quota_id"] == "2-133"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "water_stabilized_paver_rescued"
    assert meta["ltr_guard"]["water_stabilized_paver_rescue"]["blocked"] is True


def test_ltr_guard_rescues_pc_laminated_slab_from_wall_panel(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "5-205",
            "name": "装配式混凝土构件 外墙面板(PCF板)",
            "param_match": True,
            "param_score": 0.92,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-196",
            "name": "装配式混凝土构件 叠合板",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-204",
            "name": "装配式混凝土构件 外墙板",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "PC叠合楼板",
            "description": "预制装配式PC叠合楼板安装",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-196"
    assert meta["raw_ltr_top1_id"] == "5-205"
    assert meta["post_ltr_top1_id"] == "5-196"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "precast_laminated_slab_rescued"
    assert meta["ltr_guard"]["precast_laminated_slab_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_pc_wall_panel_item(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "5-205",
            "name": "装配式混凝土构件 外墙面板(PCF板)",
            "param_match": True,
            "param_score": 0.92,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-196",
            "name": "装配式混凝土构件 叠合板",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "PC外墙面板",
            "description": "装配式混凝土构件 PCF板外墙面板安装",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-205"
    assert meta["ltr_guard"]["precast_laminated_slab_rescue"]["blocked"] is False


def test_ltr_guard_rescues_bridge_concrete_foundation_from_stone_foundation(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "4-71",
            "name": "块石基础 灌混凝土",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-189",
            "name": "混凝土基础混凝土",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-190",
            "name": "混凝土基础模板",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "混凝土基础",
            "description": "C30非泵送商品混凝土基础，商品混凝土运距包干。",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "3-189"
    assert meta["raw_ltr_top1_id"] == "4-71"
    assert meta["post_ltr_top1_id"] == "3-189"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "concrete_foundation_rescued"
    assert meta["ltr_guard"]["concrete_foundation_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_stone_foundation_item(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "4-71",
            "name": "块石基础 灌混凝土",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-189",
            "name": "混凝土基础混凝土",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "块石基础",
            "description": "块石基础灌C30商品混凝土",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "4-71"
    assert meta["ltr_guard"]["concrete_foundation_rescue"]["blocked"] is False


def test_ltr_guard_rescues_foam_expansion_joint_from_oil_hemp(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "9-117",
            "name": "嵌填缝 油浸麻丝 缝断面(mm2)30×150立面",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-122",
            "name": "嵌填缝 泡沫塑料填塞 缝断面(mm2)30×150平面",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-123",
            "name": "嵌填缝 泡沫塑料填塞 缝断面(mm2)30×150立面",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面变形缝",
            "description": "地下室后浇带 聚苯乙烯泡沫塑料板250*30，聚氨酯密封膏",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-122"
    assert meta["raw_ltr_top1_id"] == "9-117"
    assert meta["post_ltr_top1_id"] == "9-122"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "foam_expansion_joint_rescued"
    assert meta["ltr_guard"]["foam_expansion_joint_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_oil_hemp_joint_item(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "9-117",
            "name": "嵌填缝 油浸麻丝 缝断面(mm2)30×150立面",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-122",
            "name": "嵌填缝 泡沫塑料填塞 缝断面(mm2)30×150平面",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面变形缝",
            "description": "嵌填缝 油浸麻丝 30×150",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-117"
    assert meta["ltr_guard"]["foam_expansion_joint_rescue"]["blocked"] is False


def test_ltr_guard_rescues_embedded_iron_above_25kg_from_cross_book(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}],
    )

    candidates = [
        {
            "quota_id": "1-279",
            "name": "铁件制作、安装 预埋铁件",
            "param_match": True,
            "param_score": 0.35,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-96",
            "name": "预埋铁件 25kg/块以上",
            "param_match": True,
            "param_score": 0.50,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.55,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-95",
            "name": "预埋铁件 25kg/块以内",
            "param_match": True,
            "param_score": 0.50,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.52,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "预埋铁件",
            "description": "钢梁与混凝土连接的预埋钢板，重量25kg以上，含防锈漆",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-96"
    assert meta["raw_ltr_top1_id"] == "1-279"
    assert meta["post_ltr_top1_id"] == "5-96"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "embedded_iron_rescued"
    assert meta["ltr_guard"]["embedded_iron_rescue"]["blocked"] is True


def test_ltr_guard_rescues_embedded_iron_within_25kg_from_plating(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}],
    )

    candidates = [
        {
            "quota_id": "14-121",
            "name": "金属面镀锌",
            "param_match": True,
            "param_score": 0.69,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.61,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-96",
            "name": "预埋铁件 25kg/块以上",
            "param_match": True,
            "param_score": 0.65,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.55,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-95",
            "name": "预埋铁件 25kg/块以内",
            "param_match": True,
            "param_score": 0.65,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.51,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "预埋铁件",
            "description": "预埋铁件 25kg/块以内，表面热镀锌",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-95"
    assert meta["raw_ltr_top1_id"] == "14-121"
    assert meta["post_ltr_top1_id"] == "5-95"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "embedded_iron_rescued"
    assert meta["ltr_guard"]["embedded_iron_rescue"]["blocked"] is True


def test_ltr_guard_rescues_road_embedded_iron_to_road_ironwork(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "5-95",
            "name": "预埋铁件 25kg/块以内",
            "param_match": True,
            "param_score": 0.47,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-279",
            "name": "铁件制作、安装 预埋铁件",
            "param_match": True,
            "param_score": 0.45,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "预埋铁件",
            "description": "热镀锌角钢四周包边",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-279"
    assert meta["raw_ltr_top1_id"] == "5-95"
    assert meta["post_ltr_top1_id"] == "1-279"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "embedded_iron_rescued"
    assert meta["ltr_guard"]["embedded_iron_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_road_embedded_iron_without_road_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "5-95",
            "name": "预埋铁件 25kg/块以内",
            "param_match": True,
            "param_score": 0.47,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "10-150",
            "name": "预埋铁件",
            "param_match": True,
            "param_score": 0.45,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "预埋铁件",
            "description": "热镀锌角钢四周包边",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "5-95"
    assert meta["ltr_guard"]["embedded_iron_rescue"]["blocked"] is False


def test_ltr_guard_rescues_postcast_hrb400_rebar_10mm_from_lightning(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "4-9-40",
            "name": "避雷引下线敷设 利用建筑结构钢筋引下",
            "param_match": True,
            "param_score": 0.97,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-236",
            "name": "后浇混凝土 带肋钢筋HRB400以内 直径10mm以内",
            "param_match": True,
            "param_score": 0.64,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-238",
            "name": "后浇混凝土 带肋钢筋HRB400以内 直径25mm以内",
            "param_match": True,
            "param_score": 0.64,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "后浇构件钢筋",
            "description": "热轧带肋钢筋HRB400盘条，直径Φ=8；用途：直条筋铺设；部位：后浇混凝土钢筋",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-236"
    assert meta["raw_ltr_top1_id"] == "4-9-40"
    assert meta["post_ltr_top1_id"] == "5-236"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "postcast_rebar_rescued"
    assert meta["ltr_guard"]["postcast_rebar_rescue"]["blocked"] is True


def test_postcast_rebar_intent_accepts_literal_diameter_wording():
    intent = LTRRanker._postcast_rebar_intent(
        "\u540e\u6d47\u6784\u4ef6\u94a2\u7b4b "
        "\u70ed\u8f67\u5e26\u808b\u94a2\u7b4bHRB400 "
        "\u76f4\u5f848mm",
        "C5",
    )

    assert intent["building"] is True
    assert intent["wants_postcast"] is True
    assert intent["wants_deformed"] is True
    assert intent["diameter_values"] == [8.0]
    assert intent["diameter_le_10"] is True


def test_ltr_guard_does_not_rescue_postcast_hoop_rebar_to_straight_bar(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "5-236",
            "name": "后浇混凝土 带肋钢筋HRB400以内 直径10mm以内",
            "param_match": True,
            "param_score": 0.64,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "5-245",
            "name": "后浇混凝土 箍筋带肋钢筋HRB400以内 直径10mm以内",
            "param_match": True,
            "param_score": 0.64,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "后浇构件钢筋",
            "description": "热轧带肋钢筋HRB400箍筋，规格Φ8，部位：后浇混凝土钢筋",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-236"
    assert meta["ltr_guard"]["postcast_rebar_rescue"]["blocked"] is False


def test_ltr_guard_rescues_manhole_surround_gravel_backfill_from_manhole(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "1-394",
            "name": "砖砌检查井深4m以内",
            "param_match": True,
            "param_score": 0.54,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.38,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-309",
            "name": "沟槽回填 砂砾石人工级配",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.34,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-308",
            "name": "沟槽回填 砂砾石天然级配",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.32,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-248",
            "name": "井 垫层砂砾石人工级配",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.31,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "检查井四周回填",
            "description": "检查井四周粒径小于40mm的级配砂砾石回填",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-308"
    assert meta["raw_ltr_top1_id"] == "1-394"
    assert meta["post_ltr_top1_id"] == "6-308"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "manhole_surround_backfill_rescued"
    assert meta["ltr_guard"]["manhole_surround_backfill_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_artificial_grade_manhole_backfill_to_natural(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "6-309",
            "name": "沟槽回填 砂砾石人工级配",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.34,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-308",
            "name": "沟槽回填 砂砾石天然级配",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.32,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "检查井四周回填",
            "description": "检查井四周人工级配砂砾石回填",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-309"
    assert meta["ltr_guard"]["manhole_surround_backfill_rescue"]["blocked"] is False


def test_ltr_guard_rescues_sinking_well_bottom_slab_from_cross_book(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}, {"f1": 0.5}],
    )

    candidates = [
        {
            "quota_id": "4-377",
            "name": "沉井制作 混凝土底板",
            "param_match": True,
            "param_score": 0.98,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.90,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-621",
            "name": "沉井 混凝土垫层",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-250",
            "name": "井 底板混凝土",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.42,
            "rerank_score": 0.86,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-624",
            "name": "沉井制作 底板厚度50cm以内",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.84,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-625",
            "name": "沉井制作 底板厚度50cm外",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.38,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "沉井混凝土底板",
            "description": "C35泵送商品混凝土底板,混凝土抗渗为P8。",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-624"
    assert meta["raw_ltr_top1_id"] == "4-377"
    assert meta["post_ltr_top1_id"] == "6-624"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "sinking_well_bottom_slab_rescued"
    assert meta["ltr_guard"]["sinking_well_bottom_slab_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_sinking_well_bedding_to_bottom_slab(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}],
    )

    candidates = [
        {
            "quota_id": "4-377",
            "name": "沉井制作 混凝土底板",
            "param_match": True,
            "param_score": 0.98,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.90,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-624",
            "name": "沉井制作 底板厚度50cm以内",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.84,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-621",
            "name": "沉井 混凝土垫层",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.38,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "沉井混凝土垫层",
            "description": "C15商品混凝土垫层,厚度10cm。",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "4-377"
    assert meta["ltr_guard"]["sinking_well_bottom_slab_rescue"]["blocked"] is False


def test_ltr_guard_rescues_surplus_soil_disposal_to_road_soil_haul(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "4-102",
            "name": "自卸汽车运土(m)1000以内",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.85,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-94",
            "name": "自卸汽车运土方运距1km以内",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-95",
            "name": "自卸汽车运土方运距每增加1km",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-84",
            "name": "石碴回填",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.43,
            "rerank_score": 0.74,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "余方弃置",
            "description": "土石方外运,运距自行考虑包干",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-94"
    assert meta["raw_ltr_top1_id"] == "4-102"
    assert meta["post_ltr_top1_id"] == "1-94"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "surplus_soil_disposal_rescued"
    assert meta["ltr_guard"]["surplus_soil_disposal_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_stone_disposal_to_soil_haul(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "1-155",
            "name": "自卸汽车运石碴运距1km以内",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-94",
            "name": "自卸汽车运土方运距1km以内",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "余方弃置",
            "description": "石方外运,运距自行考虑",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-155"
    assert meta["ltr_guard"]["surplus_soil_disposal_rescue"]["blocked"] is False


def test_ltr_guard_rescues_municipal_portal_frame_sign_from_cross_book(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "3-183",
            "name": "钢管拱安装 系杆",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.45,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-295",
            "name": "悬臂式、门式架 门式架",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.43,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-294",
            "name": "悬臂式、门式架 悬臂式T杆",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.42,
            "rerank_score": 0.74,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-92",
            "name": "钢支撑15m以内拆除",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "标杆",
            "description": "类型:门式架拆除，包含杆件及杆上设备拆除、堆放、保管",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-295"
    assert meta["raw_ltr_top1_id"] == "3-183"
    assert meta["post_ltr_top1_id"] == "2-295"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "portal_frame_sign_rescued"
    assert meta["ltr_guard"]["portal_frame_sign_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_cantilever_t_pole_to_portal_frame(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "2-294",
            "name": "悬臂式、门式架 悬臂式T杆",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.42,
            "rerank_score": 0.74,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-295",
            "name": "悬臂式、门式架 门式架",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.43,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "标杆",
            "description": "类型:悬臂式T杆拆除，包含杆件及杆上设备拆除",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-294"
    assert meta["ltr_guard"]["portal_frame_sign_rescue"]["blocked"] is False


def test_ltr_guard_rescues_municipal_curb_stone_from_demolition_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.80, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.5}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "4-44",
            "name": "人工翻挖侧平石侧石",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.83,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-250",
            "name": "侧石石质",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-246",
            "name": "人工铺装混凝土垫层",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "安砌侧（平、缘）石",
            "description": "材料品种、规格:现状机非绿化带石质圆弧侧石（10*20cm)(更换）",
            "specialty": "C2",
        },
        candidates,
        {},
    )

    assert ranked[0]["quota_id"] == "2-250"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "curb_stone_rescued"
    assert meta["ltr_guard"]["curb_stone_rescue"]["blocked"] is True


def test_ltr_guard_confirms_municipal_curb_stone_ltr_top1(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.80, 0.95, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 0.5}, {"f1": 1.0}, {"f1": 0.0}],
    )

    candidates = [
        {
            "quota_id": "4-44",
            "name": "\u4eba\u5de5\u7ffb\u6316\u4fa7\u5e73\u77f3\u4fa7\u77f3",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.83,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-250",
            "name": "\u4fa7\u77f3\u77f3\u8d28",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-246",
            "name": "\u4eba\u5de5\u94fa\u88c5\u6df7\u51dd\u571f\u57ab\u5c42",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u5b89\u780c\u4fa7\uff08\u5e73\u3001\u7f18\uff09\u77f3",
            "description": "\u77f3\u8d28\u5706\u5f27\u4fa7\u77f3 10*20cm",
            "specialty": "C2",
            "query_route": {"route": "semantic_description"},
        },
        candidates,
        {"query_route": {"route": "semantic_description"}},
    )

    assert ranked[0]["quota_id"] == "2-250"
    assert meta["raw_ltr_top1_id"] == "2-250"
    assert meta["post_ltr_top1_id"] == "2-250"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "curb_stone_confirmed"
    assert meta["ltr_guard"]["curb_stone_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_curb_stone_demolition_item(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.5}],
    )

    candidates = [
        {
            "quota_id": "4-44",
            "name": "人工翻挖侧平石侧石",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.83,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-250",
            "name": "侧石石质",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "拆除侧、平（缘）石",
            "description": "侧石拆除",
            "specialty": "C2",
        },
        candidates,
        {},
    )

    assert ranked[0]["quota_id"] == "4-44"
    assert meta["ltr_guard"]["curb_stone_rescue"]["blocked"] is False


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


def test_ltr_guard_allows_rebar_planting_challenger_over_scope_incumbent(monkeypatch):
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
            "quota_id": "4-9-44",
            "name": "避雷网安装 均压环敷设利用圈梁钢筋",
            "param_match": True,
            "param_score": 0.75,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.90,
            "rerank_score": 0.01,
            "hybrid_score": 0.03,
            "candidate_scope_match": 1.0,
            "candidate_scope_conflict": False,
            "candidate_canonical_features": {},
        },
        {
            "quota_id": "5-89",
            "name": "植筋(钢筋直径6.5mm以内)",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 1.0,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "rerank_score": 0.47,
            "hybrid_score": 0.03,
            "candidate_scope_match": 0.0,
            "candidate_scope_conflict": True,
            "candidate_canonical_features": {},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "植筋",
            "description": "墙体钻孔，上胶安装，钢筋直径6.5mm以内[钢筋另计]。",
            "specialty": "C5",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C5"},
    )

    assert ranked[0]["quota_id"] == "5-89"
    assert meta["raw_ltr_top1_id"] == "5-89"
    assert meta["post_ltr_top1_id"] == "5-89"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["reason"] == "challenger_explicit_semantic_advantage"
    assert "rebar_planting_keyword_alignment" in meta["ltr_guard"]["semantic_guard"]["details"]["signals"]


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


def test_ltr_guard_blocks_structurally_stable_pre_ltr_top1(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.20, 0.95]

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
                "family_match": 0,
                "semantic_rerank_zscore": 1.40,
            },
            {
                "f1": 1.0,
                "entity_match": 0,
                "canonical_name_match": 0,
                "system_match": 0,
                "family_match": 0,
                "semantic_rerank_zscore": 0.45,
            },
        ],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "接地母线敷设",
            "param_match": True,
            "param_score": 0.76,
            "logic_score": 0.5,
            "feature_alignment_score": 0.82,
            "context_alignment_score": 0.8,
            "rerank_score": 0.94,
            "hybrid_score": 0.94,
            "candidate_canonical_features": {
                "entity": "接地母线",
                "system": "电气",
            },
        },
        {
            "quota_id": "B",
            "name": "平整场地",
            "param_match": True,
            "param_score": 0.70,
            "logic_score": 0.5,
            "feature_alignment_score": 0.50,
            "context_alignment_score": 0.8,
            "rerank_score": 0.64,
            "hybrid_score": 0.90,
            "candidate_canonical_features": {},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "接地母线",
            "description": "接地母线敷设",
            "canonical_features": {
                "entity": "接地母线",
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
    assert meta["ltr_guard"]["reason"] == "pre_ltr_structural_stability"
    assert meta["ltr_guard"]["pre_ltr_stability_guard"]["blocked"] is True
    assert meta["ltr_guard"]["pre_ltr_stability_guard"]["details"]["incumbent_struct_matches"] == 2


def test_ltr_guard_allows_structural_pre_ltr_top1_when_challenger_is_not_weaker(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.20, 0.95]

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
                "system_match": 0,
                "family_match": 1,
                "semantic_rerank_zscore": 0.84,
            },
            {
                "f1": 1.0,
                "entity_match": 0,
                "canonical_name_match": 0,
                "system_match": 0,
                "family_match": 0,
                "semantic_rerank_zscore": 0.86,
            },
        ],
    )

    candidates = [
        {
            "quota_id": "A",
            "name": "钢管煨弯 管外径89",
            "param_match": True,
            "param_score": 0.82,
            "logic_score": 0.5,
            "feature_alignment_score": 0.82,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.94,
            "candidate_canonical_features": {
                "entity": "钢管",
                "family": "pipe",
            },
        },
        {
            "quota_id": "B",
            "name": "标志杆 单柱式 φ89",
            "param_match": True,
            "param_score": 0.93,
            "logic_score": 0.5,
            "feature_alignment_score": 0.50,
            "context_alignment_score": 0.8,
            "rerank_score": 0.89,
            "hybrid_score": 0.70,
            "candidate_canonical_features": {},
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "标杆",
            "description": "单柱式 φ89",
            "canonical_features": {
                "entity": "标志杆",
            },
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "B"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["pre_ltr_stability_guard"]["blocked"] is False


def test_ltr_guard_blocks_surface_orientation_flip(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.20, 0.95]

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
            "name": "聚合物水泥防水涂料 厚度1.2mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.95,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "B",
            "name": "聚合物水泥防水涂料 厚度1.2mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面涂膜防水",
            "description": "墙面 JS 聚合物水泥基防水涂料",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["A", "B"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "A"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "surface_orientation_protected"
    assert meta["ltr_guard"]["surface_orientation_guard"]["blocked"] is True


def test_ltr_guard_allows_roof_item_when_challenger_matches_vertical_description(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.20, 0.95]

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
            "name": "改性沥青自粘卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 0.98,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.96,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "B",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 0.98,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.84,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "屋面卷材防水 立面",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["B", "A"]
    assert meta["raw_ltr_top1_id"] == "B"
    assert meta["post_ltr_top1_id"] == "B"
    assert meta["ltr_guard"]["action"] == "allowed"
    assert meta["ltr_guard"]["surface_orientation_guard"]["blocked"] is False


def test_ltr_guard_rescues_surface_orientation_candidate_when_ltr_top1_is_wrong(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.70]

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
            "quota_id": "9-77",
            "name": "改性沥青防水涂料 厚度2.0mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.85,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-76",
            "name": "改性沥青防水涂料 厚度2.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.82,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面涂膜防水",
            "description": "屋面改性沥青防水涂料",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-76", "9-77"]
    assert meta["raw_ltr_top1_id"] == "9-77"
    assert meta["post_ltr_top1_id"] == "9-76"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "surface_orientation_rescued"
    assert meta["ltr_guard"]["surface_orientation_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["surface_orientation_rescue"]["details"]["rescued_rank"] == 2


def test_ltr_guard_does_not_rescue_surface_orientation_across_books(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.70]

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
            "quota_id": "9-77",
            "name": "改性沥青防水涂料 厚度2.0mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.85,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "10-76",
            "name": "改性沥青防水涂料 厚度2.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.82,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面涂膜防水",
            "description": "屋面改性沥青防水涂料",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-77", "10-76"]
    assert meta["post_ltr_top1_id"] == "9-77"
    assert meta["ltr_guard"]["action"] == "no_change"
    assert meta["ltr_guard"]["surface_orientation_rescue"]["blocked"] is False


def test_ltr_guard_does_not_rescue_surface_orientation_across_material_pairs(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.70]

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
            "quota_id": "9-52",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.95,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-70",
            "name": "高分子卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "rerank_score": 0.83,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "屋面4.0厚自粘聚合物改性沥青卷材",
        },
        candidates,
        {},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-52", "9-70"]
    assert meta["post_ltr_top1_id"] == "9-52"
    assert meta["ltr_guard"]["action"] == "no_change"
    assert meta["ltr_guard"]["surface_orientation_rescue"]["blocked"] is False


def test_ltr_guard_rescues_roof_self_adhesive_polymer_membrane_from_bitumen(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "9-52",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-70",
            "name": "高分子卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.84,
            "hybrid_score": 0.70,
        },
        {
            "quota_id": "9-71",
            "name": "高分子卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.83,
            "hybrid_score": 0.68,
        },
        {
            "quota_id": "9-72",
            "name": "高分子卷材自粘法 每增一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.82,
            "hybrid_score": 0.65,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "1.5mm厚自粘式高分子合成防水卷材，部位：屋面2",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-70"
    assert meta["raw_ltr_top1_id"] == "9-52"
    assert meta["post_ltr_top1_id"] == "9-70"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "self_adhesive_polymer_membrane_rescued"
    assert meta["ltr_guard"]["self_adhesive_polymer_membrane_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_modified_bitumen_membrane_to_polymer(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "9-52",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-70",
            "name": "高分子卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.84,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面卷材防水",
            "description": "3厚自粘聚合物改性沥青防水卷材(聚酯胎)两道",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-52", "9-70"]
    assert meta["post_ltr_top1_id"] == "9-52"
    assert meta["ltr_guard"]["self_adhesive_polymer_membrane_rescue"]["blocked"] is False


def test_ltr_guard_rescues_cementitious_crystalline_waterproof_from_bitumen(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80, 0.75, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}, {"f1": 0.5}, {"f1": 0.4}],
    )

    candidates = [
        {
            "quota_id": "9-79",
            "name": "改性沥青防水涂料 厚度每增减0.1mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-78",
            "name": "改性沥青防水涂料 厚度每增减0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.86,
            "hybrid_score": 0.85,
        },
        {
            "quota_id": "9-76",
            "name": "改性沥青防水涂料 厚度2.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.84,
            "hybrid_score": 0.80,
        },
        {
            "quota_id": "9-84",
            "name": "水泥基渗透结晶型防水涂料 厚度1.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.82,
            "hybrid_score": 0.70,
        },
        {
            "quota_id": "9-85",
            "name": "水泥基渗透结晶型防水涂料 厚度1.0mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.80,
            "hybrid_score": 0.68,
        },
        {
            "quota_id": "9-86",
            "name": "水泥基渗透结晶型防水涂料 厚度每增0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.78,
            "hybrid_score": 0.65,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面涂膜防水",
            "description": "桩头防水：1mm厚水泥基渗透结晶防水涂料",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-84"
    assert meta["raw_ltr_top1_id"] == "9-79"
    assert meta["post_ltr_top1_id"] == "9-84"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["action"] == "blocked"
    assert meta["ltr_guard"]["reason"] == "cementitious_crystalline_waterproof_rescued"
    assert meta["ltr_guard"]["cementitious_crystalline_waterproof_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_js_polymer_cement_to_crystalline(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "9-82",
            "name": "聚合物水泥防水涂料 厚度每增0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.88,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-84",
            "name": "水泥基渗透结晶型防水涂料 厚度1.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "rerank_score": 0.84,
            "hybrid_score": 0.70,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面涂膜防水",
            "description": "1.5mm厚环保型聚合物水泥防水涂料JS I型",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-82", "9-84"]
    assert meta["post_ltr_top1_id"] == "9-82"
    assert meta["ltr_guard"]["cementitious_crystalline_waterproof_rescue"]["blocked"] is False


def test_ltr_guard_rescues_polymer_cement_floor_coating_from_increment(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85, 0.80]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "9-82",
            "name": "聚合物水泥防水涂料 厚度每增0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.83,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-83",
            "name": "聚合物水泥防水涂料 厚度每增0.1mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-80",
            "name": "聚合物水泥防水涂料 厚度1.2mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.46,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-81",
            "name": "聚合物水泥防水涂料 厚度1.2mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.42,
            "rerank_score": 0.41,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面涂膜防水",
            "description": "楼（地）面涂膜防水：1.5mm厚环保型聚合物水泥防水涂料JS I型",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-80"
    assert meta["raw_ltr_top1_id"] == "9-82"
    assert meta["post_ltr_top1_id"] == "9-80"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "polymer_cement_waterproof_coating_rescued"
    assert meta["ltr_guard"]["polymer_cement_waterproof_coating_rescue"]["blocked"] is True


def test_ltr_guard_rescues_polymer_cement_floor_upturn_from_vertical(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90, 0.85]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}, {"f1": 0.7}],
    )

    candidates = [
        {
            "quota_id": "9-81",
            "name": "聚合物水泥防水涂料 厚度1.2mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.47,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-80",
            "name": "聚合物水泥防水涂料 厚度1.2mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.53,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-82",
            "name": "聚合物水泥防水涂料 厚度每增0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.79,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面涂膜防水",
            "description": "楼面-1：1.5厚聚合物水泥防水涂料(Ⅱ型)，遇侧墙板翻至立面，高出完成面250mm",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-80"
    assert meta["raw_ltr_top1_id"] == "9-81"
    assert meta["post_ltr_top1_id"] == "9-80"
    assert meta["ltr_guard"]["reason"] == "polymer_cement_waterproof_coating_rescued"
    assert meta["ltr_guard"]["polymer_cement_waterproof_coating_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_polymer_cement_increment_item_to_base(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.95, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "9-82",
            "name": "聚合物水泥防水涂料 厚度每增0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.83,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-80",
            "name": "聚合物水泥防水涂料 厚度1.2mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.46,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "楼（地）面涂膜防水",
            "description": "聚合物水泥防水涂料厚度每增0.1mm，平面",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-82"
    assert meta["ltr_guard"]["polymer_cement_waterproof_coating_rescue"]["blocked"] is False


def test_ltr_guard_does_not_rescue_polymer_cement_wall_item_with_ground_word_to_flat(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.90, 0.95]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.8}],
    )

    candidates = [
        {
            "quota_id": "9-80",
            "name": "聚合物水泥防水涂料 厚度1.2mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.53,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-81",
            "name": "聚合物水泥防水涂料 厚度1.2mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.47,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面涂膜防水",
            "description": "地面两道JS聚合物防水层",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-81"
    assert meta["ltr_guard"]["polymer_cement_waterproof_coating_rescue"]["blocked"] is False
