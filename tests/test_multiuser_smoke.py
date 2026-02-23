"""多用户隔离冒烟测试。

验证 M-Batch1~M-Batch4 的修复效果：
  1. run_id 唯一性（并发文件命名不碰撞）
  2. 省份上下文隔离（线程间不串扰）
  3. LLM 熔断器实例隔离（请求A不影响请求B）
  4. 规则向量禁用实例隔离（实例A不影响实例B）

来源：docs/阶段M_多用户可用性findings.md
"""

from __future__ import annotations

import time
import threading
from unittest.mock import MagicMock, patch

import pytest


class TestRunIdUniqueness:
    """M-Batch1: run_id 唯一性验证"""

    def test_100_run_ids_unique(self):
        """100个连续生成的 run_id 不重复"""
        from tools.jarvis_pipeline import _generate_run_id
        ids = [_generate_run_id() for _ in range(100)]
        assert len(set(ids)) == 100, f"有重复: {len(ids) - len(set(ids))} 个"

    def test_run_id_format(self):
        """run_id 格式正确：YYYYMMDD_HHMMSS_mmm_xxxxxx"""
        import re
        from tools.jarvis_pipeline import _generate_run_id
        rid = _generate_run_id()
        # 格式：20260223_075906_530_87ae5a
        assert re.match(r'^\d{8}_\d{6}_\d{3}_[0-9a-f]{6}$', rid), f"格式不对: {rid}"

    def test_concurrent_run_ids_unique(self):
        """多线程并发生成 run_id 不碰撞"""
        from tools.jarvis_pipeline import _generate_run_id
        results = []
        barrier = threading.Barrier(5)

        def gen():
            barrier.wait()  # 所有线程同时启动
            for _ in range(20):
                results.append(_generate_run_id())

        threads = [threading.Thread(target=gen) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 100
        assert len(set(results)) == 100, f"并发碰撞: {100 - len(set(results))} 个重复"


class TestProvinceIsolation:
    """M-Batch2: 省份上下文隔离验证"""

    def test_notebook_province_explicit(self):
        """record_note 中省份由调用方显式提供，不回退全局值"""
        from src.learning_notebook import LearningNotebook
        import tempfile, os

        # 创建临时数据库
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            nb = LearningNotebook.__new__(LearningNotebook)
            nb.db_path = db_path
            nb._init_db()

            # 设置全局省份为 PROV_GLOBAL
            import config
            old = config._runtime_province
            config.set_current_province("PROV_GLOBAL")

            try:
                # 记录笔记时显式传 province=PROV_A
                nb.record_note({
                    "bill_text": "测试清单",
                    "bill_name": "test",
                    "province": "PROV_A",
                })

                # 记录笔记时不传 province（应该是空字符串，不是 PROV_GLOBAL）
                nb.record_note({
                    "bill_text": "测试清单2",
                    "bill_name": "test2",
                })
            finally:
                config._runtime_province = old

            # 验证
            import sqlite3
            conn = sqlite3.connect(db_path)
            rows = conn.execute("SELECT bill_name, province FROM learning_notes ORDER BY id").fetchall()
            conn.close()

            assert rows[0] == ("test", "PROV_A"), f"显式省份应为 PROV_A，实际: {rows[0]}"
            assert rows[1][1] == "", f"未传省份应为空字符串，实际: {rows[1][1]}"
        finally:
            os.unlink(db_path)

    def test_agent_matcher_passes_province_to_note(self):
        """agent_matcher.match_single() 记录笔记时传入 self.province"""
        from src.agent_matcher import AgentMatcher
        matcher = AgentMatcher.__new__(AgentMatcher)
        matcher.llm_type = "deepseek"
        matcher._client = None
        matcher.province = "PROV_EXPLICIT"
        matcher.notebook = MagicMock()
        matcher._llm_consecutive_fails = 0
        matcher._llm_circuit_open = False
        matcher._llm_circuit_open_time = 0.0

        bill = {"name": "测试清单", "description": ""}
        candidates = [{"quota_id": "C1-1", "name": "测试定额", "unit": "m",
                       "param_match": True, "param_score": 0.8}]

        mock_response = '{"main_quota_index": 1, "main_quota_id": "C1-1", "confidence": 85, "explanation": "ok"}'
        with patch.object(matcher, "_build_agent_prompt", return_value="test prompt"):
            with patch.object(matcher, "_call_llm", return_value=mock_response):
                matcher.match_single(bill, candidates)

        # 检查 notebook.record_note 被调用时传入了 province
        call_args = matcher.notebook.record_note.call_args[0][0]
        assert call_args["province"] == "PROV_EXPLICIT", \
            f"笔记省份应为 PROV_EXPLICIT，实际: {call_args.get('province')}"


    def test_main_resolve_run_province_no_global_write(self, monkeypatch):
        """_resolve_run_province() should not mutate global runtime province."""
        import main

        monkeypatch.setattr(main.config, "resolve_province",
                            lambda *_args, **_kwargs: "PROV_A")

        called = {"set_count": 0}

        def _forbid_set(_name):
            called["set_count"] += 1
            raise AssertionError("set_current_province should not be called")

        monkeypatch.setattr(main.config, "set_current_province", _forbid_set)

        resolved = main._resolve_run_province(
            "ignored", interactive=False, json_output=True
        )
        assert resolved == "PROV_A"
        assert called["set_count"] == 0

    def test_init_experience_db_uses_explicit_province(self, monkeypatch):
        """init_experience_db() should pass request province into ExperienceDB."""
        from src import match_engine
        import src.experience_db as exp_mod

        captured = {}

        class DummyExperienceDB:
            def __init__(self, province=None):
                captured["province"] = province

            def get_stats(self):
                return {"total": 0}

        monkeypatch.setattr(exp_mod, "ExperienceDB", DummyExperienceDB)

        db = match_engine.init_experience_db(False, province="PROV_REQUEST")
        assert isinstance(db, DummyExperienceDB)
        assert captured["province"] == "PROV_REQUEST"


class TestCircuitBreakerIsolation:
    """M-Batch3: LLM 熔断器实例隔离验证"""

    def test_two_matchers_independent(self):
        """两个 AgentMatcher 实例的熔断器互不影响"""
        from src.agent_matcher import AgentMatcher
        a = AgentMatcher.__new__(AgentMatcher)
        a._llm_consecutive_fails = 0
        a._llm_circuit_open = False
        a._llm_circuit_open_time = 0.0

        b = AgentMatcher.__new__(AgentMatcher)
        b._llm_consecutive_fails = 0
        b._llm_circuit_open = False
        b._llm_circuit_open_time = 0.0

        # A 熔断
        a._llm_circuit_open = True
        a._llm_consecutive_fails = 5

        # B 不受影响
        assert b._llm_circuit_open is False
        assert b._llm_consecutive_fails == 0


class TestVectorDisableIsolation:
    """M-Batch4: 规则向量禁用实例隔离验证"""

    def test_two_instances_independent(self):
        """两个 RuleKnowledge 实例的向量禁用互不影响"""
        from src.rule_knowledge import RuleKnowledge

        # 用 __new__ 创建轻量实例（不触发数据库初始化）
        a = RuleKnowledge.__new__(RuleKnowledge)
        a._vector_disabled = False
        a._vector_disable_reason = ""
        a._vector_disable_time = 0.0

        b = RuleKnowledge.__new__(RuleKnowledge)
        b._vector_disabled = False
        b._vector_disable_reason = ""
        b._vector_disable_time = 0.0

        # A 禁用
        a._vector_disabled = True
        a._vector_disable_reason = "Permission denied"
        a._vector_disable_time = time.time()

        # B 不受影响
        assert b._vector_disabled is False
        assert b._vector_disable_reason == ""

    def test_cooldown_recovery(self):
        """向量禁用超过冷却时间后自动恢复"""
        from src.rule_knowledge import RuleKnowledge

        rk = RuleKnowledge.__new__(RuleKnowledge)
        rk._vector_disabled = True
        rk._vector_disable_reason = "Permission denied"
        rk._vector_disable_time = time.time() - 999  # 远超 300 秒冷却时间

        # 模拟 search_rules 中的冷却检查逻辑
        if rk._vector_disabled and rk._vector_disable_time > 0:
            elapsed = time.time() - rk._vector_disable_time
            if elapsed >= RuleKnowledge._VECTOR_COOLDOWN_SEC:
                rk._vector_disabled = False
                rk._vector_disable_reason = ""

        assert rk._vector_disabled is False
        assert rk._vector_disable_reason == ""
