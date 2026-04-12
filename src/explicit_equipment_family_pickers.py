# -*- coding: utf-8 -*-
"""Explicit family pickers for equipment-like installation families."""

from __future__ import annotations

from src.compat_primitives import connections_compatible
from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser


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


def _pick_explicit_distribution_box_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not any(
        keyword in text
        for keyword in (
            "\u914d\u7535\u7bb1",
            "\u914d\u7535\u67dc",
            "\u63a7\u5236\u7bb1",
            "\u63a7\u5236\u67dc",
            "\u52a8\u529b\u7bb1",
            "\u7167\u660e\u7bb1",
            "\u7a0b\u5e8f\u63a7\u5236\u7bb1",
        )
    ):
        return None

    bill_params = text_parser.parse(text)
    install_method = str(bill_params.get("install_method") or "")
    box_mount_mode = str(bill_params.get("box_mount_mode") or "")
    prefer_floor = any(
        keyword in text for keyword in ("\u843d\u5730", "\u67dc\u57fa\u7840", "\u57fa\u7840\u69fd\u94a2")
    )
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

    bill_half_perimeter = bill_params.get("half_perimeter")
    bill_circuits = bill_params.get("circuits")
    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        candidate_params = text_parser.parse(quota_name)
        candidate_box_mount_mode = str(candidate_params.get("box_mount_mode") or "")
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
        score = 0
        if prefer_floor:
            if "\u843d\u5730" in quota_name:
                score += 12
            if any(
                word in quota_name
                for word in ("\u60ac\u6302", "\u5d4c\u5165", "\u5899\u4e0a", "\u67f1\u4e0a", "\u6302\u5899")
            ):
                score -= 10
        if prefer_wall:
            if any(
                word in quota_name
                for word in ("\u60ac\u6302", "\u5d4c\u5165", "\u5899\u4e0a", "\u67f1\u4e0a", "\u6302\u5899")
            ):
                score += 12
            if "\u843d\u5730" in quota_name:
                score -= 10
        if box_mount_mode and candidate_box_mount_mode:
            if box_mount_mode == candidate_box_mount_mode:
                score += 10
            else:
                score -= 10
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
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_motor_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    from src.explicit_framework_family_pickers import _pick_explicit_motor_family_candidate as _delegate

    return _delegate(bill_text, candidates)


