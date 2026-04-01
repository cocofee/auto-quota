# -*- coding: utf-8 -*-
"""Explicit family pickers for cable and wiring installation families."""

from __future__ import annotations

import re

from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser


def _infer_cable_conductor_anchor(text: str, parsed: dict | None = None) -> str:
    parsed = parsed or {}
    wire_type = str(parsed.get("wire_type") or "").upper()
    material = str(parsed.get("material") or "")
    combined = f"{text or ''} {material}".upper()
    if "铝合金" in combined:
        return "铝合金"
    if any(keyword in combined for keyword in ("铝芯", "压铝", "铝电缆")):
        return "铝芯"
    if any(keyword in combined for keyword in ("铜芯", "压铜", "铜电缆")):
        return "铜芯"
    if wire_type.startswith(("YJLV", "VLV", "VLL", "YJHLV")):
        return "铝芯"
    if wire_type.startswith((
        "BPYJV", "YJV", "YJY", "VV", "KYJY", "KVV", "KVVP",
        "BTLY", "BTTRZ", "BTTZ", "YTTW", "BBTRZ",
    )):
        return "铜芯"
    return ""


def _extract_cable_head_craft_anchor(text: str) -> str:
    raw = str(text or "")
    if any(keyword in raw for keyword in ("热缩", "冷缩", "热(冷)缩", "热（冷）缩")):
        return "热缩"
    if "浇注" in raw:
        return "浇注"
    if "干包" in raw:
        return "干包"
    return ""


