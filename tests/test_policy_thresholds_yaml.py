import config

from src.ambiguity_gate import analyze_ambiguity
from src.match_pipeline.pickers import _guard_explicit_candidate
from src.match_pipeline.reconcilers import _reconcile_search_and_experience
from src.policy_engine import PolicyEngine


def test_policy_engine_reads_thresholds_yaml_override(tmp_path, monkeypatch):
    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text(
        "\n".join(
            [
                "confidence:",
                "  same_quota_confirm_boost: 95",
                "fastpath:",
                "  reranker_failure_window: 1",
                "pickers:",
                "  explicit_hybrid_margin: 0.02",
                "route_policies:",
                "  balanced:",
                "    rule_direct_confidence: 84",
                "    require_param_match: false",
                "  installation_spec:",
                "    agent_fastpath_score_gap: 0.09",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "POLICY_THRESHOLDS_PATH", str(thresholds_path), raising=False)
    PolicyEngine.clear_caches()

    balanced = PolicyEngine.get_route_policy({"route": "balanced"})
    installation = PolicyEngine.get_route_policy({"route": "installation_spec"})
    semantic = PolicyEngine.get_route_policy({"route": "semantic_description"})
    unknown = PolicyEngine.get_route_policy({"route": "nonexistent"})

    assert balanced.rule_direct_confidence == 84
    assert installation.agent_fastpath_score_gap >= 0.09
    assert balanced.require_param_match is False
    assert semantic.require_param_match is False
    assert unknown.require_param_match is False
    assert PolicyEngine.get_confidence_threshold("same_quota_confirm_boost", 0) == 95
    assert PolicyEngine.get_fastpath_threshold("reranker_failure_window", 0) == 1
    assert PolicyEngine.get_picker_threshold("explicit_hybrid_margin", 0.0) == 0.02

    PolicyEngine.clear_caches()


def test_reconcile_search_and_experience_uses_configured_confidence_thresholds(tmp_path, monkeypatch):
    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text(
        "\n".join(
            [
                "confidence:",
                "  experience_exact_degrade_cap: 83",
                "  same_quota_confirm_boost: 95",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "POLICY_THRESHOLDS_PATH", str(thresholds_path), raising=False)
    PolicyEngine.clear_caches()

    confirmed, hits = _reconcile_search_and_experience(
        {
            "confidence": 82,
            "quotas": [{"quota_id": "Q1"}],
            "explanation": "search",
        },
        {
            "match_source": "experience_exact",
            "confidence": 99,
            "quotas": [{"quota_id": "Q1"}],
        },
        0,
    )
    assert confirmed["confidence"] == 95
    assert hits == 1

    overridden, hits = _reconcile_search_and_experience(
        {
            "confidence": 80,
            "quotas": [{"quota_id": "Q2"}],
            "explanation": "search",
        },
        {
            "match_source": "experience_exact",
            "confidence": 99,
            "quotas": [{"quota_id": "Q1"}],
        },
        0,
    )
    assert overridden["confidence"] == 83
    assert hits == 1

    PolicyEngine.clear_caches()


def test_ambiguity_and_picker_use_configured_thresholds(tmp_path, monkeypatch):
    thresholds_path = tmp_path / "thresholds.yaml"
    thresholds_path.write_text(
        "\n".join(
            [
                "fastpath:",
                "  reranker_failure_window: 1",
                "pickers:",
                "  explicit_hybrid_margin: 0.02",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "POLICY_THRESHOLDS_PATH", str(thresholds_path), raising=False)
    PolicyEngine.clear_caches()

    decision = analyze_ambiguity(
        [
            {
                "quota_id": "Q1",
                "param_match": True,
                "param_score": 0.95,
                "rerank_score": 0.92,
            },
            {
                "quota_id": "Q2",
                "param_match": True,
                "param_score": 0.60,
                "rerank_score": 0.70,
                "reranker_failed": True,
            },
        ]
    )
    assert decision.can_fastpath is True

    guarded = _guard_explicit_candidate(
        {},
        {"quota_id": "Q1", "hybrid_score": 0.90},
        {"quota_id": "Q2", "hybrid_score": 0.885},
    )
    assert guarded["quota_id"] == "Q2"

    PolicyEngine.clear_caches()
