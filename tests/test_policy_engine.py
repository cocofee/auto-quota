from src.policy_engine import PolicyEngine


def test_installation_spec_policy_is_stricter_than_balanced():
    balanced = PolicyEngine.get_route_policy({"route": "balanced"})
    installation = PolicyEngine.get_route_policy({"route": "installation_spec"})

    assert installation.rule_direct_confidence >= balanced.rule_direct_confidence
    assert installation.agent_fastpath_score > balanced.agent_fastpath_score
    assert installation.agent_fastpath_score_gap > balanced.agent_fastpath_score_gap


def test_ambiguous_short_policy_requires_more_candidates():
    policy = PolicyEngine.get_route_policy({"route": "ambiguous_short"})

    assert policy.agent_fastpath_min_candidates == 3
    assert policy.rule_direct_confidence >= 88


def test_semantic_description_policy_keeps_rule_direct_available():
    allow, threshold = PolicyEngine.should_use_rule_direct(
        80,
        route_profile={"route": "semantic_description"},
    )

    assert allow is True
    assert threshold == 80
