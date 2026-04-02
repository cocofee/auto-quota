# -*- coding: utf-8 -*-
"""Explicit family pickers for remaining accessory/device domains."""

from __future__ import annotations

import re

from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser


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


def _pick_explicit_plumbing_accessory_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    prefer_flexible_joint = False
    prefer_pipe_clamp = False

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []

    if any(keyword in text for keyword in ("地漏", "洗衣机地漏", "侧排地漏")):
        expected_words.extend(["地漏"])
        forbidden_words.extend(["排水栓", "伸缩器"])
        if "方形" in text:
            prefer_words.append("方形")
        if "侧排" in text:
            prefer_words.append("侧排")
    elif any(keyword in text for keyword in ("雨水斗", "87型雨水斗", "侧入雨水斗")):
        expected_words.extend(["雨水斗"])
        forbidden_words.extend(["排水塑料管", "排水管"])
        if "87型" in text:
            prefer_words.append("87型")
        if "侧入" in text:
            prefer_words.append("侧入")
    elif "水表" in text:
        expected_words.extend(["水表"])
        forbidden_words.extend(["阀门", "伸缩器", "支架"])
    elif any(keyword in text for keyword in ("真空破坏器", "水锤消除器")):
        if "真空破坏器" in text:
            expected_words.extend(["真空破坏器"])
            forbidden_words.extend(["过滤器", "除污器"])
        if "水锤消除器" in text:
            expected_words.extend(["水锤消除器"])
            forbidden_words.extend(["过滤器", "除污器"])
    elif any(keyword in text for keyword in ("过滤器", "除污器", "Y型过滤器", "管道过滤器")):
        expected_words.extend(["过滤器", "除污器"])
        forbidden_words.extend(["水锤消除器"])
        if "Y型" in text:
            prefer_words.append("Y型")
    elif "倒流防止器" in text:
        expected_words.extend(["倒流防止器"])
        forbidden_words.extend(["阀门"])
        if "水表" in text:
            prefer_words.append("带水表")
            forbidden_words.append("不带水表")
        else:
            prefer_words.append("不带水表")
            forbidden_words.append("带水表")
    elif any(keyword in text for keyword in ("软接头", "伸缩节", "柔性接头", "橡胶接头")):
        prefer_flexible_joint = True
        expected_words.extend(["软接头", "伸缩节", "柔性接头", "橡胶接头", "柔性接口"])
        forbidden_words.extend([
            "法兰安装", "螺纹法兰安装", "法兰阀门",
            "塑料给水管", "塑料排水管", "给水管", "排水管",
        ])
        if "法兰" in text:
            prefer_words.append("法兰")
        if "螺纹" in text or "丝扣" in text:
            prefer_words.append("螺纹")
    elif any(keyword in text for keyword in ("塑料管卡", "管卡", "管夹", "卡箍", "管箍")):
        prefer_pipe_clamp = True
        expected_words.extend(["管卡", "管夹", "卡箍", "管箍"])
        forbidden_words.extend([
            "塑料给水管", "塑料排水管", "给水管", "排水管",
            "钢管", "管道安装",
        ])
        if "塑料" in text:
            prefer_words.append("塑料")
    elif any(keyword in text for keyword in ("喇叭口", "溢水喇叭口")):
        expected_words.extend(["喇叭口"])
        forbidden_words.extend(["广播喇叭", "音箱"])
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if prefer_flexible_joint and not any(word in quota_name for word in ("软接头", "伸缩节", "柔性", "橡胶接头")):
            continue
        if prefer_pipe_clamp and not any(word in quota_name for word in ("管卡", "管夹", "卡箍", "管箍")):
            continue
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            candidate_params = text_parser.parse(quota_name)
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 5
                elif candidate_dn > bill_dn:
                    score += 2
                else:
                    score -= 4
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_fire_device_candidate(bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "消火栓" not in text or any(keyword in text for keyword in ("钢管", "管道", "立管", "支管")):
        return None
    if any(keyword in text for keyword in ("灭火器", "干粉")):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    prefer_words: list[str] = []
    forbidden_words = ["钢管", "管道"]

    if "试验消火栓" in text:
        prefer_words.extend(["试验用消火栓", "消火栓"])
        forbidden_words.extend(["室内消火栓安装"])
    elif "室内消火栓" in text:
        prefer_words.extend(["室内消火栓", "消火栓"])
        for keyword in ("单栓", "双栓", "卷盘", "暗装", "明装"):
            if keyword in text:
                prefer_words.append(keyword)
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        if bill_dn is not None:
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 5
                elif candidate_dn > bill_dn:
                    score += 2
                else:
                    score -= 6
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_network_device_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "交换机" not in text:
        return None

    bill_params = text_parser.parse(text)
    port_count = bill_params.get("port_count")
    if port_count is None:
        port_match = re.search(r"(\d+)\s*口", text)
        if port_match:
            port_count = int(port_match.group(1))
    if port_count is None:
        return None
    prefer_small = port_count <= 24

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        score = 0
        candidate_port = candidate_params.get("port_count")
        if candidate_port is not None:
            if port_count == candidate_port:
                score += 12
            elif port_count < candidate_port:
                score += 7
            else:
                score -= 10
        if prefer_small:
            if any(word in quota_name for word in ("≤24口", "24口及以下", "24口以内")):
                score += 12
            if any(word in quota_name for word in (">24口", "24口以上")):
                score -= 10
        else:
            if any(word in quota_name for word in (">24口", "24口以上")):
                score += 12
            if any(word in quota_name for word in ("≤24口", "24口及以下", "24口以内")):
                score -= 10
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)
