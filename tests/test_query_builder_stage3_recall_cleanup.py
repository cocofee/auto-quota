# -*- coding: utf-8 -*-

from src.query_builder import _apply_synonyms, build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_build_quota_query_strengthens_pipe_wiring_lighting_aliases():
    query = build_quota_query(
        parser,
        "\u914d\u7ebf",
        "\u89c4\u683c\uff1aBYJ2.5mm2 \u914d\u7ebf\u7ebf\u5236\uff1a\u7ba1\u5185\u7a7f\u7ebf",
    )

    assert "\u7ba1\u5185\u7a7f\u7ebf" in query
    assert "\u7167\u660e\u7ebf\u8def" in query
    assert "\u94dc\u82af" in query
    assert "2.5" in query


def test_build_quota_query_prefers_floor_outlet_terms_over_generic_outlet():
    query = build_quota_query(
        parser,
        "\u63d2\u5ea7",
        "\u4e24\u5b54\u52a0\u4e09\u5b54\u5730\u9762\u63d2\u5ea7",
    )

    assert "\u5730\u9762\u63d2\u5ea7" in query
    assert "\u666e\u901a\u63d2\u5ea7\u5b89\u88c5" not in query
    assert "\u6697\u88c5" not in query


def test_build_quota_query_adds_family_anchor_for_mop_sink():
    query = build_quota_query(
        parser,
        "\u62d6\u628a\u6c60",
        "\u6750\u8d28:\u74f7\u8d28 \u7ec4\u88c5\u5f62\u5f0f:\u843d\u5730\u5b89\u88c5 "
        "\u5176\u4ed6:\u672a\u5c3d\u4e8b\u9879\uff0c\u53c2\u7167\u56fe\u7eb8\u53ca\u8bbe\u8ba1\u8bf4\u660e",
    )

    assert "\u5176\u4ed6\u6210\u54c1\u536b\u751f\u5668\u5177" in query
    assert "\u6210\u54c1\u62d6\u5e03\u6c60\u5b89\u88c5" in query


def test_build_quota_query_biases_camera_combo_spd_to_video_protection_family():
    query = build_quota_query(
        parser,
        "\u6d6a\u6d8c\u4fdd\u62a4\u5668",
        "\u540d\u79f0\uff1a\u7f51\u7edc+\u7535\u6e90\u9632\u96f7\u5668 "
        "\u89c4\u683c\uff1a\u4e0e\u4e91\u53f0\u6444\u50cf\u673a\u914d\u5957\u4f7f\u7528",
    )

    assert "\u7535\u5b50\u8bbe\u5907\u9632\u96f7\u63a5\u5730\u88c5\u7f6e\u5b89\u88c5" in query
    assert "\u7535\u89c6\u6444\u50cf\u5934\u907f\u96f7\u5668" in query
    assert "\u7f51\u7edc+\u7535\u6e90\u9632\u96f7\u5668" in query


def test_apply_synonyms_adds_stage3_electrical_recall_aliases():
    lamp_query = _apply_synonyms("普通荧光灯", "C4")
    cable_query = _apply_synonyms("电缆", "C4")
    wire_query = _apply_synonyms("电线", "C4")
    smoke_query = _apply_synonyms("感烟探测器", "C4")

    assert "荧光灯具安装" in lamp_query
    assert "电缆敷设" in cable_query
    assert "管内穿线" in wire_query
    assert "点型探测器安装 感烟" in smoke_query


def test_apply_synonyms_adds_stage3_mep_family_aliases():
    seamless_query = _apply_synonyms("无缝钢管")
    low_pressure_query = _apply_synonyms("低压碳钢管")
    axial_query = _apply_synonyms("轴流式通风机")
    centrifugal_query = _apply_synonyms("离心式通风机")
    support_query = _apply_synonyms("支吊架")

    assert "室内无缝钢管" in seamless_query
    assert "低压钢管" in low_pressure_query
    assert "轴流式通风机安装" in axial_query
    assert "离心式通风机安装" in centrifugal_query
    assert "支吊架安装" in support_query
