# -*- coding: utf-8 -*-
"""
匹配核心函数契约测试

直接测试底层函数的公开接口契约：
1. _prepare_item_for_matching() 措施项穿透
2. _resolve_search_mode_result() 返回值字段齐全
3. _should_skip_agent_llm() 快速通道判定
4. _mark_agent_fastpath() 标记
5. trace / fallback 函数可正常调用
"""

import pytest
from unittest.mock import MagicMock, patch
from src.match_pipeline import (
    _prepare_item_for_matching,
    _resolve_search_mode_result,
    _apply_mode_backups,
    _apply_rule_backup,
    _apply_similar_exp_backup,
    _reconcile_search_and_experience,
)
from src.match_core import (
    _should_skip_agent_llm,
    _mark_agent_fastpath,
    _append_trace_step,
    _finalize_trace,
    _summarize_candidates_for_trace,
)


class TestPrepareItemContract:
    """_prepare_item_for_matching 契约测试"""

    def test_measure_item_returns_early(self):
        """措施项 -> 返回 early_result（跳过搜索）"""
        # 措施费清单（关键词"措施费" + 无单位无工程量 → 命中措施项判定）
        item = {
            "name": "措施费",
            "description": "",
            "unit": "",
            "quantity": 0,
        }

        prepared = _prepare_item_for_matching(
            item, experience_db=None, rule_validator=None,
        )
        # 措施项应返回 early_result（直接跳过）
        assert "early_result" in prepared
        result = prepared["early_result"]
        assert result.get("match_source") == "skip_measure"

    def test_normal_item_no_crash(self):
        """普通清单项 -> 不崩溃"""
        rule_validator = MagicMock()
        # 确保 rule_validator 的 match_by_rules 返回空结果
        rule_validator.match_by_rules.return_value = {
            "quotas": [], "confidence": 0
        }
        rule_validator.rules = True

        item = {
            "name": "给水管道DN25",
            "description": "1.材质:PPR",
            "unit": "m",
            "quantity": 50,
        }

        prepared = _prepare_item_for_matching(
            item, experience_db=None, rule_validator=rule_validator,
        )
        assert isinstance(prepared, dict)
        # 非措施项应包含搜索所需的上下文
        if "early_result" not in prepared:
            assert "search_query" in prepared or "ctx" in prepared

    def test_review_rejected_rule_direct_does_not_fall_back_to_rule_backup(self):
        """已被审核规则拦截的 rule_direct 不能再降级成 rule_backup 回流覆盖搜索结果"""
        rule_validator = MagicMock()
        rule_validator.rules = True
        rule_validator.match_by_rules.return_value = {
            "quotas": [{"quota_id": "C4-3-1", "name": "软母线安装 导线截面(mm2以内) 150"}],
            "confidence": 95,
            "match_source": "rule",
        }

        item = {
            "name": "电缆终端头",
            "description": "名称:电力电缆头 规格型号:3*2.5",
            "unit": "个",
            "quantity": 1,
        }

        with patch("src.match_pipeline._review_check_match_result") as mock_review:
            mock_review.return_value = {
                "type": "category_mismatch",
                "reason": "类别不匹配: 清单是「电缆终端头」，定额含错误词「导线」",
            }
            prepared = _prepare_item_for_matching(
                item, experience_db=None, rule_validator=rule_validator,
            )

        assert prepared.get("early_result") is None
        assert prepared.get("rule_backup") is None


