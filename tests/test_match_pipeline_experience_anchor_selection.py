from types import SimpleNamespace

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
