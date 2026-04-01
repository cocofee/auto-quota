import pytest

from src.candidate_scoring import (
    compute_candidate_structured_score,
    explain_candidate_rank_score,
    score_candidates_two_stage,
    sort_candidates_with_stage_priority,
)


def _candidate(quota_id: str, *, advisory: bool = False, arbiter: bool = False) -> dict:
    return {
        "quota_id": quota_id,
        "name": f"Candidate {quota_id}",
        "param_match": True,
        "param_tier": 2,
        "param_score": 0.82,
        "logic_score": 0.84,
        "feature_alignment_score": 0.86,
        "context_alignment_score": 0.80,
        "rerank_score": 0.72,
        "hybrid_score": 0.72,
        "param_rectify_selected": advisory,
        "param_rectify_selected_rules": ["feature_rectify"] if advisory else [],
        "arbiter_recommended": arbiter,
    }


def test_structured_score_is_not_affected_by_advisory_flags():
    plain = _candidate("A")
    advised = _candidate("B", advisory=True, arbiter=True)

    assert compute_candidate_structured_score(plain) == compute_candidate_structured_score(advised)


def test_stage_priority_sort_keeps_stable_order_when_only_advisory_differs():
    candidates = [
        _candidate("A"),
        _candidate("B", advisory=True, arbiter=True),
    ]

    ranked = sort_candidates_with_stage_priority(candidates)

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


def test_stage_priority_sort_prefers_authority_experience_prior_when_scores_tie():
    plain = _candidate("A")
    prior = _candidate("B")
    prior.update(
        {
            "match_source": "experience_injected_exact",
            "knowledge_prior_sources": ["experience"],
            "knowledge_prior_score": 1.10,
            "experience_layer": "authority",
        }
    )

    ranked = sort_candidates_with_stage_priority([plain, prior])

    assert [row["quota_id"] for row in ranked] == ["B", "A"]


def test_stage_priority_sort_exact_experience_anchor_can_beat_search_param_match():
    search = _candidate("A")
    prior = _candidate("B")
    search.update(
        {
            "param_match": True,
            "param_score": 0.94,
            "logic_score": 0.92,
            "feature_alignment_score": 0.90,
            "rerank_score": 0.88,
            "hybrid_score": 0.88,
        }
    )
    prior.update(
        {
            "param_match": False,
            "param_score": 0.42,
            "logic_score": 0.50,
            "feature_alignment_score": 0.56,
            "rerank_score": 0.31,
            "hybrid_score": 0.31,
            "match_source": "experience_injected_exact",
            "knowledge_prior_sources": ["experience"],
            "knowledge_prior_score": 1.10,
        }
    )

    ranked = sort_candidates_with_stage_priority([search, prior])

    assert [row["quota_id"] for row in ranked] == ["B", "A"]


def test_stage_priority_sort_exact_experience_anchor_does_not_override_fatal_conflict():
    search = _candidate("A")
    prior = _candidate("B")
    prior.update(
        {
            "param_match": False,
            "param_score": 0.42,
            "logic_score": 0.50,
            "feature_alignment_score": 0.56,
            "rerank_score": 0.31,
            "hybrid_score": 0.31,
            "match_source": "experience_injected_exact",
            "knowledge_prior_sources": ["experience"],
            "knowledge_prior_score": 1.10,
            "feature_alignment_hard_conflict": True,
        }
    )

    ranked = sort_candidates_with_stage_priority([search, prior])

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


def test_explain_candidate_rank_score_matches_structured_scoring_and_stage_priority():
    candidate = _candidate("A")
    candidate.update(
        {
            "family_gate_score": 1.1,
            "feature_alignment_exact_anchor_count": 2,
            "knowledge_prior_sources": ["experience"],
            "knowledge_prior_score": 1.1,
            "match_source": "experience_injected_exact",
        }
    )

    breakdown = explain_candidate_rank_score(candidate)

    assert breakdown["rank_score"] == pytest.approx(compute_candidate_structured_score(candidate))
    assert breakdown["structured"]["score"] == pytest.approx(compute_candidate_structured_score(candidate))
    assert breakdown["structured"]["flags"]["strong_family"] is True
    assert breakdown["stage_priority"]["exact_experience_anchor"] is True
    assert breakdown["stage_priority"]["family_aligned"] is True


def test_structured_score_prefers_in_scope_candidate_when_scores_are_close():
    off_scope = _candidate("01-12-7-12")
    in_scope = _candidate("03-2-5-38")
    off_scope.update(
        {
            "rerank_score": 0.90,
            "candidate_scope_match": 0.0,
            "candidate_scope_conflict": True,
        }
    )
    in_scope.update(
        {
            "rerank_score": 0.74,
            "candidate_scope_match": 1.0,
            "candidate_scope_conflict": False,
        }
    )

    assert compute_candidate_structured_score(in_scope) > compute_candidate_structured_score(off_scope)

    ranked = sort_candidates_with_stage_priority([off_scope, in_scope])

    assert [row["quota_id"] for row in ranked] == ["03-2-5-38", "01-12-7-12"]


