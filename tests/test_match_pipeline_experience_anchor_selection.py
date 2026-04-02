from types import SimpleNamespace

import config
import src.match_pipeline as match_pipeline


def _candidate(
    quota_id: str,
    *,
    name: str | None = None,
    param_match: bool,
    param_score: float,
    rerank_score: float = 0.72,
    knowledge_prior_sources: list[str] | None = None,
    knowledge_prior_score: float | None = None,
    match_source: str = "search",
) -> dict:
    return {
        "quota_id": quota_id,
        "name": name or f"Candidate {quota_id}",
        "unit": "m",
        "param_match": param_match,
        "param_tier": 1,
        "param_score": param_score,
        "logic_score": 0.82,
        "feature_alignment_score": 0.84,
        "context_alignment_score": 0.80,
        "rerank_score": rerank_score,
        "hybrid_score": rerank_score,
        "name_bonus": 0.0,
        "match_source": match_source,
        "knowledge_prior_sources": knowledge_prior_sources or [],
        "knowledge_prior_score": knowledge_prior_score,
        "candidate_canonical_features": {"entity": "阀门"},
    }


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr(
        match_pipeline,
        "_apply_plugin_route_gate",
        lambda item, candidates: (list(candidates), {"applied": False}),
    )
    monkeypatch.setattr(
        match_pipeline,
        "_apply_plugin_candidate_biases",
        lambda item, candidates: list(candidates),
    )
    monkeypatch.setattr(
        match_pipeline,
        "rerank_candidates_with_ltr",
        lambda item, candidates, ctx: (list(candidates), {}),
    )
    monkeypatch.setattr(
        match_pipeline,
        "arbitrate_candidates",
        lambda item, candidates, route_profile=None: (
            list(candidates),
            {"applied": False, "advisory_applied": False},
        ),
    )
    monkeypatch.setattr(
        match_pipeline,
        "_promote_explicit_distribution_box_candidate",
        lambda item, candidates: (list(candidates), {}),
    )
    monkeypatch.setattr(
        match_pipeline,
        "analyze_ambiguity",
        lambda *args, **kwargs: SimpleNamespace(as_dict=lambda: {}),
    )


def test_search_result_keeps_exact_experience_anchor_in_decision_pool(monkeypatch):
    _patch_pipeline(monkeypatch)

    item = {"name": "DN32不锈钢阀门", "description": "", "query_route": {}}
    exact_anchor = _candidate(
        "Q-EXP",
        name="螺纹阀门安装 DN32",
        param_match=False,
        param_score=0.86,
        match_source="experience_injected_exact",
        knowledge_prior_sources=["experience"],
        knowledge_prior_score=1.10,
    )
    search_top = _candidate(
        "Q-SEARCH-1",
        name="法兰阀门安装 DN32",
        param_match=True,
        param_score=0.93,
        rerank_score=0.95,
    )
    search_alt = _candidate(
        "Q-SEARCH-2",
        name="法兰阀门安装 DN50",
        param_match=True,
        param_score=0.61,
        rerank_score=0.71,
    )

    result = match_pipeline._build_search_result_from_candidates(
        item,
        [exact_anchor, search_top, search_alt],
    )

    assert result["quotas"][0]["quota_id"] == "Q-SEARCH-1"
    assert result["post_final_top1_id"] == "Q-SEARCH-1"
    assert result["post_anchor_top1_id"] == "Q-SEARCH-1"
    assert "Q-EXP" in result["all_candidate_ids"]
    assert result["rank_decision_owner"] != "experience_anchor"


def test_search_result_uses_exact_experience_anchor_when_no_param_match_exists(monkeypatch):
    _patch_pipeline(monkeypatch)

    item = {"name": "电气配管 SC32", "description": "", "query_route": {}}
    search_top = _candidate(
        "Q-SEARCH-1",
        name="镀锌钢管暗配 ≤32",
        param_match=False,
        param_score=0.90,
        rerank_score=0.96,
    )
    exact_anchor = _candidate(
        "Q-EXP",
        name="波纹电线管敷设 ≤32",
        param_match=False,
        param_score=0.79,
        match_source="experience_injected_exact",
        knowledge_prior_sources=["experience"],
        knowledge_prior_score=1.10,
    )

    result = match_pipeline._build_search_result_from_candidates(
        item,
        [search_top, exact_anchor],
    )

    assert result["quotas"][0]["quota_id"] == "Q-SEARCH-1"
    assert result["post_final_top1_id"] == "Q-SEARCH-1"
    assert result["post_anchor_top1_id"] == "Q-SEARCH-1"
    assert "Q-EXP" in result["all_candidate_ids"]
    assert result["rank_decision_owner"] != "experience_anchor"


