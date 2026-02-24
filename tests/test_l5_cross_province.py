# -*- coding: utf-8 -*-
"""
L5 跨省迁移学习测试
"""

import pytest
from unittest.mock import patch, MagicMock

import config


# ============================================================
# Direction A: 通用知识库同步测试
# ============================================================

class TestUniversalKBSync:
    def test_diff_learner_sync_called_on_correction(self):
        """diff_learner 修正后应调用通用知识库同步"""
        from src.diff_learner import DiffLearner
        learner = DiffLearner()

        mock_kb = MagicMock()
        with patch("src.diff_learner.config") as mock_config:
            mock_config.UNIVERSAL_KB_SYNC_ENABLED = True
            mock_config.get_current_province.return_value = "北京2024"
            with patch("src.universal_kb.UniversalKB", return_value=mock_kb):
                learner._sync_to_universal_kb(
                    "给水管道安装 DN25",
                    ["管道安装 镀锌钢管 DN25", "管卡安装"],
                    "北京2024",
                )
        mock_kb.learn_from_correction.assert_called_once_with(
            bill_text="给水管道安装 DN25",
            quota_names=["管道安装 镀锌钢管 DN25", "管卡安装"],
            province="北京2024",
        )

    def test_sync_skipped_when_disabled(self):
        """配置关闭时不同步"""
        from src.diff_learner import DiffLearner
        learner = DiffLearner()

        mock_kb = MagicMock()
        with patch.object(config, "UNIVERSAL_KB_SYNC_ENABLED", False):
            with patch("src.universal_kb.UniversalKB", return_value=mock_kb):
                learner._sync_to_universal_kb(
                    "给水管道", ["管道安装"], "北京2024")
        mock_kb.learn_from_correction.assert_not_called()

    def test_sync_skipped_when_no_quota_names(self):
        """无定额名称时不同步"""
        from src.diff_learner import DiffLearner
        learner = DiffLearner()

        mock_kb = MagicMock()
        with patch.object(config, "UNIVERSAL_KB_SYNC_ENABLED", True):
            with patch("src.universal_kb.UniversalKB", return_value=mock_kb):
                learner._sync_to_universal_kb(
                    "给水管道", [], "北京2024")
        mock_kb.learn_from_correction.assert_not_called()

    def test_sync_failure_does_not_raise(self):
        """通用知识库同步失败不抛异常"""
        from src.diff_learner import DiffLearner
        learner = DiffLearner()

        with patch.object(config, "UNIVERSAL_KB_SYNC_ENABLED", True):
            with patch("src.universal_kb.UniversalKB",
                       side_effect=Exception("数据库连接失败")):
                # 不应抛异常
                learner._sync_to_universal_kb(
                    "给水管道", ["管道安装"], "北京2024")

    def test_feedback_learner_sync_called(self):
        """feedback_learner 也有同步方法且可正常调用"""
        from src.feedback_learner import FeedbackLearner
        learner = FeedbackLearner()

        mock_kb = MagicMock()
        with patch.object(config, "UNIVERSAL_KB_SYNC_ENABLED", True):
            with patch("src.universal_kb.UniversalKB", return_value=mock_kb):
                learner._sync_to_universal_kb(
                    "配电箱安装",
                    ["配电箱安装 暗装"],
                    "北京2024",
                )
        mock_kb.learn_from_correction.assert_called_once()


# ============================================================
# Direction B: 跨省搜索测试
# ============================================================

class TestCrossProvinceSearch:
    def test_cross_province_hints_stored_on_item(self):
        """跨省搜索结果存到item上作为提示"""
        from src.match_core import try_experience_match

        mock_exp_db = MagicMock()
        # 本省搜索返回空
        mock_exp_db.search_similar.return_value = []
        # 跨省搜索返回参考
        mock_exp_db.search_cross_province.return_value = [
            {"quota_names": ["管道安装 镀锌钢管"], "similarity": 0.85,
             "source_province": "天津2024", "confidence": 90},
        ]

        item = {"name": "给水管道", "description": "DN25"}

        with patch.object(config, "CROSS_PROVINCE_WARMUP_ENABLED", True):
            result = try_experience_match(
                "给水管道 DN25", item, mock_exp_db, province="广东2024")

        # 不直通（返回None）
        assert result is None
        # 但item上有跨省提示
        assert "_cross_province_hints" in item
        assert "管道安装 镀锌钢管" in item["_cross_province_hints"]

    def test_cross_province_disabled_no_hints(self):
        """跨省预热关闭时item上没有提示"""
        from src.match_core import try_experience_match

        mock_exp_db = MagicMock()
        mock_exp_db.search_similar.return_value = []

        item = {"name": "给水管道", "description": "DN25"}

        with patch.object(config, "CROSS_PROVINCE_WARMUP_ENABLED", False):
            result = try_experience_match(
                "给水管道 DN25", item, mock_exp_db, province="广东2024")

        assert result is None
        assert "_cross_province_hints" not in item

    def test_cross_province_not_triggered_when_local_hit(self):
        """本省经验命中时不触发跨省搜索"""
        from src.match_core import try_experience_match

        mock_exp_db = MagicMock()
        # 本省搜索命中
        mock_exp_db.search_similar.return_value = [{
            "match_type": "exact",
            "quota_ids": ["C10-1-10"],
            "quota_names": ["管道安装"],
            "confidence": 95,
            "similarity": 1.0,
            "confirm_count": 3,
            "materials": "[]",
        }]

        item = {"name": "给水管道", "params": {"dn": 25}}
        mock_validator = MagicMock()
        mock_validator.validate_single.return_value = []

        with patch.object(config, "CROSS_PROVINCE_WARMUP_ENABLED", True):
            result = try_experience_match(
                "给水管道 DN25", item, mock_exp_db,
                rule_validator=mock_validator, province="北京2024")

        # 本省命中时不调用跨省搜索
        mock_exp_db.search_cross_province.assert_not_called()

    def test_search_query_enhanced_with_hints(self):
        """跨省提示被追加到搜索查询中"""
        from src.match_core import _prepare_candidates_from_prepared

        # 模拟 prepared 上下文
        item = {
            "name": "给水管道",
            "_cross_province_hints": ["管道安装 镀锌钢管", "管卡安装"],
        }
        prepared = {
            "ctx": {
                "full_query": "给水管道 DN25",
                "search_query": "给水管道 DN25",
                "item": item,
            },
            "classification": {"primary": "C10", "fallbacks": []},
            "exp_backup": None,
            "rule_backup": None,
        }

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        mock_reranker = None
        mock_validator = MagicMock()
        mock_validator.validate_candidates.return_value = []

        with patch("src.match_core.cascade_search", return_value=[]) as mock_cascade:
            _prepare_candidates_from_prepared(
                prepared, mock_searcher, mock_reranker, mock_validator)
            # 验证传给 cascade_search 的 search_query 包含跨省提示
            call_args = mock_cascade.call_args
            actual_query = call_args[0][1]  # 第2个位置参数是 search_query
            assert "管道安装 镀锌钢管" in actual_query
            assert "管卡安装" in actual_query