def test_explain_candidate_rank_score_exposes_scope_signal():
    candidate = _candidate("03-2-5-38")
    candidate.update(
        {
            "candidate_scope_match": 1.0,
            "candidate_scope_conflict": False,
        }
    )

    breakdown = explain_candidate_rank_score(candidate)

    assert breakdown["structured"]["components"]["scope_match"]["contribution"] == pytest.approx(0.26)
    assert breakdown["stage_priority"]["scope_match"] == pytest.approx(1.0)
    assert breakdown["stage_priority"]["scope_conflict"] is False


def test_structured_score_prefers_in_scope_candidate_even_when_text_score_gap_is_large():
    off_scope = _candidate("01-12-7-7")
    in_scope = _candidate("03-2-5-38")
    off_scope.update(
        {
            "rerank_score": 0.98,
            "candidate_scope_match": 0.0,
            "candidate_scope_conflict": True,
            "name_bonus": 0.10,
        }
    )
    in_scope.update(
        {
            "rerank_score": 0.63,
            "candidate_scope_match": 1.0,
            "candidate_scope_conflict": False,
            "name_bonus": 0.0,
        }
    )

    assert compute_candidate_structured_score(in_scope) > compute_candidate_structured_score(off_scope)

    ranked = sort_candidates_with_stage_priority([off_scope, in_scope])

    assert [row["quota_id"] for row in ranked] == ["03-2-5-38", "01-12-7-7"]


def test_two_stage_sort_prefers_semantic_family_before_cross_family_tier_signal():
    wrong_family = _candidate("A")
    right_family = _candidate("B")
    wrong_family.update(
        {
            "param_score": 0.97,
            "logic_score": 0.96,
            "rerank_score": 0.86,
            "hybrid_score": 0.86,
            "family_gate_score": -1.2,
            "feature_alignment_score": 0.30,
            "context_alignment_score": 0.40,
            "candidate_canonical_features": {"family": "electrical_box", "entity": "配电箱"},
        }
    )
    right_family.update(
        {
            "param_score": 0.70,
            "logic_score": 0.72,
            "rerank_score": 0.79,
            "hybrid_score": 0.79,
            "family_gate_score": 1.2,
            "feature_alignment_score": 0.93,
            "context_alignment_score": 0.88,
            "candidate_canonical_features": {"family": "valve_body", "entity": "阀门"},
        }
    )

    ranked = sort_candidates_with_stage_priority([wrong_family, right_family])

    assert [row["quota_id"] for row in ranked] == ["B", "A"]


def test_two_stage_sort_uses_param_logic_to_rank_within_same_family():
    exact_tier = _candidate("A")
    wider_tier = _candidate("B")
    exact_tier.update(
        {
            "param_score": 0.99,
            "logic_score": 0.98,
            "rerank_score": 0.66,
            "hybrid_score": 0.66,
            "logic_exact_primary_match": True,
            "candidate_canonical_features": {"family": "cable_family", "entity": "电缆"},
        }
    )
    wider_tier.update(
        {
            "param_score": 0.78,
            "logic_score": 0.70,
            "rerank_score": 0.93,
            "hybrid_score": 0.93,
            "candidate_canonical_features": {"family": "cable_family", "entity": "电缆"},
        }
    )

    ranked = sort_candidates_with_stage_priority([wider_tier, exact_tier])

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


def test_single_stage_mode_keeps_legacy_cross_family_rerank_bias():
    wrong_family = _candidate("A")
    right_family = _candidate("B")
    wrong_family.update(
        {
            "param_score": 0.99,
            "logic_score": 0.98,
            "rerank_score": 0.96,
            "hybrid_score": 0.96,
            "feature_alignment_score": 0.35,
            "context_alignment_score": 0.35,
            "family_gate_score": 0.0,
            "candidate_canonical_features": {"family": "electrical_box", "entity": "配电箱"},
        }
    )
    right_family.update(
        {
            "param_score": 0.70,
            "logic_score": 0.70,
            "rerank_score": 0.70,
            "hybrid_score": 0.70,
            "feature_alignment_score": 0.86,
            "context_alignment_score": 0.86,
            "family_gate_score": 1.1,
            "candidate_canonical_features": {"family": "valve_body", "entity": "阀门"},
        }
    )

    ranked = sort_candidates_with_stage_priority(
        [wrong_family, right_family],
        scoring_mode="single_stage",
    )

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