def _pick_explicit_valve_family_candidate(bill_text: str,
                                          candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    upper_text = text.upper()
    if "\u5012\u6d41\u9632\u6b62\u5668" in text:
        return None
    if not any(
        keyword in text
        for keyword in (
            "\u87ba\u7eb9\u9600\u95e8",
            "\u710a\u63a5\u6cd5\u5170\u9600\u95e8",
            "\u6cd5\u5170\u9600\u95e8",
            "\u87ba\u7eb9\u6cd5\u5170\u9600\u95e8",
            "\u78b3\u94a2\u9600\u95e8",
            "\u5851\u6599\u9600\u95e8",
            "PPR\u9600\u95e8",
            "PP-R\u9600\u95e8",
        )
    ):
        return None
    if any(
        keyword in text
        for keyword in (
            "\u98ce\u9600",
            "\u9632\u706b\u9600",
            "\u8c03\u8282\u9600",
            "\u591a\u53f6\u8c03\u8282\u9600",
            "\u5b9a\u98ce\u91cf\u9600",
            "\u4eba\u9632",
            "\u5bc6\u95ed\u9600",
        )
    ):
        return None

    bill_params = text_parser.parse(text)
    bill_dn = bill_params.get("dn")
    bill_connection = bill_params.get("connection")
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

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        if "\u9600" not in quota_name:
            continue
        candidate_params = text_parser.parse(quota_name)
        candidate_valve_family = str(candidate_params.get("valve_connection_family") or "")
        candidate_valve_type = str(candidate_params.get("valve_type") or "")
        score = 0
        if "\u9600\u95e8" in quota_name:
            score += 8
        if bill_valve_family and candidate_valve_family:
            if bill_valve_family == candidate_valve_family:
                score += 10
            else:
                score -= 10
        score += sum(6 for word in prefer_words if word and word in quota_name)
        score -= sum(10 for word in forbidden_words if word and word in quota_name)
        if bill_valve_type and candidate_valve_type:
            if bill_valve_type == candidate_valve_type:
                score += 8
            else:
                score -= 8
        if bill_dn is not None:
            candidate_dn = candidate_params.get("dn")
            if candidate_dn is not None:
                if candidate_dn == bill_dn:
                    score += 10
                elif candidate_dn > bill_dn:
                    gap = candidate_dn - bill_dn
                    score += max(1, 4 - gap / 25)
                else:
                    score -= 10
        if bill_connection:
            candidate_connection = candidate_params.get("connection")
            if candidate_connection:
                if candidate_connection == bill_connection:
                    score += 4
                elif not connections_compatible(bill_connection, candidate_connection):
                    score -= 5
        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _pick_explicit_equipment_family_candidate(bill_text: str,
                                              candidates: list[dict]) -> dict | None:
    text = bill_text or ""
    if not text:
        return None
    if "水泵接合器" in text:
        return None
    if any(
        keyword in text
        for keyword in ("坐便", "坐便器", "蹲便", "蹲便器", "小便器", "连体水箱", "高水箱", "低水箱", "隐蔽水箱", "隐藏水箱")
    ):
        return None

    category = ""
    expected_words: list[str] = []
    prefer_words: list[str] = []
    forbidden_words = ["风机", "风口", "风阀", "水泵接合器", "水表", "阀门"]

    if "气压罐" in text:
        category = "pressure_tank"
        expected_words = ["气压罐"]
        forbidden_words.extend(["水箱", "便器", "风机"])
    elif "水箱" in text:
        category = "water_tank"
        expected_words = ["水箱"]
        prefer_words = ["生活", "不锈钢", "整体"]
        forbidden_words.extend(["气压罐", "连体水箱", "高水箱", "低水箱", "隐藏水箱", "隐蔽水箱", "便器"])
    elif any(keyword in text for keyword in ("变频泵组", "变频给水设备", "变频供水设备", "稳压设备", "加压泵组")):
        category = "pump_group"
        expected_words = ["变频"]
        prefer_words = ["泵组", "给水设备", "供水设备", "稳压设备"]
        forbidden_words.extend(["气压罐", "水箱", "风机"])
    elif any(keyword in text for keyword in ("潜污泵", "潜水泵", "排污泵", "污水泵", "水泵", "离心泵")):
        category = "pump"
        expected_words = ["泵"]
        prefer_words = ["潜污泵", "潜水泵", "排污泵", "污水泵", "水泵", "离心泵"]
        forbidden_words.extend(["气压罐", "水箱", "风机"])
    else:
        return None

    scored: list[tuple[tuple[int, float, float], dict]] = []
    for candidate in candidates:
        quota_name = candidate.get("name", "") or ""
        score = 0
        score += sum(10 for word in expected_words if word and word in quota_name)
        score += sum(5 for word in prefer_words if word and word in quota_name)
        score -= sum(12 for word in forbidden_words if word and word in quota_name)

        if category == "water_tank":
            if "水箱" in quota_name:
                score += 10
            if any(keyword in quota_name for keyword in ("制作", "安装", "整体")):
                score += 2
        elif category == "pressure_tank":
            if "气压罐" in quota_name:
                score += 12
        elif category == "pump_group":
            if any(keyword in quota_name for keyword in ("变频", "泵组", "给水设备", "供水设备", "稳压设备")):
                score += 12
            if "水泵" in quota_name and not any(keyword in quota_name for keyword in ("变频", "泵组", "给水设备", "供水设备")):
                score -= 8
        elif category == "pump":
            if any(keyword in quota_name for keyword in ("潜污泵", "潜水泵", "排污泵", "污水泵", "水泵", "离心泵")):
                score += 10
            if "机组" in quota_name:
                score -= 6

        if score <= 0:
            continue
        scored.append(score_candidate(candidate, score))

    return pick_best_candidate(scored)


def _promote_explicit_distribution_box_candidate(item: dict,
                                                 candidates: list[dict]) -> tuple[list[dict], dict]:
    ordered = [dict(candidate) for candidate in (candidates or [])]
    if not ordered:
        return ordered, {}

    bill_text = " ".join(
        str(part or "").strip()
        for part in (item.get("name"), item.get("description"))
        if str(part or "").strip()
    )
    recommended = _pick_explicit_distribution_box_candidate(bill_text, ordered)
    if not recommended:
        return ordered, {}

    recommended_id = str(recommended.get("quota_id", "") or "").strip()
    if not recommended_id:
        return ordered, {}

    top1 = ordered[0]
    original_top_quota_id = str(top1.get("quota_id", "") or "").strip()
    if not original_top_quota_id or recommended_id == original_top_quota_id:
        return ordered, {}

    winner = next(
        (candidate for candidate in ordered if str(candidate.get("quota_id", "") or "").strip() == recommended_id),
        None,
    )
    if winner is None:
        return ordered, {}

    signal = {
        "reason": "distribution_box_family_advisory",
        "original_top_quota_id": original_top_quota_id,
        "recommended_quota_id": recommended_id,
        "applied": False,
        "advisory_applied": True,
    }
    winner["explicit_recommended"] = True
    winner.setdefault("explicit_signals", []).append({**signal, "role": "recommended"})
    top1.setdefault("explicit_signals", []).append({**signal, "role": "current_top1"})
    return ordered, signal
