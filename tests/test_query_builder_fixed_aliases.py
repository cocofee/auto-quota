# -*- coding: utf-8 -*-

from src.query_builder import (
    _append_terms_with_budget,
    _apply_synonyms,
    _query_text_len,
    build_primary_query_profile,
    build_quota_query,
)
from src.text_parser import TextParser


parser = TextParser()


def test_apply_synonyms_adds_sanitary_pipe_fixed_alias():
    query = _apply_synonyms("大便槽冲洗管 DN32")

    assert "大便槽冲洗管" in query
    assert "大便冲洗管" in query


def test_build_quota_query_keeps_sanitary_pipe_alias_for_short_item():
    query = build_quota_query(
        parser,
        "大便槽冲洗管",
        "大便槽冲洗管 大便冲洗管 DN32",
    )

    assert "DN32" in query
    assert "大便冲洗管" in query


def test_build_quota_query_maps_ups_system_debug_to_quota_term():
    query = build_quota_query(
        parser,
        "",
        "不间断电源系统调试 电源型号、规格:UPS不停电装置 电源容量:15kVA以下",
    )

    assert "不间断电源系统调试" in query
    assert "保安电源系统调试" in query


def test_build_quota_query_injects_quota_alias_for_short_sanitary_subject():
    query = build_quota_query(
        parser,
        "清扫口",
        "清扫口 DN32",
    )

    assert "DN32" in query
    assert "地面扫除口安装" in query


def test_build_quota_query_keeps_decisive_field_query_without_unrelated_quota_alias():
    query = build_quota_query(
        parser,
        "",
        "名称:PE100给水管 规格:DN100 连接方式:电热熔连接",
    )

    assert "PE100" in query
    assert "DN100" in query
    assert "地面扫除口安装" not in query


def test_build_primary_query_profile_adds_floor_drain_family_aliases():
    profile = build_primary_query_profile("洗衣机地漏", "洗衣机地漏 DN50")

    assert "地漏安装 DN50" in profile["quota_aliases"]
    assert "地漏安装" in profile["quota_aliases"]


def test_build_primary_query_profile_adds_fire_collar_aliases():
    profile = build_primary_query_profile("阻火圈", "阻火圈 DN100")

    assert "阻火圈安装 DN100" in profile["quota_aliases"]
    assert "阻火圈安装" in profile["quota_aliases"]


def test_build_primary_query_profile_adds_sanitary_flush_pipe_family_aliases():
    profile = build_primary_query_profile("大便槽冲洗管", "大便槽冲洗管 大便冲洗管 DN32")

    assert "大便冲洗管 DN32" in profile["quota_aliases"]
    assert "大便冲洗管" in profile["quota_aliases"]


def test_build_quota_query_keeps_short_subject_ahead_of_spec_token():
    query = build_quota_query(
        parser,
        "大便槽冲洗管",
        "大便槽冲洗管 大便冲洗管 DN32",
    )

    assert query.split()[0] != "DN32"
    assert "大便槽冲洗管" in query or "大便冲洗管" in query


def test_append_terms_with_budget_skips_low_value_terms_and_caps_growth():
    base = "背景音乐系统调试"
    result = _append_terms_with_budget(
        base,
        ["安装", "含", "综合", "附件", "扬声器数量≤50台", "电气", "分区试响"],
        budget_chars=4,
    )

    assert "安装" not in result
    assert "含" not in result
    assert "综合" not in result
    assert "附件" not in result
    assert _query_text_len(result) - _query_text_len(base) <= 4
