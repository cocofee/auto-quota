from src.policy_engine import PolicyEngine, RoutePolicy, RULE_DIRECT_CONFIDENCE


# ===== 基本策略获取 =====

def test_default_route_is_balanced():
    policy = PolicyEngine.get_route_policy()
    assert policy.route == "balanced"


def test_none_route_returns_balanced():
    policy = PolicyEngine.get_route_policy(None)
    assert policy.route == "balanced"


def test_empty_dict_returns_balanced():
    policy = PolicyEngine.get_route_policy({})
    assert policy.route == "balanced"


def test_string_route_accepted():
    policy = PolicyEngine.get_route_policy("installation_spec")
    assert policy.route == "installation_spec"


def test_unknown_route_returns_default_policy():
    policy = PolicyEngine.get_route_policy({"route": "nonexistent"})
    assert policy.route == "nonexistent"
    assert policy.rule_direct_confidence == RULE_DIRECT_CONFIDENCE
    assert policy.agent_fastpath_min_candidates == 2


# ===== RoutePolicy 数据结构 =====

def test_route_policy_is_frozen():
    policy = PolicyEngine.get_route_policy()
    try:
        policy.route = "changed"
        assert False, "Should raise FrozenInstanceError"
    except AttributeError:
        pass


def test_route_policy_as_dict():
    policy = PolicyEngine.get_route_policy({"route": "balanced"})
    d = policy.as_dict()
    assert isinstance(d, dict)
    assert d["route"] == "balanced"
    assert "rule_direct_confidence" in d
    assert "agent_fastpath_score" in d
    assert "agent_fastpath_score_gap" in d
    assert "agent_fastpath_min_candidates" in d
    assert "require_param_match" in d


def test_route_policy_has_all_fields():
    policy = PolicyEngine.get_route_policy()
    assert hasattr(policy, "route")
    assert hasattr(policy, "rule_direct_confidence")
    assert hasattr(policy, "agent_fastpath_score")
    assert hasattr(policy, "agent_fastpath_score_gap")
    assert hasattr(policy, "agent_fastpath_min_candidates")
    assert hasattr(policy, "require_param_match")


# ===== installation_spec 路由 =====

def test_installation_spec_rule_direct_confidence_at_least_82():
    policy = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert policy.rule_direct_confidence >= 82


def test_installation_spec_agent_score_at_least_072():
    policy = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert policy.agent_fastpath_score >= 0.72


def test_installation_spec_score_gap_at_least_005():
    policy = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert policy.agent_fastpath_score_gap >= 0.05


def test_installation_spec_requires_param_match():
    policy = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert policy.require_param_match is True


def test_installation_spec_min_candidates_is_2():
    policy = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert policy.agent_fastpath_min_candidates == 2


# ===== material 路由 =====

def test_material_rule_direct_confidence_at_least_82():
    policy = PolicyEngine.get_route_policy({"route": "material"})
    assert policy.rule_direct_confidence >= 82


def test_material_agent_score_at_least_068():
    policy = PolicyEngine.get_route_policy({"route": "material"})
    assert policy.agent_fastpath_score >= 0.68


def test_material_requires_param_match():
    policy = PolicyEngine.get_route_policy({"route": "material"})
    assert policy.require_param_match is True


def test_material_less_strict_than_installation():
    mat = PolicyEngine.get_route_policy({"route": "material"})
    inst = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert mat.agent_fastpath_score < inst.agent_fastpath_score


# ===== ambiguous_short 路由 =====

def test_ambiguous_short_min_candidates_is_3():
    policy = PolicyEngine.get_route_policy({"route": "ambiguous_short"})
    assert policy.agent_fastpath_min_candidates == 3


def test_ambiguous_short_rule_direct_confidence_at_least_88():
    policy = PolicyEngine.get_route_policy({"route": "ambiguous_short"})
    assert policy.rule_direct_confidence >= 88


def test_ambiguous_short_agent_score_at_least_078():
    policy = PolicyEngine.get_route_policy({"route": "ambiguous_short"})
    assert policy.agent_fastpath_score >= 0.78


def test_ambiguous_short_most_strict():
    amb = PolicyEngine.get_route_policy({"route": "ambiguous_short"})
    inst = PolicyEngine.get_route_policy({"route": "installation_spec"})
    assert amb.rule_direct_confidence >= inst.rule_direct_confidence
    assert amb.agent_fastpath_score >= inst.agent_fastpath_score
    assert amb.agent_fastpath_min_candidates >= inst.agent_fastpath_min_candidates


# ===== semantic_description 路由 =====

def test_semantic_description_keeps_base_rule_direct():
    policy = PolicyEngine.get_route_policy({"route": "semantic_description"})
    assert policy.rule_direct_confidence == RULE_DIRECT_CONFIDENCE


def test_semantic_description_agent_score_at_least_058():
    policy = PolicyEngine.get_route_policy({"route": "semantic_description"})
    assert policy.agent_fastpath_score >= 0.58


def test_semantic_description_score_gap_at_least_002():
    policy = PolicyEngine.get_route_policy({"route": "semantic_description"})
    assert policy.agent_fastpath_score_gap >= 0.02


def test_semantic_description_param_match_follows_config():
    policy = PolicyEngine.get_route_policy({"route": "semantic_description"})
    # 跟随config配置，不强制覆盖
    assert isinstance(policy.require_param_match, bool)


# ===== should_use_rule_direct =====

def test_should_use_rule_direct_passes_when_confident():
    allow, threshold = PolicyEngine.should_use_rule_direct(85, {"route": "balanced"})
    assert allow is True
    assert threshold == 80


def test_should_use_rule_direct_fails_when_low():
    allow, threshold = PolicyEngine.should_use_rule_direct(70, {"route": "balanced"})
    assert allow is False


def test_should_use_rule_direct_boundary_exact():
    allow, _ = PolicyEngine.should_use_rule_direct(80, {"route": "balanced"})
    assert allow is True


def test_should_use_rule_direct_boundary_below():
    allow, _ = PolicyEngine.should_use_rule_direct(79, {"route": "balanced"})
    assert allow is False


def test_should_use_rule_direct_none_confidence():
    allow, _ = PolicyEngine.should_use_rule_direct(None, {"route": "balanced"})
    assert allow is False


def test_should_use_rule_direct_installation_spec_threshold():
    allow, threshold = PolicyEngine.should_use_rule_direct(81, {"route": "installation_spec"})
    assert allow is False
    assert threshold >= 82


def test_should_use_rule_direct_installation_spec_passes():
    allow, threshold = PolicyEngine.should_use_rule_direct(85, {"route": "installation_spec"})
    assert allow is True


def test_should_use_rule_direct_ambiguous_short_stricter():
    _, amb_threshold = PolicyEngine.should_use_rule_direct(0, {"route": "ambiguous_short"})
    _, bal_threshold = PolicyEngine.should_use_rule_direct(0, {"route": "balanced"})
    assert amb_threshold > bal_threshold


# ===== 路由间对比 =====

def test_all_routes_have_min_candidates_at_least_2():
    for route in ["balanced", "installation_spec", "material", "ambiguous_short", "semantic_description"]:
        policy = PolicyEngine.get_route_policy({"route": route})
        assert policy.agent_fastpath_min_candidates >= 2


def test_all_special_routes_stricter_than_balanced():
    balanced = PolicyEngine.get_route_policy({"route": "balanced"})
    for route in ["installation_spec", "material", "ambiguous_short"]:
        policy = PolicyEngine.get_route_policy({"route": route})
        assert policy.rule_direct_confidence >= balanced.rule_direct_confidence