def _pick_explicit_cable_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    """对明确电缆样本，优先按家族与芯数/截面/终端头类型重选候选。"""
    text = bill_text or ""
    upper_text = text.upper()
    if "电缆" not in text:
        return None

    bill_params = text_parser.parse(text)
    bill_cores = bill_params.get("cable_cores")
    bill_section = bill_params.get("cable_section")
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
    bill_cable_type = str(bill_params.get("cable_type") or "")
    bill_head_type = str(bill_params.get("cable_head_type") or "")
    bill_conductor = _infer_cable_conductor_anchor(text, bill_params)
    bill_craft = _extract_cable_head_craft_anchor(text)
    bill_voltage = "35kV" if "35KV" in upper_text or "35KV" in upper_text else (
        "10kV" if "10KV" in upper_text or "10KV" in upper_text else (
            "1kV" if any(token in upper_text for token in ("0.6/1KV", "1KV")) else ""
        )
    )

    is_head = any(keyword in text for keyword in ("终端头", "电缆头", "中间头"))
    is_middle_head = "中间头" in text
    is_control = (
        "控制" in text
        or bill_cable_type == "控制电缆"
        or any(keyword in upper_text for keyword in ("KVV", "KVVP", "KVVR", "RVVSP", "RVSP"))
    )
    is_power = not is_control

    expected_words: list[str] = []
    forbidden_words: list[str] = []
    core_words: list[str] = []

    if is_control:
        expected_words.append("控制电缆")
        forbidden_words.append("电力电缆")
    elif is_power:
        expected_words.append("电力电缆")
        forbidden_words.append("控制电缆")

    if is_head:
        if is_middle_head:
            expected_words.append("中间头")
            forbidden_words.extend(["终端头", "电缆头"])
        else:
            expected_words.extend(["终端头", "电缆头"])
            forbidden_words.append("中间头")
    else:
        forbidden_words.extend(["终端头", "电缆头", "中间头"])

    if "单芯" in text:
        core_words.append("单芯")
    if "四芯" in text or re.search(r"4\s*[×xX*]", text):
        core_words.append("四芯")
    if "五芯" in text or re.search(r"5\s*[×xX*]", text):
        core_words.append("五芯")

    core_count_match = re.search(r"(\d+)\s*[×xX*]\s*\d+(?:\.\d+)?", text)
    if core_count_match:
        core_count = int(core_count_match.group(1))
        if is_control or is_head:
            core_words.extend([f"<={core_count}", f"{core_count}芯", f"{core_count}"])

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_laying_method = str(candidate_params.get("laying_method") or "")
        candidate_wire_type = str(candidate_params.get("wire_type") or "")
        candidate_cable_type = str(candidate_params.get("cable_type") or "")
        candidate_head_type = str(candidate_params.get("cable_head_type") or "")
        candidate_conductor = _infer_cable_conductor_anchor(quota_name, candidate_params)
        candidate_craft = _extract_cable_head_craft_anchor(quota_name)
        score = 0
        score += sum(8 for word in expected_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        score += sum(4 for word in core_words if word and word in quota_name)
        if not is_head and any(keyword in quota_name for keyword in ("终端头", "电缆头", "中间头")):
            score -= 20
        if is_head and "敷设" in quota_name and not any(keyword in quota_name for keyword in ("终端头", "电缆头", "中间头")):
            score -= 16
        candidate_cores = candidate_params.get("cable_cores")
        if bill_cores is not None and candidate_cores is not None:
            if bill_cores == candidate_cores:
                score += 8
            elif bill_cores < candidate_cores:
                score += 4
            else:
                score -= 10
        candidate_section = candidate_params.get("cable_section")
        if bill_section is not None and candidate_section is not None:
            if bill_section == candidate_section:
                score += 6
            elif bill_section < candidate_section:
                score += 3
            else:
                score -= 8
        if bill_laying_method:
            if (
                candidate_laying_method and (
                    bill_laying_method == candidate_laying_method
                    or any(token and token in candidate_laying_method for token in bill_laying_method.split("/"))
                )
            ) or (
                ("穿管" in bill_laying_method and any(token in quota_name for token in ("穿导管", "穿管", "管内")))
                or ("桥架" in bill_laying_method and "桥架" in quota_name)
                or ("线槽" in bill_laying_method and "线槽" in quota_name)
                or ("排管" in bill_laying_method and "排管" in quota_name)
                or ("直埋" in bill_laying_method and "埋地" in quota_name)
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
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_wiring_family_candidate(bill_text: str,
                                           candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(keyword in text for keyword in ("配线", "电气配线", "线槽配线", "管内穿线", "穿线")):
        return None
    if "电缆" in text:
        return None

    bill_params = text_parser.parse(text)
    bill_cores = bill_params.get("cable_cores")
    bill_section = bill_params.get("cable_section")
    bill_laying_method = str(bill_params.get("laying_method") or "")
    bill_wire_type = str(bill_params.get("wire_type") or "")
    upper_text = text.upper()

    prefer_words: list[str] = []
    forbidden_words: list[str] = []

    if any(keyword in text for keyword in ("线槽配线", "线槽", "槽内")):
        prefer_words.extend(["线槽配线", "线槽"])
        forbidden_words.extend(["管内穿", "桥架内布放"])
    elif any(keyword in text for keyword in ("管内", "穿管", "穿线管")):
        prefer_words.extend(["管内穿", "穿线"])
        forbidden_words.extend(["线槽配线", "桥架内布放"])
    else:
        prefer_words.append("配线")

    if any(keyword in upper_text for keyword in ("RY", "RYS")):
        prefer_words.extend(["软导线", "多芯软导线"])
    elif any(keyword in upper_text for keyword in ("BYJ", "BV")):
        prefer_words.extend(["导线", "铜芯"])

    if bill_cores == 1:
        prefer_words.append("单芯")
        forbidden_words.extend(["二芯", "三芯", "多芯"])
    elif bill_cores is not None and bill_cores > 1:
        prefer_words.extend(["多芯", f"{int(bill_cores)}芯"])
        forbidden_words.append("单芯")
        if bill_cores <= 2:
            prefer_words.append("二芯")

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_laying_method = str(candidate_params.get("laying_method") or "")
        candidate_wire_type = str(candidate_params.get("wire_type") or "")
        score = sum(8 for word in prefer_words if word and word in quota_name)
        score -= sum(8 for word in forbidden_words if word and word in quota_name)
        if bill_section is not None:
            candidate_section = candidate_params.get("cable_section")
            if candidate_section is not None:
                if candidate_section == bill_section:
                    score += 6
                elif candidate_section > bill_section:
                    score += 3
                else:
                    score -= 8
        if bill_laying_method:
            if (
                candidate_laying_method and (
                    bill_laying_method == candidate_laying_method
                    or any(token and token in candidate_laying_method for token in bill_laying_method.split("/"))
                )
            ) or (
                ("穿管" in bill_laying_method and any(token in quota_name for token in ("管内穿", "穿线", "穿管")))
                or ("线槽" in bill_laying_method and "线槽" in quota_name)
                or ("桥架" in bill_laying_method and "桥架" in quota_name)
            ):
                score += 6
            elif candidate_laying_method:
                score -= 6
        if bill_wire_type and candidate_wire_type:
            score += 4 if bill_wire_type == candidate_wire_type else -4
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)
