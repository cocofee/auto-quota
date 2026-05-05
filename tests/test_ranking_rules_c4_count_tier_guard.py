from src.ltr_ranker import LTRRanker
from src.ranking_rules.c4_count_tier_guard import apply_c4_count_tier_guard


def _candidate(quota_id: str, name: str) -> dict:
    return {
        "quota_id": quota_id,
        "name": name,
        "manual_structured_score": 0.8,
        "ltr_score": 0.8,
        "_rank_score_source": "ltr",
    }


def test_c4_count_tier_guard_prefers_nearest_upward_circuit_tier():
    item = {
        "bill_text": "配电箱1-AL 规格:7回路 安装方式:底边距地1.2m明装",
    }
    ranked = [
        _candidate("C4-4-32", "配电箱墙上(柱上)明装 规格(回路以内) 16"),
        _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8"),
        _candidate("C4-4-30", "配电箱墙上(柱上)明装 规格(回路以内) 4"),
    ]

    blocked, reason, details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is True
    assert reason == "c4_count_tier_rescued"
    assert rescued[0]["quota_id"] == "C4-4-31"
    assert details["item_param"] == 7


def test_c4_count_tier_guard_prefers_circuit_quota_over_box_body_quota():
    item = {
        "bill_text": "配电箱B1-AT-SFBU 型号:600*700*250 规格:4回路 安装方式:底边距地1.3m明装",
    }
    ranked = [
        _candidate("C4-4-37", "配电箱箱体安装 配电箱半周长(m以内) 明装 2.5"),
        _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8"),
        _candidate("C4-4-30", "配电箱墙上(柱上)明装 规格(回路以内) 4"),
    ]

    blocked, reason, _details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is True
    assert reason == "c4_count_tier_rescued"
    assert rescued[0]["quota_id"] == "C4-4-30"


def test_c4_count_tier_guard_prefers_exact_switch_gang():
    item = {
        "bill_text": "照明开关 名称:单控单联开关 规格:250V 10A 安装方式:暗装距地1.3m",
    }
    ranked = [
        _candidate("C4-4-104", "跷板式暗开关(单控) 双联"),
        _candidate("C4-4-103", "跷板式暗开关(单控) 单联"),
    ]

    blocked, reason, details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is True
    assert reason == "c4_count_tier_rescued"
    assert rescued[0]["quota_id"] == "C4-4-103"
    assert details["param_name"] == "switch_gangs"


def test_c4_count_tier_guard_maps_install_only_control_box_to_small_wall_box():
    item = {
        "bill_text": "控制箱AC-B1-WS1~4 名称:控制箱AC-B1-WS1~4 型号:设备自带控制箱 安装方式:底边距地1.2m明装 仅考虑安装费",
    }
    ranked = [
        _candidate("C4-4-36", "配电箱箱体安装 配电箱半周长(m以内) 明装 1"),
        _candidate("C4-4-32", "配电箱墙上(柱上)明装 规格(回路以内) 16"),
        _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8"),
        _candidate("C4-4-30", "配电箱墙上(柱上)明装 规格(回路以内) 4"),
    ]

    blocked, reason, details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is True
    assert reason == "c4_count_tier_rescued"
    assert rescued[0]["quota_id"] == "C4-4-30"
    assert details["family"] == "control_box_install_only"


def test_c4_count_tier_guard_maps_at_ale_box_to_dual_power_tier():
    item = {
        "bill_text": "配电箱7-AT-B1-ALE 名称:配电箱7-AT-B1-ALE 型号:500*700*220 规格:3回路 安装方式:底边距地1.2m明装",
    }
    ranked = [
        _candidate("C4-4-30", "配电箱墙上(柱上)明装 规格(回路以内) 4"),
        _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8"),
        _candidate("C4-4-36", "配电箱箱体安装 配电箱半周长(m以内) 明装 1"),
    ]

    blocked, reason, details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is True
    assert reason == "c4_count_tier_rescued"
    assert rescued[0]["quota_id"] == "C4-4-31"
    assert details["family"] == "dual_power_lighting_box"


def test_c4_count_tier_guard_ignores_box_without_explicit_count():
    item = {"bill_text": "配电箱 名称:AL 安装方式:明装"}
    ranked = [
        _candidate("C4-4-32", "配电箱墙上(柱上)明装 规格(回路以内) 16"),
        _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8"),
    ]

    blocked, reason, details, rescued = apply_c4_count_tier_guard(item, ranked)

    assert blocked is False
    assert reason == ""
    assert details["intent"] == "no_c4_count_intent"
    assert rescued == ranked


def test_ltr_guard_uses_registered_c4_count_tier_guard():
    item = {
        "bill_text": "配电箱1-AL 规格:7回路 安装方式:底边距地1.2m明装",
    }
    incumbent = _candidate("C4-4-31", "配电箱墙上(柱上)明装 规格(回路以内) 8")
    challenger = _candidate("C4-4-32", "配电箱墙上(柱上)明装 规格(回路以内) 16")

    guarded, meta = LTRRanker._apply_ltr_guard(item, [incumbent], [challenger, incumbent])

    assert guarded[0]["quota_id"] == "C4-4-31"
    assert guarded[0]["ltr_guard_blocked"] is True
    assert meta["action"] == "blocked"
    assert meta["reason"] == "c4_count_tier_rescued"
    assert meta["registered_ranking_guards"]["blocked"] is True
