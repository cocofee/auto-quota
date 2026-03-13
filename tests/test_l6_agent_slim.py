# -*- coding: utf-8 -*-
"""L6 Agent瘦身 测试用例"""

import pytest


class TestRulePostChecker:
    """规则知识后置校验器测试"""

    def test_check_by_rules_no_kb(self, monkeypatch):
        """规则知识库不可用时，返回空列表（不报错）"""
        from src.rule_post_checker import check_by_rules
        import src.rule_post_checker as mod
        # 重置缓存（新版用dict缓存）
        monkeypatch.setattr(mod, "_rule_kb_cache", {})
        monkeypatch.setattr(mod, "_rule_kb_failed", {})
        # 模拟导入失败
        monkeypatch.setattr(mod, "_get_rule_kb", lambda province=None: None)

        item = {"name": "镀锌钢管DN25", "description": ""}
        result = {"quotas": [{"quota_id": "C10-1-1", "name": "管道安装"}]}
        hints = check_by_rules(item, result, province="test")
        assert hints == []

    def test_check_by_rules_no_quotas(self, monkeypatch):
        """无匹配定额时返回空"""
        from src.rule_post_checker import check_by_rules
        import src.rule_post_checker as mod
        monkeypatch.setattr(mod, "_get_rule_kb", lambda province=None: None)

        item = {"name": "测试", "description": ""}
        result = {"quotas": []}
        hints = check_by_rules(item, result)
        assert hints == []

    def test_format_rule_hints_empty(self):
        """空列表格式化为空字符串"""
        from src.rule_post_checker import format_rule_hints
        assert format_rule_hints([]) == ""

    def test_format_rule_hints_join(self):
        """多条提示用竖线分隔"""
        from src.rule_post_checker import format_rule_hints
        assert format_rule_hints(["提示1", "提示2"]) == "提示1｜提示2"

    def test_coefficient_regex(self):
        """系数提取正则测试"""
        from src.rule_post_checker import _RE_COEFFICIENT
        text = "施工消耗量宜乘以系数1.20"
        match = _RE_COEFFICIENT.search(text)
        assert match is not None
        coef = match.group(1) or match.group(2) or match.group(3)
        assert coef == "1.20"

    def test_scope_regex(self):
        """包含/不包含正则测试"""
        from src.rule_post_checker import _RE_SCOPE
        text = "已包括管卡安装和配件费"
        match = _RE_SCOPE.search(text)
        assert match is not None
        assert match.group(1) == "已包括"

    def test_is_relevant_scope(self):
        """相关性检测：scope_text中的词出现在清单名中"""
        from src.rule_post_checker import _is_relevant_scope
        assert _is_relevant_scope("镀锌钢管DN25", "管道安装", "管道试压费用") is True
        assert _is_relevant_scope("镀锌钢管DN25", "管道安装", "电梯机房费用") is False


