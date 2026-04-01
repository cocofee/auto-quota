# -*- coding: utf-8 -*-
"""Shared helpers for pipe-family query building and candidate routing."""

from __future__ import annotations

PIPE_ACCESSORY_WORDS = (
    "管件", "弯头", "三通", "异径", "法兰", "接头", "阀", "套管", "地漏", "雨水斗",
    "水表", "过滤器", "除污器", "补偿器", "软接头", "伸缩节", "支架", "吊架",
    "管卡", "成品管卡", "管夹",
)

PIPE_ELECTRICAL_QUOTA_WORDS = (
    "敷设", "暗配", "明配", "砖、混凝土结构", "钢导管", "紧定式",
)

PIPE_RUN_ANCHOR_WORDS = (
    "管道", "给水管", "排水管", "钢塑复合管", "复合管", "塑料给水管", "塑料排水管",
    "铸铁管", "钢管", "管安装",
)

PIPE_USAGE_WORDS = ("给水", "排水", "污水", "废水", "雨水", "消防")
PIPE_LOCATION_WORDS = ("室内", "室外")


def normalize_pipe_material_hint(text: str, material: str = "") -> str:
    combined = f"{text or ''} {material or ''}"
    if any(
        keyword in combined
        for keyword in (
            "PSP钢塑复合管",
            "钢塑复合压力给水管",
            "钢塑复合给水管",
            "钢塑复合管",
            "钢塑复合",
        )
    ):
        return "钢塑复合管"
    if any(
        keyword in combined
        for keyword in (
            "钢骨架塑料复合管",
            "金属骨架塑料复合管",
        )
    ):
        return "钢骨架塑料复合管"
    if "金属骨架复合管" in combined:
        return "金属骨架复合管"
    if "铝塑复合" in combined:
        return "铝塑复合管"
    if "衬塑钢管" in combined:
        return "衬塑钢管"
    if "涂塑钢管" in combined:
        return "涂塑钢管"
    if any(keyword in combined for keyword in ("PP-R管", "PPR管", "PPR复合管", "PP-R", "PPR")):
        return "PPR管"
    if "复合管" in combined:
        return "复合管"
    return material or ""
