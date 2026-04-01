# -*- coding: utf-8 -*-

from src.query_builder import _append_terms_with_budget, _finalize_query, _query_text_len


def test_append_terms_with_budget_skips_normalized_low_value_terms():
    base = "背景音乐系统"
    result = _append_terms_with_budget(
        base,
        ["安装：", "敷设", "制作", "附件", "分区试响"],
        budget_chars=4,
    )

    assert "安装" not in result
    assert "敷设" not in result
    assert "制作" not in result
    assert "附件" not in result
    assert "分区试响" in result


def test_finalize_query_caps_growth_at_half_of_base_query_length():
    query = _finalize_query(
        "配电箱安装信息",
        canonical_features={
            "canonical_name": "配电箱",
            "system": "电气",
            "entity": "配电箱",
            "install_method": "安装",
            "laying_method": "敷设",
        },
        apply_synonyms=False,
    )

    assert _query_text_len(query) - _query_text_len("配电箱安装信息") <= (_query_text_len("配电箱安装信息") // 2)


def test_finalize_query_skips_generic_install_and_material_noise_terms():
    query = _finalize_query(
        "配电箱安装信息",
        canonical_features={
            "canonical_name": "配电箱",
            "entity": "配电箱",
            "system": "电气",
            "install_method": "安装",
            "laying_method": "敷设",
        },
        context_prior={
            "context_hints": ["不含内部组件", "主材"],
        },
        apply_synonyms=False,
    )

    assert "不含内部组件" not in query
    assert "主材" not in query
    assert "安装 敷设" not in query
