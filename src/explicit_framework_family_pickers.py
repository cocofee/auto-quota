# -*- coding: utf-8 -*-
"""Framework-backed explicit picker migrations."""

from __future__ import annotations

import re

from src.compat_primitives import connections_compatible
from src.explicit_electrical_family_pickers import (
    _extract_cable_head_craft_anchor,
    _infer_cable_conductor_anchor,
)
from src.explicit_picker_framework import ExplicitPickerFramework
from src.text_parser import parser as text_parser

_PICKER_FRAMEWORK = ExplicitPickerFramework()


def _build_motor_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    prefer_check = "检查接线" in text
    prefer_load = "负载调试" in text
    if not prefer_check and not prefer_load:
        return None

    prefer_words: list[str] = []
    forbidden_words: list[str] = []
    if prefer_check:
        prefer_words.append("检查接线")
        forbidden_words.append("负载调试")
    if prefer_load:
        prefer_words.append("负载调试")
        forbidden_words.append("检查接线")

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "prefer_words": prefer_words,
        "forbidden_words": forbidden_words,
    }


def _build_fire_device_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    prefer_words: list[str] = []

    if "试验消火栓" in text:
        prefer_words.extend(["试验用消火栓", "消火栓"])
    elif "室内消火栓" in text:
        prefer_words.extend(["室内消火栓", "消火栓"])
        for keyword in ("单栓", "双栓", "卷盘", "暗装", "明装"):
            if keyword in text:
                prefer_words.append(keyword)
    else:
        return None

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "prefer_words": prefer_words,
    }


def _build_network_device_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    port_count = bill_params.get("port_count")
    if port_count is None:
        port_match = re.search(r"(\d+)\s*口", text)
        if port_match:
            port_count = int(port_match.group(1))
    if port_count is None:
        return None

    prefer_small = port_count <= 24
    prefer_words: list[str] = []
    forbidden_words: list[str] = []
    if prefer_small:
        prefer_words.extend(["≤24口", "24口及以下", "24口以内"])
        forbidden_words.extend([">24口", "24口以上"])
    else:
        prefer_words.extend([">24口", "24口以上", f"{int(port_count)}口"])
        forbidden_words.extend(["≤24口", "24口及以下", "24口以内"])

    bill_params = dict(bill_params)
    bill_params["port_count"] = port_count
    return {
        "bill_text": text,
        "bill_params": bill_params,
        "prefer_words": prefer_words,
        "forbidden_words": forbidden_words,
    }


def _build_wiring_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    bill_cores = bill_params.get("cable_cores")
    upper_text = text.upper()

    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if any(keyword in text for keyword in ("\u7ebf\u69fd\u914d\u7ebf", "\u7ebf\u69fd", "\u69fd\u5185")):
        prefer_words.extend(["\u7ebf\u69fd\u914d\u7ebf", "\u7ebf\u69fd"])
        forbidden_words.extend(["\u7ba1\u5185\u7a7f", "\u6865\u67b6\u5185\u5e03\u653e"])
    elif any(keyword in text for keyword in ("\u7ba1\u5185", "\u7a7f\u7ba1", "\u7a7f\u7ebf\u7ba1")):
        prefer_words.extend(["\u7ba1\u5185\u7a7f", "\u7a7f\u7ebf"])
        forbidden_words.extend(["\u7ebf\u69fd\u914d\u7ebf", "\u6865\u67b6\u5185\u5e03\u653e"])
    else:
        prefer_words.append("\u914d\u7ebf")

    if any(keyword in upper_text for keyword in ("RY", "RYS")):
        prefer_words.extend(["\u8f6f\u5bfc\u7ebf", "\u591a\u82af\u8f6f\u5bfc\u7ebf"])
    elif any(keyword in upper_text for keyword in ("BYJ", "BV")):
        prefer_words.extend(["\u5bfc\u7ebf", "\u94dc\u82af"])

    if bill_cores == 1:
        prefer_words.append("\u5355\u82af")
        forbidden_words.extend(["\u4e8c\u82af", "\u4e09\u82af", "\u591a\u82af"])
    elif bill_cores is not None and bill_cores > 1:
        prefer_words.extend(["\u591a\u82af", f"{int(bill_cores)}\u82af"])
        forbidden_words.append("\u5355\u82af")
        if bill_cores <= 2:
            prefer_words.append("\u4e8c\u82af")

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "prefer_words": prefer_words,
        "forbidden_words": forbidden_words,
        "bill_laying_method": str(bill_params.get("laying_method") or ""),
        "bill_wire_type": str(bill_params.get("wire_type") or ""),
    }


def _score_wiring_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    quota_name = str(candidate_context.get("quota_name", "") or "")
    candidate_params = candidate_context.get("candidate_params") or {}
    bill_laying_method = str(context.get("bill_laying_method") or "")
    bill_wire_type = str(context.get("bill_wire_type") or "")
    candidate_laying_method = str(candidate_params.get("laying_method") or "")
    candidate_wire_type = str(candidate_params.get("wire_type") or "")

    if bill_laying_method:
        if (
            candidate_laying_method
            and (
                bill_laying_method == candidate_laying_method
                or any(token and token in candidate_laying_method for token in bill_laying_method.split("/"))
            )
        ) or (
            ("\u7a7f\u7ba1" in bill_laying_method and any(token in quota_name for token in ("\u7ba1\u5185\u7a7f", "\u7a7f\u7ebf", "\u7a7f\u7ba1")))
            or ("\u7ebf\u69fd" in bill_laying_method and "\u7ebf\u69fd" in quota_name)
            or ("\u6865\u67b6" in bill_laying_method and "\u6865\u67b6" in quota_name)
        ):
            score += 6
        elif candidate_laying_method:
            score -= 6

    if bill_wire_type and candidate_wire_type:
        score += 4 if bill_wire_type == candidate_wire_type else -4

    return score


