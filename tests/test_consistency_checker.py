# -*- coding: utf-8 -*-
"""
L3 一致性反思模块测试
"""

import pytest

from src.consistency_checker import (
    _normalize_core_name,
    _build_fingerprint,
    _compute_vote_weight,
    _quota_signature,
    check_and_fix,
)


# ============================================================
# 辅助函数：快速构造测试用 result
# ============================================================

def _make_result(name="给水管道", dn=None, material=None, connection=None,
                 cable_section=None, cable_type=None,
                 specialty="C10", quota_id="C10-3-15", quota_name="测试定额",
                 confidence=85, source="agent"):
    """构造一条模拟匹配结果"""
    params = {}
    if dn is not None:
        params["dn"] = dn
    if material is not None:
        params["material"] = material
    if connection is not None:
        params["connection"] = connection
    if cable_section is not None:
        params["cable_section"] = cable_section

    item = {
        "name": name,
        "description": "",
        "specialty": specialty,
        "params": params,
    }
    if cable_type:
        item["cable_type"] = cable_type

    return {
        "bill_item": item,
        "quotas": [{"quota_id": quota_id, "name": quota_name}] if quota_id else [],
        "confidence": confidence,
        "match_source": source,
        "explanation": "测试",
    }


# ============================================================
# 名称标准化测试
# ============================================================

class TestNormalizeCoreName:
    def test_remove_location_prefix(self):
        assert _normalize_core_name("室内给水管道") == "给水管道"
        assert _normalize_core_name("室外排水管道") == "排水管道"

    def test_remove_action_suffix(self):
        assert _normalize_core_name("给水管道安装") == "给水管道"
        assert _normalize_core_name("电缆敷设") == "电缆"

    def test_remove_both(self):
        assert _normalize_core_name("室内给水管道安装") == "给水管道"

    def test_keep_short_name(self):
        # 名称太短时不去后缀（避免空字符串）
        assert _normalize_core_name("安装") == "安装"

    def test_empty(self):
        assert _normalize_core_name("") == ""
        assert _normalize_core_name(None) == ""


# ============================================================
# 指纹测试
# ============================================================

class TestBuildFingerprint:
    def test_same_item_same_fingerprint(self):
        """相同清单应该生成相同指纹"""
        item1 = {"name": "给水管道", "specialty": "C10",
                 "params": {"dn": 25, "material": "PPR"}}
        item2 = {"name": "给水管道", "specialty": "C10",
                 "params": {"dn": 25, "material": "PPR"}}
        assert _build_fingerprint(item1) == _build_fingerprint(item2)

    def test_different_dn_different_fingerprint(self):
        """不同管径应该生成不同指纹"""
        item1 = {"name": "给水管道", "specialty": "C10",
                 "params": {"dn": 25}}
        item2 = {"name": "给水管道", "specialty": "C10",
                 "params": {"dn": 50}}
        assert _build_fingerprint(item1) != _build_fingerprint(item2)

    def test_location_prefix_normalized(self):
        """室内/室外前缀归一化后指纹相同"""
        item1 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 25}}
        item2 = {"name": "室内给水管道", "specialty": "C10", "params": {"dn": 25}}
        assert _build_fingerprint(item1) == _build_fingerprint(item2)


# ============================================================
# 核心功能测试
# ============================================================

class TestCheckAndFix:
    def test_consistent_group_no_correction(self):
        """同类清单匹配一致 → 不纠正"""
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90),
            _make_result(dn=25, quota_id="C10-3-15", confidence=85),
            _make_result(dn=25, quota_id="C10-3-15", confidence=80),
        ]
        check_and_fix(results)
        assert not any(r.get("reflection_corrected") for r in results)

    def test_majority_correction(self):
        """多数票纠正少数派"""
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90, source="agent"),
            _make_result(dn=25, quota_id="C10-3-15", confidence=85, source="agent"),
            _make_result(name="室内给水管道", dn=25, quota_id="C10-3-18",
                         confidence=65, source="search"),
        ]
        check_and_fix(results)

        # 第3条应该被纠正为 C10-3-15
        assert results[2]["quotas"][0]["quota_id"] == "C10-3-15"
        assert results[2].get("reflection_corrected") is True
        assert results[2].get("reflection_old_quota") == "C10-3-18"
        # 前两条不应被改
        assert not results[0].get("reflection_corrected")
        assert not results[1].get("reflection_corrected")

    def test_high_confidence_protection(self):
        """高置信度结果（>=90）不被纠正"""
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=92,
                         source="experience_exact"),
            _make_result(dn=25, quota_id="C10-3-18", confidence=60, source="search"),
            _make_result(dn=25, quota_id="C10-3-18", confidence=55, source="search"),
        ]
        check_and_fix(results)

        # 经验库结果（92分）不被推翻
        assert results[0]["quotas"][0]["quota_id"] == "C10-3-15"
        assert not results[0].get("reflection_corrected")

    def test_different_params_different_group(self):
        """参数不同不算同类，不检查一致性"""
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90),
            _make_result(dn=50, quota_id="C10-3-16", confidence=90),
        ]
        check_and_fix(results)
        # 两条都不应被纠正
        assert not any(r.get("reflection_corrected") for r in results)

    def test_graceful_failure(self):
        """异常数据不崩溃"""
        # bill_item 为空
        results = [
            {"bill_item": {}, "quotas": [], "confidence": 0, "match_source": "search"},
            {"bill_item": None, "quotas": [], "confidence": 0, "match_source": "search"},
        ]
        returned = check_and_fix(results)
        assert len(returned) == 2  # 原样返回

    def test_single_item_no_check(self):
        """只有一条不需要检查"""
        results = [_make_result(dn=25, quota_id="C10-3-15")]
        check_and_fix(results)
        assert not results[0].get("reflection_corrected")

    def test_empty_results(self):
        """空列表不崩溃"""
        assert check_and_fix([]) == []


class TestVoteWeight:
    def test_experience_highest(self):
        """经验库精确匹配权重最高"""
        r_exp = _make_result(source="experience_exact", confidence=90)
        r_search = _make_result(source="search", confidence=90)
        assert _compute_vote_weight(r_exp) > _compute_vote_weight(r_search)

    def test_confidence_matters(self):
        """同来源下高置信度权重更高"""
        r_high = _make_result(source="agent", confidence=95)
        r_low = _make_result(source="agent", confidence=50)
        assert _compute_vote_weight(r_high) > _compute_vote_weight(r_low)

    def test_long_prefix_matched_first(self):
        """长前缀优先匹配：agent_fastpath=1.5，不被agent=2.0抢走"""
        r_fastpath = _make_result(source="agent_fastpath", confidence=100)
        r_agent = _make_result(source="agent", confidence=100)
        assert _compute_vote_weight(r_fastpath) == 1.5
        assert _compute_vote_weight(r_agent) == 2.0

    def test_experience_confirmed_distinct(self):
        """experience_exact_confirmed 和 experience_exact 权重不同"""
        r_confirmed = _make_result(source="experience_exact_confirmed", confidence=100)
        r_exact = _make_result(source="experience_exact", confidence=100)
        assert _compute_vote_weight(r_confirmed) == 3.5
        assert _compute_vote_weight(r_exact) == 5.0
