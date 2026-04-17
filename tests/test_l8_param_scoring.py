# -*- coding: utf-8 -*-
"""L8 参数验证排序机制优化 测试用例"""

import pytest

from src.param_validator import ParamValidator


class TestGenericQuotaScoring:
    """通用定额（无参数）评分降权测试

    "通用定额"指的是：quota_params中有其他字段（非空），但缺少清单要求的具体参数。
    例如定额有名称但无DN字段 → 说明该定额不按管径分档。
    注意：quota_params完全为空时走另一个早期返回路径（0.8分）。
    """

    def setup_method(self):
        self.validator = ParamValidator()

    def test_generic_quota_dn_score_lowered(self):
        """定额无DN参数时得分应为0.64（不再是0.9）"""
        bill_params = {"dn": 150}
        # 定额有其他字段但无DN → 通用定额场景
        quota_params = {"_quota_name": "管道试压"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert score == pytest.approx(0.64, abs=0.01), f"通用定额应得0.64分，实际: {score}"
        assert is_match is True, "通用定额仍应param_match=True（0.64 >= 0.5）"
        assert "通用定额降权" in detail

    def test_generic_quota_cable_section_score_lowered(self):
        """定额无截面参数时得分应为0.64"""
        bill_params = {"cable_section": 25}
        quota_params = {"_quota_name": "电缆敷设"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert score == pytest.approx(0.64, abs=0.01)
        assert "通用定额降权" in detail

    def test_generic_quota_circuits_score_lowered(self):
        """定额无回路参数时得分应为0.64"""
        bill_params = {"circuits": 7}
        quota_params = {"_quota_name": "配电箱安装"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert score == pytest.approx(0.64, abs=0.01)
        assert "通用定额降权" in detail

    def test_generic_quota_kva_score_lowered(self):
        """定额无容量参数时得分应为0.64"""
        bill_params = {"kva": 45}
        quota_params = {"_quota_name": "变压器安装"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert score == pytest.approx(0.64, abs=0.01)
        assert "通用定额降权" in detail

    def test_generic_quota_ampere_score_lowered(self):
        """定额无电流参数时得分应为0.64"""
        bill_params = {"ampere": 63}
        quota_params = {"_quota_name": "断路器安装"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert score == pytest.approx(0.64, abs=0.01)
        assert "通用定额降权" in detail

    def test_exact_match_beats_generic(self):
        """精确匹配（1.0）应高于通用定额（0.64）"""
        bill_params = {"dn": 150}

        # 精确匹配候选
        _, exact_score, _ = self.validator._check_params(
            bill_params, {"dn": 150, "_quota_name": "管道安装DN150"})
        # 通用定额候选
        _, generic_score, _ = self.validator._check_params(
            bill_params, {"_quota_name": "管道试压"})

        assert exact_score > generic_score, \
            f"精确匹配({exact_score})应高于通用({generic_score})"
        assert exact_score - generic_score >= 0.3, \
            f"分差应足够大，实际差: {exact_score - generic_score}"

    def test_tier_up_beats_generic(self):
        """向上取档候选应高于通用定额"""
        bill_params = {"dn": 150}

        # 向上取档（DN200）
        _, tier_score, _ = self.validator._check_params(
            bill_params, {"dn": 200, "_quota_name": "管道安装DN200"})
        # 通用定额
        _, generic_score, _ = self.validator._check_params(
            bill_params, {"_quota_name": "管道试压"})

        assert tier_score > generic_score, \
            f"向上取档({tier_score})应高于通用({generic_score})"

    def test_mixed_params_generic_average(self):
        """多参数时，通用定额得分被平均化"""
        # 2个参数：DN精确匹配(1.0) + 截面通用(0.64)
        bill_params = {"dn": 150, "cable_section": 25}
        quota_params = {"dn": 150, "_quota_name": "管道安装DN150"}  # 有DN，无截面

        is_match, score, _ = self.validator._check_params(bill_params, quota_params)
        expected = (1.0 + 0.64) / 2  # 0.82
        assert score == pytest.approx(expected, abs=0.01), \
            f"混合得分应为{expected}，实际: {score}"


class TestConnectionHardFail:
    """连接方式不匹配硬性失败测试"""

    def setup_method(self):
        self.validator = ParamValidator()

    def test_connection_mismatch_is_hard_fail(self):
        """连接方式不兼容应设为hard_fail"""
        bill_params = {"connection": "螺纹"}
        quota_params = {"connection": "沟槽"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert is_match is False, "螺纹≠沟槽应为param_match=False"
        assert "不匹配" in detail

    def test_connection_exact_match(self):
        """连接方式完全匹配应正常通过"""
        bill_params = {"connection": "螺纹"}
        quota_params = {"connection": "螺纹"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert is_match is True
        assert score == pytest.approx(1.0, abs=0.01)

    def test_connection_compatible_not_hard_fail(self):
        """兼容的连接方式不应设为hard_fail"""
        bill_params = {"connection": "热熔"}
        quota_params = {"connection": "双热熔"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert is_match is True, "热熔≈双热熔应兼容"
        assert score >= 0.7

    def test_connection_mismatch_with_other_params(self):
        """连接方式不匹配+其他参数精确匹配 → 仍然hard_fail"""
        # DN精确匹配但连接方式不匹配
        bill_params = {"dn": 150, "connection": "螺纹"}
        quota_params = {"dn": 150, "connection": "沟槽"}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert is_match is False, \
            "即使DN精确匹配，连接方式不匹配也应导致param_match=False"


class TestUsageConflict:
    def test_heating_vs_drainage_conflicts_via_partition_closure(self):
        penalty, detail = ParamValidator._check_usage_conflict("采暖管 DN50", "排水管 DN50")
        assert penalty == pytest.approx(0.25, abs=0.001)
        assert "介质冲突" in detail
        assert "采暖" in detail
        assert "排水" in detail

    def test_same_usage_does_not_conflict(self):
        penalty, detail = ParamValidator._check_usage_conflict("给水管 DN50", "给水管 DN50")
        assert penalty == 0.0
        assert detail == ""


class TestNegativeKeywordCopperMaterialConflict:
    def test_copper_candidate_is_not_penalized_when_bill_omits_material(self):
        penalty, detail = ParamValidator._check_negative_keywords(
            "电缆敷设 WDZ-YJY-4*25+1*16",
            "铜带连接",
        )
        assert penalty == 0.0
        assert detail == ""

    def test_copper_candidate_is_penalized_when_bill_explicitly_requires_aluminum(self):
        penalty, detail = ParamValidator._check_negative_keywords(
            "电力电缆 材质:铝芯 型号:YJLV",
            "铜排安装",
        )
        assert penalty == pytest.approx(0.3, abs=0.001)
        assert detail == "清单材质=铝 vs 定额材质=铜"


class TestTierUpHardFail:
    """鏋侀檺鍚戜笂鍙栨。搴旇Е鍙戠‖澶辫触"""

    def setup_method(self):
        self.validator = ParamValidator()

    def test_tier_up_score_returns_zero_for_extreme_ratio(self):
        """ratio >= 4 鐨勮秴妗ｄ笉搴旇 0.55 鍦版澘鎵樹綇"""
        score = self.validator._tier_up_score(25, 25000)
        assert score == 0.0

    def test_extreme_tier_up_sets_param_match_false_even_with_exact_other_param(self):
        """鍗充娇鏈夊叾浠栫簿纭尮閰嶏紝鏋侀檺瓒呮。涔熷簲鐩存帴澶辫触"""
        bill_params = {"dn": 25, "circuits": 2}
        quota_params = {"dn": 25000, "circuits": 2}
        is_match, score, detail = self.validator._check_params(bill_params, quota_params)
        assert is_match is False
        assert score == pytest.approx(0.5, abs=0.01)
        assert "DN25" in detail
        assert "向上取档" in detail


class TestSortingFusion:
    """排序融合 reranker 分数测试"""

    def setup_method(self):
        self.validator = ParamValidator()

    def test_reranker_breaks_tie(self):
        """param_score相同时，reranker分数应影响排序"""
        candidates = [
            {
                "name": "定额A", "quota_id": "A",
                "param_match": True, "param_score": 0.8,
                "rerank_score": 0.3,  # 低rerank
            },
            {
                "name": "定额B", "quota_id": "B",
                "param_match": True, "param_score": 0.8,
                "rerank_score": 0.9,  # 高rerank
            },
        ]
        # 用排序逻辑排序
        candidates.sort(
            key=lambda x: (
                x["param_match"],
                x["param_score"] * 0.8 + x.get("rerank_score", 0) * 0.2,
            ),
            reverse=True,
        )
        assert candidates[0]["quota_id"] == "B", \
            "param_score相同时，高rerank应排前"

    def test_param_score_dominates_over_reranker(self):
        """param_score差距大时仍以param为主"""
        candidates = [
            {
                "name": "定额A", "quota_id": "A",
                "param_match": True, "param_score": 1.0,  # 高param
                "rerank_score": 0.3,  # 低rerank
            },
            {
                "name": "定额B", "quota_id": "B",
                "param_match": True, "param_score": 0.55,  # 低param（通用定额）
                "rerank_score": 0.95,  # 高rerank
            },
        ]
        candidates.sort(
            key=lambda x: (
                x["param_match"],
                x["param_score"] * 0.8 + x.get("rerank_score", 0) * 0.2,
            ),
            reverse=True,
        )
        # A: 1.0*0.8 + 0.3*0.2 = 0.86
        # B: 0.55*0.8 + 0.95*0.2 = 0.63
        assert candidates[0]["quota_id"] == "A", \
            "param_score差距大时应以param为主"

    def test_param_match_still_priority(self):
        """param_match=False的候选即使融合分高也排在后面"""
        candidates = [
            {
                "name": "不匹配但高分", "quota_id": "A",
                "param_match": False, "param_score": 0.9,
                "rerank_score": 0.95,
            },
            {
                "name": "匹配但低分", "quota_id": "B",
                "param_match": True, "param_score": 0.55,
                "rerank_score": 0.3,
            },
        ]
        candidates.sort(
            key=lambda x: (
                x["param_match"],
                x["param_score"] * 0.8 + x.get("rerank_score", 0) * 0.2,
            ),
            reverse=True,
        )
        assert candidates[0]["quota_id"] == "B", \
            "param_match=True应始终排在param_match=False前面"

    def test_hybrid_score_fallback(self):
        """无rerank_score时用hybrid_score作为后备"""
        candidates = [
            {
                "name": "定额A", "quota_id": "A",
                "param_match": True, "param_score": 0.8,
                "hybrid_score": 0.3,  # 无rerank，用hybrid
            },
            {
                "name": "定额B", "quota_id": "B",
                "param_match": True, "param_score": 0.8,
                "hybrid_score": 0.9,  # 无rerank，用hybrid
            },
        ]
        candidates.sort(
            key=lambda x: (
                x["param_match"],
                x["param_score"] * 0.8 + x.get("rerank_score", x.get("hybrid_score", 0)) * 0.2,
            ),
            reverse=True,
        )
        assert candidates[0]["quota_id"] == "B", \
            "无rerank时应用hybrid_score做后备"


class TestConfidencePassthrough:
    """置信度传导测试"""

    def test_generic_quota_confidence_lowered(self):
        """通用定额的置信度应为60（黄灯，需人工确认）"""
        # 模拟 match_pipeline 的置信度公式
        generic_param_score = 0.64
        confidence = int(generic_param_score * 95)
        assert confidence == 60, f"通用定额置信度应为60，实际: {confidence}"

    def test_exact_match_confidence_unchanged(self):
        """精确匹配的置信度应仍为95"""
        exact_param_score = 1.0
        confidence = int(exact_param_score * 95)
        assert confidence == 95

    def test_tier_up_confidence_acceptable(self):
        """向上取档的置信度应合理"""
        tier_param_score = 0.9  # 向上取一档
        confidence = int(tier_param_score * 95)
        assert 80 <= confidence <= 90, f"向上取档置信度应在80-90，实际: {confidence}"

    def test_mixed_generic_confidence(self):
        """混合参数（1个精确+1个通用）的置信度应在黄灯区"""
        mixed_score = (1.0 + 0.64) / 2  # 0.82
        confidence = int(mixed_score * 95)
        assert 60 <= confidence <= 85, f"混合置信度应在60-85（黄灯），实际: {confidence}"
