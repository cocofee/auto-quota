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

