# -*- coding: utf-8 -*-
"""L9 品类硬排斥优化 测试用例

测试目标：
1. 跨品类硬排斥（泵vs喷头、电缆vs导线等）
2. 组内冲突检测（泵vs风机）
3. 同品类不误杀
4. review_rules.json 的品类覆盖
"""

import json
import os
import pytest

from src.param_validator import ParamValidator


class TestCategoryHardRejects:
    """跨品类硬排斥测试（CATEGORY_HARD_REJECTS）"""

    def test_pump_vs_nozzle_reject(self):
        """喷淋泵 vs 洒水喷头 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "喷淋泵", "洒水喷头安装")
        assert penalty >= 0.3, f"泵vs喷头应硬排斥，实际惩罚: {penalty}"
        assert "硬排斥" in detail or "冲突" in detail

    def test_fire_pump_vs_nozzle_reject(self):
        """消防泵 vs 喷头安装 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "消防泵", "喷头安装")
        assert penalty >= 0.3, f"消防泵vs喷头应硬排斥，实际惩罚: {penalty}"

    def test_pump_vs_hydrant_reject(self):
        """水泵 vs 消火栓安装 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "加压泵", "消火栓安装")
        assert penalty >= 0.3, f"泵vs消火栓应硬排斥，实际惩罚: {penalty}"

    def test_pump_vs_extinguisher_reject(self):
        """排污泵 vs 灭火器配置 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "排污泵", "灭火器配置")
        assert penalty >= 0.3, f"泵vs灭火器应硬排斥，实际惩罚: {penalty}"

    def test_nozzle_vs_pump_reject(self):
        """喷头 vs 泵安装 → 硬排斥（反向）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "喷头", "消防泵安装")
        assert penalty >= 0.3, f"喷头vs泵应硬排斥，实际惩罚: {penalty}"

    def test_cable_vs_wire_reject(self):
        """电力电缆 vs 导线敷设 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "电力电缆", "导线敷设")
        assert penalty >= 0.3, f"电缆vs导线应硬排斥，实际惩罚: {penalty}"

    def test_wire_vs_cable_reject(self):
        """导线 vs 电缆敷设 → 硬排斥（反向）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "导线BV-2.5", "电缆敷设")
        assert penalty >= 0.3, f"导线vs电缆应硬排斥，实际惩罚: {penalty}"

    def test_tray_vs_conduit_reject(self):
        """桥架 vs 穿线管 → 硬排斥"""
        penalty, detail = ParamValidator._check_category_conflict(
            "桥架200×100", "穿线管敷设")
        assert penalty >= 0.3, f"桥架vs穿线管应硬排斥，实际惩罚: {penalty}"


class TestCategoryGroupConflict:
    """组内冲突测试（CATEGORY_CONFLICTS 改"泵"后）"""

    def test_pump_vs_fan_group_conflict(self):
        """喷淋泵 vs 风机安装 → 组内冲突（泵和风机在同一组）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "喷淋泵", "风机安装")
        assert penalty >= 0.3, f"泵vs风机应组内冲突，实际惩罚: {penalty}"
        assert "冲突" in detail

    def test_pump_vs_air_outlet_group_conflict(self):
        """消防泵 vs 风口安装 → 组内冲突"""
        penalty, detail = ParamValidator._check_category_conflict(
            "消防泵", "风口安装")
        assert penalty >= 0.3, f"泵vs风口应组内冲突，实际惩罚: {penalty}"

    def test_hydrant_vs_nozzle_group_conflict(self):
        """消火栓 vs 喷头 → 组内冲突（消防组）"""
        penalty, detail = ParamValidator._check_category_conflict(
            "消火栓", "喷头安装")
        assert penalty >= 0.3, f"消火栓vs喷头应组内冲突，实际惩罚: {penalty}"


