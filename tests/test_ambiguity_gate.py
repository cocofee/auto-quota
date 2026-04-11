from src.ambiguity_gate import analyze_ambiguity


def test_single_candidate_is_treated_as_ambiguous():
    decision = analyze_ambiguity([
        {
            "quota_id": "C10-1-1",
            "param_match": True,
            "param_score": 0.95,
            "rerank_score": 0.96,
        }
    ])

    assert decision.can_fastpath is False
    assert decision.is_ambiguous is True
    assert decision.reason == "insufficient_candidates"


def test_backup_conflict_blocks_fastpath():
    decision = analyze_ambiguity(
        [
            {
                "quota_id": "C10-1-1",
                "param_match": True,
                "param_score": 0.95,
                "rerank_score": 0.96,
            },
            {
                "quota_id": "C10-1-2",
                "param_match": True,
                "param_score": 0.70,
                "rerank_score": 0.80,
            },
        ],
        exp_backup={
            "confidence": 80,
            "quotas": [{"quota_id": "C10-9-9"}],
        },
    )

    assert decision.can_fastpath is False
    assert decision.reason == "backup_conflict"


def test_large_gap_allows_fastpath():
    decision = analyze_ambiguity([
        {
            "quota_id": "C10-1-1",
            "param_match": True,
            "param_score": 0.95,
            "rerank_score": 0.98,
        },
        {
            "quota_id": "C10-1-2",
            "param_match": True,
            "param_score": 0.62,
            "rerank_score": 0.80,
        },
    ])

    assert decision.can_fastpath is True
    assert decision.is_ambiguous is False
    assert decision.reason == "high_confidence"


def test_hard_conflict_forces_ambiguity_and_final_review():
    decision = analyze_ambiguity([
        {
            "quota_id": "C10-1-1",
            "param_match": True,
            "param_score": 0.95,
            "rerank_score": 0.98,
            "logic_hard_conflict": True,
        },
        {
            "quota_id": "C10-1-2",
            "param_match": True,
            "param_score": 0.62,
            "rerank_score": 0.80,
        },
    ], route_profile={"route": "installation_spec"})

    assert decision.can_fastpath is False
    assert decision.is_ambiguous is True
    assert decision.reason == "hard_conflict"
    assert decision.require_final_review is True
    assert decision.route == "installation_spec"


def test_arbitrated_candidate_without_gap_still_requires_reasoning():
    decision = analyze_ambiguity(
        [
            {
                "quota_id": "C10-1-2",
                "param_match": True,
                "param_score": 0.90,
                "rerank_score": 0.90,
            },
            {
                "quota_id": "C10-1-1",
                "param_match": True,
                "param_score": 0.88,
                "rerank_score": 0.84,
            },
        ],
        route_profile={"route": "installation_spec"},
        arbitration={"applied": True},
    )

    assert decision.can_fastpath is False
    assert decision.is_ambiguous is True
    assert decision.reason == "arbitrated_small_gap"
    assert decision.arbitration_applied is True


def test_accept_head_can_allow_fastpath_when_enabled(monkeypatch):
    monkeypatch.setattr("config.CGR_ACCEPT_HEAD_ENABLED", True)
    monkeypatch.setattr("config.CGR_ACCEPT_THRESHOLD", 0.62)
    monkeypatch.setattr("config.CGR_MIN_TOP1_PROB", 0.45)
    decision = analyze_ambiguity(
        [
            {
                "quota_id": "C10-1-1",
                "param_match": True,
                "param_score": 0.81,
                "rerank_score": 0.70,
                "cgr_accept_score": 0.88,
                "cgr_accept": True,
                "cgr_probability": 0.71,
                "cgr_prob_gap_top2": 0.42,
            },
            {
                "quota_id": "C10-1-2",
                "param_match": True,
                "param_score": 0.72,
                "rerank_score": 0.69,
                "cgr_accept_score": 0.88,
                "cgr_accept": True,
                "cgr_probability": 0.29,
            },
        ],
        route_profile={"route": "semantic_description"},
    )

    assert decision.can_fastpath is True
    assert decision.is_ambiguous is False
    assert decision.reason == "accept_head_confident"


def test_borderline_param_score_fastpath_recommends_audit(monkeypatch):
    monkeypatch.setattr("config.AGENT_FASTPATH_MARGIN", 0.03)

    decision = analyze_ambiguity([
        {
            "quota_id": "C10-1-1",
            "param_match": True,
            "param_score": 0.62,
            "rerank_score": 0.90,
        },
        {
            "quota_id": "C10-1-2",
            "param_match": True,
            "param_score": 0.58,
            "rerank_score": 0.80,
        },
    ])

    assert decision.can_fastpath is True
    assert decision.audit_recommended is True
    assert "borderline_param_score" in decision.audit_reasons


def test_borderline_score_gap_fastpath_recommends_audit(monkeypatch):
    monkeypatch.setattr("config.AGENT_FASTPATH_MARGIN", 0.03)

    decision = analyze_ambiguity([
        {
            "quota_id": "C10-1-1",
            "param_match": True,
            "param_score": 0.90,
            "rerank_score": 0.64,
        },
        {
            "quota_id": "C10-1-2",
            "param_match": True,
            "param_score": 0.70,
            "rerank_score": 0.60,
        },
    ])

    assert decision.can_fastpath is True
    assert decision.audit_recommended is True
    assert "borderline_score_gap" in decision.audit_reasons


def test_arbitrated_fastpath_recommends_audit_and_final_review(monkeypatch):
    monkeypatch.setattr("config.CGR_ACCEPT_HEAD_ENABLED", True)
    monkeypatch.setattr("config.CGR_ACCEPT_THRESHOLD", 0.62)
    monkeypatch.setattr("config.CGR_MIN_TOP1_PROB", 0.45)

    decision = analyze_ambiguity(
        [
            {
                "quota_id": "C10-1-1",
                "param_match": True,
                "param_score": 0.88,
                "rerank_score": 0.70,
                "cgr_accept_score": 0.90,
                "cgr_accept": True,
                "cgr_probability": 0.72,
                "cgr_prob_gap_top2": 0.30,
            },
            {
                "quota_id": "C10-1-2",
                "param_match": True,
                "param_score": 0.75,
                "rerank_score": 0.60,
                "cgr_accept_score": 0.90,
                "cgr_accept": True,
                "cgr_probability": 0.28,
            },
        ],
        arbitration={"applied": True},
    )

    assert decision.can_fastpath is True
    assert decision.require_final_review is True
    assert decision.risk_level == "medium"
    assert decision.audit_recommended is True
    assert "arbitration_applied" in decision.audit_reasons
