# -*- coding: utf-8 -*-
"""Explicit family pickers for terminal devices and fixtures."""

from __future__ import annotations

from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser


def _pick_explicit_sanitary_family_candidate(bill_text: str,
                                             candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("便器", "水龙头", "洗脸盆", "洗涤盆", "水槽", "小便器")):
        return None

    bill_params = text_parser.parse(text)
    sanitary_subtype = str(bill_params.get("sanitary_subtype") or "")
    sanitary_mount_mode = str(bill_params.get("sanitary_mount_mode") or "")
    sanitary_flush_mode = str(bill_params.get("sanitary_flush_mode") or "")
    sanitary_water_mode = str(bill_params.get("sanitary_water_mode") or "")
    sanitary_nozzle_mode = str(bill_params.get("sanitary_nozzle_mode") or "")
    sanitary_tank_mode = str(bill_params.get("sanitary_tank_mode") or "")
    expected_words: list[str] = []
    forbidden_words: list[str] = []
    prefer_words: list[str] = []
    if sanitary_subtype == "坐便器":
        expected_words.extend(["坐式大便器", "坐便器"])
        forbidden_words.extend(["蹲式大便器", "小便器", "洗脸盆", "洗发盆", "洗涤盆", "净身盆", "水龙头"])
    elif sanitary_subtype == "蹲便器":
        expected_words.extend(["蹲式大便器", "蹲便器"])
        forbidden_words.extend(["坐式大便器", "小便器", "洗脸盆", "洗发盆", "洗涤盆", "净身盆", "水龙头"])
    elif sanitary_subtype == "小便器":
        expected_words.append("小便器")
        forbidden_words.extend(["大便器", "洗脸盆", "洗发盆", "洗涤盆", "净身盆", "水龙头"])
    elif sanitary_subtype == "洗脸盆":
        expected_words.extend(["洗脸盆", "洗手盆"])
        forbidden_words.extend(["洗发盆", "净身盆", "洗涤盆", "水龙头", "大便器", "小便器"])
    elif sanitary_subtype == "洗发盆":
        expected_words.append("洗发盆")
        forbidden_words.extend(["洗脸盆", "洗手盆", "洗涤盆", "净身盆", "大便器", "小便器"])
    elif sanitary_subtype == "洗涤盆":
        expected_words.append("洗涤盆")
        forbidden_words.extend(["洗脸盆", "洗发盆", "净身盆", "水龙头", "大便器", "小便器"])
    elif sanitary_subtype == "净身盆":
        expected_words.append("净身盆")
        forbidden_words.extend(["洗脸盆", "洗发盆", "洗涤盆", "大便器", "小便器"])
    if any(keyword in text for keyword in ("水龙头", "龙头")):
        expected_words.append("水龙头")
        forbidden_words.extend(["控制器", "探测器", "侵入"])
        if "感应" in text:
            prefer_words.append("感应")
        if "脚踏" in text:
            prefer_words.append("脚踏")
    if "感应" in text:
        expected_words.extend(["感应开关", "感应"])
        forbidden_words.append("脚踏开关")
    if "脚踏" in text:
        expected_words.append("脚踏开关")
        forbidden_words.append("感应开关")
    if "连体水箱" in text:
        expected_words.append("连体水箱")
        forbidden_words.append("隐藏水箱")
    if "隐藏水箱" in text:
        expected_words.append("隐藏水箱")
        forbidden_words.append("连体水箱")
    if "挂墙" in text:
        expected_words.append("挂墙式")
    if "立式" in text:
        expected_words.append("立式")
    if "壁挂" in text:
        expected_words.append("壁挂式")
    if "嵌入" in text:
        prefer_words.append("嵌入式")

    if not expected_words and not forbidden_words:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_mount_mode = str(candidate_params.get("sanitary_mount_mode") or "")
        candidate_flush_mode = str(candidate_params.get("sanitary_flush_mode") or "")
        candidate_water_mode = str(candidate_params.get("sanitary_water_mode") or "")
        candidate_nozzle_mode = str(candidate_params.get("sanitary_nozzle_mode") or "")
        candidate_tank_mode = str(candidate_params.get("sanitary_tank_mode") or "")
        candidate_subtype = str(candidate_params.get("sanitary_subtype") or "")
        score = sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(3 for word in prefer_words if word and word in quota_name)
        if sanitary_subtype and candidate_subtype:
            if sanitary_subtype == candidate_subtype:
                score += 12
            else:
                score -= 12
        if sanitary_mount_mode and candidate_mount_mode:
            if sanitary_mount_mode == candidate_mount_mode:
                score += 10
            else:
                score -= 10
        if sanitary_flush_mode and candidate_flush_mode:
            if sanitary_flush_mode == candidate_flush_mode:
                score += 10
            else:
                score -= 10
        if sanitary_water_mode and candidate_water_mode:
            if sanitary_water_mode == candidate_water_mode:
                score += 10
            else:
                score -= 10
        if sanitary_nozzle_mode and candidate_nozzle_mode:
            if sanitary_nozzle_mode == candidate_nozzle_mode:
                score += 8
            else:
                score -= 8
        if sanitary_tank_mode and candidate_tank_mode:
            if sanitary_tank_mode == candidate_tank_mode:
                score += 10
            else:
                score -= 10
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_lamp_family_candidate(bill_text: str,
                                         candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "灯" not in text or any(keyword in text for keyword in ("扬声器", "广播")):
        return None

    bill_params = text_parser.parse(text)
    lamp_type = str(bill_params.get("lamp_type") or "")
    install_method = str(bill_params.get("install_method") or "")
    if not lamp_type and not install_method:
        return None

    incompatible_map = {
        "吸顶灯": {"筒灯", "灯带", "壁灯", "标志灯", "应急灯", "车库灯", "投光灯"},
        "筒灯": {"吸顶灯", "灯带", "壁灯", "标志灯", "应急灯", "车库灯", "投光灯"},
        "灯带": {"吸顶灯", "筒灯", "壁灯", "标志灯", "应急灯"},
        "壁灯": {"吸顶灯", "筒灯", "灯带"},
        "标志灯": {"吸顶灯", "筒灯", "灯带", "壁灯"},
        "应急灯": {"吸顶灯", "筒灯", "灯带", "壁灯"},
        "车库灯": {"吸顶灯", "筒灯", "壁灯"},
        "投光灯": {"吸顶灯", "筒灯", "壁灯"},
    }

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_lamp_type = str(candidate_params.get("lamp_type") or "")
        candidate_install_method = str(candidate_params.get("install_method") or "")
        score = 0
        if lamp_type and candidate_lamp_type:
            if lamp_type == candidate_lamp_type:
                score += 12
            elif candidate_lamp_type in incompatible_map.get(lamp_type, set()):
                score -= 10
        if install_method and candidate_install_method:
            if install_method == candidate_install_method:
                score += 8
            elif install_method in {"吊装", "吸顶"} and candidate_install_method in {"吊装", "吸顶"}:
                score += 2
            else:
                score -= 8
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_button_broadcast_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("扬声器", "按钮")):
        return None

    bill_params = text_parser.parse(text)
    bill_features = text_parser.parse_canonical(text, params=bill_params)
    install_method = str(bill_params.get("install_method") or "")
    bill_entity = str(bill_features.get("entity") or "")
    scored: list[tuple[tuple[int, float, float], dict]] = []

    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_features = text_parser.parse_canonical(quota_name, params=candidate_params)
        candidate_install_method = str(candidate_params.get("install_method") or "")
        candidate_entity = str(candidate_features.get("entity") or "")
        score = 0

        if "扬声器" in text:
            if "扬声器" in quota_name:
                score += 10
            if install_method == "挂墙":
                if any(word in quota_name for word in ("壁挂", "挂墙", "壁装")):
                    score += 10
                if "吸顶" in quota_name:
                    score -= 10
            elif install_method == "吸顶":
                if "吸顶" in quota_name:
                    score += 10
                if any(word in quota_name for word in ("壁挂", "挂墙", "壁装")):
                    score -= 10
            if install_method and candidate_install_method:
                if install_method == candidate_install_method:
                    score += 8
                elif {install_method, candidate_install_method} == {"挂墙", "吸顶"}:
                    score -= 8
            if bill_entity and candidate_entity:
                if bill_entity == candidate_entity:
                    score += 6
                else:
                    score -= 6

        if "按钮" in text:
            if "紧急呼叫" in text and all(word not in text for word in ("消防", "报警", "消火栓")):
                if "按钮" in quota_name:
                    score += 8
                if any(word in quota_name for word in ("报警按钮", "消火栓")):
                    score -= 10
            elif any(word in text for word in ("手动报警", "报警按钮", "消火栓")):
                if any(word in quota_name for word in ("报警按钮", "消火栓")):
                    score += 10
                if "普通开关、按钮安装 按钮" in quota_name:
                    score -= 8

        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)