def _build_distribution_box_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    install_method = str(bill_params.get("install_method") or "")
    box_mount_mode = str(bill_params.get("box_mount_mode") or "")
    prefer_floor = any(keyword in text for keyword in ("\u843d\u5730", "\u67dc\u57fa\u7840", "\u57fa\u7840\u69fd\u94a2"))
    prefer_wall = any(
        keyword in text
        for keyword in (
            "\u60ac\u6302",
            "\u5d4c\u5165",
            "\u660e\u88c5",
            "\u6697\u88c5",
            "\u6302\u5899",
            "\u58c1\u6302",
            "\u5899\u4e0a",
            "\u67f1\u4e0a",
            "\u8ddd\u5730",
        )
    )
    if box_mount_mode == "\u843d\u5730\u5f0f":
        prefer_floor = True
        prefer_wall = False
    elif box_mount_mode == "\u60ac\u6302/\u5d4c\u5165\u5f0f":
        prefer_wall = True
    if install_method == "\u843d\u5730":
        prefer_floor = True
        prefer_wall = False
    elif (
        install_method in {"\u6302\u5899", "\u5d4c\u5165"}
        or "\u660e\u88c5" in install_method
        or "\u6697\u88c5" in install_method
        or "\u60ac\u6302" in install_method
    ):
        prefer_wall = True
    if not prefer_floor and not prefer_wall:
        if any(keyword in text for keyword in ("\u914d\u7535\u67dc", "\u63a7\u5236\u67dc")):
            prefer_floor = True
        elif any(
            keyword in text
            for keyword in (
                "\u914d\u7535\u7bb1",
                "\u63a7\u5236\u7bb1",
                "\u52a8\u529b\u7bb1",
                "\u7167\u660e\u7bb1",
                "\u7a0b\u5e8f\u63a7\u5236\u7bb1",
            )
        ):
            prefer_wall = True
    if not prefer_floor and not prefer_wall:
        return None

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "box_mount_mode": box_mount_mode,
        "prefer_floor": prefer_floor,
        "prefer_wall": prefer_wall,
    }


def _build_plumbing_accessory_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    prefer_flexible_joint = False
    prefer_pipe_clamp = False

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []

    if any(keyword in text for keyword in ("\u5730\u6f0f", "\u6d17\u8863\u673a\u5730\u6f0f", "\u4fa7\u6392\u5730\u6f0f")):
        expected_words.extend(["\u5730\u6f0f"])
        forbidden_words.extend(["\u6392\u6c34\u6813", "\u4f38\u7f29\u5668"])
        if "\u65b9\u5f62" in text:
            prefer_words.append("\u65b9\u5f62")
        if "\u4fa7\u6392" in text:
            prefer_words.append("\u4fa7\u6392")
    elif "\u963b\u706b\u5708" in text:
        expected_words.extend(["\u963b\u706b\u5708"])
        forbidden_words.extend(["\u6392\u6c34\u7ba1", "\u7ed9\u6c34\u7ba1", "\u4fdd\u62a4\u7ba1", "\u5957\u7ba1"])
    elif any(keyword in text for keyword in ("\u6e05\u626b\u53e3", "\u626b\u9664\u53e3", "\u5730\u9762\u6e05\u626b\u53e3")):
        expected_words.extend(["\u6e05\u626b\u53e3", "\u626b\u9664\u53e3"])
        forbidden_words.extend(["\u6d88\u58f0\u5668", "\u6392\u6c34\u7ba1", "\u7ed9\u6c34\u7ba1"])
        if "\u5730\u9762" in text:
            prefer_words.append("\u5730\u9762")
        if "\u5851\u6599" in text:
            prefer_words.append("\u5851\u6599")
    elif any(keyword in text for keyword in ("\u96e8\u6c34\u6597", "87\u578b\u96e8\u6c34\u6597", "\u4fa7\u5165\u96e8\u6c34\u6597")):
        expected_words.extend(["\u96e8\u6c34\u6597"])
        forbidden_words.extend(["\u6392\u6c34\u5851\u6599\u7ba1", "\u6392\u6c34\u7ba1"])
        if "87\u578b" in text:
            prefer_words.append("87\u578b")
        if "\u4fa7\u5165" in text:
            prefer_words.append("\u4fa7\u5165")
    elif "\u6c34\u8868" in text:
        expected_words.extend(["\u6c34\u8868"])
        forbidden_words.extend(["\u9600\u95e8", "\u4f38\u7f29\u5668", "\u652f\u67b6"])
    elif any(keyword in text for keyword in ("\u771f\u7a7a\u7834\u574f\u5668", "\u6c34\u9524\u6d88\u9664\u5668")):
        if "\u771f\u7a7a\u7834\u574f\u5668" in text:
            expected_words.extend(["\u771f\u7a7a\u7834\u574f\u5668"])
            forbidden_words.extend(["\u8fc7\u6ee4\u5668", "\u9664\u6c61\u5668"])
        if "\u6c34\u9524\u6d88\u9664\u5668" in text:
            expected_words.extend(["\u6c34\u9524\u6d88\u9664\u5668"])
            forbidden_words.extend(["\u8fc7\u6ee4\u5668", "\u9664\u6c61\u5668"])
    elif any(keyword in text for keyword in ("\u8fc7\u6ee4\u5668", "\u9664\u6c61\u5668", "Y\u578b\u8fc7\u6ee4\u5668", "\u7ba1\u9053\u8fc7\u6ee4\u5668")):
        expected_words.extend(["\u8fc7\u6ee4\u5668", "\u9664\u6c61\u5668"])
        forbidden_words.extend(["\u6c34\u9524\u6d88\u9664\u5668"])
        if "Y\u578b" in text:
            prefer_words.append("Y\u578b")
    elif "\u5012\u6d41\u9632\u6b62\u5668" in text:
        expected_words.extend(["\u5012\u6d41\u9632\u6b62\u5668"])
        forbidden_words.extend(["\u9600\u95e8"])
        if "\u6c34\u8868" in text:
            prefer_words.append("\u5e26\u6c34\u8868")
            forbidden_words.append("\u4e0d\u5e26\u6c34\u8868")
        else:
            prefer_words.append("\u4e0d\u5e26\u6c34\u8868")
            forbidden_words.append("\u5e26\u6c34\u8868")
    elif any(keyword in text for keyword in ("\u8f6f\u63a5\u5934", "\u4f38\u7f29\u8282", "\u67d4\u6027\u63a5\u5934", "\u6a61\u80f6\u63a5\u5934")):
        prefer_flexible_joint = True
        expected_words.extend(["\u8f6f\u63a5\u5934", "\u4f38\u7f29\u8282", "\u67d4\u6027\u63a5\u5934", "\u6a61\u80f6\u63a5\u5934", "\u67d4\u6027\u63a5\u53e3"])
        forbidden_words.extend([
            "\u6cd5\u5170\u5b89\u88c5", "\u87ba\u7eb9\u6cd5\u5170\u5b89\u88c5", "\u6cd5\u5170\u9600\u95e8",
            "\u5851\u6599\u7ed9\u6c34\u7ba1", "\u5851\u6599\u6392\u6c34\u7ba1", "\u7ed9\u6c34\u7ba1", "\u6392\u6c34\u7ba1"
        ])
        if "\u6cd5\u5170" in text:
            prefer_words.append("\u6cd5\u5170")
        if "\u87ba\u7eb9" in text or "\u4e1d\u6263" in text:
            prefer_words.append("\u87ba\u7eb9")
    elif any(keyword in text for keyword in ("\u5851\u6599\u7ba1\u5361", "\u7ba1\u5361", "\u7ba1\u7bbd", "\u5361\u7b8d", "\u7ba1\u7b8d")):
        prefer_pipe_clamp = True
        expected_words.extend(["\u7ba1\u5361", "\u7ba1\u7bbd", "\u5361\u7b8d", "\u7ba1\u7b8d"])
        forbidden_words.extend([
            "\u5851\u6599\u7ed9\u6c34\u7ba1", "\u5851\u6599\u6392\u6c34\u7ba1", "\u7ed9\u6c34\u7ba1", "\u6392\u6c34\u7ba1",
            "\u94a2\u7ba1", "\u7ba1\u9053\u5b89\u88c5"
        ])
        if "\u5851\u6599" in text:
            prefer_words.append("\u5851\u6599")
    elif any(keyword in text for keyword in ("\u5587\u53ed\u53e3", "\u6ea2\u6c34\u5587\u53ed\u53e3")):
        expected_words.extend(["\u5587\u53ed\u53e3"])
        forbidden_words.extend(["\u5e7f\u64ad\u5587\u53ed", "\u97f3\u7bb1"])
    else:
        return None

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "expected_words": expected_words,
        "forbidden_words": forbidden_words,
        "prefer_words": prefer_words,
        "prefer_flexible_joint": prefer_flexible_joint,
        "prefer_pipe_clamp": prefer_pipe_clamp,
    }