class TestAgentBatchMode:
    """Agent批量审核模式测试"""

    def test_batch_prompt_format(self, monkeypatch):
        """批量审核prompt包含所有项目"""
        from src.agent_matcher import AgentMatcher

        # 跳过API客户端初始化
        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "notebook", type("N", (), {"record_note": lambda *a, **k: None})()) or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()
        batch_items = [
            {
                "bill_item": {"name": "镀锌钢管DN25", "description": "给水管", "unit": "m", "specialty": "C10"},
                "candidates": [
                    {"quota_id": "C10-1-1", "name": "管道安装DN25以内"},
                    {"quota_id": "C10-1-2", "name": "管道安装DN32以内"},
                ],
                "search_query": "镀锌钢管DN25",
            },
        ]
        prompt = agent._build_batch_prompt(batch_items)
        assert "镀锌钢管DN25" in prompt
        assert "C10-1-1" in prompt
        assert "管道安装DN25以内" in prompt

    def test_parse_batch_response_approve(self, monkeypatch):
        """批量审核：确认推荐定额"""
        import json
        from src.agent_matcher import AgentMatcher

        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()

        batch_items = [{
            "bill_item": {"name": "测试项"},
            "candidates": [
                {"quota_id": "Q1", "name": "定额1"},
                {"quota_id": "Q2", "name": "定额2"},
            ],
            "search_query": "test",
        }]

        response = json.dumps([{"seq": 1, "approve": True, "confidence": 90}])
        results = agent._parse_batch_response(response, batch_items)
        assert len(results) == 1
        assert results[0]["quotas"][0]["quota_id"] == "Q1"
        assert results[0]["confidence"] == 90
        assert results[0]["match_source"] == "agent_batch"

    def test_parse_batch_response_correct(self, monkeypatch):
        """批量审核：纠正为备选定额"""
        import json
        from src.agent_matcher import AgentMatcher

        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()

        batch_items = [{
            "bill_item": {"name": "测试项"},
            "candidates": [
                {"quota_id": "Q1", "name": "定额1"},
                {"quota_id": "Q2", "name": "定额2"},
            ],
            "search_query": "test",
        }]

        response = json.dumps([{
            "seq": 1, "approve": False,
            "corrected_index": 2, "confidence": 85,
            "reason": "材质不匹配"
        }])
        results = agent._parse_batch_response(response, batch_items)
        assert len(results) == 1
        assert results[0]["quotas"][0]["quota_id"] == "Q2"
        assert "纠正" in results[0]["explanation"]

    def test_parse_batch_response_invalid_json(self, monkeypatch):
        """批量审核：JSON解析失败时降级为fallback"""
        from src.agent_matcher import AgentMatcher

        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()

        batch_items = [{
            "bill_item": {"name": "测试项"},
            "candidates": [{"quota_id": "Q1", "name": "定额1"}],
            "search_query": "test",
        }]

        # 无效JSON
        results = agent._parse_batch_response("这不是JSON", batch_items)
        assert len(results) == 1
        # 降级结果应该使用top1候选
        assert results[0]["quotas"][0]["quota_id"] == "Q1"

    def test_parse_batch_response_partial_salvage(self, monkeypatch):
        """批量审核：JSON整体解析失败时，逐条抢救能救回部分结果"""
        from src.agent_matcher import AgentMatcher

        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()

        # 两条清单
        batch_items = [
            {
                "bill_item": {"name": "测试项1"},
                "candidates": [{"quota_id": "Q1", "name": "定额1"},
                               {"quota_id": "Q2", "name": "定额2"}],
                "search_query": "test1",
            },
            {
                "bill_item": {"name": "测试项2"},
                "candidates": [{"quota_id": "Q3", "name": "定额3"}],
                "search_query": "test2",
            },
        ]

        # 模拟大模型返回格式不完美的JSON（尾部多余逗号导致整体解析失败）
        broken_json = '[{"seq": 1, "approve": true, "confidence": 92}, {"seq": 2, "approve": false, "corrected_index": 0, "confidence": 20, "reason": "不匹配"},]'
        results = agent._parse_batch_response(broken_json, batch_items)
        assert len(results) == 2
        # 第1条应该被抢救回来（approve=true）
        assert results[0]["quotas"][0]["quota_id"] == "Q1"
        assert results[0]["confidence"] == 92
        # 第2条也被抢救回来（corrected_index=0 → 无匹配）
        assert results[1]["confidence"] == 0  # 无定额时置信度归0

    def test_salvage_batch_json(self, monkeypatch):
        """逐条抢救方法的单元测试"""
        from src.agent_matcher import AgentMatcher

        monkeypatch.setattr(
            AgentMatcher, "__init__",
            lambda self, **kwargs: setattr(self, "province", "测试省") or
                                    setattr(self, "llm_type", "test") or
                                    setattr(self, "_llm_circuit_open", False) or
                                    setattr(self, "_llm_consecutive_fails", 0) or
                                    setattr(self, "_llm_circuit_open_time", 0.0)
        )
        agent = AgentMatcher()

        # 正常情况不需要抢救
        assert agent._salvage_batch_json("纯文本没有JSON") is None

        # 能从破碎文本中提取出带seq的对象
        broken = '模型说了些废话 {"seq": 1, "approve": true, "confidence": 90} 然后又说了些 {"seq": 2, "approve": false, "confidence": 50}'
        result = agent._salvage_batch_json(broken)
        assert result is not None
        assert len(result) == 2
        assert result[0]["seq"] == 1
        assert result[1]["seq"] == 2


class TestPromptSlimming:
    """Agent Prompt 瘦身测试"""

    def test_rules_not_in_prompt_by_default(self):
        """默认配置下，规则不注入prompt"""
        import config
        assert config.AGENT_RULES_IN_PROMPT is False

    def test_method_cards_not_in_prompt_by_default(self):
        """默认配置下，方法卡片不注入prompt"""
        import config
        assert config.AGENT_METHOD_CARDS_IN_PROMPT is False

    def test_batch_enabled_by_default(self):
        """默认配置下，批量审核已启用"""
        import config
        assert config.AGENT_BATCH_ENABLED is True


class TestMatchByModeRuleHints:
    """match_by_mode 规则提示集成测试"""

    def test_apply_rule_hints_no_module(self, monkeypatch):
        """rule_post_checker模块不存在时静默跳过"""
        from src.match_engine import _apply_rule_hints
        results = [{"bill_item": {"name": "test"}, "quotas": []}]
        # 不应该抛异常
        _apply_rule_hints(results, [{"name": "test"}], province="test")

    def test_apply_rule_hints_adds_to_result(self, monkeypatch):
        """规则提示写入result字典"""
        from src import match_engine

        # mock rule_post_checker
        def fake_check(item, result, province=None):
            if item.get("name") == "有提示":
                return ["系数1.2: 超高乘以系数1.2"]
            return []

        def fake_format(hints):
            return "｜".join(hints) if hints else ""

        import src.rule_post_checker as rpc
        monkeypatch.setattr(rpc, "check_by_rules", fake_check)
        monkeypatch.setattr(rpc, "format_rule_hints", fake_format)

        results = [
            {"bill_item": {"name": "有提示"}, "quotas": [{"quota_id": "Q1"}]},
            {"bill_item": {"name": "无提示"}, "quotas": []},
        ]
        match_engine._apply_rule_hints(results, [], province="test")

        assert results[0].get("rule_hints") == "系数1.2: 超高乘以系数1.2"
        assert "rule_hints" not in results[1]
