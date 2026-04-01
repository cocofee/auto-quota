"""query_builder.py 单元测试 — 覆盖纯函数逻辑"""

import pytest
from src.query_builder import (
    _format_number_for_query,
    _strip_cable_accessory_noise,
    _infer_cable_conductor,
    _dedupe_terms,
    _get_desc_field,
    _normalize_bill_name,
    _normalize_explicit_pipe_material,
    _extract_distribution_box_model,
    _normalize_distribution_box_name,
    _extract_distribution_box_half_perimeter_mm,
    _bucket_distribution_box_half_perimeter,
)


# ===== _format_number_for_query =====

class TestFormatNumberForQuery:
    def test_integer(self):
        assert _format_number_for_query(25.0) == "25"

    def test_integer_no_decimal(self):
        assert _format_number_for_query(100.0) == "100"

    def test_decimal(self):
        assert _format_number_for_query(3.14) == "3.14"

    def test_zero(self):
        assert _format_number_for_query(0.0) == "0"

    def test_negative_integer(self):
        assert _format_number_for_query(-10.0) == "-10"


# ===== _strip_cable_accessory_noise =====

class TestStripCableAccessoryNoise:
    def test_removes_cable_head(self):
        text = "YJV-3*70+2*35 电缆头制作安装"
        result = _strip_cable_accessory_noise(text)
        assert "电缆头" not in result
        assert "YJV" in result

    def test_removes_terminal_head(self):
        text = "YJV-3*35 终端头制作安装"
        result = _strip_cable_accessory_noise(text)
        assert "终端头" not in result

    def test_removes_copper_terminal(self):
        text = "YJV-3*70 压铜接线端子 70mm2"
        result = _strip_cable_accessory_noise(text)
        assert "接线端子" not in result

    def test_empty_string(self):
        assert _strip_cable_accessory_noise("") == ""

    def test_none(self):
        assert _strip_cable_accessory_noise(None) == ""

    def test_clean_text_unchanged(self):
        text = "YJV-3*70+2*35 铜芯电力电缆"
        result = _strip_cable_accessory_noise(text)
        assert "YJV" in result
        assert "铜芯" in result


# ===== _infer_cable_conductor =====

class TestInferCableConductor:
    def test_copper_from_text(self):
        result = _infer_cable_conductor(text="铜芯电力电缆 YJV")
        assert result == "铜芯"

    def test_aluminum_from_text(self):
        result = _infer_cable_conductor(text="铝芯电力电缆")
        assert result == "铝芯"

    def test_aluminum_alloy(self):
        result = _infer_cable_conductor(text="铝合金电缆")
        assert result == "铝合金"

    def test_copper_from_wire_type(self):
        result = _infer_cable_conductor(text="", wire_type="YJV")
        assert result == "铜芯"

    def test_aluminum_from_wire_type(self):
        result = _infer_cable_conductor(text="", wire_type="YJLV")
        assert result == "铝芯"

    def test_vlv_is_aluminum(self):
        result = _infer_cable_conductor(text="", wire_type="VLV")
        assert result == "铝芯"

    def test_vv_is_copper(self):
        result = _infer_cable_conductor(text="", wire_type="VV")
        assert result == "铜芯"

    def test_empty_returns_empty(self):
        result = _infer_cable_conductor(text="")
        assert result == ""

    def test_material_overrides(self):
        result = _infer_cable_conductor(text="电缆", material="铝芯")
        assert result == "铝芯"

    def test_wire_type_case_insensitive(self):
        result = _infer_cable_conductor(text="", wire_type="yjv")
        assert result == "铜芯"


# ===== _dedupe_terms =====

class TestDedupeTerms:
    def test_removes_duplicates(self):
        assert _dedupe_terms(["a", "b", "a"]) == ["a", "b"]

    def test_preserves_order(self):
        assert _dedupe_terms(["z", "a", "z", "b"]) == ["z", "a", "b"]

    def test_strips_whitespace(self):
        assert _dedupe_terms([" a ", "b", " a "]) == ["a", "b"]

    def test_skips_empty(self):
        assert _dedupe_terms(["a", "", "b", "  "]) == ["a", "b"]

    def test_empty_list(self):
        assert _dedupe_terms([]) == []

    def test_none_values(self):
        assert _dedupe_terms([None, "a", None]) == ["a"]


# ===== _get_desc_field =====

class TestGetDescField:
    def test_exact_match(self):
        assert _get_desc_field({"名称": "阀门"}, "名称") == "阀门"

    def test_suffix_match(self):
        assert _get_desc_field({"钢阀门 名称": "截止阀"}, "名称") == "截止阀"

    def test_substring_match(self):
        assert _get_desc_field({"材质": "不锈钢"}, "材质") == "不锈钢"

    def test_not_found(self):
        assert _get_desc_field({"名称": "阀门"}, "型号") == ""

    def test_empty_fields(self):
        assert _get_desc_field({}, "名称") == ""


# ===== _normalize_bill_name =====