def _build_valve_picker_context(text: str, rules: dict) -> dict | None:
    upper_text = text.upper()
    bill_params = text_parser.parse(text)
    bill_valve_family = str(bill_params.get("valve_connection_family") or "")
    bill_valve_type = str(bill_params.get("valve_type") or "")

    prefer_words: list[str] = []
    forbidden_words = [
        "\u5851\u6599\u6cd5\u5170",
        "\u98ce\u7ba1",
        "\u9632\u706b\u9600",
        "\u8c03\u8282\u9600",
        "\u591a\u53f6",
        "\u6392\u70df\u9600",
        "\u5b9a\u98ce\u91cf\u9600",
        "\u5851\u6599\u7ed9\u6c34\u7ba1",
        "\u5851\u6599\u6392\u6c34\u7ba1",
        "\u7ed9\u6c34\u7ba1",
        "\u6392\u6c34\u7ba1",
    ]

    if "\u78b3\u94a2\u9600\u95e8" in text:
        prefer_words.extend(["\u9600\u95e8", "\u78b3\u94a2"])
    elif "\u5851\u6599\u9600\u95e8" in text or (("PPR" in upper_text or "PP-R" in upper_text) and "\u9600" in text):
        prefer_words.extend(["\u9600\u95e8", "\u5851\u6599"])
        forbidden_words.extend(["\u6cd5\u5170\u5b89\u88c5", "\u87ba\u7eb9\u6cd5\u5170\u5b89\u88c5"])
    elif "\u87ba\u7eb9\u6cd5\u5170\u9600\u95e8" in text:
        prefer_words.extend(["\u6cd5\u5170\u9600\u95e8", "\u9600\u95e8"])
        forbidden_words.extend(["\u6cd5\u5170\u5b89\u88c5", "\u87ba\u7eb9\u6cd5\u5170\u5b89\u88c5"])
    elif "\u710a\u63a5\u6cd5\u5170\u9600\u95e8" in text or "\u6cd5\u5170\u9600\u95e8" in text:
        prefer_words.extend(["\u6cd5\u5170\u9600\u95e8", "\u9600\u95e8"])
        forbidden_words.extend(
            [
                "\u6cd5\u5170\u5b89\u88c5",
                "\u87ba\u7eb9\u6cd5\u5170\u5b89\u88c5",
                "\u5bf9\u710a\u9600\u95e8",
                "\u5bf9\u710a\u9600\u5b89\u88c5",
            ]
        )
    elif "\u87ba\u7eb9\u9600\u95e8" in text:
        prefer_words.extend(["\u87ba\u7eb9\u9600", "\u9600\u95e8"])
        forbidden_words.extend(["\u6cd5\u5170\u5b89\u88c5", "\u5851\u6599\u6cd5\u5170"])
    else:
        return None

    for keyword in (
        "\u95f8\u9600",
        "\u8776\u9600",
        "\u622a\u6b62\u9600",
        "\u6b62\u56de\u9600",
        "\u7403\u9600",
        "\u51cf\u538b\u9600",
        "\u5b89\u5168\u9600",
        "\u7535\u78c1\u9600",
    ):
        if keyword in text:
            prefer_words.append(keyword)

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "bill_valve_family": bill_valve_family,
        "bill_valve_type": bill_valve_type,
        "bill_connection": bill_params.get("connection"),
        "prefer_words": prefer_words,
        "forbidden_words": forbidden_words,
    }


