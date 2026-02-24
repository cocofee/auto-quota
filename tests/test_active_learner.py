# -*- coding: utf-8 -*-
"""
L4 主动学习模块测试
"""

import pytest

from src.active_learner import (
    mark_learning_groups,
    _select_representative,
    _make_group_label,
)


# ============================================================
# 辅助函数
# ============================================================

def _make_result(name="给水管道", dn=None, material=None,
                 specialty="C10", quota_id="C10-3-15",
                 confidence=70, source="search"):
    """构造一条模拟匹配结果"""
    params = {}
    if dn is not None:
        params["dn"] = dn
    if material is not None:
        params["material"] = material

    return {
        "bill_item": {
            "name": name,
            "specialty": specialty,
            "params": params,
        },
        "quotas": [{"quota_id": quota_id, "name": "测试"}] if quota_id else [],
        "confidence": confidence,
        "match_source": source,
        "explanation": "测试说明",
    }


# ============================================================
# 核心功能测试
# ============================================================

class TestMarkLearningGroups:
    def test_all_high_confidence_no_mark(self):
        """全部高置信度 → 不标注任何L4字段"""
        results = [
            _make_result(dn=25, confidence=90),
            _make_result(dn=25, confidence=88),
            _make_result(dn=50, confidence=92),
        ]
        mark_learning_groups(results)
        assert not any(r.get("l4_representative") for r in results)
        assert not any(r.get("l4_follower") for r in results)

    def test_multiple_uncertain_same_type(self):
        """多条同类低置信度 → 标一条代表+其余从属"""
        results = [
            _make_result(dn=25, confidence=70, source="search"),
            _make_result(dn=25, confidence=60, source="search"),
            _make_result(name="室内给水管道", dn=25, confidence=65, source="agent"),
        ]
        mark_learning_groups(results)

        # 应该有1条代表和2条从属
        reps = [r for r in results if r.get("l4_representative")]
        followers = [r for r in results if r.get("l4_follower")]
        assert len(reps) == 1
        assert len(followers) == 2

        # 所有都有组ID和组大小
        for r in results:
            assert r.get("l4_group_id") is not None
            assert r.get("l4_group_size") == 3

    def test_single_uncertain_no_group(self):
        """单条低置信度 → 不分组（保持原有[需复核]）"""
        results = [
            _make_result(dn=25, confidence=60),
            _make_result(dn=50, confidence=60),  # DN不同，不同组
        ]
        mark_learning_groups(results)
        assert not any(r.get("l4_representative") for r in results)
        assert not any(r.get("l4_follower") for r in results)

    def test_representative_is_lowest_confidence(self):
        """代表选择：置信度最低的被选中"""
        results = [
            _make_result(dn=25, confidence=75),
            _make_result(dn=25, confidence=50),  # 最低
            _make_result(dn=25, confidence=65),
        ]
        mark_learning_groups(results)

        # 第2条（confidence=50）应该是代表
        assert results[1].get("l4_representative") is True
        assert results[0].get("l4_follower") is True
        assert results[2].get("l4_follower") is True

    def test_graceful_failure(self):
        """异常数据不崩溃"""
        results = [
            {"bill_item": None, "quotas": [], "confidence": 30, "match_source": "search"},
            {"bill_item": {}, "quotas": [], "confidence": 20, "match_source": "search"},
        ]
        returned = mark_learning_groups(results)
        assert len(returned) == 2

    def test_empty_results(self):
        """空列表不崩溃"""
        assert mark_learning_groups([]) == []

    def test_mixed_confidence_levels(self):
        """混合置信度：只有低置信度的参与分组"""
        results = [
            _make_result(dn=25, confidence=90),   # 高置信度，不参与
            _make_result(dn=25, confidence=60),   # 低置信度
            _make_result(dn=25, confidence=55),   # 低置信度
        ]
        mark_learning_groups(results)

        # 第1条（90分）不应被标注
        assert not results[0].get("l4_representative")
        assert not results[0].get("l4_follower")

        # 第2和第3条应被分组
        assert results[2].get("l4_representative") is True  # 55最低
        assert results[1].get("l4_follower") is True


class TestSelectRepresentative:
    def test_prefer_lowest_confidence(self):
        """选置信度最低的"""
        members = [
            (0, {"confidence": 80, "quotas": [{"quota_id": "X"}]}),
            (1, {"confidence": 50, "quotas": [{"quota_id": "X"}]}),
            (2, {"confidence": 70, "quotas": [{"quota_id": "X"}]}),
        ]
        assert _select_representative(members) == 1

    def test_prefer_has_quota(self):
        """同置信度选有定额的"""
        members = [
            (0, {"confidence": 50, "quotas": []}),
            (1, {"confidence": 50, "quotas": [{"quota_id": "X"}]}),
        ]
        assert _select_representative(members) == 1

    def test_prefer_earlier_index(self):
        """同条件选序号小的"""
        members = [
            (5, {"confidence": 50, "quotas": [{"quota_id": "X"}]}),
            (2, {"confidence": 50, "quotas": [{"quota_id": "X"}]}),
        ]
        assert _select_representative(members) == 2


class TestMakeGroupLabel:
    def test_basic_label(self):
        """基本标签生成"""
        fp = "给水管道|C10|dn=25|material=PPR"
        label = _make_group_label(fp)
        assert "给水管道" in label
        assert "DN25" in label

    def test_no_params(self):
        """无参数只有名称"""
        fp = "阀门|C10|"
        label = _make_group_label(fp)
        assert "阀门" in label
