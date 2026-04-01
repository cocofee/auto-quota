# -*- coding: utf-8 -*-
"""Explicit family pickers for sleeve, conduit, bridge, and ventilation."""

from __future__ import annotations

import re

from loguru import logger

from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser


def _pick_explicit_plastic_sleeve_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    """For explicit PVC/plastic sleeves, prefer the plastic sleeve family."""
    text = bill_text or ""
    if "套管" not in text or not any(keyword in text for keyword in ("PVC", "塑料", "管套")):
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        score = 0
        if "塑料套管" in quota_name:
            score += 10
        if "钢套管" in quota_name:
            score -= 8
        if "制作安装" in quota_name:
            score += 2
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_sleeve_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("套管", "堵洞", "封堵")):
        return None
    if any(keyword in text for keyword in ("电气配管", "导管", "穿线管", "可挠金属套管")):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []

    if any(keyword in text for keyword in ("堵洞", "封堵")):
        expected_words.extend(["堵洞", "封堵"])
        forbidden_words.extend(["套管", "钢套管", "防水套管", "管道"])
    elif any(keyword in text for keyword in ("刚性防水", "刚性防水套管")):
        expected_words.extend(["刚性防水套管"])
        forbidden_words.extend(["柔性防水", "一般钢套管", "塑料套管", "堵洞"])
    elif any(keyword in text for keyword in ("柔性防水", "柔性防水套管")):
        expected_words.extend(["柔性防水套管"])
        forbidden_words.extend(["刚性防水", "一般钢套管", "塑料套管", "堵洞"])
    elif any(keyword in text for keyword in ("密闭", "人防", "防护密闭")):
        expected_words.extend(["密闭套管", "人防", "防护密闭"])
        forbidden_words.extend(["一般钢套管", "塑料套管", "堵洞"])
    else:
        expected_words.extend(["钢套管", "一般钢套管", "塑料套管"])
        forbidden_words.extend(["刚性防水", "柔性防水", "塑料套管", "成品防火", "堵洞"])

    if "穿墙" in text:
        prefer_words.append("穿墙")
    if "穿楼板" in text:
        prefer_words.append("穿楼板")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if "套管" not in quota_name and not any(keyword in quota_name for keyword in ("堵洞", "封堵")):
            continue
        candidate_params = text_parser.parse(quota_name)
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 6
                elif candidate_dn > bill_dn:
                    score += 2
                else:
                    score -= 4
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_conduit_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    """For explicit electrical conduit semantics, pick the conduit family early."""
    if not candidates:
        return None

    text = bill_text or ""
    bill_params = text_parser.parse(text)
    upper_text = text.upper()
    code_match = re.search(r"(?<![A-Z0-9])(JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G)\s*\d+\b", upper_text)
    bill_conduit_type = str(bill_params.get("conduit_type") or (code_match.group(1) if code_match else ""))
    bill_conduit_dn = bill_params.get("conduit_dn")
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
    bill_cable_type = str(bill_params.get("cable_type") or "")
    bill_head_type = str(bill_params.get("cable_head_type") or "")
    explicit_electrical = any(keyword in text for keyword in (
        "电气配管", "穿线管", "导管", "金属软管", "可挠金属套管",
    ))
    if not explicit_electrical and not (bill_conduit_type and "配管" in text):
        return None

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    layout_words: list[str] = []
    size_tokens: list[str] = []

    if "暗配" in text:
        layout_words.append("暗配")
    if "明配" in text:
        layout_words.append("明配")

    if "金属软管" in text:
        expected_words = ["金属软管"]
    elif "可挠" in text:
        expected_words = ["可挠金属套管"]
    else:
        conduit_code = bill_conduit_type
        if conduit_code in {"JDG", "KBG"}:
            expected_words = ["JDG", "紧定式", "钢导管"]
            forbidden_words = ["防爆钢管", "电缆保护"]
        elif conduit_code in {"PC", "PVC"}:
            expected_words = ["刚性阻燃管", "PVC阻燃塑料管"]
            forbidden_words = ["电缆保护", "防爆钢管"]
        elif conduit_code == "FPC":
            expected_words = ["半硬质阻燃管", "半硬质塑料管"]
            forbidden_words = ["电缆保护", "防爆钢管"]
        elif conduit_code in {"SC", "G", "DG", "RC", "MT"}:
            expected_words = ["镀锌钢管", "镀锌电线管", "钢管敷设"]
            forbidden_words = ["防爆钢管", "电缆保护"]

    size_match = re.search(r"(?<![A-Z0-9])(?:JDG|KBG|FPC|PVC|PC|SC|RC|MT|DG|G|DN|Φ|∅)\s*(\d+)\b", upper_text)
    if size_match:
        size = size_match.group(1)
        size_tokens = [f"{size}", f"≤{size}"]

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_conduit_type = str(candidate_params.get("conduit_type") or "")
        candidate_conduit_dn = candidate_params.get("conduit_dn")
        candidate_laying_method = str(candidate_params.get("laying_method") or "")
        candidate_wire_type = str(candidate_params.get("wire_type") or "")
        candidate_cable_type = str(candidate_params.get("cable_type") or "")
        candidate_head_type = str(candidate_params.get("cable_head_type") or "")
        family_hits = sum(1 for word in expected_words if word and word in quota_name)
        family_penalty = sum(1 for word in forbidden_words if word and word in quota_name)
        layout_hits = sum(1 for word in layout_words if word and word in quota_name)
        size_hits = sum(1 for token in size_tokens if token and token in quota_name)
        score = family_hits * 10 + layout_hits * 4 + size_hits * 2 - family_penalty * 8
        if bill_conduit_type and candidate_conduit_type:
            score += 10 if bill_conduit_type == candidate_conduit_type else -10
        if bill_conduit_dn is not None and candidate_conduit_dn is not None:
            if bill_conduit_dn == candidate_conduit_dn:
                score += 8
            elif bill_conduit_dn < candidate_conduit_dn:
                score += 2
            else:
                score -= 8
        if bill_laying_method and candidate_laying_method:
            if bill_laying_method == candidate_laying_method or any(
                token and token in candidate_laying_method for token in bill_laying_method.split("/")
            ):
                score += 6
            else:
                score -= 6
        if bill_wire_type and candidate_wire_type:
            score += 4 if bill_wire_type == candidate_wire_type else -4
        if bill_cable_type and candidate_cable_type:
            score += 8 if bill_cable_type == candidate_cable_type else -8
        if bill_head_type and candidate_head_type:
            score += 10 if bill_head_type == candidate_head_type else -10
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    if not scored:
        return None

    best = pick_best_candidate(scored)
    logger.debug(
        "显式电气配管候选重选: bill={} -> quota={}",
        bill_text[:80],
        best.get("name", "")[:80],
    )
    return best