def _build_ventilation_picker_context(text: str, rules: dict) -> dict | None:
    ventilation_entity_keywords = (
        "\u98ce\u53e3", "\u6563\u6d41\u5668", "\u767e\u53f6", "\u98ce\u673a", "\u6392\u6c14\u6247", "\u6362\u6c14\u6247", "\u901a\u98ce\u5668", "\u8f6f\u98ce\u7ba1", "\u6d88\u58f0\u5668"
    )
    ventilation_valve_keywords = (
        "\u6b62\u56de\u9600", "\u8c03\u8282\u9600", "\u9632\u706b\u9600", "\u6392\u70df\u9600", "\u5b9a\u98ce\u91cf\u9600", "\u63d2\u677f\u9600"
    )
    ventilation_context_keywords = (
        "\u98ce\u7ba1", "\u901a\u98ce", "\u7a7a\u8c03", "\u9001\u98ce", "\u56de\u98ce", "\u6392\u70df", "\u98ce\u91cf", "\u591a\u53f6", "\u5bf9\u5f00", "\u9632\u706b"
    )

    if (
        any(keyword in text for keyword in ventilation_valve_keywords)
        and not any(keyword in text for keyword in ventilation_context_keywords)
        and not any(keyword in text for keyword in ventilation_entity_keywords)
    ):
        return None

    bill_params = text_parser.parse(text)
    bill_features = text_parser.parse_canonical(text, params=bill_params)
    bill_entity = str(bill_features.get("entity") or "")
    bill_canonical_name = str(bill_features.get("canonical_name") or "")
    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if bill_canonical_name == "\u536b\u751f\u95f4\u901a\u98ce\u5668" or any(keyword in text for keyword in ("\u536b\u751f\u95f4\u901a\u98ce\u5668", "\u5438\u9876\u5f0f\u901a\u98ce\u5668", "\u540a\u9876\u5f0f\u901a\u98ce\u5668")):
        prefer_words.extend(["\u536b\u751f\u95f4\u901a\u98ce\u5668", "\u5929\u82b1\u5f0f\u6392\u6c14\u6247", "\u6392\u6c14\u6247"])
        forbidden_words.extend(["\u98ce\u673a\u5b89\u88c5", "\u79bb\u5fc3\u5f0f\u901a\u98ce\u673a"])
    elif bill_canonical_name == "\u6696\u98ce\u673a" or bill_entity == "\u6696\u98ce\u673a" or "\u6696\u98ce\u673a" in text:
        prefer_words.extend(["\u6696\u98ce\u673a"])
        forbidden_words.extend(["\u98ce\u673a\u5b89\u88c5"])
    elif "\u67d4\u6027\u8f6f\u98ce\u7ba1" in text or "\u8f6f\u98ce\u7ba1" in text:
        prefer_words.extend(["\u67d4\u6027\u63a5\u53e3", "\u4f38\u7f29\u8282", "\u8f6f\u98ce\u7ba1"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5"])
    elif any(keyword in text for keyword in ("\u98ce\u7ba1\u6b62\u56de\u9600", "\u6b62\u56de\u9600")):
        prefer_words.extend(["\u98ce\u7ba1\u6b62\u56de\u9600", "\u6b62\u56de\u9600"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5", "\u67d4\u6027\u8f6f\u98ce\u7ba1", "\u6c34\u6d41\u6307\u793a\u5668"])
    elif any(keyword in text for keyword in ("\u6392\u70df\u9632\u706b\u9600", "\u9632\u706b\u9600")):
        prefer_words.extend(["\u9632\u706b\u9600"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5", "\u67d4\u6027\u8f6f\u98ce\u7ba1"])
        if "\u6392\u70df" in text or "280" in text:
            prefer_words.append("\u6392\u70df")
    elif any(keyword in text for keyword in ("\u624b\u52a8\u8c03\u8282\u9600", "\u7535\u52a8\u8c03\u8282\u9600", "\u98ce\u91cf\u8c03\u8282\u9600", "\u591a\u53f6\u8c03\u8282\u9600", "\u8c03\u8282\u9600")):
        prefer_words.extend(["\u8c03\u8282\u9600"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5", "\u67d4\u6027\u8f6f\u98ce\u7ba1"])
        if any(keyword in text for keyword in ("\u591a\u53f6", "\u5bf9\u5f00\u591a\u53f6")):
            prefer_words.append("\u591a\u53f6")
        if "\u7535\u52a8" in text:
            prefer_words.append("\u7535\u52a8")
        if "\u624b\u52a8" in text:
            prefer_words.append("\u624b\u52a8")
    elif any(keyword in text for keyword in ("\u5b9a\u98ce\u91cf\u9600", "\u98ce\u91cf\u9600")):
        prefer_words.extend(["\u5b9a\u98ce\u91cf\u9600", "\u98ce\u91cf\u9600"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5"])
    elif "\u63d2\u677f\u9600" in text:
        prefer_words.extend(["\u63d2\u677f\u9600"])
        forbidden_words.extend(["\u9600\u95e8\u5b89\u88c5"])
    elif "\u767e\u53f6" in text and any(keyword in text for keyword in ("\u98ce\u53e3", "\u6563\u6d41\u5668", "\u767e\u53f6\u7a97")):
        prefer_words.extend(["\u767e\u53f6\u98ce\u53e3"])
        forbidden_words.extend(["\u94a2\u767e\u53f6\u7a97"])
    elif any(keyword in text for keyword in ("\u5929\u82b1\u677f", "\u5929\u82b1\u5f0f", "\u7ba1\u9053\u5f0f\u6362\u6c14\u6247")):
        prefer_words.extend(["\u5929\u82b1\u5f0f\u6392\u6c14\u6247", "\u6392\u6c14\u6247"])
        forbidden_words.extend(["\u58c1\u6247"])
    elif any(keyword in text for keyword in ("\u58c1\u5f0f\u6392\u98ce\u673a", "\u58c1\u5f0f")):
        prefer_words.extend(["\u6392\u6c14\u6247"])
        forbidden_words.extend(["\u58c1\u6247"])
    elif "\u6d88\u58f0\u5668" in text:
        prefer_words.extend(["\u6d88\u58f0\u5668"])
    else:
        if "\u901a\u98ce\u5668" in text:
            prefer_words.extend(["\u536b\u751f\u95f4\u901a\u98ce\u5668", "\u5929\u82b1\u5f0f\u6392\u6c14\u6247", "\u6392\u6c14\u6247"])
            forbidden_words.extend(["\u98ce\u673a\u5b89\u88c5"])
        if "\u98ce\u673a" in text:
            prefer_words.extend(["\u98ce\u673a", "\u901a\u98ce\u673a"])
        if "\u98ce\u53e3" in text or "\u6563\u6d41\u5668" in text:
            prefer_words.extend(["\u98ce\u53e3", "\u6563\u6d41\u5668"])
        if "\u9600" in text:
            prefer_words.extend(["\u9600"])
        if not prefer_words:
            return None

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "bill_entity": bill_entity,
        "bill_canonical_name": bill_canonical_name,
        "prefer_words": prefer_words,
        "forbidden_words": forbidden_words,
    }


def _build_cable_picker_context(text: str, rules: dict) -> dict | None:
    upper_text = text.upper()
    bill_params = text_parser.parse(text)
    bill_cable_type = str(bill_params.get("cable_type") or "")
    is_head = any(keyword in text for keyword in ("\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934", "\u4e2d\u95f4\u5934"))
    is_middle_head = "\u4e2d\u95f4\u5934" in text
    is_control = (
        "\u63a7\u5236" in text
        or bill_cable_type == "\u63a7\u5236\u7535\u7f06"
        or any(keyword in upper_text for keyword in ("KVV", "KVVP", "KVVR", "RVVSP", "RVSP"))
    )

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    core_words: list[str] = []

    if is_control:
        expected_words.append("\u63a7\u5236\u7535\u7f06")
        forbidden_words.append("\u7535\u529b\u7535\u7f06")
    else:
        expected_words.append("\u7535\u529b\u7535\u7f06")
        forbidden_words.append("\u63a7\u5236\u7535\u7f06")

    if is_head:
        if is_middle_head:
            expected_words.append("\u4e2d\u95f4\u5934")
            forbidden_words.extend(["\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934"])
        else:
            expected_words.extend(["\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934"])
            forbidden_words.append("\u4e2d\u95f4\u5934")
    else:
        forbidden_words.extend(["\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934", "\u4e2d\u95f4\u5934"])

    if "\u5355\u82af" in text:
        core_words.append("\u5355\u82af")
    if "\u56db\u82af" in text or re.search(r"4\s*[脳xX*]", text):
        core_words.append("\u56db\u82af")
    if "\u4e94\u82af" in text or re.search(r"5\s*[脳xX*]", text):
        core_words.append("\u4e94\u82af")

    core_count_match = re.search(r"(\d+)\s*[脳xX*]\s*\d+(?:\.\d+)?", text)
    if core_count_match:
        core_count = int(core_count_match.group(1))
        if is_control or is_head:
            core_words.extend([f"<={core_count}", f"{core_count}\u82af", f"{core_count}"])

    bill_voltage = ""
    if "35KV" in upper_text:
        bill_voltage = "35kV"
    elif "10KV" in upper_text:
        bill_voltage = "10kV"
    elif any(token in upper_text for token in ("0.6/1KV", "1KV")):
        bill_voltage = "1kV"

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "expected_words": expected_words,
        "forbidden_words": forbidden_words,
        "core_words": core_words,
        "is_head": is_head,
        "bill_laying_method": str(bill_params.get("laying_method") or ""),
        "bill_wire_type": str(bill_params.get("wire_type") or ""),
        "bill_cable_type": bill_cable_type,
        "bill_head_type": str(bill_params.get("cable_head_type") or ""),
        "bill_conductor": _infer_cable_conductor_anchor(text, bill_params),
        "bill_craft": _extract_cable_head_craft_anchor(text),
        "bill_voltage": bill_voltage,
    }


def _build_support_picker_context(text: str, rules: dict) -> dict | None:
    bill_params = text_parser.parse(text)
    bill_support_scope = str(bill_params.get("support_scope") or "")
    bill_support_action = str(bill_params.get("support_action") or "")
    bill_support_material = str(bill_params.get("support_material") or "")
    prefer_aseismic = "\u6297\u9707" in text or bill_support_scope == "\u6297\u9707\u652f\u67b6"
    prefer_side = "\u4fa7\u5411" in text
    prefer_longitudinal = "\u7eb5\u5411" in text
    prefer_single = "\u5355\u7ba1" in text
    prefer_multi = any(keyword in text for keyword in ("\u591a\u7ba1", "\u53cc\u7ba1", "\u4e24\u7ba1", "\u591a\u7ba1\u9053", "\u4e24\u7ba1\u9053"))
    prefer_door_frame = "\u95e8\u578b" in text
    generic_pipe_support = any(keyword in text for keyword in ("\u6309\u9700\u5236\u4f5c", "\u4e00\u822c\u7ba1\u67b6"))
    prefer_equipment = bill_support_scope == "\u8bbe\u5907\u652f\u67b6" or any(
        keyword in text for keyword in ("\u8bbe\u5907\u652f\u67b6", "\u8bbe\u5907\u540a\u67b6", "\u8bbe\u5907\u652f\u540a\u67b6")
    )
    prefer_duct = any(keyword in text for keyword in ("\u901a\u98ce", "\u7a7a\u8c03", "\u98ce\u7ba1", "\u98ce\u53e3"))
    if not any(keyword in text for keyword in ("\u652f\u67b6", "\u540a\u67b6", "\u652f\u540a\u67b6", "\u652f\u6491\u67b6")):
        return None

    prefer_bridge = (
        bill_support_scope == "\u6865\u67b6\u652f\u67b6"
        or any(keyword in text for keyword in ("\u6865\u67b6", "\u7535\u7f06\u6865\u67b6", "\u6865\u67b6\u652f\u6491\u67b6", "\u6865\u67b6\u4fa7\u7eb5\u5411"))
    )
    prefer_pipe = (
        bill_support_scope == "\u7ba1\u9053\u652f\u67b6"
        or any(keyword in text for keyword in ("\u7ba1\u9053", "\u7ba1\u67b6", "\u7ba1\u9053\u652f\u67b6", "\u7ed9\u6392\u6c34", "\u6d88\u9632\u6c34", "\u55b7\u6dcb", "\u6d88\u706b\u6813", "\u6c34\u7ba1"))
    )
    prefer_fabrication = (
        bill_support_action in {"\u5236\u4f5c", "\u5236\u4f5c\u5b89\u88c5"}
        or any(keyword in text for keyword in ("\u56fe\u96c6", "\u8be6\u89c1\u56fe\u96c6", "\u5236\u4f5c", "\u5355\u4ef6\u91cd\u91cf", "\u578b\u94a2"))
    )
    if not prefer_bridge and not prefer_pipe and not prefer_equipment and not prefer_duct and not prefer_aseismic:
        return None

    return {
        "bill_text": text,
        "bill_params": bill_params,
        "bill_support_scope": bill_support_scope,
        "bill_support_action": bill_support_action,
        "bill_support_material": bill_support_material,
        "prefer_aseismic": prefer_aseismic,
        "prefer_side": prefer_side,
        "prefer_longitudinal": prefer_longitudinal,
        "prefer_single": prefer_single,
        "prefer_multi": prefer_multi,
        "prefer_door_frame": prefer_door_frame,
        "generic_pipe_support": generic_pipe_support,
        "prefer_equipment": prefer_equipment,
        "prefer_duct": prefer_duct,
        "prefer_bridge": prefer_bridge,
        "prefer_pipe": prefer_pipe,
        "prefer_fabrication": prefer_fabrication,
    }


def _score_distribution_box_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    quota_name = str(candidate_context.get("quota_name", "") or "")
    bill_params = context.get("bill_params") or {}
    candidate_params = candidate_context.get("candidate_params") or {}
    box_mount_mode = str(context.get("box_mount_mode") or "")
    candidate_box_mount_mode = str(candidate_params.get("box_mount_mode") or "")
    bill_half_perimeter = bill_params.get("half_perimeter")
    bill_circuits = bill_params.get("circuits")
    candidate_half_perimeter = candidate_params.get("half_perimeter")
    candidate_circuits = candidate_params.get("circuits")
    candidate_is_junction_box = any(
        word in quota_name for word in ("\u63a5\u7ebf\u7bb1", "\u63a5\u7ebf\u76d2", "\u5206\u7ebf\u76d2")
    )
    candidate_is_box_wiring = "\u76d8\u3001\u67dc\u3001\u7bb1\u3001\u677f\u914d\u7ebf" in quota_name
    candidate_is_box_install = any(
        word in quota_name
        for word in (
            "\u914d\u7535\u7bb1",
            "\u914d\u7535\u67dc",
            "\u63a7\u5236\u7bb1",
            "\u63a7\u5236\u67dc",
            "\u52a8\u529b\u7bb1",
            "\u7167\u660e\u7bb1",
            "\u7a0b\u5e8f\u63a7\u5236\u7bb1",
            "\u7bb1\u4f53\u5b89\u88c5",
        )
    )

    if context.get("prefer_floor"):
        if "\u843d\u5730" in quota_name:
            score += 12
        if any(word in quota_name for word in ("\u60ac\u6302", "\u5d4c\u5165", "\u5899\u4e0a", "\u67f1\u4e0a", "\u6302\u5899")):
            score -= 10
    if context.get("prefer_wall"):
        if any(word in quota_name for word in ("\u60ac\u6302", "\u5d4c\u5165", "\u5899\u4e0a", "\u67f1\u4e0a", "\u6302\u5899")):
            score += 12
        if "\u843d\u5730" in quota_name:
            score -= 10
    if box_mount_mode and candidate_box_mount_mode:
        score += 10 if box_mount_mode == candidate_box_mount_mode else -10
    if candidate_is_box_install:
        score += 10
    if candidate_is_junction_box:
        score -= 28
    if candidate_is_box_wiring:
        score -= 26
    if "\u914d\u7ebf" in quota_name and "\u5b89\u88c5" not in quota_name:
        score -= 12
    if bill_half_perimeter is not None:
        if candidate_half_perimeter is None:
            score -= 6
        elif candidate_half_perimeter < bill_half_perimeter:
            score -= 18
        elif candidate_half_perimeter == bill_half_perimeter:
            score += 16
        elif candidate_half_perimeter <= bill_half_perimeter * 1.2:
            score += 12
        else:
            score += 8
    if bill_circuits is not None:
        if candidate_circuits is None:
            score -= 4
        elif candidate_circuits < bill_circuits:
            score -= 16
        elif candidate_circuits == bill_circuits:
            score += 12
        elif candidate_circuits <= bill_circuits * 1.5:
            score += 9
        else:
            score += 6

    return score


def _score_valve_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    quota_name = str(candidate_context.get("quota_name", "") or "")
    candidate_params = candidate_context.get("candidate_params") or {}
    bill_valve_family = str(context.get("bill_valve_family") or "")
    bill_valve_type = str(context.get("bill_valve_type") or "")
    bill_dn = (context.get("bill_params") or {}).get("dn")
    bill_connection = context.get("bill_connection")
    candidate_valve_family = str(candidate_params.get("valve_connection_family") or "")
    candidate_valve_type = str(candidate_params.get("valve_type") or "")

    if "\u9600\u95e8" in quota_name:
        score += 8

    if bill_valve_family and candidate_valve_family:
        score += 10 if bill_valve_family == candidate_valve_family else -10

    if bill_valve_type and candidate_valve_type:
        score += 8 if bill_valve_type == candidate_valve_type else -8

    if bill_dn is not None:
        candidate_dn = candidate_params.get("dn")
        if candidate_dn is not None:
            if candidate_dn == bill_dn:
                score += 10
            elif candidate_dn > bill_dn:
                gap = candidate_dn - bill_dn
                score += max(1, int(4 - gap / 25))
            else:
                score -= 10

    if bill_connection:
        candidate_connection = candidate_params.get("connection")
        if candidate_connection:
            if candidate_connection == bill_connection:
                score += 4
            elif not connections_compatible(bill_connection, candidate_connection):
                score -= 5

    return score


def _score_ventilation_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    text = str(context.get("bill_text", "") or "")
    candidate_params = candidate_context.get("candidate_params") or {}
    quota_name = str(candidate_context.get("quota_name", "") or "")
    candidate_features = text_parser.parse_canonical(quota_name, params=candidate_params)
    bill_entity = str(context.get("bill_entity") or "")
    bill_canonical_name = str(context.get("bill_canonical_name") or "")
    candidate_entity = str(candidate_features.get("entity") or "")
    candidate_canonical_name = str(candidate_features.get("canonical_name") or "")

    if bill_canonical_name and candidate_canonical_name:
        if bill_canonical_name == candidate_canonical_name:
            score += 12
        elif bill_canonical_name == "\u536b\u751f\u95f4\u901a\u98ce\u5668" and candidate_canonical_name == "\u6392\u6c14\u6247":
            score += 4
        elif frozenset((bill_entity, candidate_entity)) in {
            frozenset(("\u536b\u751f\u95f4\u901a\u98ce\u5668", "\u98ce\u673a")),
            frozenset(("\u6696\u98ce\u673a", "\u98ce\u673a")),
            frozenset(("\u6392\u6c14\u6247", "\u98ce\u673a")),
        }:
            score -= 12
    elif bill_entity and candidate_entity:
        if bill_entity == candidate_entity:
            score += 8

    if "\u98ce\u53e3" in text and "\u98ce\u53e3" in quota_name:
        score += 4
    if "\u98ce\u673a" in text and "\u98ce\u673a" in quota_name:
        score += 4
    if "\u9600" in text and "\u9600" in quota_name:
        score += 4

    return score


def _score_cable_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    quota_name = str(candidate_context.get("quota_name", "") or "")
    candidate_params = candidate_context.get("candidate_params") or {}
    is_head = bool(context.get("is_head"))
    bill_laying_method = str(context.get("bill_laying_method") or "")
    bill_wire_type = str(context.get("bill_wire_type") or "")
    bill_cable_type = str(context.get("bill_cable_type") or "")
    bill_head_type = str(context.get("bill_head_type") or "")
    bill_conductor = str(context.get("bill_conductor") or "")
    bill_craft = str(context.get("bill_craft") or "")
    bill_voltage = str(context.get("bill_voltage") or "")

    candidate_laying_method = str(candidate_params.get("laying_method") or "")
    candidate_wire_type = str(candidate_params.get("wire_type") or "")
    candidate_cable_type = str(candidate_params.get("cable_type") or "")
    candidate_head_type = str(candidate_params.get("cable_head_type") or "")
    candidate_conductor = _infer_cable_conductor_anchor(quota_name, candidate_params)
    candidate_craft = _extract_cable_head_craft_anchor(quota_name)

    if not is_head and any(keyword in quota_name for keyword in ("\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934", "\u4e2d\u95f4\u5934")):
        score -= 20
    if is_head and "\u6577\u8bbe" in quota_name and not any(keyword in quota_name for keyword in ("\u7ec8\u7aef\u5934", "\u7535\u7f06\u5934", "\u4e2d\u95f4\u5934")):
        score -= 16

    if bill_laying_method:
        if (
            candidate_laying_method
            and (
                bill_laying_method == candidate_laying_method
                or any(token and token in candidate_laying_method for token in bill_laying_method.split("/"))
            )
        ) or (
            ("\u7a7f\u7ba1" in bill_laying_method and any(token in quota_name for token in ("\u7a7f\u5bfc\u7ba1", "\u7a7f\u7ba1", "\u7ba1\u5185")))
            or ("\u6865\u67b6" in bill_laying_method and "\u6865\u67b6" in quota_name)
            or ("\u7ebf\u69fd" in bill_laying_method and "\u7ebf\u69fd" in quota_name)
            or ("\u6392\u7ba1" in bill_laying_method and "\u6392\u7ba1" in quota_name)
            or ("\u76f4\u57cb" in bill_laying_method and "\u57cb\u5730" in quota_name)
        ):
            score += 6
        elif candidate_laying_method:
            score -= 6

    if bill_wire_type and candidate_wire_type:
        score += 4 if bill_wire_type == candidate_wire_type else -4
    if bill_cable_type and candidate_cable_type:
        score += 8 if bill_cable_type == candidate_cable_type else -8
    if bill_head_type and candidate_head_type:
        score += 10 if bill_head_type == candidate_head_type else -10
    if bill_conductor and candidate_conductor:
        score += 8 if bill_conductor == candidate_conductor else -10
    if is_head and bill_craft and candidate_craft:
        score += 6 if bill_craft == candidate_craft else -8
    if is_head and bill_voltage:
        if bill_voltage in quota_name.upper():
            score += 4
        elif any(token in quota_name.upper() for token in ("10KV", "35KV", "1KV")):
            score -= 6

    return score


def _score_support_candidate(candidate: dict, context: dict, candidate_context: dict) -> int:
    score = 0
    quota_name = str(candidate_context.get("quota_name", "") or "")
    bill_params = context.get("bill_params") or {}
    candidate_params = candidate_context.get("candidate_params") or {}
    bill_support_scope = str(context.get("bill_support_scope") or "")
    bill_support_action = str(context.get("bill_support_action") or "")
    bill_support_material = str(context.get("bill_support_material") or "")
    bill_weight = bill_params.get("weight_t")
    candidate_support_scope = str(candidate_params.get("support_scope") or "")
    candidate_support_action = str(candidate_params.get("support_action") or "")
    candidate_support_material = str(candidate_params.get("support_material") or "")

    support_special_shape_words = ("\u6728\u57ab\u5f0f", "\u5f39\u7c27\u5f0f", "\u4fa7\u5411", "\u7eb5\u5411", "\u95e8\u578b", "\u5355\u7ba1", "\u591a\u7ba1")
    equipment_support_words = ("\u8bbe\u5907\u652f\u67b6", "\u8bbe\u5907\u540a\u67b6", "\u8bbe\u5907\u53ca\u90e8\u4ef6\u652f\u67b6")
    bridge_support_words = ("\u6865\u67b6\u652f\u6491\u67b6", "\u7535\u7f06\u6865\u67b6")
    pipe_support_words = ("\u7ba1\u67b6", "\u7ba1\u9053\u652f\u67b6", "\u7ba1\u9053\u652f\u540a\u67b6", "\u540a\u6258\u652f\u67b6", "\u652f\u540a\u67b6")
    instrument_support_words = ("\u4eea\u8868\u652f\u67b6", "\u4eea\u8868\u652f\u540a\u67b6")

    if bill_support_scope and candidate_support_scope:
        if bill_support_scope == candidate_support_scope:
            score += 12
        elif bill_support_scope == "\u6297\u9707\u652f\u67b6" and candidate_support_scope in {"\u6865\u67b6\u652f\u67b6", "\u7ba1\u9053\u652f\u67b6"}:
            score += 2
        else:
            score -= 12

    if bill_support_action and candidate_support_action:
        if bill_support_action == candidate_support_action:
            score += 10
        elif bill_support_action == "\u5236\u4f5c" and candidate_support_action == "\u5236\u4f5c\u5b89\u88c5":
            score -= 4
        elif bill_support_action == "\u5b89\u88c5" and candidate_support_action == "\u5236\u4f5c\u5b89\u88c5":
            score -= 2
        else:
            score -= 8

    if bill_support_material and candidate_support_material:
        score += 8 if bill_support_material == candidate_support_material else -8

    if context.get("prefer_aseismic"):
        if "\u6297\u9707" in quota_name:
            score += 12
        elif context.get("prefer_bridge") and any(word in quota_name for word in bridge_support_words):
            score += 2
        elif (context.get("prefer_pipe") or context.get("prefer_duct")) and any(word in quota_name for word in pipe_support_words):
            score += 2
        elif any(word in quota_name for word in ("\u4e00\u822c\u7ba1\u67b6", "\u652f\u6491\u67b6\u5236\u4f5c", "\u6865\u67b6\u652f\u6491\u67b6\u5236\u4f5c")):
            score -= 4
    elif "\u6297\u9707" in quota_name:
        score -= 8

    if context.get("prefer_bridge"):
        if any(word in quota_name for word in bridge_support_words):
            score += 12
        if any(word in quota_name for word in ("\u652f\u67b6\u5236\u4f5c", "\u652f\u67b6\u5b89\u88c5")) and "\u6865\u67b6" in quota_name:
            score += 6
        if any(word in quota_name for word in pipe_support_words):
            score -= 10
        if any(word in quota_name for word in equipment_support_words):
            score -= 8
    elif context.get("prefer_pipe"):
        if any(word in quota_name for word in pipe_support_words):
            score += 8
        if any(word in quota_name for word in bridge_support_words):
            score -= 10
        if context.get("generic_pipe_support") and "\u4e00\u822c\u7ba1\u67b6" in quota_name:
            score += 10
        for word in support_special_shape_words:
            if word in quota_name and word not in str(context.get("bill_text") or ""):
                score -= 12
        if any(word in quota_name for word in instrument_support_words):
            score -= 10
        if any(word in quota_name for word in equipment_support_words):
            score -= 10
    elif context.get("prefer_duct"):
        if any(word in quota_name for word in ("\u652f\u540a\u67b6", "\u540a\u6258\u652f\u67b6", "\u98ce\u7ba1\u652f\u540a\u67b6")):
            score += 8
        if any(word in quota_name for word in bridge_support_words):
            score -= 10
        if any(word in quota_name for word in instrument_support_words):
            score -= 10
        if any(word in quota_name for word in equipment_support_words):
            score -= 8
    elif context.get("prefer_equipment"):
        if any(word in quota_name for word in equipment_support_words):
            score += 12
        if any(word in quota_name for word in pipe_support_words + bridge_support_words):
            score -= 10
        if any(word in quota_name for word in ("\u5355\u4ef6\u91cd\u91cf", "\u6bcf\u4e2a\u652f\u67b6\u91cd\u91cf", "\u6bcf\u7ec4\u91cd\u91cf", "kg", "\u91cd\u91cf")):
            score += 6

    if context.get("prefer_side"):
        if "\u4fa7\u5411" in quota_name:
            score += 8
        elif any(word in quota_name for word in ("\u7eb5\u5411", "\u95e8\u578b")):
            score -= 8
    if context.get("prefer_longitudinal"):
        if "\u7eb5\u5411" in quota_name:
            score += 8
        elif any(word in quota_name for word in ("\u4fa7\u5411", "\u95e8\u578b")):
            score -= 8
    if context.get("prefer_door_frame"):
        if "\u95e8\u578b" in quota_name:
            score += 8
        elif any(word in quota_name for word in ("\u4fa7\u5411", "\u7eb5\u5411")):
            score -= 6
    if context.get("prefer_single"):
        if "\u5355\u7ba1" in quota_name:
            score += 6
        elif "\u591a\u7ba1" in quota_name:
            score -= 6
    if context.get("prefer_multi"):
        if any(word in quota_name for word in ("\u591a\u7ba1", "\u591a\u6839")):
            score += 6
        elif any(word in quota_name for word in ("\u5355\u7ba1", "\u5355\u6839")):
            score -= 6
    if context.get("prefer_fabrication"):
        if "\u5236\u4f5c" in quota_name:
            score += 10
        if any(word in quota_name for word in ("\u5355\u4ef6\u91cd\u91cf", "kg", "\u91cd\u91cf")):
            score += 6
        if any(word in quota_name for word in ("\u5b89\u88c5", "\u4e00\u822c\u7ba1\u67b6")):
            score -= 8

    if bill_weight is not None:
        candidate_weight = candidate_params.get("weight_t")
        if candidate_weight is not None:
            if candidate_weight == bill_weight:
                score += 10
            elif candidate_weight > bill_weight:
                score += 4
            else:
                score -= 8

    return score


def _filter_plumbing_accessory_candidate(candidate: dict, context: dict, candidate_context: dict) -> bool:
    quota_name = str(candidate_context.get("quota_name", "") or "")
    if context.get("prefer_flexible_joint"):
        if not any(word in quota_name for word in ("\u8f6f\u63a5\u5934", "\u4f38\u7f29\u8282", "\u67d4\u6027", "\u6a61\u80f6\u63a5\u5934")):
            return False
    if context.get("prefer_pipe_clamp"):
        if not any(word in quota_name for word in ("\u7ba1\u5361", "\u7ba1\u7bbd", "\u5361\u7b8d", "\u7ba1\u7b8d")):
            return False
    return True


def _filter_valve_candidate(candidate: dict, context: dict, candidate_context: dict) -> bool:
    quota_name = str(candidate_context.get("quota_name", "") or "")
    return "\u9600" in quota_name


def _filter_support_candidate(candidate: dict, context: dict, candidate_context: dict) -> bool:
    quota_name = str(candidate_context.get("quota_name", "") or "")
    candidate_params = candidate_context.get("candidate_params") or {}
    candidate_support_scope = str(candidate_params.get("support_scope") or "")
    support_anchor_words = ("\u652f\u67b6", "\u540a\u67b6", "\u652f\u540a\u67b6", "\u652f\u6491\u67b6", "\u7ba1\u67b6", "\u6297\u9707")
    surface_process_words = ("\u9664\u9508", "\u5237\u6cb9", "\u6cb9\u6f06", "\u9632\u9508\u6f06", "\u7ea2\u4e39", "\u94f6\u7c89\u6f06", "\u8c03\u548c\u6f06")
    support_action_words = ("\u5236\u4f5c", "\u5b89\u88c5", "\u5236\u4f5c\u5b89\u88c5", "\u5236\u5b89")

    has_support_anchor = bool(candidate_support_scope) or any(word in quota_name for word in support_anchor_words)
    if not has_support_anchor:
        return False

    candidate_is_surface_process = (
        any(word in quota_name for word in surface_process_words)
        and not any(word in quota_name for word in support_action_words)
    )
    if candidate_is_surface_process:
        return False
    if (
        any(word in quota_name for word in surface_process_words)
        and not any(word in str(context.get("bill_text") or "") for word in surface_process_words)
    ):
        return False
    return True


def _pick_explicit_motor_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "motor",
        build_context=_build_motor_picker_context,
    )


def _pick_explicit_fire_device_candidate(bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "fire_device",
        build_context=_build_fire_device_picker_context,
    )


def _pick_explicit_network_device_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "network_device",
        build_context=_build_network_device_picker_context,
    )


def _pick_explicit_wiring_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "wiring",
        build_context=_build_wiring_picker_context,
        score_adjuster=_score_wiring_candidate,
    )


def _pick_explicit_distribution_box_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "distribution_box",
        build_context=_build_distribution_box_picker_context,
        score_adjuster=_score_distribution_box_candidate,
    )


def _pick_explicit_plumbing_accessory_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "plumbing_accessory",
        build_context=_build_plumbing_accessory_picker_context,
        candidate_filter=_filter_plumbing_accessory_candidate,
    )


def _pick_explicit_valve_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "valve",
        build_context=_build_valve_picker_context,
        candidate_filter=_filter_valve_candidate,
        score_adjuster=_score_valve_candidate,
    )


def _pick_explicit_ventilation_family_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "ventilation",
        build_context=_build_ventilation_picker_context,
        score_adjuster=_score_ventilation_candidate,
    )


def _pick_explicit_cable_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "cable",
        build_context=_build_cable_picker_context,
        score_adjuster=_score_cable_candidate,
    )


def _pick_explicit_support_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    return _PICKER_FRAMEWORK.pick(
        bill_text,
        candidates,
        "support",
        build_context=_build_support_picker_context,
        candidate_filter=_filter_support_candidate,
        score_adjuster=_score_support_candidate,
    )
