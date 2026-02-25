"""测试向量/LLM 熔断机制。

背景：
  向量模型不可用时，encode/search 会反复触发 NoneType.encode 异常（6479次/轮）。
  LLM 网络异常时，每条清单都重试 + 低置信度再重试，放大失败开销。
  修复后：
  - 向量模型 None 时快速返回空结果，只警告一次
  - LLM 连续失败 5 次后熔断，剩余清单走确定性兜底

复现证据：
  历史稳定性问题记录（已归档/清理）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_matcher():
    """创建一个带完整实例变量的 AgentMatcher（跳过网络初始化）"""
    from src.agent_matcher import AgentMatcher
    matcher = AgentMatcher.__new__(AgentMatcher)
    matcher.llm_type = "deepseek"
    matcher._client = None
    matcher.province = "测试省份"
    matcher.notebook = MagicMock()
    # 实例级熔断器状态（__new__ 跳过了 __init__，需要手动初始化）
    matcher._llm_consecutive_fails = 0
    matcher._llm_circuit_open = False
    matcher._llm_circuit_open_time = 0.0
    return matcher


class TestVectorModelUnavailableGuard:
    """测试向量模型不可用时的快速跳过"""

    def setup_method(self):
        """每个测试前重置类级标志"""
        from src.vector_engine import VectorEngine
        VectorEngine._model_unavailable_warned = False
        VectorEngine._model_skip_count = 0

    def test_encode_queries_returns_none_list_when_model_none(self):
        """model=None时，encode_queries 返回 [None]*N 而非崩溃"""
        from src.vector_engine import VectorEngine
        engine = VectorEngine.__new__(VectorEngine)
        engine._model = None  # 模拟模型不可用

        # mock ModelCache 返回 None
        with patch("src.vector_engine.VectorEngine.model", new_callable=lambda: property(lambda self: None)):
            result = engine.encode_queries(["测试查询1", "测试查询2"])

        assert result == [None, None]
        assert VectorEngine._model_skip_count >= 1

    def test_encode_queries_warns_only_once(self):
        """多次调用只警告一次（不刷屏）"""
        from src.vector_engine import VectorEngine
        engine = VectorEngine.__new__(VectorEngine)
        engine._model = None

        with patch("src.vector_engine.VectorEngine.model", new_callable=lambda: property(lambda self: None)):
            engine.encode_queries(["q1"])
            warned_after_first = VectorEngine._model_unavailable_warned
            engine.encode_queries(["q2"])
            engine.encode_queries(["q3"])

        assert warned_after_first is True
        assert VectorEngine._model_skip_count == 3  # 3次调用都被计数


class TestExperienceDBModelGuard:
    """测试经验库向量搜索在模型不可用时的快速跳过"""

    def test_search_similar_returns_empty_when_model_none(self):
        """model=None时，search_similar 返回空（不崩溃）"""
        from src.experience_db import ExperienceDB
        db = ExperienceDB.__new__(ExperienceDB)
        db._model = None

        # mock model 属性返回 None，collection.count 返回 > 0
        with patch.object(type(db), "model", new_callable=lambda: property(lambda self: None)):
            mock_collection = MagicMock()
            mock_collection.count.return_value = 10
            with patch.object(type(db), "collection", new_callable=lambda: property(lambda self: mock_collection)):
                # mock _connect 和 SQLite 查询（精确匹配路径）
                with patch.object(db, "_connect") as mock_conn:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = None  # 无精确匹配
                    mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                    mock_conn.return_value.__exit__ = MagicMock(return_value=False)

                    result = db.search_similar("DN25镀锌钢管", province="test")

        assert result == []  # 模型不可用，向量搜索跳过，返回空


class TestLLMCircuitBreaker:
    """测试LLM连续失败熔断机制（实例级隔离）"""

    def test_circuit_opens_after_consecutive_failures(self):
        """连续5次LLM失败后，熔断器打开"""
        matcher = _make_matcher()

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        # mock _build_agent_prompt 和 _call_llm，让 _call_llm 总是抛异常
        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm", side_effect=Exception("网络超时")):
                for i in range(5):
                    result = matcher.match_single(bill, candidates)
                    assert result.get("match_source") in ("agent_fallback", "agent")

        # 5次后熔断器打开（实例级）
        assert matcher._llm_circuit_open is True
        assert matcher._llm_consecutive_fails == 5

    def test_circuit_open_skips_llm(self):
        """熔断打开且仍在冷却期内，match_single 不再调用 _call_llm"""
        import time as _time
        matcher = _make_matcher()
        matcher._llm_circuit_open = True  # 直接打开熔断
        matcher._llm_circuit_open_time = _time.time()  # 刚刚熔断，仍在冷却期

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm") as mock_llm:
                result = matcher.match_single(bill, candidates)
                mock_llm.assert_not_called()  # LLM不应被调用

        assert result.get("match_source") == "agent_circuit_break"

    def test_success_resets_fail_count(self):
        """LLM成功一次后，连续失败计数归零"""
        matcher = _make_matcher()
        matcher._llm_consecutive_fails = 3  # 已失败3次

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        # mock成功的LLM响应
        mock_response = '{"main_quota_index": 1, "main_quota_id": "C1-1", "confidence": 85, "explanation": "ok"}'
        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm", return_value=mock_response):
                result = matcher.match_single(bill, candidates)

        assert matcher._llm_consecutive_fails == 0  # 重置
        assert matcher._llm_circuit_open is False

    def test_instance_isolation(self):
        """实例A触发熔断不影响实例B（多用户隔离核心测试）"""
        matcher_a = _make_matcher()
        matcher_b = _make_matcher()

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        # A连续失败5次触发熔断
        with patch.object(matcher_a, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher_a, "_call_llm", side_effect=Exception("网络超时")):
                for i in range(5):
                    matcher_a.match_single(bill, candidates)

        assert matcher_a._llm_circuit_open is True

        # B应该完全不受影响
        assert matcher_b._llm_circuit_open is False
        assert matcher_b._llm_consecutive_fails == 0

        # B仍然可以正常调用LLM
        mock_response = '{"main_quota_index": 1, "main_quota_id": "C1-1", "confidence": 85, "explanation": "ok"}'
        with patch.object(matcher_b, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher_b, "_call_llm", return_value=mock_response) as mock_llm:
                result = matcher_b.match_single(bill, candidates)
                mock_llm.assert_called_once()  # B的LLM正常被调用

        assert result.get("match_source") == "agent"


class TestDegradationSummary:
    """测试降级统计汇总接口"""

    def test_get_degradation_summary_returns_dict(self):
        """get_degradation_summary 返回有效的统计字典"""
        from src.model_cache import ModelCache
        summary = ModelCache.get_degradation_summary()
        assert "vector_available" in summary
        assert "reranker_available" in summary
        assert "vector_skip_count" in summary
        assert "llm_circuit_open" in summary
        assert "llm_consecutive_fails" in summary

    def test_get_degradation_summary_with_agent_matcher(self):
        """传入 agent_matcher 实例时返回其熔断状态"""
        from src.model_cache import ModelCache
        matcher = _make_matcher()
        matcher._llm_circuit_open = True
        matcher._llm_consecutive_fails = 3

        summary = ModelCache.get_degradation_summary(agent_matcher=matcher)
        assert summary["llm_circuit_open"] is True
        assert summary["llm_consecutive_fails"] == 3


class TestCircuitBreakerReset:
    """测试熔断器重置机制"""

    def test_reset_circuit_breaker(self):
        """reset_circuit_breaker() 将熔断状态归零"""
        matcher = _make_matcher()
        # 模拟已熔断状态
        matcher._llm_circuit_open = True
        matcher._llm_consecutive_fails = 5

        matcher.reset_circuit_breaker()

        assert matcher._llm_circuit_open is False
        assert matcher._llm_consecutive_fails == 0

    def test_new_instance_has_clean_state(self):
        """新实例自带干净的熔断器状态（替代旧的 reset_circuit_breaker classmethod）"""
        matcher_old = _make_matcher()
        matcher_old._llm_circuit_open = True
        matcher_old._llm_consecutive_fails = 5

        # 新实例应有干净状态
        matcher_new = _make_matcher()

        assert matcher_new._llm_circuit_open is False
        assert matcher_new._llm_consecutive_fails == 0

        # 新实例能正常调用 LLM
        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        mock_response = '{"main_quota_index": 1, "main_quota_id": "C1-1", "confidence": 85, "explanation": "ok"}'
        with patch.object(matcher_new, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher_new, "_call_llm", return_value=mock_response) as mock_llm:
                result = matcher_new.match_single(bill, candidates)
                mock_llm.assert_called_once()  # LLM 被正常调用（未被熔断跳过）

        assert result.get("match_source") == "agent"

    def test_half_open_after_cooldown(self):
        """cooldown 过后，熔断器进入半开状态，允许一次 LLM 试探"""
        import time as _time
        matcher = _make_matcher()

        # 模拟很久以前就已熔断（远超 cooldown）
        matcher._llm_circuit_open = True
        matcher._llm_consecutive_fails = 5
        matcher._llm_circuit_open_time = _time.time() - 999  # 999秒前

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        mock_response = '{"main_quota_index": 1, "main_quota_id": "C1-1", "confidence": 85, "explanation": "ok"}'
        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm", return_value=mock_response) as mock_llm:
                result = matcher.match_single(bill, candidates)
                mock_llm.assert_called_once()  # 半开允许调用

        # 成功后熔断器应已关闭
        assert matcher._llm_circuit_open is False
        assert matcher._llm_consecutive_fails == 0
        assert result.get("match_source") == "agent"

    def test_half_open_failure_re_trips(self):
        """半开试探失败后，熔断器重新关闭（更新 cooldown 计时器）"""
        import time as _time
        matcher = _make_matcher()

        matcher._llm_circuit_open = True
        matcher._llm_consecutive_fails = 5
        matcher._llm_circuit_open_time = _time.time() - 999  # 已过 cooldown

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm", side_effect=Exception("仍然超时")):
                result = matcher.match_single(bill, candidates)

        # 仍然是熔断状态，且 cooldown 计时器已更新
        assert matcher._llm_circuit_open is True
        assert matcher._llm_consecutive_fails == 6
        assert matcher._llm_circuit_open_time > _time.time() - 5  # 刚刚更新的
        assert result.get("match_source") == "agent_fallback"
