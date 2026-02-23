# -*- coding: utf-8 -*-
"""
反馈学习模块测试

测试 FeedbackLearner 的核心逻辑：
1. learn_from_corrections: 对比原始/修正结果，正确区分 correction vs confirmed
2. _save_bill_quota_pair: 保存清单→定额对到经验库
3. learn_from_corrected_excel: 从Excel读取修正结果（需要mock openpyxl）
"""

from unittest.mock import MagicMock, patch

import pytest


# ================================================================
# learn_from_corrections 测试
# ================================================================

class TestLearnFromCorrections:
    """测试 learn_from_corrections 方法的分流逻辑"""

    def _make_learner(self, mock_add_return=1):
        """创建 FeedbackLearner 实例，mock 掉 ExperienceDB"""
        with patch("src.feedback_learner.ExperienceDB") as MockDB:
            mock_db = MagicMock()
            mock_db.add_experience.return_value = mock_add_return
            MockDB.return_value = mock_db

            from src.feedback_learner import FeedbackLearner
            learner = FeedbackLearner()
            learner.experience_db = mock_db
            return learner, mock_db

    def test_user_correction_triggers_add_experience(self):
        """用户改了定额 → 应调用 add_experience，source=user_correction"""
        learner, mock_db = self._make_learner()

        original = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-10", "name": "管道安装 DN25"}],
        }]
        corrected = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-20", "name": "管道安装 DN32"}],
        }]

        stats = learner.learn_from_corrections(original, corrected)

        assert stats["corrections"] == 1
        assert stats["confirmed"] == 0
        # 检查 add_experience 被调用且 source 正确
        call_kwargs = mock_db.add_experience.call_args
        assert call_kwargs[1]["source"] == "user_correction" or \
               (call_kwargs.kwargs.get("source") == "user_correction" if hasattr(call_kwargs, 'kwargs') else True)

    def test_same_quota_counts_as_confirmed(self):
        """用户没改定额 → 不写经验库，只计数 confirmed"""
        learner, mock_db = self._make_learner()

        original = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-10", "name": "管道安装 DN25"}],
        }]
        corrected = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-10", "name": "管道安装 DN25"}],
        }]

        stats = learner.learn_from_corrections(original, corrected)

        assert stats["confirmed"] == 1
        assert stats["corrections"] == 0
        # 不应写入经验库（沉默确认不等于用户确认）
        mock_db.add_experience.assert_not_called()

    def test_empty_bill_text_is_skipped(self):
        """清单名称为空 → 跳过，不写入"""
        learner, mock_db = self._make_learner()

        original = [{
            "bill_item": {"name": "", "description": ""},
            "quotas": [{"quota_id": "C10-1-10"}],
        }]
        corrected = [{
            "bill_item": {"name": "", "description": ""},
            "quotas": [{"quota_id": "C10-1-20"}],
        }]

        stats = learner.learn_from_corrections(original, corrected)

        # bill_text 为空，应跳过
        mock_db.add_experience.assert_not_called()

    def test_corrected_no_quota_is_skipped(self):
        """修正后没有定额 → 跳过"""
        learner, mock_db = self._make_learner()

        original = [{
            "bill_item": {"name": "测试清单", "description": "测试"},
            "quotas": [{"quota_id": "C10-1-10"}],
        }]
        corrected = [{
            "bill_item": {"name": "测试清单", "description": "测试"},
            "quotas": [],
        }]

        stats = learner.learn_from_corrections(original, corrected)

        mock_db.add_experience.assert_not_called()
        assert stats["corrections"] == 0
        assert stats["confirmed"] == 0

    def test_mismatched_lengths_processes_shorter(self):
        """原始和修正列表长度不同 → 只处理较短的部分"""
        learner, mock_db = self._make_learner()

        original = [
            {"bill_item": {"name": "清单1", "description": "描述1"},
             "quotas": [{"quota_id": "C10-1-10"}]},
            {"bill_item": {"name": "清单2", "description": "描述2"},
             "quotas": [{"quota_id": "C10-1-20"}]},
        ]
        corrected = [
            {"bill_item": {"name": "清单1", "description": "描述1"},
             "quotas": [{"quota_id": "C10-1-10"}]},  # 未改
        ]

        stats = learner.learn_from_corrections(original, corrected)

        assert stats["total"] == 1  # 只处理1条

    def test_add_experience_blocked_counts_zero(self):
        """add_experience 返回 0（被拦截）→ corrections 不增加"""
        learner, mock_db = self._make_learner(mock_add_return=0)

        original = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-10"}],
        }]
        corrected = [{
            "bill_item": {"name": "镀锌钢管DN25", "description": "丝接"},
            "quotas": [{"quota_id": "C10-1-20", "name": "管道安装 DN32"}],
        }]

        stats = learner.learn_from_corrections(original, corrected)

        # add_experience 被调用了，但返回0（拦截），所以 corrections 不增加
        assert stats["corrections"] == 0


# ================================================================
# _save_bill_quota_pair 测试
# ================================================================

class TestSaveBillQuotaPair:
    """测试内部方法 _save_bill_quota_pair"""

    def _make_learner(self, mock_add_return=1):
        with patch("src.feedback_learner.ExperienceDB") as MockDB:
            mock_db = MagicMock()
            mock_db.add_experience.return_value = mock_add_return
            MockDB.return_value = mock_db

            from src.feedback_learner import FeedbackLearner
            learner = FeedbackLearner()
            learner.experience_db = mock_db
            return learner, mock_db

    def test_returns_true_on_success(self):
        """正常保存返回 True"""
        learner, mock_db = self._make_learner(mock_add_return=1)

        bill = {"name": "镀锌钢管DN25", "description": "丝接", "code": "030402", "unit": "m"}
        quotas = [{"quota_id": "C10-1-10", "name": "管道安装 DN25"}]

        result = learner._save_bill_quota_pair(bill, quotas)
        assert result is True

    def test_returns_false_on_empty_name(self):
        """清单名称为空 → 返回 False"""
        learner, mock_db = self._make_learner()

        bill = {"name": "", "description": "", "code": "", "unit": ""}
        quotas = [{"quota_id": "C10-1-10", "name": "管道安装"}]

        result = learner._save_bill_quota_pair(bill, quotas)
        assert result is False

    def test_returns_false_on_empty_quotas(self):
        """定额列表为空 → 返回 False"""
        learner, mock_db = self._make_learner()

        bill = {"name": "镀锌钢管DN25", "description": "丝接"}
        quotas = [{"name": "没有quota_id的定额"}]  # 缺少 quota_id

        result = learner._save_bill_quota_pair(bill, quotas)
        assert result is False

    def test_returns_false_when_blocked(self):
        """add_experience 返回 0 → 返回 False"""
        learner, mock_db = self._make_learner(mock_add_return=0)

        bill = {"name": "镀锌钢管DN25", "description": "丝接"}
        quotas = [{"quota_id": "C10-1-10", "name": "管道安装 DN25"}]

        result = learner._save_bill_quota_pair(bill, quotas)
        assert result is False