class TestCategoryNoFalseKill:
    """不误杀正常匹配"""

    def test_same_pump_no_conflict(self):
        """消防泵 vs 泵安装 → 同品类，不冲突"""
        penalty, detail = ParamValidator._check_category_conflict(
            "消防泵", "泵安装")
        assert penalty == 0.0, f"同品类泵不应冲突，实际惩罚: {penalty}"

    def test_same_nozzle_no_conflict(self):
        """喷头 vs 洒水喷头 → 同品类，不冲突"""
        penalty, detail = ParamValidator._check_category_conflict(
            "喷头", "洒水喷头安装")
        assert penalty == 0.0, f"同品类喷头不应冲突，实际惩罚: {penalty}"

    def test_valve_no_conflict(self):
        """阀门DN100 vs 阀门安装DN100 → 正常匹配"""
        penalty, detail = ParamValidator._check_category_conflict(
            "阀门DN100", "阀门安装DN100")
        assert penalty == 0.0, f"同品类阀门不应冲突，实际惩罚: {penalty}"

    def test_distribution_box_no_conflict(self):
        """配电箱 vs 配电箱安装 → 正常匹配"""
        penalty, detail = ParamValidator._check_category_conflict(
            "配电箱", "配电箱安装")
        assert penalty == 0.0, f"同品类配电箱不应冲突，实际惩罚: {penalty}"

    def test_unrelated_no_conflict(self):
        """管道DN50 vs 管道安装 → 不在品类表中，不触发"""
        penalty, detail = ParamValidator._check_category_conflict(
            "管道DN50", "管道安装")
        assert penalty == 0.0, f"不在品类表中不应触发，实际惩罚: {penalty}"

    def test_pump_vs_pump_quota_no_reject(self):
        """喷淋泵 vs 泵安装 → 定额含"泵"不应被自身排斥"""
        # CATEGORY_HARD_REJECTS 中 "泵" 排斥 ["喷头", "消火栓", ...] 不含"泵"自身
        penalty, detail = ParamValidator._check_category_conflict(
            "喷淋泵", "水泵安装30kW")
        assert penalty == 0.0, f"泵vs泵安装不应排斥，实际惩罚: {penalty}"


class TestCategoryIntegration:
    """通过 validate_candidates 端到端测试品类排斥"""

    def setup_method(self):
        self.validator = ParamValidator()

    def test_pump_nozzle_end_to_end(self):
        """端到端：喷淋泵 + 洒水喷头定额 → param_match=False"""
        candidates = [{
            "name": "洒水喷头安装",
            "quota_id": "C9-1-45",
            "quota_params": {},
        }]
        results = self.validator.validate_candidates(
            query_text="喷淋泵",
            candidates=candidates,
        )
        assert len(results) == 1
        assert results[0]["param_match"] is False, \
            "泵vs喷头应导致param_match=False"

    def test_same_category_end_to_end(self):
        """端到端：配电箱 + 配电箱安装定额 → param_match=True"""
        candidates = [{
            "name": "配电箱安装",
            "quota_id": "C4-1-10",
            "quota_params": {},
        }]
        results = self.validator.validate_candidates(
            query_text="配电箱",
            candidates=candidates,
        )
        assert len(results) == 1
        assert results[0]["param_match"] is True, \
            "同品类配电箱应param_match=True"


class TestReviewRulesCoverage:
    """review_rules.json 品类覆盖测试"""

    @pytest.fixture(autouse=True)
    def load_rules(self):
        """加载 review_rules.json"""
        rules_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "review_rules.json")
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = json.load(f)

    def test_pump_variants_in_category_keywords(self):
        """所有常见泵名都应在 category_keywords 中"""
        keywords = self.rules["category_keywords"]
        pump_names = ["水泵", "消防泵", "喷淋泵", "加压泵", "稳压泵",
                      "排污泵", "循环泵", "增压泵", "离心泵", "潜水泵", "管道泵"]
        for name in pump_names:
            assert name in keywords, f"'{name}' 应在 category_keywords 中"
            assert "泵" in keywords[name], \
                f"'{name}' 的关键词应包含 '泵'"

    def test_pump_nozzle_reject_in_rules(self):
        """泵类品类应有喷头排斥规则"""
        rejects = self.rules["category_reject_keywords"]
        # 喷淋泵 应排斥 喷头
        assert "喷淋泵" in rejects, "'喷淋泵' 应在 category_reject_keywords 中"
        assert "喷头" in rejects["喷淋泵"], "'喷淋泵' 应排斥 '喷头'"

    def test_nozzle_rejects_pump(self):
        """喷头应排斥泵"""
        rejects = self.rules["category_reject_keywords"]
        assert "喷头" in rejects
        assert "泵" in rejects["喷头"], "'喷头' 应排斥 '泵'"