def test_run_rank_pipeline_tracks_full_stage_ids_for_param_matched_branch(monkeypatch):
    _patch_pipeline(monkeypatch)

    monkeypatch.setattr(
        match_pipeline,
        "rerank_candidates_with_ltr",
        lambda item, candidates, ctx: (
            [dict(candidates[1]), dict(candidates[0])],
            {"post_ltr_top1_id": "Q-LTR", "post_cgr_top1_id": "Q-CGR"},
        ),
    )
    monkeypatch.setattr(
        match_pipeline,
        "arbitrate_candidates",
        lambda item, candidates, route_profile=None: (
            [dict(candidates[1]), dict(candidates[0])],
            {"applied": True, "advisory_applied": False, "reason": "arbiter_flip"},
        ),
    )
    monkeypatch.setattr(
        match_pipeline,
        "_promote_explicit_distribution_box_candidate",
        lambda item, candidates: (
            [dict(candidates[1]), dict(candidates[0])],
            {"applied": False, "advisory_applied": True, "recommended_quota_id": "Q-EXPLICIT"},
        ),
    )
    ordered, ranking_meta, arbitration, explicit_override, best = match_pipeline._run_rank_pipeline(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-SEED", param_match=True, param_score=0.9),
            _candidate("Q-LTR", param_match=True, param_score=0.8),
        ],
        reservoir=[
            _candidate("Q-SEED", param_match=True, param_score=0.9),
            _candidate("Q-LTR", param_match=True, param_score=0.8),
        ],
        allow_arbiter=True,
        allow_explicit=True,
    )

    assert ordered[0]["quota_id"] == "Q-LTR"
    assert best["quota_id"] == "Q-LTR"
    assert ranking_meta["pre_ltr_top1_id"] == "Q-SEED"
    assert ranking_meta["post_ltr_top1_id"] == "Q-LTR"
    assert ranking_meta["post_cgr_top1_id"] == "Q-CGR"
    assert ranking_meta["post_arbiter_top1_id"] == "Q-LTR"
    assert ranking_meta["post_explicit_top1_id"] == "Q-LTR"
    assert ranking_meta["post_anchor_top1_id"] == "Q-LTR"
    assert ranking_meta["selected_top1_id"] == "Q-LTR"
    assert arbitration["reason"] == "arbiter_flip"
    assert arbitration["applied"] is False
    assert arbitration["reorder_ignored_by_pipeline"] is True
    assert explicit_override["recommended_quota_id"] == "Q-EXPLICIT"
    assert explicit_override["advisory_applied"] is True


def test_run_rank_pipeline_uses_default_no_param_arbitration(monkeypatch):
    _patch_pipeline(monkeypatch)

    monkeypatch.setattr(
        match_pipeline,
        "rerank_candidates_with_ltr",
        lambda item, candidates, ctx: (
            list(candidates),
            {"post_ltr_top1_id": "Q-FALLBACK", "post_cgr_top1_id": "Q-FALLBACK"},
        ),
    )

    ordered, ranking_meta, arbitration, explicit_override, best = match_pipeline._run_rank_pipeline(
        {"name": "测试项", "query_route": {"route": "semantic_description"}},
        [_candidate("Q-FALLBACK", param_match=False, param_score=0.4)],
        reservoir=[_candidate("Q-FALLBACK", param_match=False, param_score=0.4)],
        allow_arbiter=False,
        allow_explicit=False,
    )

    assert ordered[0]["quota_id"] == "Q-FALLBACK"
    assert best["quota_id"] == "Q-FALLBACK"
    assert ranking_meta["post_arbiter_top1_id"] == "Q-FALLBACK"
    assert ranking_meta["post_explicit_top1_id"] == "Q-FALLBACK"
    assert ranking_meta["post_anchor_top1_id"] == "Q-FALLBACK"
    assert arbitration["reason"] == "no_param_matched_candidates"
    assert explicit_override == {}


def test_run_rank_pipeline_exposes_unified_ranking_flags_without_changing_behavior(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(config, "UNIFIED_RANKING_ENABLED", False)
    monkeypatch.setattr(config, "UNIFIED_RANKING_SHADOW_MODE", True)

    ordered, ranking_meta, arbitration, explicit_override, best = match_pipeline._run_rank_pipeline(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-KEEP", param_match=True, param_score=0.88),
            _candidate("Q-ALT", param_match=True, param_score=0.70, rerank_score=0.65),
        ],
        reservoir=[
            _candidate("Q-KEEP", param_match=True, param_score=0.88),
            _candidate("Q-ALT", param_match=True, param_score=0.70, rerank_score=0.65),
        ],
        allow_arbiter=True,
        allow_explicit=True,
    )

    assert ordered[0]["quota_id"] == "Q-KEEP"
    assert best["quota_id"] == "Q-KEEP"
    assert arbitration["applied"] is False
    assert explicit_override == {}
    assert ranking_meta["unified_ranking_enabled"] is False
    assert ranking_meta["unified_ranking_shadow_mode"] is True
    assert ranking_meta["unified_ranking_mode"] == "shadow"
    assert ranking_meta["unified_ranking_executed"] is False
    assert ranking_meta["unified_result_used"] is False
    assert ranking_meta["unified_top1_id"] == ""
    assert ranking_meta["unified_ranking_error"] == ""


