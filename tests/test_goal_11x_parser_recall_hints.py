# -*- coding: utf-8 -*-

from src.goal_search.national_index import infer_family
from src.query_builder import build_quota_query
from src.text_parser import TextParser


parser = TextParser()


def test_goal_11x_family_hints_cover_s6_parser_empty_terms():
    assert infer_family("\u7535\u7bb1\u5b89\u88c5\uff08\u5229\u65e7\uff09") == "electrical_box"
    assert infer_family("LED\u5c4f\u89c6\u5c4f\u5904\u7406\u5668") == "weak_current_device"
    assert infer_family("\u89c6\u9891\u4f20\u8f93\u8bbe\u5907") == "weak_current_device"
    assert infer_family("\u6269\u58f0\u7cfb\u7edf\u8bbe\u5907") == "weak_current_device"
    assert infer_family("\u76d1\u63a7\u6444\u50cf\u8bbe\u5907") == "weak_current_device"
    assert infer_family("\u9632\u6c34\u9632\u5c18\u5438\u9876LED,12W") == "lamp"


def test_goal_11x_query_builder_adds_minimal_recall_anchors():
    box = build_quota_query(parser, "\u7535\u7bb1\u5b89\u88c5\uff08\u5229\u65e7\uff09")
    display = build_quota_query(parser, "LED\u5c4f\u89c6\u5c4f\u5904\u7406\u5668")
    camera = build_quota_query(parser, "\u76d1\u63a7\u6444\u50cf\u8bbe\u5907")
    broadcast = build_quota_query(parser, "\u6269\u58f0\u7cfb\u7edf\u8bbe\u5907")

    assert box.startswith("\u914d\u7535\u7bb1 ")
    assert "\u663e\u793a\u8bbe\u5907" in display
    assert "LED\u663e\u793a\u5c4f" in display
    assert "\u6444\u50cf\u673a" in camera
    assert "\u516c\u5171\u5e7f\u64ad" in broadcast


def test_goal_11x_query_builder_keeps_spd_camera_combo_route():
    query = build_quota_query(
        parser,
        "\u6d6a\u6d8c\u4fdd\u62a4\u5668",
        "\u540d\u79f0\uff1a\u7f51\u7edc+\u7535\u6e90\u9632\u96f7\u5668 "
        "\u89c4\u683c\uff1a\u4e0e\u4e91\u53f0\u6444\u50cf\u673a\u914d\u5957\u4f7f\u7528",
    )

    assert "\u7535\u5b50\u8bbe\u5907\u9632\u96f7\u63a5\u5730\u88c5\u7f6e\u5b89\u88c5" in query
    assert "\u7535\u89c6\u6444\u50cf\u5934\u907f\u96f7\u5668" in query
    assert "\u76d1\u63a7\u6444\u50cf\u8bbe\u5907" not in query

