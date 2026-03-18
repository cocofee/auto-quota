# -*- coding: utf-8 -*-
"""L6 Agent瘦身 测试用例"""

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