def test_build_search_result_records_unified_ranking_shadow_without_overriding_selected_result(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(config, "UNIFIED_RANKING_ENABLED", False)
    monkeypatch.setattr(config, "UNIFIED_RANKING_SHADOW_MODE", True)
    monkeypatch.setattr(
        match_pipeline,
        "_run_unified_ranking_shadow",
        lambda item, candidates, top_k=5: {
            "candidates": [
                {
                    "quota_id": "Q-SHADOW",
                    "filtered_score": 0.77,
                    "confidence": 0.66,
                    "explanation": {"top_driver": "param_match"},
                },
                {
                    **dict(candidates[0]),
                    "filtered_score": 0.52,
                    "confidence": 0.58,
                    "explanation": {"top_driver": "retrieval"},
                },
            ],
            "top1_score": 0.77,
            "top1_confidence": 0.66,
            "diagnostics": {
                "selection": {"top_quota_id": "Q-SHADOW", "top_driver": "param_match"},
            },
        },
    )

    result = match_pipeline._build_search_result_from_candidates(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-KEEP", param_match=True, param_score=0.90, rerank_score=0.90),
            _candidate("Q-ALT", param_match=True, param_score=0.60, rerank_score=0.60),
        ],
    )

    assert result["quotas"][0]["quota_id"] == "Q-KEEP"
    assert result["unified_ranking_executed"] is True
    assert result["unified_result_used"] is False
    assert result["legacy_top1_id"] == "Q-KEEP"
    assert result["unified_top1_id"] == "Q-SHADOW"
    assert result["unified_top1_score"] == 0.77
    assert result["unified_top1_confidence"] == 0.66
    assert result["unified_top1_matches_selected"] is False
    assert result["unified_top1_matches_legacy"] is False
    assert result["legacy_top1_unified_score"] == 0.52
    assert result["legacy_top1_unified_confidence"] == 0.58
    assert result["unified_legacy_score_gap"] == 0.25
    assert result["unified_shadow_comparison"]["legacy_top1_id"] == "Q-KEEP"
    assert result["unified_shadow_comparison"]["unified_top1_id"] == "Q-SHADOW"
    assert result["unified_shadow_comparison"]["matches"] is False
    assert result["unified_shadow_comparison"]["score_gap"] == 0.25
    assert result["unified_ranking_diagnostics"]["selection"]["top_driver"] == "param_match"


def test_build_search_result_uses_unified_ranking_top1_when_enabled(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(config, "UNIFIED_RANKING_ENABLED", True)
    monkeypatch.setattr(config, "UNIFIED_RANKING_SHADOW_MODE", False)
    monkeypatch.setattr(
        match_pipeline,
        "_run_unified_ranking_shadow",
        lambda item, candidates, top_k=5: {
            "candidates": [
                {
                    **dict(candidates[1]),
                    "filtered_score": 0.91,
                    "confidence": 0.81,
                    "explanation": {"top_driver": "knowledge_prior"},
                },
                {
                    **dict(candidates[0]),
                    "filtered_score": 0.83,
                    "confidence": 0.63,
                    "explanation": {"top_driver": "param_match"},
                },
            ],
            "top1_score": 0.91,
            "top1_confidence": 0.81,
            "diagnostics": {
                "selection": {"top_quota_id": "Q-UNIFIED", "top_driver": "knowledge_prior"},
            },
        },
    )

    result = match_pipeline._build_search_result_from_candidates(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-KEEP", param_match=True, param_score=0.92, rerank_score=0.90),
            _candidate(
                "Q-UNIFIED",
                param_match=False,
                param_score=0.58,
                rerank_score=0.62,
                knowledge_prior_sources=["experience"],
                knowledge_prior_score=1.15,
            ),
        ],
    )

    assert result["quotas"][0]["quota_id"] == "Q-UNIFIED"
    assert result["legacy_top1_id"] == "Q-KEEP"
    assert result["selected_top1_id"] == "Q-UNIFIED"
    assert result["post_final_top1_id"] == "Q-UNIFIED"
    assert result["confidence"] == 0.81
    assert result["explanation"] == "unified_ranking: top_driver=knowledge_prior; filtered_score=0.910"
    assert result["unified_ranking_executed"] is True
    assert result["unified_result_used"] is True
    assert result["unified_top1_id"] == "Q-UNIFIED"
    assert result["unified_top1_matches_selected"] is True
    assert result["unified_top1_matches_legacy"] is False
    assert result["legacy_top1_unified_score"] == 0.83
    assert result["legacy_top1_unified_confidence"] == 0.63
    assert round(result["unified_legacy_score_gap"], 2) == 0.08
    assert result["unified_shadow_comparison"]["legacy_top1_id"] == "Q-KEEP"
    assert result["unified_shadow_comparison"]["matches"] is False
    assert round(result["unified_shadow_comparison"]["score_gap"], 2) == 0.08
    assert result["final_changed_by"] == "unified_ranking"
    assert result["rank_decision_owner"] == "unified_ranking"