def test_scoring_mode_can_be_switched_by_environment(monkeypatch):
    wrong_family = _candidate("A")
    right_family = _candidate("B")
    wrong_family.update(
        {
            "param_score": 0.99,
            "logic_score": 0.98,
            "rerank_score": 0.96,
            "hybrid_score": 0.96,
            "feature_alignment_score": 0.35,
            "context_alignment_score": 0.35,
            "family_gate_score": 0.0,
            "candidate_canonical_features": {"family": "electrical_box", "entity": "配电箱"},
        }
    )
    right_family.update(
        {
            "param_score": 0.70,
            "logic_score": 0.70,
            "rerank_score": 0.70,
            "hybrid_score": 0.70,
            "feature_alignment_score": 0.86,
            "context_alignment_score": 0.86,
            "family_gate_score": 1.1,
            "candidate_canonical_features": {"family": "valve_body", "entity": "阀门"},
        }
    )

    monkeypatch.setenv("AUTO_QUOTA_SCORING_MODE", "single_stage")
    ranked = sort_candidates_with_stage_priority([wrong_family, right_family])

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


def test_score_candidates_two_stage_attaches_family_and_tier_metadata():
    wrong_family = _candidate("A")
    right_family = _candidate("B")
    wrong_family.update(
        {
            "param_score": 0.95,
            "logic_score": 0.94,
            "rerank_score": 0.88,
            "hybrid_score": 0.90,
            "family_gate_score": -1.1,
            "feature_alignment_score": 0.32,
            "context_alignment_score": 0.40,
            "candidate_canonical_features": {"family": "electrical_box", "entity": "配电箱"},
        }
    )
    right_family.update(
        {
            "param_score": 0.72,
            "logic_score": 0.76,
            "rerank_score": 0.80,
            "hybrid_score": 0.82,
            "family_gate_score": 1.2,
            "feature_alignment_score": 0.92,
            "context_alignment_score": 0.90,
            "candidate_canonical_features": {"family": "valve_body", "entity": "阀门"},
        }
    )

    ranked = score_candidates_two_stage(
        [wrong_family, right_family],
        bill_item={"canonical_features": {"family": "valve_body", "entity": "阀门"}},
    )

    assert [row["quota_id"] for row in ranked] == ["B", "A"]
    assert ranked[0]["_stage_rank_mode"] == "two_stage"
    assert ranked[0]["two_stage_family_rank"] == 1
    assert ranked[0]["two_stage_within_family_rank"] == 1
    assert ranked[0]["two_stage_family_winner"] is True
    assert ranked[0]["two_stage_bill_family"] == "valve_body"
    assert ranked[0]["two_stage_bill_entity"] == "阀门"
    assert ranked[0]["two_stage_family_score"] > ranked[1]["two_stage_family_score"]


def test_two_stage_within_family_score_is_not_driven_by_rerank():
    semantic_only = _candidate("A")
    exact_tier = _candidate("B")
    semantic_only.update(
        {
            "param_score": 0.68,
            "logic_score": 0.62,
            "param_tier": 1,
            "rerank_score": 0.99,
            "hybrid_score": 0.99,
            "candidate_canonical_features": {"family": "cable_family", "entity": "电缆"},
        }
    )
    exact_tier.update(
        {
            "param_score": 0.97,
            "logic_score": 0.96,
            "param_tier": 2,
            "rerank_score": 0.18,
            "hybrid_score": 0.18,
            "logic_exact_primary_match": True,
            "candidate_canonical_features": {"family": "cable_family", "entity": "电缆"},
        }
    )

    ranked = score_candidates_two_stage([semantic_only, exact_tier])

    assert [row["quota_id"] for row in ranked] == ["B", "A"]
    assert ranked[0]["two_stage_within_family_score"] > ranked[1]["two_stage_within_family_score"]


def test_two_stage_family_stage_penalizes_weak_family_semantic_hijack():
    weak_family = _candidate("A")
    right_family = _candidate("B")
    weak_family.update(
        {
            "param_score": 0.96,
            "logic_score": 0.95,
            "rerank_score": 0.94,
            "hybrid_score": 0.94,
            "family_gate_score": -0.4,
            "feature_alignment_score": 0.34,
            "context_alignment_score": 0.42,
            "candidate_canonical_features": {"family": "conduit_raceway", "entity": "接线盒"},
        }
    )
    right_family.update(
        {
            "param_score": 0.74,
            "logic_score": 0.76,
            "rerank_score": 0.78,
            "hybrid_score": 0.78,
            "family_gate_score": 0.3,
            "feature_alignment_score": 0.79,
            "context_alignment_score": 0.82,
            "candidate_canonical_features": {"family": "electrical_box", "entity": "配电箱"},
        }
    )

    ranked = score_candidates_two_stage([weak_family, right_family])

    assert [row["quota_id"] for row in ranked] == ["B", "A"]
    assert ranked[0]["two_stage_family_score"] > ranked[1]["two_stage_family_score"]