def _pick_explicit_bridge_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("桥架", "线槽", "母线槽")):
        return None
    if any(keyword in text for keyword in ("电缆", "双绞线", "网线", "光缆", "配线", "布放", "穿线", "导线")):
        return None

    bill_params = text_parser.parse(text)
    bill_bridge_wh_sum = bill_params.get("bridge_wh_sum")
    bill_bridge_type = str(bill_params.get("bridge_type") or "")
    prefer_bridge = "桥架" in text
    prefer_trunking = "线槽" in text and "桥架" not in text
    prefer_busway = "母线槽" in text
    prefer_slot = "槽式" in text
    prefer_tray = "托盘式" in text
    prefer_ladder = "梯式" in text

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        score = 0
        candidate_bridge_type = str(candidate_params.get("bridge_type") or "")
        if prefer_busway:
            if "母线槽" in quota_name:
                score += 14
            if any(word in quota_name for word in ("桥架", "线槽")):
                score -= 10
        elif prefer_bridge:
            if "桥架" in quota_name:
                score += 12
            if "支撑架" in quota_name:
                score -= 10
            if "线槽配线" in quota_name or "桥架内布线" in quota_name:
                score -= 12
            if "线槽" in quota_name and "桥架" not in quota_name:
                score -= 6
        elif prefer_trunking:
            if "线槽" in quota_name:
                score += 12
            if "桥架" in quota_name:
                score -= 8
            if "线槽配线" in quota_name:
                score -= 10
        if prefer_slot:
            if "槽式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("托盘式", "梯式")):
                score -= 8
        if prefer_tray:
            if "托盘式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("槽式", "梯式")):
                score -= 8
        if prefer_ladder:
            if "梯式" in quota_name:
                score += 8
            if any(word in quota_name for word in ("槽式", "托盘式")):
                score -= 8
        if bill_bridge_type and candidate_bridge_type:
            if bill_bridge_type == candidate_bridge_type:
                score += 10
            else:
                score -= 10
        if bill_bridge_wh_sum is not None:
            candidate_bridge_wh_sum = candidate_params.get("bridge_wh_sum")
            if candidate_bridge_wh_sum is not None:
                if candidate_bridge_wh_sum == bill_bridge_wh_sum:
                    score += 6
                elif candidate_bridge_wh_sum > bill_bridge_wh_sum:
                    score += 3
                else:
                    score -= 8
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_ventilation_family_candidate(bill_text: str,
                                                candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    ventilation_entity_keywords = (
        "风口", "散流器", "百叶", "风机", "排气扇", "换气扇", "通风器", "软风管", "消声器",
    )
    ventilation_valve_keywords = (
        "止回阀", "调节阀", "防火阀", "排烟阀", "定风量阀", "插板阀",
    )
    ventilation_context_keywords = (
        "风管", "通风", "空调", "送风", "回风", "排烟", "风量", "多叶", "对开", "防火",
    )
    if not any(keyword in text for keyword in (
        *ventilation_entity_keywords,
        *ventilation_valve_keywords,
    )):
        return None
    if (
        any(keyword in text for keyword in ventilation_valve_keywords)
        and not any(keyword in text for keyword in ventilation_context_keywords)
        and not any(keyword in text for keyword in ventilation_entity_keywords)
    ):
        return None

    bill_params = text_parser.parse(text)
    bill_features = text_parser.parse_canonical(text, params=bill_params)
    bill_perimeter = bill_params.get("perimeter")
    bill_weight = bill_params.get("weight_t")
    bill_entity = str(bill_features.get("entity") or "")
    bill_canonical_name = str(bill_features.get("canonical_name") or "")
    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if bill_canonical_name == "卫生间通风器" or any(keyword in text for keyword in ("卫生间通风器", "吊顶式通风器", "吸顶式通风器")):
        prefer_words.extend(["卫生间通风器", "天花式排气扇", "排气扇"])
        forbidden_words.extend(["风机安装", "离心式通风机"])
    elif bill_canonical_name == "暖风机" or bill_entity == "暖风机" or "暖风机" in text:
        prefer_words.extend(["暖风机"])
        forbidden_words.extend(["风机安装"])
    elif "柔性软风管" in text or "软风管" in text:
        prefer_words.extend(["柔性接口", "伸缩节", "软风管"])
        forbidden_words.extend(["阀门安装"])
    elif any(keyword in text for keyword in ("风管止回阀", "止回阀")):
        prefer_words.extend(["风管止回阀", "止回阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管", "水流指示器"])
    elif any(keyword in text for keyword in ("排烟防火阀", "防火阀")):
        prefer_words.extend(["防火阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管"])
        if "排烟" in text or "280" in text:
            prefer_words.append("排烟")
    elif any(keyword in text for keyword in ("手动调节阀", "电动调节阀", "风量调节阀", "多叶调节阀", "调节阀")):
        prefer_words.extend(["调节阀"])
        forbidden_words.extend(["阀门安装", "柔性软风管"])
        if any(keyword in text for keyword in ("多叶", "对开多叶")):
            prefer_words.append("多叶")
        if "电动" in text:
            prefer_words.append("电动")
        if "手动" in text:
            prefer_words.append("手动")
    elif any(keyword in text for keyword in ("定风量阀", "风量阀")):
        prefer_words.extend(["定风量阀", "风量阀"])
        forbidden_words.extend(["阀门安装"])
    elif "插板阀" in text:
        prefer_words.extend(["插板阀"])
        forbidden_words.extend(["阀门安装"])
    elif "百叶" in text and any(keyword in text for keyword in ("风口", "散流器", "百叶窗")):
        prefer_words.extend(["百叶风口"])
        forbidden_words.extend(["钢百叶窗"])
    elif any(keyword in text for keyword in ("天花板", "天花式", "管道式换气扇")):
        prefer_words.extend(["天花式排气扇", "排气扇"])
        forbidden_words.extend(["壁扇"])
    elif any(keyword in text for keyword in ("壁式排风机", "壁式")):
        prefer_words.extend(["排气扇"])
        forbidden_words.extend(["壁扇"])
    elif "消声器" in text:
        prefer_words.extend(["消声器"])
    else:
        if "通风器" in text:
            prefer_words.extend(["卫生间通风器", "天花式排气扇", "排气扇"])
            forbidden_words.extend(["风机安装"])
        if "风机" in text:
            prefer_words.extend(["风机", "通风机"])
        if "风口" in text or "散流器" in text:
            prefer_words.extend(["风口", "散流器"])
        if "阀" in text:
            prefer_words.extend(["阀"])
        if not prefer_words:
            return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_features = text_parser.parse_canonical(quota_name, params=candidate_params)
        candidate_entity = str(candidate_features.get("entity") or "")
        candidate_canonical_name = str(candidate_features.get("canonical_name") or "")
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        if bill_canonical_name and candidate_canonical_name:
            if bill_canonical_name == candidate_canonical_name:
                score += 12
            elif bill_canonical_name == "卫生间通风器" and candidate_canonical_name == "排气扇":
                score += 4
            elif frozenset((bill_entity, candidate_entity)) in {
                frozenset(("卫生间通风器", "风机")),
                frozenset(("暖风机", "风机")),
                frozenset(("排气扇", "风机")),
            }:
                score -= 12
        elif bill_entity and candidate_entity:
            if bill_entity == candidate_entity:
                score += 8
        if "风口" in text and "风口" in quota_name:
            score += 4
        if "风机" in text and "风机" in quota_name:
            score += 4
        if "阀" in text and "阀" in quota_name:
            score += 4
        if bill_perimeter is not None:
            candidate_perimeter = candidate_params.get("perimeter")
            if candidate_perimeter is not None:
                if candidate_perimeter == bill_perimeter:
                    score += 6
                elif candidate_perimeter > bill_perimeter:
                    score += 3
                else:
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
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)