class TestNormalizeBillName:
    def test_cable_head_to_terminal(self):
        result = _normalize_bill_name("电力电缆头")
        assert "终端头" in result

    def test_generic_cable_head(self):
        result = _normalize_bill_name("电缆头")
        assert "终端头" in result

    def test_led_ceiling_lamp(self):
        result = _normalize_bill_name("LED圆形吸顶灯")
        assert "LED" not in result
        assert "吸顶灯" in result

    def test_led_with_wattage(self):
        result = _normalize_bill_name("LED面板灯 2×28W")
        assert "28W" not in result
        assert "LED" not in result

    def test_lamp_with_voltage(self):
        result = _normalize_bill_name("LED应急灯 220V")
        assert "220V" not in result

    def test_grille_lamp(self):
        result = _normalize_bill_name("格栅灯")
        assert result == "嵌入式灯具安装"

    def test_waterproof_ceiling_lamp(self):
        result = _normalize_bill_name("防水吸顶灯")
        assert "防水" in result

    def test_non_lamp_unchanged(self):
        result = _normalize_bill_name("镀锌钢管 DN25")
        assert result == "镀锌钢管 DN25"


# ===== _normalize_explicit_pipe_material =====

class TestNormalizeExplicitPipeMaterial:
    def test_psp_steel_plastic(self):
        result = _normalize_explicit_pipe_material("PSP钢塑复合管")
        assert result == "钢塑复合管"

    def test_ppr(self):
        result = _normalize_explicit_pipe_material("PPR给水管")
        assert result == "PPR管"

    def test_ppr_hyphen(self):
        result = _normalize_explicit_pipe_material("PP-R管")
        assert result == "PPR管"

    def test_aluminum_plastic(self):
        result = _normalize_explicit_pipe_material("铝塑复合给水管")
        assert result == "铝塑复合管"

    def test_lined_steel(self):
        result = _normalize_explicit_pipe_material("衬塑钢管")
        assert result == "衬塑钢管"

    def test_coated_steel(self):
        result = _normalize_explicit_pipe_material("涂塑钢管")
        assert result == "涂塑钢管"

    def test_empty_returns_empty(self):
        assert _normalize_explicit_pipe_material("") == ""

    def test_unknown_returns_material(self):
        assert _normalize_explicit_pipe_material("", "不锈钢管") == "不锈钢管"

    def test_material_param_used(self):
        result = _normalize_explicit_pipe_material("管道", "PPR管")
        assert result == "PPR管"

    def test_combined_text_and_material(self):
        result = _normalize_explicit_pipe_material("给水管道", "PSP钢塑复合管")
        assert result == "钢塑复合管"


# ===== _extract_distribution_box_model =====

class TestExtractDistributionBoxModel:
    def test_basic_model(self):
        result = _extract_distribution_box_model("XL-21")
        assert result == "XL-21"

    def test_model_with_prefix(self):
        result = _extract_distribution_box_model("配电箱 XRM-04")
        assert result == "XRM-04"

    def test_skips_ip_rating(self):
        result = _extract_distribution_box_model("IP65")
        assert result == ""

    def test_empty(self):
        assert _extract_distribution_box_model("") == ""


# ===== _normalize_distribution_box_name =====

class TestNormalizeDistributionBoxName:
    def test_adds_install_suffix(self):
        result = _normalize_distribution_box_name("成套配电箱")
        assert "安装" in result

    def test_cabinet_adds_install(self):
        result = _normalize_distribution_box_name("成套配电柜")
        assert "安装" in result

    def test_with_model(self):
        result = _normalize_distribution_box_name("配电箱XL-21")
        assert "XL-21" in result or "配电箱" in result

    def test_empty(self):
        assert _normalize_distribution_box_name("") == ""


# ===== _extract_distribution_box_half_perimeter_mm =====

class TestExtractHalfPerimeter:
    def test_from_params(self):
        result = _extract_distribution_box_half_perimeter_mm(
            "半周长800mm", "规格", {"half_perimeter": 800}
        )
        assert result == 800.0

    def test_from_dimensions(self):
        result = _extract_distribution_box_half_perimeter_mm(
            "", "600*400", {}
        )
        assert result == 1000.0

    def test_from_dimensions_x_separator(self):
        result = _extract_distribution_box_half_perimeter_mm(
            "500x300", "", {}
        )
        assert result == 800.0

    def test_no_dimensions(self):
        result = _extract_distribution_box_half_perimeter_mm(
            "配电箱", "", {}
        )
        assert result is None

    def test_empty(self):
        result = _extract_distribution_box_half_perimeter_mm("", "", {})
        assert result is None


# ===== _bucket_distribution_box_half_perimeter =====

class TestBucketHalfPerimeter:
    def test_small(self):
        assert _bucket_distribution_box_half_perimeter(300) == "0.5m"

    def test_boundary_500(self):
        assert _bucket_distribution_box_half_perimeter(500) == "0.5m"

    def test_medium(self):
        assert _bucket_distribution_box_half_perimeter(800) == "1.0m"

    def test_boundary_1000(self):
        assert _bucket_distribution_box_half_perimeter(1000) == "1.0m"

    def test_large(self):
        assert _bucket_distribution_box_half_perimeter(2000) == "2.5m"

    def test_very_large(self):
        assert _bucket_distribution_box_half_perimeter(5000) == "3.0m"

    def test_none(self):
        assert _bucket_distribution_box_half_perimeter(None) == ""

    def test_zero(self):
        assert _bucket_distribution_box_half_perimeter(0) == ""
