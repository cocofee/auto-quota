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


def test_ltr_guard_rescues_municipal_crushed_stone_base_from_building_bedding(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.92, 0.88]

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
            "quota_id": "4-87",
            "name": "碎石垫层 干铺",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.98,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-371",
            "name": "碎石垫层",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.84,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-105",
            "name": "碎石底层 人机配合厚20cm",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "碎石",
            "description": "碎石垫层 厚度:30cm",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-105"
    assert meta["raw_ltr_top1_id"] == "4-87"
    assert meta["post_ltr_top1_id"] == "2-105"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "crushed_stone_base_rescued"
    assert meta["ltr_guard"]["crushed_stone_base_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_multi_thickness_crushed_stone_base_to_machine(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.92]

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
            "quota_id": "4-87",
            "name": "碎石垫层 干铺",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.98,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-105",
            "name": "碎石底层 人机配合厚20cm",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "碎石",
            "description": "10cm厚碎石(粒径5-8mm),10cm厚碎石(粒径15-30mm),20cm厚碎石(粒径30-50mm)",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["4-87", "2-105"]
    assert meta["post_ltr_top1_id"] == "4-87"
    assert meta["ltr_guard"]["crushed_stone_base_rescue"]["blocked"] is False


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


def test_ltr_guard_rescues_explicit_rotary_bored_pile_method(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.94, 0.89, 0.84, 0.79, 0.74, 0.69]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "3-157",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u51b2\u5b54\u6210\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-154",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u4eba\u5de5\u6316\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-156",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u65cb\u6316\u94bb\u6210\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-115",
            "name": "\u5236\u4f5c\u5b89\u8bbe\u6df7\u51dd\u571f\u62a4\u58c1",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.52,
            "rerank_score": 0.68,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-116",
            "name": "\u704c\u6ce8\u6869\u82af\u6df7\u51dd\u571f",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.64,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-244",
            "name": "\u54ac\u5408\u704c\u6ce8\u6869 \u94bb\u5b54",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.7,
            "manual_structured_score": 0.42,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-155",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u56de\u65cb\u94bb\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6ce5\u6d46\u62a4\u58c1\u6210\u5b54\u704c\u6ce8\u6869",
            "description": "\u6210\u5b54\u65b9\u6cd5\uff1a\u56de\u65cb\u94bb\u5b54\u6210\u5b54\uff1b\u6df7\u51dd\u571f\u79cd\u7c7b\u3001\u5f3a\u5ea6\u7b49\u7ea7\uff1aC30\u975e\u6cf5\u9001\u6c34\u4e0b\u5546\u54c1\u6df7\u51dd\u571f",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "3-155"
    assert meta["raw_ltr_top1_id"] == "3-157"
    assert meta["post_ltr_top1_id"] == "3-155"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "bored_pile_drilling_method_rescued"
    assert meta["ltr_guard"]["bored_pile_drilling_method_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["bored_pile_drilling_method_rescue"]["details"]["rescued_rank"] == 7


def test_ltr_guard_does_not_rescue_generic_bored_pile_method(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "3-157",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u51b2\u5b54\u6210\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-155",
            "name": "\u704c\u6ce8\u6869\u6df7\u51dd\u571f \u56de\u65cb\u94bb\u5b54",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.44,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6ce5\u6d46\u62a4\u58c1\u6210\u5b54\u704c\u6ce8\u6869",
            "description": "\u6210\u5b54\u65b9\u6cd5\uff1a\u7efc\u5408\u8003\u8651\uff1b\u6df7\u51dd\u571f\u79cd\u7c7b\u3001\u5f3a\u5ea6\u7b49\u7ea7\uff1aC30\u975e\u6cf5\u9001\u6c34\u4e0b\u5546\u54c1\u6df7\u51dd\u571f",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "3-157"
    rescue = meta["ltr_guard"].get("bored_pile_drilling_method_rescue", {})
    assert rescue.get("blocked", False) is False
    assert rescue["details"]["intent"]["generic_method"] is True


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


def test_ltr_guard_rescues_triangle_traffic_sign_shape_and_size(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.94, 0.92, 0.90, 0.88]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.9}, {"f1": 0.8}, {"f1": 0.7}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "2-283",
            "name": "\u6807\u5fd7\u724c\u9762\u79ef12m2\u4ee5\u5185",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-274",
            "name": "\u6807\u5fd7\u724c \u957f\u65b9\u5f62(cm)60\u00d730",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.69,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-263",
            "name": "\u6807\u5fd7\u724c \u4e09\u89d2\u5f62(cm)\u25b390",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.59,
            "rerank_score": 0.68,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-265",
            "name": "\u6807\u5fd7\u724c \u4e09\u89d2\u5f62(cm)\u25b3130",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.67,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-286",
            "name": "\u6807\u5fd7\u6746 \u5355\u67f1\u5f0f(mm)\u03c660\u00d73000\u4ee5\u5185",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.57,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6807\u5fd7\u677f",
            "description": "\u7c7b\u578b:\u6807\u5fd7\u724c \u6750\u8d28\u3001\u89c4\u683c\u5c3a\u5bf8:\u25b390*0.2cm",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-263"
    assert meta["raw_ltr_top1_id"] == "2-283"
    assert meta["post_ltr_top1_id"] == "2-263"
    assert meta["ltr_guard"]["reason"] == "traffic_sign_shape_rescued"
    assert meta["ltr_guard"]["traffic_sign_shape_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_rectangle_traffic_sign_to_triangle(monkeypatch):
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
            "quota_id": "2-274",
            "name": "\u6807\u5fd7\u724c \u957f\u65b9\u5f62(cm)60\u00d730",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.69,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-263",
            "name": "\u6807\u5fd7\u724c \u4e09\u89d2\u5f62(cm)\u25b390",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.59,
            "rerank_score": 0.68,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6807\u5fd7\u677f",
            "description": "\u7c7b\u578b:\u6807\u5fd7\u724c \u6750\u8d28\u3001\u89c4\u683c\u5c3a\u5bf8:60\u00d730cm",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-274"
    assert meta["ltr_guard"].get("traffic_sign_shape_rescue", {}).get("blocked", False) is False


def test_ltr_guard_rescues_geotextile_stress_absorbing_tape_to_joint_paste(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.94, 0.92, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.9}, {"f1": 0.8}, {"f1": 0.7}],
    )

    candidates = [
        {
            "quota_id": "2-58",
            "name": "\u571f\u5de5\u5408\u6210\u6750\u6599 \u571f\u5de5\u5e03\u5e73\u94fa",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-59",
            "name": "\u571f\u5de5\u5408\u6210\u6750\u6599 \u571f\u5de5\u5e03\u659c\u94fa",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.69,
            "rerank_score": 0.97,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-229",
            "name": "\u571f\u5de5\u5e03\u8d34\u7f1d",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.42,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-19",
            "name": "\u571f\u5de5\u5e03 \u7f1d\u5408\u5e73\u94fa200g/m2",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.49,
            "rerank_score": 0.64,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u571f\u5de5\u5408\u6210\u6750\u6599",
            "description": "\u9ad8\u6027\u80fd\u5e94\u529b\u5438\u6536\u8d34\uff0c\u9632\u88c2\u6027\u80fd\u6ee1\u8db3\u89c4\u8303\u8981\u6c42",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-229"
    assert meta["raw_ltr_top1_id"] == "2-58"
    assert meta["post_ltr_top1_id"] == "2-229"
    assert meta["ltr_guard"]["reason"] == "geotextile_tape_rescued"
    assert meta["ltr_guard"]["geotextile_tape_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_plain_geotextile_laying_to_tape(monkeypatch):
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
            "quota_id": "2-58",
            "name": "\u571f\u5de5\u5408\u6210\u6750\u6599 \u571f\u5de5\u5e03\u5e73\u94fa",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-229",
            "name": "\u571f\u5de5\u5e03\u8d34\u7f1d",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.42,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u571f\u5de5\u5408\u6210\u6750\u6599",
            "description": "\u571f\u5de5\u5e03\u5e73\u94fa",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-58"
    assert meta["ltr_guard"].get("geotextile_tape_rescue", {}).get("blocked", False) is False


def test_ltr_guard_rescues_road_saw_cut_joint_from_geotextile_tape(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.96, 0.92, 0.88, 0.84, 0.60]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "2-229",
            "name": "\u571f\u5de5\u5e03\u8d34\u7f1d",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.67,
            "rerank_score": 0.92,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-217",
            "name": "\u4f38\u7f1d \u6ca5\u9752\u739b\u8e44\u8102",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.65,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-221",
            "name": "\u952f\u7f1d\u673a\u5207\u7f1d \u6bcf\u589e\u51cf1cm",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.84,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-215",
            "name": "\u8def\u9762\u9632\u6ed1\u6761",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "4-87",
            "name": "\u697c\u5730\u9762\u51ff\u6bdb",
            "param_match": True,
            "param_score": 0.88,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.7,
            "manual_structured_score": 0.58,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-219",
            "name": "\u952f\u7f1d\u673a\u5207\u7f1d \u7f1d\u6df1(cm)5",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u8def\u9762\u5272\u636e\u7f1d",
            "description": "\u8def\u9762\u5272\u636e\u7f1d\uff0c\u7f1d\u6df1\u6839\u636e\u73b0\u573a\u5b9e\u9645\u60c5\u51b5\u7efc\u5408\u8003\u8651",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-219"
    assert meta["raw_ltr_top1_id"] == "2-229"
    assert meta["post_ltr_top1_id"] == "2-219"
    assert meta["ltr_guard"]["reason"] == "road_saw_cut_joint_rescued"
    assert meta["ltr_guard"]["road_saw_cut_joint_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["road_saw_cut_joint_rescue"]["details"]["rescued_rank"] == 6


def test_ltr_guard_does_not_rescue_deformation_joint_to_road_saw_cut(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.90, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "3-482",
            "name": "\u53d8\u5f62\u7f1d \u4f38\u7f1d",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-217",
            "name": "\u4f38\u7f1d \u6ca5\u9752\u739b\u8e44\u8102",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.92,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-219",
            "name": "\u952f\u7f1d\u673a\u5207\u7f1d \u7f1d\u6df1(cm)5",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u8def\u9762\u53d8\u5f62\u7f1d",
            "description": "\u53d8\u5f62\u7f1d \u4f38\u7f1d\uff0c\u6da8\u7f1d\uff0c\u7f1d\u5185\u586b\u6ca5\u9752\u739b\u8e44\u8102\uff1b\u542b\u952f\u7f1d",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "3-482"
    saw_cut_rescue = meta["ltr_guard"].get("road_saw_cut_joint_rescue", {})
    assert saw_cut_rescue.get("blocked", False) is False
    assert saw_cut_rescue["details"]["intent"]["deformation_joint"] is True


def test_ltr_guard_rescues_shotcrete_slope_base_from_increment(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.95, 0.90, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "2-88",
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761 \u5761\u5ea6<60\u00b0\u539a\u5ea6\u6bcf\u589e\u51cf10mm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.52,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "4-221",
            "name": "\u6d1e\u5185 \u55b7\u5c04\u6df7\u51dd\u571f(\u62f1\u90e8)\u6df7\u51dd\u571f\u6bcf\u589e1cm",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.7,
            "manual_structured_score": 0.72,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-90",
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761 \u5761\u5ea6>60\u00b0\u539a\u5ea6\u6bcf\u589e\u51cf10mm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-87",
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761 \u5761\u5ea6<60\u00b0\u539a\u5ea650mm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f\u3001\u6c34\u6ce5\u7802\u6d46",
            "description": "80mm\u539aC20\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761\uff1b\u5761\u5ea660\u00b0\u4ee5\u5185",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-87"
    assert meta["raw_ltr_top1_id"] == "2-88"
    assert meta["post_ltr_top1_id"] == "2-87"
    assert meta["ltr_guard"]["reason"] == "shotcrete_slope_base_rescued"
    assert meta["ltr_guard"]["shotcrete_slope_base_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["shotcrete_slope_base_rescue"]["details"]["rescued_rank"] == 4


def test_ltr_guard_does_not_rescue_tunnel_shotcrete_increment_to_slope_base(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-221",
            "name": "\u6d1e\u5185 \u55b7\u5c04\u6df7\u51dd\u571f(\u62f1\u90e8)\u6df7\u51dd\u571f\u6bcf\u589e1cm",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.7,
            "manual_structured_score": 0.72,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-87",
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f\u62a4\u5761 \u5761\u5ea6<60\u00b0\u539a\u5ea650mm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u55b7\u5c04\u6df7\u51dd\u571f",
            "description": "\u6d1e\u5185\u55b7\u5c04\u6df7\u51dd\u571f\u62f1\u90e8\u6bcf\u589e1cm",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "4-221"
    rescue = meta["ltr_guard"].get("shotcrete_slope_base_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_road_milling_base_from_asphalt_demolition(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.95, 0.90, 0.86, 0.82, 0.60]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-341",
            "name": "\u62c6\u9664\u6ca5\u9752\u67cf\u6cb9\u7c7b\u8def\u9762\u5c42 \u98ce\u9550\u62c6\u9664\u539a10cm\u4ee5\u5185",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.72,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-342",
            "name": "\u62c6\u9664\u6ca5\u9752\u67cf\u6cb9\u7c7b\u8def\u9762\u5c42 \u98ce\u9550\u62c6\u9664\u6bcf\u589e1cm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-413",
            "name": "\u8def\u9762\u51ff\u6bdb \u6ca5\u9752\u6df7\u51dd\u571f\u4eba\u5de5",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-343",
            "name": "\u98ce\u9550\u62c6\u9664\u6df7\u51dd\u571f\u7c7b\u8def\u9762\u5c42 \u65e0\u7b4b\u539a15cm\u4ee5\u5185",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-352",
            "name": "\u94e3\u5228\u673a\u94e3\u5228\u8def\u9762\u6bcf\u589e\u51cf1cm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-351",
            "name": "\u94e3\u5228\u673a\u94e3\u5228\u8def\u9762\u539a\u5ea63cm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.54,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u94e3\u5228\u8def\u9762",
            "description": "\u6750\u8d28:\u62c6\u9664\u73b0\u72b6\u6ca5\u9752\u9762\u5c42 \u539a\u5ea6:20cm",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-351"
    assert meta["raw_ltr_top1_id"] == "1-341"
    assert meta["post_ltr_top1_id"] == "1-351"
    assert meta["ltr_guard"]["reason"] == "road_milling_base_rescued"
    assert meta["ltr_guard"]["road_milling_base_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["road_milling_base_rescue"]["details"]["rescued_rank"] == 6


def test_ltr_guard_does_not_rescue_plain_asphalt_demolition_to_milling(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-341",
            "name": "\u62c6\u9664\u6ca5\u9752\u67cf\u6cb9\u7c7b\u8def\u9762\u5c42 \u98ce\u9550\u62c6\u9664\u539a10cm\u4ee5\u5185",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.72,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-351",
            "name": "\u94e3\u5228\u673a\u94e3\u5228\u8def\u9762\u539a\u5ea63cm",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.54,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u62c6\u9664\u6ca5\u9752\u8def\u9762",
            "description": "\u62c6\u9664\u73b0\u72b6\u6ca5\u9752\u9762\u5c42",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-341"
    rescue = meta["ltr_guard"].get("road_milling_base_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_blind_plate_install_by_dn_from_anchor_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.95, 0.90, 0.70, 0.60]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-257",
            "name": "\u951a\u6746\u5236\u4f5c\u3001\u5b89\u88c5\u94a2\u7b4b",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-81",
            "name": "\u951a\u6746\u5236\u4f5c\u3001\u5b89\u88c5\u94a2\u7b4b",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.55,
            "rerank_score": 0.76,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "12-450",
            "name": "\u94fa\u6728\u5de5\u677f\u5236\u4f5c\u3001\u5b89\u88c5",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.54,
            "rerank_score": 0.72,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "7-492",
            "name": "\u76f2(\u5835)\u677f\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84(mm\u4ee5\u5185)1000",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.65,
            "rerank_score": 0.68,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "7-491",
            "name": "\u76f2(\u5835)\u677f\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84(mm\u4ee5\u5185)800",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u76f2\u5835\u677f\u5236\u4f5c\u3001\u5b89\u88c5",
            "description": "\u76f2\u5835\u677f\uff1aDN800",
            "specialty": "C7",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C7"},
    )

    assert ranked[0]["quota_id"] == "7-491"
    assert meta["raw_ltr_top1_id"] == "1-257"
    assert meta["post_ltr_top1_id"] == "7-491"
    assert meta["ltr_guard"]["reason"] == "blind_plate_install_rescued"
    assert meta["ltr_guard"]["blind_plate_install_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["blind_plate_install_rescue"]["details"]["rescued_rank"] == 5


def test_ltr_guard_does_not_rescue_blind_plate_demolition_to_install(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.90]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "7-490",
            "name": "盲(堵)板拆除 公称直径(mm以内)100",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.68,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "7-491",
            "name": "盲(堵)板安装 公称直径(mm以内)100",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.65,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "盲板拆除",
            "description": "拆卸盲堵板 DN100",
            "specialty": "C7",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C7"},
    )

    assert ranked[0]["quota_id"] == "7-490"
    rescue = meta["ltr_guard"].get("blind_plate_install_rescue", {})
    assert rescue.get("blocked", False) is False
    assert rescue["details"]["intent"]["removal_task"] is True


def test_ltr_guard_does_not_rescue_anchor_item_to_blind_plate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-257",
            "name": "\u951a\u6746\u5236\u4f5c\u3001\u5b89\u88c5\u94a2\u7b4b",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.56,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "7-491",
            "name": "\u76f2(\u5835)\u677f\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84(mm\u4ee5\u5185)800",
            "param_match": True,
            "param_score": 0.66,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.60,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u951a\u6746\u5236\u4f5c\u3001\u5b89\u88c5",
            "description": "\u94a2\u7b4b",
            "specialty": "C7",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C7"},
    )

    assert ranked[0]["quota_id"] == "1-257"
    rescue = meta["ltr_guard"].get("blind_plate_install_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_sidewalk_mortar_bedding_from_pc_pile(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.92, 0.84, 0.76, 0.68]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "3-30",
            "name": "\u6253\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u6869(PC\u6869)\u03c6400,L\u226424m\u9646\u4e0a",
            "param_match": True,
            "param_score": 0.81,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "13-1-15",
            "name": "\u9884\u5236\u5757\u4eba\u884c\u9053\u4fee\u590d\u539a\u5ea6(mm)\u226460",
            "param_match": True,
            "param_score": 0.44,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.42,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-231",
            "name": "\u4eba\u884c\u9053\u57fa\u7840 \u6df7\u51dd\u571f\u6bcf\u589e\u51cf1cm",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.39,
            "rerank_score": 0.66,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-233",
            "name": "\u4eba\u884c\u9053\u677f\u7802\u57ab\u5c42\u539a\u5ea65cm",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.35,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-232",
            "name": "\u4eba\u884c\u9053\u677f\u7802\u6d46\u57ab\u5c42\u539a\u5ea62cm",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.34,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u4eba\u884c\u9053\u5757\u6599\u94fa\u8bbe",
            "description": "\u5757\u6599\u54c1\u79cd\u3001\u89c4\u683c:400*200*60mm\u539a\u4eff\u767d\u9ebbPC\u900f\u6c34\u7816 \u57fa\u7840\u3001\u57ab\u5c42\uff1a30mm\u539a1:2\u5e72\u786c\u6027\u6c34\u6ce5\u7802\u6d46",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-232"
    assert meta["raw_ltr_top1_id"] == "3-30"
    assert meta["post_ltr_top1_id"] == "2-232"
    assert meta["ltr_guard"]["reason"] == "sidewalk_mortar_bedding_rescued"
    assert meta["ltr_guard"]["sidewalk_mortar_bedding_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["sidewalk_mortar_bedding_rescue"]["details"]["rescued_rank"] == 5


def test_ltr_guard_does_not_rescue_pc_pile_item_to_sidewalk_bedding(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "3-30",
            "name": "\u6253\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u6869(PC\u6869)\u03c6400,L\u226424m\u9646\u4e0a",
            "param_match": True,
            "param_score": 0.81,
            "logic_score": 0.5,
            "feature_alignment_score": 1.0,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-232",
            "name": "\u4eba\u884c\u9053\u677f\u7802\u6d46\u57ab\u5c42\u539a\u5ea62cm",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.34,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6253\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u6869",
            "description": "PC\u6869\u03c6400,L\u226424m\u9646\u4e0a",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "3-30"
    rescue = meta["ltr_guard"].get("sidewalk_mortar_bedding_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_does_not_rescue_granite_sidewalk_surface_to_bedding(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "2-236",
            "name": "\u4eba\u884c\u9053\u3001\u5e7f\u573a",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.52,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-232",
            "name": "\u4eba\u884c\u9053\u677f\u7802\u6d46\u57ab\u5c42\u539a\u5ea62cm",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.34,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u4eba\u884c\u9053\u5757\u6599\u94fa\u8bbe",
            "description": "\u5757\u6599\u54c1\u79cd\u3001\u89c4\u683c:50mm\u539a\u829d\u9ebb\u767d\u82b1\u5c97\u5ca9 \u57fa\u7840\u3001\u57ab\u5c42\uff1a3cm\u539aM10\u6c34\u6ce5\u7802\u6d46\u57ab\u5c42",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-236"
    rescue = meta["ltr_guard"].get("sidewalk_mortar_bedding_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_hrb400_rebar_from_segment_candidate(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.92, 0.86, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-316",
            "name": "\u9884\u5236\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u7247\u94a2\u7b4b\u5236\u4f5c",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.84,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "10-149",
            "name": "\u94a2\u7b4b\u690d\u7b4b",
            "param_match": True,
            "param_score": 0.64,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "10-148",
            "name": "\u9884\u5236\u6784\u4ef6\u666e\u901a\u94a2\u7b4b\u5236\u4f5c\u3001\u5b89\u88c5 \u5e26\u808b\u94a2\u7b4bHRB400\u4ee5\u5185",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-269",
            "name": "\u666e\u901a\u94a2\u7b4b\u5236\u4f5c\u3001\u5b89\u88c5 \u5e26\u808b\u94a2\u7b4b",
            "param_match": True,
            "param_score": 0.62,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u9884\u5236\u6784\u4ef6\u94a2\u7b4b",
            "description": "HRB400\u87ba\u7eb9\u94a2\uff0c\u573a\u5185\u8fd0\u8f93\u7efc\u5408\u8003\u8651",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-269"
    assert meta["raw_ltr_top1_id"] == "4-316"
    assert meta["post_ltr_top1_id"] == "1-269"
    assert meta["ltr_guard"]["reason"] == "hrb400_rebar_install_rescued"
    assert meta["ltr_guard"]["hrb400_rebar_install_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["hrb400_rebar_install_rescue"]["details"]["rescued_rank"] == 4


def test_ltr_guard_does_not_rescue_segment_rebar_item_to_road_rebar(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-316",
            "name": "\u9884\u5236\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u7247\u94a2\u7b4b\u5236\u4f5c",
            "param_match": True,
            "param_score": 0.51,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.84,
            "rerank_score": 0.80,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-269",
            "name": "\u666e\u901a\u94a2\u7b4b\u5236\u4f5c\u3001\u5b89\u88c5 \u5e26\u808b\u94a2\u7b4b",
            "param_match": True,
            "param_score": 0.62,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.40,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u9884\u5236\u94a2\u7b4b\u6df7\u51dd\u571f\u7ba1\u7247\u94a2\u7b4b",
            "description": "HRB400\u87ba\u7eb9\u94a2",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "4-316"
    rescue = meta["ltr_guard"].get("hrb400_rebar_install_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_brick_manhole_shaft_plaster_from_chimney(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.92, 0.84, 0.76]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "17-2",
            "name": "\u7816\u70df\u56f1\u7b52\u8eab\u5168\u9ad8(m\u4ee5\u5185)20\u70e7\u7ed3\u666e\u901a\u7816",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.35,
            "manual_structured_score": 0.58,
            "rerank_score": 0.86,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "17-142",
            "name": "\u7816\u780c\u7a96\u4e95(\u5185\u5f84\u5468\u957f:m\u4ee5\u5185)1.5\u6bcf\u589e\u51cf20cm\u6df1",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.35,
            "manual_structured_score": 0.56,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-261",
            "name": "\u7816\u5899 \u62b9\u7070\u4e95\u5e95",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.53,
            "rerank_score": 0.55,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-260",
            "name": "\u7816\u5899 \u62b9\u7070\u4e95\u58c1",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.48,
            "rerank_score": 0.44,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u7816\u780c\u4e95\u7b52",
            "description": "\u780c\u7b51\u6750\u6599:M10\u6c34\u6ce5\u7802\u6d46\u780c\u7b51MU20\u6df7\u51dd\u571f\u5b9e\u5fc3\u7816. \u5176\u5b83:\u5185\u591620mm\u539a1:2\u6c34\u6ce5\u7802\u6d46\u62b9\u7070.",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-260"
    assert meta["raw_ltr_top1_id"] == "17-2"
    assert meta["post_ltr_top1_id"] == "6-260"
    assert meta["ltr_guard"]["reason"] == "brick_manhole_shaft_plaster_rescued"
    assert meta["ltr_guard"]["brick_manhole_shaft_plaster_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["brick_manhole_shaft_plaster_rescue"]["details"]["rescued_rank"] == 4


def test_ltr_guard_does_not_rescue_electrical_handhole_to_drainage_manhole(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "11-1-42",
            "name": "\u7816\u780c\u914d\u7ebf\u624b\u5b54\u4e00\u53f7\u624b\u5b54(SK1)",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.35,
            "manual_structured_score": 0.56,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-260",
            "name": "\u7816\u5899 \u62b9\u7070\u4e95\u58c1",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.48,
            "rerank_score": 0.44,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u7816\u780c\u914d\u7ebf\u624b\u5b54",
            "description": "\u4e95\u58c1\u5185\u591620mm\u539a1:2\u6c34\u6ce5\u7802\u6d46\u62b9\u7070",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "11-1-42"
    rescue = meta["ltr_guard"].get("brick_manhole_shaft_plaster_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_collision_barrel_from_stone_drum(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.92, 0.84, 0.76]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "8-122",
            "name": "\u6bdb\u6599\u77f3\u5706\u5f62\u9f13\u78f4\u5236\u4f5c(\u4e8c\u904d\u5241\u65a7)\u3001\u5b89\u88c5\u89c4\u683c(cm)\u03c620\u539a13\u4ee5\u5185",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.55,
            "rerank_score": 0.25,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "12-81",
            "name": "\u5706\u6728\u6401\u6805\u76f4\u5f84(cm)\u03c620\u4ee5\u4e0a",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.53,
            "rerank_score": 0.22,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-364",
            "name": "\u9632\u649e\u9694\u79bb\u8bbe\u65bd \u9632\u649e\u7b52\u5851\u6599\u9632\u649e\u7b52",
            "param_match": True,
            "param_score": 0.70,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.47,
            "rerank_score": 0.60,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-365",
            "name": "\u9632\u649e\u9694\u79bb\u8bbe\u65bd \u9632\u649e\u7b52\u6a61\u80f6\u9632\u649e\u7b52",
            "param_match": True,
            "param_score": 0.70,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u9632\u649e\u7b52\uff08\u58a9\uff09",
            "description": "\u6750\u6599\u54c1\u79cd:\u82b1\u5c97\u5ca9\u5706\u67f1\u8def\u969c\u77f3,H=60cm\uff0c\u03c620cm\uff0cC25\u975e\u6cf5\u9001\u6df7\u51dd\u571f\u57fa\u7840",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-365"
    assert meta["raw_ltr_top1_id"] == "8-122"
    assert meta["post_ltr_top1_id"] == "2-365"
    assert meta["ltr_guard"]["reason"] == "collision_barrel_rescued"
    assert meta["ltr_guard"]["collision_barrel_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["collision_barrel_rescue"]["details"]["rescued_rank"] == 4


def test_ltr_guard_does_not_rescue_water_horse_to_collision_barrel(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "2-366",
            "name": "\u9632\u649e\u9694\u79bb\u8bbe\u65bd \u6c34\u9a6c\u5851\u6599\u6c34\u9a6c",
            "param_match": True,
            "param_score": 0.70,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-365",
            "name": "\u9632\u649e\u9694\u79bb\u8bbe\u65bd \u9632\u649e\u7b52\u6a61\u80f6\u9632\u649e\u7b52",
            "param_match": True,
            "param_score": 0.70,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.62,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6c34\u9a6c",
            "description": "\u5851\u6599\u6c34\u9a6c\u5b89\u88c5",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-366"
    rescue = meta["ltr_guard"].get("collision_barrel_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_c6_yellow_sand_backfill_from_generic_soil(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.92, 0.84]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-65",
            "name": "\u56de\u586b\u571f\u673a\u68b0\u592f\u5b9e",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.99,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-311",
            "name": "\u6c9f\u69fd\u56de\u586b \u5858\u78b4",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.55,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-306",
            "name": "\u6c9f\u69fd\u56de\u586b \u9ec4\u7802",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.56,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "100\u539a\u7c97\u7802\u56de\u586b",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-306"
    assert meta["raw_ltr_top1_id"] == "4-65"
    assert meta["post_ltr_top1_id"] == "6-306"
    assert meta["ltr_guard"]["reason"] == "drainage_backfill_material_rescued"
    assert meta["ltr_guard"]["drainage_backfill_material_rescue"]["blocked"] is True
    assert meta["ltr_guard"]["drainage_backfill_material_rescue"]["details"]["rescued_rank"] == 3


def test_ltr_guard_does_not_rescue_plain_soil_backfill_to_c6_material(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-53",
            "name": "\u586b\u571f\u592f\u5b9e\u69fd\u3001\u5751",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.58,
            "rerank_score": 0.64,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-306",
            "name": "\u6c9f\u69fd\u56de\u586b \u9ec4\u7802",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.56,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "\u6c9f\u69fd\u571f\u65b9\u56de\u586b\uff0c\u5229\u7528\u5f00\u6316\u65b9",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "1-53"
    rescue = meta["ltr_guard"].get("drainage_backfill_material_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_c6_standalone_concrete_bedding(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.82, 0.76, 0.70]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-435",
            "name": "\u57fa\u5751\u57ab\u5c42 \u6df7\u51dd\u571f\u57ab\u5c42",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.98,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-187",
            "name": "\u57ab\u5c42\u6df7\u51dd\u571f",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.74,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-292",
            "name": "\u6e20(\u7ba1)\u9053\u57ab\u5c42 \u6df7\u51dd\u571f",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.62,
            "rerank_score": 0.55,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-287",
            "name": "\u6e20(\u7ba1)\u9053\u57ab\u5c42 \u788e\u77f3\u5e72\u94fa",
            "param_match": True,
            "param_score": 0.9,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.60,
            "rerank_score": 0.50,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u57ab\u5c42",
            "description": "10cm\u539aC20\u975e\u6cf5\u9001\u5546\u54c1\u6df7\u51dd\u571f\u57ab\u5c42",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-292"
    assert meta["raw_ltr_top1_id"] == "4-435"
    assert meta["post_ltr_top1_id"] == "6-292"
    assert meta["ltr_guard"]["reason"] == "drainage_channel_concrete_bedding_rescued"
    assert meta["ltr_guard"]["drainage_channel_concrete_bedding_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_manhole_bedding_to_channel_bedding(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "6-249",
            "name": "\u4e95 \u57ab\u5c42\u6df7\u51dd\u571f",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.64,
            "rerank_score": 0.68,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-292",
            "name": "\u6e20(\u7ba1)\u9053\u57ab\u5c42 \u6df7\u51dd\u571f",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.58,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u6df7\u51dd\u571f\u4e95",
            "description": "\u96e8\u6c34\u68c0\u67e5\u4e95 100mm\u539aC20\u6df7\u51dd\u571f\u57ab\u5c42",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-249"
    rescue = meta["ltr_guard"].get("drainage_channel_concrete_bedding_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_c2_tangkeng_backfill_to_roadbed_fill(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.86, 0.75]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-65",
            "name": "\u56de\u586b\u571f\u673a\u68b0\u592f\u5b9e",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.99,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "1-84",
            "name": "\u77f3\u78b4\u56de\u586b",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.62,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-68",
            "name": "\u8def\u57fa\u586b\u7b51 \u5858\u6e23",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.60,
            "rerank_score": 0.55,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "\u516c\u4ea4\u7ad9\u53f0\u53ca\u8fc7\u8857\u94fa\u88c5\u7ed3\u6784\u5e95\u56de\u586b\u5858\u6e23\uff0c\u539a\u5ea640cm\u3002",
            "specialty": "C2",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C2"},
    )

    assert ranked[0]["quota_id"] == "2-68"
    assert meta["raw_ltr_top1_id"] == "4-65"
    assert meta["post_ltr_top1_id"] == "2-68"
    assert meta["ltr_guard"]["reason"] == "road_tangkeng_backfill_rescued"
    assert meta["ltr_guard"]["road_tangkeng_backfill_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_c1_excavated_backfill_to_road_tangkeng(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "1-53",
            "name": "\u586b\u571f\u592f\u5b9e\u69fd\u3001\u5751",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.70,
            "rerank_score": 0.70,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2-68",
            "name": "\u8def\u57fa\u586b\u7b51 \u5858\u6e23",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.58,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "\u6c9f\u69fd\u571f\u65b9\u56de\u586b\uff0c\u5bc6\u5b9e\u5ea6\u7b26\u5408\u8bbe\u8ba1\u8981\u6c42\uff0c\u5229\u7528\u5f00\u6316\u65b9",
            "specialty": "C1",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C1"},
    )

    assert ranked[0]["quota_id"] == "1-53"
    rescue = meta["ltr_guard"].get("road_tangkeng_backfill_rescue", {})
    assert rescue.get("blocked", False) is False


def test_ltr_guard_rescues_c6_tangkeng_backfill_with_utilized_material(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.99, 0.72]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "4-65",
            "name": "\u56de\u586b\u571f\u673a\u68b0\u592f\u5b9e",
            "param_match": True,
            "param_score": 0.90,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.99,
            "rerank_score": 0.98,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-311",
            "name": "\u6c9f\u69fd\u56de\u586b \u5858\u78b4",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.55,
            "rerank_score": 0.56,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "\u8def\u57fa\u77f3\u78b4\u56de\u586b\uff0c\u5229\u7528\u65b9\u538b\u5b9e\u7cfb\u6570\u8be6\u89c1\u65bd\u5de5\u56fe\u7eb8",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-311"
    assert meta["raw_ltr_top1_id"] == "4-65"
    assert meta["post_ltr_top1_id"] == "6-311"
    assert meta["ltr_guard"]["reason"] == "drainage_backfill_material_rescued"
    assert meta["ltr_guard"]["drainage_backfill_material_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_c6_bedding_as_backfill(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

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
        lambda item, candidates, context: [{"f1": 1.0} for _ in candidates],
    )

    candidates = [
        {
            "quota_id": "6-290",
            "name": "\u6e20(\u7ba1)\u9053\u57ab\u5c42 \u7802",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.58,
            "rerank_score": 0.64,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "6-306",
            "name": "\u6c9f\u69fd\u56de\u586b \u9ec4\u7802",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 1.0,
            "manual_structured_score": 0.56,
            "rerank_score": 0.58,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u56de\u586b\u65b9",
            "description": "\u7ba1\u9053\u6c9f\u69fd\u4e2d\u7c97\u7802\u57ab\u5c42",
            "specialty": "C6",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C6"},
    )

    assert ranked[0]["quota_id"] == "6-290"
    rescue = meta["ltr_guard"].get("drainage_backfill_material_rescue", {})
    assert rescue.get("blocked", False) is False


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


def test_ltr_guard_rescues_roof_polyurethane_coating_from_bitumen(monkeypatch):
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
            "quota_id": "9-78",
            "name": "改性沥青防水涂料 厚度每增减0.1mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.86,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-88",
            "name": "聚氨酯防水涂料 厚度1.5mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.82,
            "hybrid_score": 0.70,
        },
        {
            "quota_id": "9-89",
            "name": "聚氨酯防水涂料 厚度1.5mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.80,
            "hybrid_score": 0.68,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面涂膜防水",
            "description": "屋面涂膜防水 1.5mm厚聚氨酯防水涂料(I型)",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-88"
    assert meta["raw_ltr_top1_id"] == "9-78"
    assert meta["post_ltr_top1_id"] == "9-88"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "polyurethane_waterproof_coating_rescued"
    assert meta["ltr_guard"]["polyurethane_waterproof_coating_rescue"]["blocked"] is True


def test_ltr_guard_rescues_wall_polyurethane_typo_coating_from_polymer(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.91, 0.86]

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
            "rerank_score": 0.84,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-88",
            "name": "聚氨酯防水涂料 厚度1.5mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.48,
            "rerank_score": 0.82,
            "hybrid_score": 0.70,
        },
        {
            "quota_id": "9-89",
            "name": "聚氨酯防水涂料 厚度1.5mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.80,
            "hybrid_score": 0.68,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面涂膜防水",
            "description": "墙面涂膜防水 1.5厚聚胺脂涂膜防水涂料",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-89"
    assert meta["raw_ltr_top1_id"] == "9-81"
    assert meta["post_ltr_top1_id"] == "9-89"
    assert meta["ltr_guard"]["reason"] == "polyurethane_waterproof_coating_rescued"
    assert meta["ltr_guard"]["polyurethane_waterproof_coating_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_js_polymer_cement_to_polyurethane(monkeypatch):
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
            "quota_id": "9-81",
            "name": "聚合物水泥防水涂料 厚度1.2mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.84,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-89",
            "name": "聚氨酯防水涂料 厚度1.5mm 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.80,
            "hybrid_score": 0.68,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "墙面涂膜防水",
            "description": "1.5厚JS聚合物水泥防水涂料II型",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-81", "9-89"]
    assert meta["post_ltr_top1_id"] == "9-81"
    assert meta["ltr_guard"]["polyurethane_waterproof_coating_rescue"]["blocked"] is False


def test_ltr_guard_does_not_rescue_cementitious_crystalline_to_polyurethane(monkeypatch):
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
            "quota_id": "9-84",
            "name": "水泥基渗透结晶型防水涂料 厚度1.0mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.84,
            "hybrid_score": 0.90,
        },
        {
            "quota_id": "9-88",
            "name": "聚氨酯防水涂料 厚度1.5mm 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.46,
            "rerank_score": 0.80,
            "hybrid_score": 0.68,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "桩头防水",
            "description": "桩头防水 1mm厚水泥基渗透结晶型防水涂料",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert [candidate["quota_id"] for candidate in ranked] == ["9-84", "9-88"]
    assert meta["post_ltr_top1_id"] == "9-84"
    assert meta["ltr_guard"]["polyurethane_waterproof_coating_rescue"]["blocked"] is False


def test_ltr_guard_rescues_large_equipment_demob_from_site_grading(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.92, 0.88, 0.84]

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
            "quota_id": "4-66",
            "name": "场地机械平整",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.86,
            "rerank_score": 0.67,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2019",
            "name": "安拆费用 TRD搅拌桩机III型",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.61,
            "rerank_score": 0.87,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3027",
            "name": "场外运输费用 三轴搅拌机",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.63,
            "rerank_score": 0.90,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2002",
            "name": "安拆费用 柴油打桩机",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.79,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "大型机械设备进出场及安拆",
            "description": "机械设备名称：双头搅拌桩机；机械设备规格型号：投标人自行考虑",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}},
    )

    assert ranked[0]["quota_id"] == "2002"
    assert meta["raw_ltr_top1_id"] == "4-66"
    assert meta["post_ltr_top1_id"] == "2002"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "large_equipment_demob_rescued"
    assert meta["ltr_guard"]["large_equipment_demob_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_large_equipment_transport_only_to_demob(monkeypatch):
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
            "quota_id": "3027",
            "name": "场外运输费用 三轴搅拌机",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.63,
            "rerank_score": 0.90,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "2002",
            "name": "安拆费用 柴油打桩机",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.58,
            "rerank_score": 0.79,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "大型机械设备进出场",
            "description": "机械设备名称：三轴搅拌机，场外运输费用",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}},
    )

    assert ranked[0]["quota_id"] == "3027"
    assert meta["ltr_guard"]["large_equipment_demob_rescue"]["blocked"] is False


def test_ltr_guard_rescues_bridge_expansion_joint_fiber_concrete_from_steel_shape(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.90, 0.86]

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
            "quota_id": "3-477",
            "name": "安装伸缩缝 型钢伸缩缝",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.59,
            "rerank_score": 0.79,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-274",
            "name": "伸缩缝钢纤维混凝土",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.53,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-478",
            "name": "安装伸缩缝 橡胶板",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.43,
            "rerank_score": 0.37,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "桥梁伸缩装置",
            "description": "材料品种:PUTF聚氨酯填充式伸缩缝 混凝土强度等级:100mm钢纤维混凝土铺装",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "3-274"
    assert meta["raw_ltr_top1_id"] == "3-477"
    assert meta["post_ltr_top1_id"] == "3-274"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "bridge_expansion_joint_rescued"
    assert meta["ltr_guard"]["bridge_expansion_joint_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_explicit_steel_shape_expansion_joint(monkeypatch):
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
            "quota_id": "3-477",
            "name": "安装伸缩缝 型钢伸缩缝",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.59,
            "rerank_score": 0.79,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "3-274",
            "name": "伸缩缝钢纤维混凝土",
            "param_match": True,
            "param_score": 0.95,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.50,
            "rerank_score": 0.53,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "桥梁伸缩装置",
            "description": "材料品种:型钢伸缩缝",
            "specialty": "C3",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C3"},
    )

    assert ranked[0]["quota_id"] == "3-477"
    assert meta["ltr_guard"]["bridge_expansion_joint_rescue"]["blocked"] is False


def test_ltr_guard_rescues_modified_bitumen_self_adhesive_roof_membrane_from_polymer(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.91, 0.86, 0.82]

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
            "quota_id": "9-70",
            "name": "高分子卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.64,
            "rerank_score": 0.83,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-51",
            "name": "改性沥青自粘卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.69,
            "rerank_score": 0.96,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-52",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.68,
            "rerank_score": 0.95,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-53",
            "name": "改性沥青自粘卷材自粘法 每增一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.66,
            "rerank_score": 0.88,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "顶板1、2：3.0厚SBS弹性体改性沥青防水卷材（PY类），采用湿铺法施工，上翻高度详见图纸",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-51"
    assert meta["raw_ltr_top1_id"] == "9-70"
    assert meta["post_ltr_top1_id"] == "9-51"
    assert meta["primary_stage"] == "ltr_guard"
    assert meta["ltr_guard"]["reason"] == "modified_bitumen_membrane_rescued"
    assert meta["ltr_guard"]["modified_bitumen_membrane_rescue"]["blocked"] is True


def test_ltr_guard_rescues_modified_bitumen_self_adhesive_vertical_membrane(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.91, 0.86]

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
            "quota_id": "9-71",
            "name": "高分子卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.64,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-51",
            "name": "改性沥青自粘卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.69,
            "rerank_score": 0.96,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-52",
            "name": "改性沥青自粘卷材自粘法 一层 立面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.95,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "3厚自粘聚合物改性沥青防水卷材(聚酯胎)立面",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-52"
    assert meta["ltr_guard"]["reason"] == "modified_bitumen_membrane_rescued"
    assert meta["ltr_guard"]["modified_bitumen_membrane_rescue"]["blocked"] is True


def test_ltr_guard_rescues_wall_modified_bitumen_self_adhesive_membrane(monkeypatch):
    monkeypatch.setattr("config.LTR_V2_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_ENABLED", True)
    monkeypatch.setattr("config.LTR_GUARD_THRESHOLD", 6.0)
    monkeypatch.setattr("config.CONSTRAINED_GATED_RANKER_ENABLED", False)

    class _FakeModel:
        def predict(self, matrix):
            return [0.96, 0.95, 0.82]

    monkeypatch.setattr(
        LTRRanker,
        "_load",
        classmethod(lambda cls: (_FakeModel(), ["f1"])),
    )
    monkeypatch.setattr(
        "src.ltr_ranker.extract_group_features",
        lambda item, candidates, context: [{"f1": 1.0}, {"f1": 0.9}, {"f1": 0.6}],
    )

    candidates = [
        {
            "quota_id": "9-51",
            "name": "\u6539\u6027\u6ca5\u9752\u81ea\u7c98\u5377\u6750\u81ea\u7c98\u6cd5 \u4e00\u5c42 \u5e73\u9762",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.69,
            "rerank_score": 0.96,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-52",
            "name": "\u6539\u6027\u6ca5\u9752\u81ea\u7c98\u5377\u6750\u81ea\u7c98\u6cd5 \u4e00\u5c42 \u7acb\u9762",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.70,
            "rerank_score": 0.95,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-70",
            "name": "\u9ad8\u5206\u5b50\u5377\u6750\u81ea\u7c98\u6cd5 \u4e00\u5c42 \u5e73\u9762",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.64,
            "rerank_score": 0.82,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "\u5899\u9762\u5377\u6750\u9632\u6c34",
            "description": "\u4fa7\u677f1\uff08\u6c34\u6c60\uff09\uff1a3\u539a\u81ea\u7c98SBS\u6539\u6027\u6ca5\u9752\u9632\u6c34\u5377\u6750",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-52"
    assert meta["raw_ltr_top1_id"] == "9-51"
    assert meta["ltr_guard"]["reason"] == "modified_bitumen_membrane_rescued"
    assert meta["ltr_guard"]["modified_bitumen_membrane_rescue"]["blocked"] is True


def test_ltr_guard_does_not_rescue_true_polymer_membrane_to_modified_bitumen(monkeypatch):
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
            "quota_id": "9-70",
            "name": "高分子卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.64,
            "rerank_score": 0.83,
            "hybrid_score": 0.03,
        },
        {
            "quota_id": "9-51",
            "name": "改性沥青自粘卷材自粘法 一层 平面",
            "param_match": True,
            "param_score": 1.0,
            "logic_score": 0.5,
            "feature_alignment_score": 0.5,
            "context_alignment_score": 0.8,
            "manual_structured_score": 0.69,
            "rerank_score": 0.96,
            "hybrid_score": 0.03,
        },
    ]

    ranked, meta = LTRRanker.rerank_candidates_with_ltr(
        {
            "name": "屋面卷材防水",
            "description": "1.5厚高分子自粘防水卷材 平面",
            "specialty": "C9",
        },
        candidates,
        {"query_route": {"route": "semantic_description"}, "specialty": "C9"},
    )

    assert ranked[0]["quota_id"] == "9-70"
    assert meta["ltr_guard"].get("modified_bitumen_membrane_rescue", {}).get("blocked", False) is False