def test_build_search_result_falls_back_when_unified_ranking_enabled_errors(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(config, "UNIFIED_RANKING_ENABLED", True)
    monkeypatch.setattr(config, "UNIFIED_RANKING_SHADOW_MODE", False)

    def _raise_unified_error(item, candidates, top_k=5):
        raise RuntimeError("unified failed")

    monkeypatch.setattr(match_pipeline, "_run_unified_ranking_shadow", _raise_unified_error)

    result = match_pipeline._build_search_result_from_candidates(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-KEEP", param_match=True, param_score=0.90, rerank_score=0.89),
            _candidate("Q-ALT", param_match=True, param_score=0.72, rerank_score=0.70),
        ],
    )

    assert result["quotas"][0]["quota_id"] == "Q-KEEP"
    assert result["legacy_top1_id"] == "Q-KEEP"
    assert result["selected_top1_id"] == "Q-KEEP"
    assert result["unified_ranking_executed"] is False
    assert result["unified_result_used"] is False
    assert result["unified_top1_id"] == ""
    assert result["unified_ranking_error"] == "unified failed"
    assert result["unified_shadow_comparison"]["legacy_top1_id"] == "Q-KEEP"
    assert result["unified_shadow_comparison"]["unified_top1_id"] == ""
    assert result["unified_shadow_comparison"]["matches"] is False
    assert result["unified_shadow_comparison"]["failure_reason"] == "unified failed"


def test_run_rank_pipeline_skips_legacy_rank_stages_when_unified_primary_enabled(monkeypatch):
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(config, "UNIFIED_RANKING_ENABLED", True)
    monkeypatch.setattr(config, "UNIFIED_RANKING_SHADOW_MODE", False)

    def _unexpected(*args, **kwargs):
        raise AssertionError("legacy ranking stage should be skipped in unified enabled mode")

    monkeypatch.setattr(match_pipeline, "rerank_candidates_with_ltr", _unexpected)
    monkeypatch.setattr(match_pipeline, "arbitrate_candidates", _unexpected)
    monkeypatch.setattr(match_pipeline, "_promote_explicit_distribution_box_candidate", _unexpected)

    ordered, ranking_meta, arbitration, explicit_override, best = match_pipeline._run_rank_pipeline(
        {"name": "测试项", "query_route": {"route": "installation_spec"}},
        [
            _candidate("Q-SEED", param_match=True, param_score=0.92, rerank_score=0.92),
            _candidate("Q-ALT", param_match=True, param_score=0.71, rerank_score=0.71),
        ],
        reservoir=[
            _candidate("Q-SEED", param_match=True, param_score=0.92, rerank_score=0.92),
            _candidate("Q-ALT", param_match=True, param_score=0.71, rerank_score=0.71),
        ],
        allow_arbiter=True,
        allow_explicit=True,
    )

    assert ordered[0]["quota_id"] == "Q-SEED"
    assert best["quota_id"] == "Q-SEED"
    assert ranking_meta["unified_ranking_mode"] == "enabled"
    assert ranking_meta["post_ltr_top1_id"] == "Q-SEED"
    assert ranking_meta["post_cgr_top1_id"] == "Q-SEED"
    assert ranking_meta["post_arbiter_top1_id"] == "Q-SEED"
    assert ranking_meta["post_explicit_top1_id"] == "Q-SEED"
    assert ranking_meta["post_anchor_top1_id"] == "Q-SEED"
    assert ranking_meta["ltr"]["legacy_stage_disabled"] is True
    assert arbitration["reason"] == "skipped_by_unified_primary"
    assert arbitration["legacy_stage_disabled"] is True
    assert explicit_override["reason"] == "skipped_by_unified_primary"
    assert explicit_override["legacy_stage_disabled"] is True
