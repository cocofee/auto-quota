# -*- coding: utf-8 -*-
"""Explicit family pickers for pipe-adjacent domains."""

from __future__ import annotations

from src.compat_primitives import connections_compatible
from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.pipe_rule_utils import (
    PIPE_ACCESSORY_WORDS,
    PIPE_ELECTRICAL_QUOTA_WORDS,
    PIPE_LOCATION_WORDS,
    PIPE_RUN_ANCHOR_WORDS,
    PIPE_USAGE_WORDS,
    normalize_pipe_material_hint,
)
from src.text_parser import parser as text_parser


def _pick_explicit_cast_iron_pipe_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if "铸铁" not in text or "管" not in text:
        return None

    drainage_context = any(keyword in text for keyword in ("排水", "污水", "废水", "污废水", "雨水"))
    if not drainage_context:
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    expected_words = ["铸铁"]
    forbidden_words = [
        "钢塑复合管", "复合管", "塑料给水管", "塑料排水管",
        "给水管", "PPR", "PP-R", "钢管",
    ]
    prefer_words: list[str] = []

    if "雨水" in text:
        expected_words.append("雨水")
        forbidden_words.append("排水管")
    else:
        expected_words.append("排水")
        forbidden_words.append("雨水")

    if "室内" in text:
        prefer_words.append("室内")
        forbidden_words.append("室外")
    elif "室外" in text:
        prefer_words.append("室外")
        forbidden_words.append("室内")

    if any(keyword in text for keyword in ("机械接口", "机械连接")):
        prefer_words.extend(["机械接口", "机械连接"])
        forbidden_words.extend(["卡箍", "胶圈"])
    elif any(keyword in text for keyword in ("卡箍", "无承口")):
        prefer_words.extend(["卡箍", "无承口"])
        forbidden_words.append("机械接口")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if "铸铁" not in quota_name:
            continue
        candidate_params = text_parser.parse(quota_name)
        score = sum(10 for word in expected_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        score += sum(4 for word in prefer_words if word and word in quota_name)
        if bill_dn is not None:
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 8
                elif candidate_dn > bill_dn:
                    score += 3
                else:
                    score -= 6
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_pipe_run_candidate(bill_text: str,
                                      candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not text:
        return None

    if any(word in text for word in ("电气配管", "配管", "导管", "穿线管", "桥架", "线槽", "金属软管", "可挠金属套管")):
        return None
    if any(word in text for word in PIPE_ACCESSORY_WORDS):
        return None
    if not any(word in text for word in ("管", "给水", "排水", "污水", "废水", "雨水", "复合管", "钢管", "塑料管", "铸铁管")):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    bill_connection = str(bill_params.get("connection") or "")
    bill_material = normalize_pipe_material_hint(text, str(bill_params.get("material") or ""))
    usage_words = [word for word in PIPE_USAGE_WORDS if word in text]
    location_words = [word for word in PIPE_LOCATION_WORDS if word in text]
    forbidden_words = (
        "管件", "弯头", "三通", "异径", "法兰安装", "接头", "套管", "水表", "过滤器",
        "除污器", "补偿器", "软接头", "伸缩节", "低压管件",
    )

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if any(word in quota_name for word in forbidden_words):
            continue
        if not any(word in quota_name for word in PIPE_RUN_ANCHOR_WORDS):
            continue

        candidate_params = text_parser.parse(quota_name)
        candidate_connection = str(candidate_params.get("connection") or "")
        score = 12
        if "给排水管道" in quota_name:
            score += 10
        elif any(word in quota_name for word in ("给水", "排水", "室内", "室外")):
            score += 4
        score -= sum(8 for word in PIPE_ELECTRICAL_QUOTA_WORDS if word in quota_name)
        score += sum(8 for word in usage_words if word in quota_name)
        score += sum(3 for word in location_words if word in quota_name)

        if bill_material:
            if bill_material in quota_name:
                score += 10
            elif bill_material == "钢塑复合管" and any(word in quota_name for word in ("钢塑复合管", "PSP", "衬塑钢管")):
                score += 8
            elif "复合管" in bill_material and "复合管" in quota_name:
                score += 4

        if bill_connection and candidate_connection:
            if bill_connection == candidate_connection:
                score += 6
            elif connections_compatible(bill_connection, candidate_connection):
                score += 4
            else:
                score -= 6

        if bill_dn is not None:
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 8
                elif candidate_dn > bill_dn:
                    score += 2
                else:
                    score -= 8

        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_support_family_candidate(bill_text: str,
                                            candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    bill_params = text_parser.parse(text)
    bill_support_scope = str(bill_params.get("support_scope") or "")
    bill_support_action = str(bill_params.get("support_action") or "")
    bill_weight = bill_params.get("weight_t")
    bill_support_material = str(bill_params.get("support_material") or "")
    prefer_aseismic = "抗震" in text or bill_support_scope == "抗震支架"
    prefer_side = "侧向" in text
    prefer_longitudinal = "纵向" in text
    prefer_single = "单管" in text
    prefer_multi = any(keyword in text for keyword in ("多管", "双管", "两管", "多管道", "两管道"))
    prefer_door_frame = "门型" in text
    generic_pipe_support = any(keyword in text for keyword in ("按需制作", "一般管架"))
    prefer_equipment = bill_support_scope == "设备支架" or any(
        keyword in text for keyword in ("设备支架", "设备吊架", "设备支吊架")
    )
    prefer_duct = any(
        keyword in text for keyword in ("通风", "空调", "风管", "风口")
    )
    if not any(keyword in text for keyword in ("支架", "吊架", "支吊架", "支撑架")):
        return None

    prefer_bridge = (
        bill_support_scope == "桥架支架"
        or any(keyword in text for keyword in ("桥架", "电缆桥架", "桥架支撑架", "桥架侧纵向"))
    )
    prefer_pipe = (
        bill_support_scope == "管道支架"
        or any(keyword in text for keyword in ("管道", "管架", "管道支架", "给排水", "消防水", "喷淋", "消火栓", "水管"))
    )
    prefer_fabrication = (
        bill_support_action in {"制作", "制作安装"}
        or any(keyword in text for keyword in ("图集", "详见图集", "制作", "单件重量", "型钢"))
    )
    if not prefer_bridge and not prefer_pipe and not prefer_equipment and not prefer_duct and not prefer_aseismic:
        return None

    support_anchor_words = ("支架", "吊架", "支吊架", "支撑架", "管架", "抗震")
    surface_process_words = ("除锈", "刷油", "油漆", "防锈漆", "红丹", "银粉漆", "调和漆")
    support_special_shape_words = ("木垫式", "弹簧式", "侧向", "纵向", "门型", "单管", "多管", "双管", "两管")
    support_action_words = ("制作", "安装", "制作安装", "制安")
    equipment_support_words = ("设备支架", "设备吊架", "设备及部件支架")
    bridge_support_words = ("桥架支撑架", "电缆桥架")
    pipe_support_words = ("管架", "管道支架", "管道支吊架", "吊托支架", "支吊架")
    instrument_support_words = ("仪表支架", "仪表支吊架")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_support_scope = str(candidate_params.get("support_scope") or "")
        candidate_support_action = str(candidate_params.get("support_action") or "")
        candidate_support_material = str(candidate_params.get("support_material") or "")
        has_support_anchor = (
            bool(candidate_support_scope)
            or any(word in quota_name for word in support_anchor_words)
        )
        if not has_support_anchor:
            continue

        candidate_is_surface_process = (
            any(word in quota_name for word in surface_process_words)
            and not any(word in quota_name for word in support_action_words)
        )
        if candidate_is_surface_process:
            continue
        if (
            any(word in quota_name for word in surface_process_words)
            and not any(word in text for word in surface_process_words)
        ):
            continue

        score = 0
        if bill_support_scope and candidate_support_scope:
            if bill_support_scope == candidate_support_scope:
                score += 12
            elif (
                bill_support_scope == "抗震支架"
                and candidate_support_scope in {"桥架支架", "管道支架"}
            ):
                score += 2
            else:
                score -= 12
        if bill_support_action and candidate_support_action:
            if bill_support_action == candidate_support_action:
                score += 10
            elif bill_support_action == "制作" and candidate_support_action == "制作安装":
                score -= 4
            elif bill_support_action == "安装" and candidate_support_action == "制作安装":
                score -= 2
            else:
                score -= 8
        if bill_support_material and candidate_support_material:
            if bill_support_material == candidate_support_material:
                score += 8
            else:
                score -= 8

        if prefer_aseismic:
            if "抗震" in quota_name:
                score += 12
            elif prefer_bridge and any(word in quota_name for word in bridge_support_words):
                score += 2
            elif (prefer_pipe or prefer_duct) and any(word in quota_name for word in pipe_support_words):
                score += 2
            elif any(word in quota_name for word in ("一般管架", "支撑架制作", "桥架支撑架制作")):
                score -= 4
        elif "抗震" in quota_name:
            score -= 8

        if prefer_bridge:
            if any(word in quota_name for word in bridge_support_words):
                score += 12
            if any(word in quota_name for word in ("支架制作", "支架安装")) and "桥架" in quota_name:
                score += 6
            if any(word in quota_name for word in pipe_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 8
        elif prefer_pipe:
            if any(word in quota_name for word in pipe_support_words):
                score += 8
            if any(word in quota_name for word in bridge_support_words):
                score -= 10
            if generic_pipe_support and "一般管架" in quota_name:
                score += 10
            for word in support_special_shape_words:
                if word in quota_name and word not in text:
                    score -= 12
            if any(word in quota_name for word in instrument_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 10
        elif prefer_duct:
            if any(word in quota_name for word in ("支吊架", "吊托支架", "风管支吊架")):
                score += 8
            if any(word in quota_name for word in bridge_support_words):
                score -= 10
            if any(word in quota_name for word in instrument_support_words):
                score -= 10
            if any(word in quota_name for word in equipment_support_words):
                score -= 8
        elif prefer_equipment:
            if any(word in quota_name for word in equipment_support_words):
                score += 12
            if any(word in quota_name for word in pipe_support_words + bridge_support_words):
                score -= 10
            if any(word in quota_name for word in ("单件重量", "每个支架重量", "每组重量", "kg", "重量")):
                score += 6

        if prefer_side:
            if "侧向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("纵向", "门型")):
                score -= 8
        if prefer_longitudinal:
            if "纵向" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "门型")):
                score -= 8
        if prefer_door_frame:
            if "门型" in quota_name:
                score += 8
            elif any(word in quota_name for word in ("侧向", "纵向")):
                score -= 6
        if prefer_single:
            if "单管" in quota_name:
                score += 6
            elif "多管" in quota_name:
                score -= 6
        if prefer_multi:
            if any(word in quota_name for word in ("多管", "多根")):
                score += 6
            elif any(word in quota_name for word in ("单管", "单根")):
                score -= 6
        if prefer_fabrication:
            if "制作" in quota_name:
                score += 10
            if any(word in quota_name for word in ("单件重量", "kg", "重量")):
                score += 6
            if any(word in quota_name for word in ("安装", "一般管架")):
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


def _pick_explicit_insulation_family_candidate(bill_text: str,
                                               candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("绝热", "保温", "保冷", "防潮层", "保护层")):
        return None

    bill_params = text_parser.parse(text)
    bill_thickness = bill_params.get("thickness")
    prefer_pipe = any(keyword in text for keyword in (
        "管道", "给水", "排水", "采暖", "消防", "风管", "阀门", "法兰", "弯头",
    ))
    prefer_equipment = any(keyword in text for keyword in (
        "设备", "容器", "储罐", "塔器", "换热器", "机组",
    ))
    insulation_words = ("绝热", "保温", "保冷", "防潮层", "保护层")
    pipe_anchor_words = ("管道", "风管", "管壳", "弯头", "法兰", "阀门", "给排水")
    equipment_anchor_words = ("设备", "立式设备", "卧式设备", "容器", "储罐", "塔器", "换热器")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if not any(word in quota_name for word in insulation_words):
            continue
        candidate_params = text_parser.parse(quota_name)
        score = sum(5 for word in insulation_words if word in quota_name)

        if prefer_pipe:
            if any(word in quota_name for word in pipe_anchor_words):
                score += 12
            if any(word in quota_name for word in equipment_anchor_words):
                score -= 12
        if prefer_equipment:
            if any(word in quota_name for word in equipment_anchor_words):
                score += 10
            if any(word in quota_name for word in pipe_anchor_words):
                score -= 10

        if "防潮层" in text and "防潮层" in quota_name:
            score += 6
        if "保护层" in text and "保护层" in quota_name:
            score += 6

        if bill_thickness is not None:
            candidate_thickness = candidate_params.get("thickness")
            if candidate_thickness is not None:
                if candidate_thickness == bill_thickness:
                    score += 8
                elif candidate_thickness > bill_thickness:
                    score += 2
                else:
                    score -= 6

        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)
