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
