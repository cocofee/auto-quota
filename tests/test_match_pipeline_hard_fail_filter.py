from src.match_pipeline import orchestrator


def test_build_search_result_from_candidates_rejects_all_param_hard_fail_candidates(monkeypatch):
    monkeypatch.setattr(
        orchestrator,
        "_apply_plugin_route_gate",
        lambda item, candidates: (list(candidates), {}),
    )
    monkeypatch.setattr(
        orchestrator,
        "_apply_plugin_candidate_biases",
        lambda item, candidates: list(candidates),
    )
    monkeypatch.setattr(
        orchestrator,
        "_annotate_candidate_scope_signals",
        lambda item, candidates: list(candidates),
    )
    monkeypatch.setattr(
        orchestrator,
        "_apply_unified_ranking_shadow",
        lambda item, candidates, ranking_meta: {},
    )
    monkeypatch.setattr(
        orchestrator,
        "_apply_unified_enabled_selection",
        lambda item, valid_candidates, matched_candidates, ranking_meta, arbitration, unified_result, best, confidence, explanation, reasoning_decision: (
            valid_candidates,
            matched_candidates,
            best,
            confidence,
            explanation,
            reasoning_decision,
        ),
    )

    result = orchestrator._build_search_result_from_candidates(
        {"name": "管道安装 DN100", "query_route": {}},
        [
            {
                "quota_id": "Q-BAD",
                "name": "管道安装 DN80",
                "unit": "m",
                "param_match": False,
                "param_tier": 0,
                "param_score": 0.64,
                "param_validation_tier": "hard_fail",
                "rerank_score": 0.91,
                "hybrid_score": 0.91,
            }
        ],
    )

    assert result["quotas"] == []
    assert result["hard_param_fail_rejected_count"] == 1
    assert result["primary_reason"] == "param_hard_fail"
    assert "param_hard_fail" in result["reason_tags"]