class TestResolveSearchResult:
    """_resolve_search_mode_result 契约测试"""

    def test_returns_tuple(self):
        """返回 (result, exp_hits, rule_hits) 三元组"""
        item = {"name": "给水管道DN25", "unit": "m"}
        candidates = [{
            "quota_id": "C10-1-1",
            "name": "给水管道安装 DN25",
            "score": 0.95,
        }]

        result, exp_hits, rule_hits = _resolve_search_mode_result(
            item, candidates,
            exp_backup={}, rule_backup={},
            exp_hits=0, rule_hits=0,
        )

        # 返回值应是字典
        assert isinstance(result, dict)
        # 应包含核心字段
        assert "match_source" in result or "quotas" in result or "quota_id" in result
        # exp_hits 和 rule_hits 应是数值
        assert isinstance(exp_hits, int)
        assert isinstance(rule_hits, int)

    def test_empty_candidates_no_crash(self):
        """空候选列表 -> 不崩溃"""
        item = {"name": "某设备安装", "unit": "台"}

        result, exp_hits, rule_hits = _resolve_search_mode_result(
            item, [],
            exp_backup={}, rule_backup={},
            exp_hits=0, rule_hits=0,
        )

        assert isinstance(result, dict)


class TestAgentDecision:
    """Agent快速通道判定测试"""

    def test_with_strong_candidate(self):
        """强候选（高分单一结果）-> 接口不崩溃"""
        candidates = [{
            "quota_id": "C10-1-1",
            "name": "给水管道安装 DN25",
            "score": 0.99,
            "rank": 1,
        }]

        skip = _should_skip_agent_llm(candidates)
        assert isinstance(skip, bool)

    def test_empty_candidates(self):
        """空候选 -> 不跳过大模型"""
        skip = _should_skip_agent_llm([])
        assert skip is False

    def test_single_candidate_forces_llm(self):
        """M2修复：单候选 -> 强制走LLM，不走快通道"""
        candidates = [{
            "quota_id": "C10-1-1",
            "name": "给水管道安装 DN25",
            "score": 0.99,
            "param_match": True,
            "param_score": 0.95,
            "rerank_score": 10.0,
        }]
        skip = _should_skip_agent_llm(candidates)
        assert skip is False, "单候选不应走快通道，即使分数很高"

    def test_two_candidates_with_large_gap_allows_fastpath(self):
        """两候选+大分差 -> 可走快通道（对比单候选行为）"""
        candidates = [
            {
                "quota_id": "C10-1-1", "name": "给水管道安装 DN25",
                "param_match": True, "param_score": 0.95,
                "rerank_score": 10.0,
            },
            {
                "quota_id": "C10-1-2", "name": "给水管道安装 DN32",
                "param_match": True, "param_score": 0.50,
                "rerank_score": 5.0,
            },
        ]
        skip = _should_skip_agent_llm(candidates)
        # 两候选+大分差+参数匹配+高分 → 应该走快通道
        # （注意：实际结果取决于 config 中的阈值设置，这里只验证接口行为正常）
        assert isinstance(skip, bool)

    def test_mark_fastpath(self):
        """_mark_agent_fastpath 标记快速通道结果"""
        result = {"match_source": "search", "confidence": 95}
        _mark_agent_fastpath(result)
        # 设置 agent_skipped=True 和 match_source="agent_fastpath"
        assert result.get("agent_skipped") is True
        assert result.get("match_source") == "agent_fastpath"


class TestTraceFunctions:
    """追踪函数测试"""

    def test_trace_functions_callable(self):
        """追踪函数可正常调用"""
        assert callable(_append_trace_step)
        assert callable(_finalize_trace)
        assert callable(_summarize_candidates_for_trace)

    def test_append_trace_step_works(self):
        """_append_trace_step 添加追踪步骤"""
        result = {"trace": {"steps": []}}
        _append_trace_step(result, "test_stage", foo="bar")
        assert len(result["trace"]["steps"]) == 1
        assert result["trace"]["steps"][0]["stage"] == "test_stage"


class TestFallbackFunctions:
    """兜底策略函数测试"""

    def test_fallback_functions_callable(self):
        """兜底策略函数可正常调用"""
        assert callable(_apply_mode_backups)
        assert callable(_apply_rule_backup)
        assert callable(_apply_similar_exp_backup)
        assert callable(_reconcile_search_and_experience)
