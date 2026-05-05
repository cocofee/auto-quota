from __future__ import annotations

import re

from src.text_parser import parser as text_parser


def _text(value: object) -> str:
    return str(value or "").strip()


def _item_text(item: dict, context: dict | None = None) -> str:
    context = context or {}
    parts = [
        item.get("bill_text"),
        item.get("bill_name"),
        item.get("name"),
        item.get("description"),
        context.get("bill_text"),
        context.get("query_text"),
    ]
    return " ".join(_text(part) for part in parts if _text(part))


def _candidate_text(candidate: dict) -> str:
    parts = [
        candidate.get("name"),
        candidate.get("quota_name"),
        candidate.get("description"),
        candidate.get("standard_name"),
    ]
    return " ".join(_text(part) for part in parts if _text(part))


def _candidate_prefix(candidate: dict) -> str:
    quota_id = _text(candidate.get("quota_id")).upper()
    parts = quota_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return quota_id


def _is_distribution_box_text(text: str) -> bool:
    return any(term in text for term in ("配电箱", "控制箱", "动力箱", "照明箱"))


def _is_switch_text(text: str) -> bool:
    return "开关" in text and "压力开关" not in text


def _is_control_box_install_only_text(text: str) -> bool:
    return "控制箱" in text and any(term in text for term in ("设备自带", "仅考虑安装费", "仅安装费"))


def _is_dual_power_lighting_box_text(text: str) -> bool:
    upper = text.upper()
    if "双电源" in text:
        return True
    return bool(re.search(r"(^|[-_\s])AT([-_\s]|$)", upper)) and "ALE" in upper


def _is_beijing_wall_circuit_box(candidate_text: str) -> bool:
    return "配电箱" in candidate_text and "墙上" in candidate_text and "回路" in candidate_text


def _rank_param_candidate(
    *,
    rank: int,
    candidate: dict,
    item_param: int,
    param_name: str,
    family: str,
) -> dict | None:
    candidate_text = _candidate_text(candidate)
    quota_id = _text(candidate.get("quota_id"))
    if _candidate_prefix(candidate) != "C4-4":
        return None
    if family == "distribution_box" and not _is_distribution_box_text(candidate_text):
        return None
    if family == "switch" and not _is_switch_text(candidate_text):
        return None
    if family == "control_box_install_only":
        if not _is_beijing_wall_circuit_box(candidate_text):
            return None
        params = text_parser.parse(candidate_text)
        candidate_param = params.get("circuits")
        score = 120 if quota_id == "C4-4-30" else 80
        if candidate_param is not None:
            score -= min(int(candidate_param), 20)
        return {
            "rank": rank,
            "quota_id": quota_id,
            "text": candidate_text,
            "param": int(candidate_param) if candidate_param is not None else None,
            "score": score,
            "candidate": candidate,
        }
    if family == "dual_power_lighting_box":
        if not _is_beijing_wall_circuit_box(candidate_text):
            return None
        params = text_parser.parse(candidate_text)
        candidate_param = params.get("circuits")
        if quota_id == "C4-4-31" or candidate_param == 8:
            score = 120
        elif quota_id == "C4-4-30" or candidate_param == 4:
            score = 70
        else:
            score = 60
        return {
            "rank": rank,
            "quota_id": quota_id,
            "text": candidate_text,
            "param": int(candidate_param) if candidate_param is not None else None,
            "score": score,
            "candidate": candidate,
        }

    params = text_parser.parse(candidate_text)
    candidate_param = params.get(param_name)
    has_param = candidate_param is not None
    if has_param:
        candidate_param = int(candidate_param)
        under = candidate_param < item_param
        overage = candidate_param - item_param
    else:
        under = False
        overage = 999

    score = 0
    if has_param and not under:
        score += 100 - min(overage, 50)
    elif under:
        score -= 80
    else:
        score -= 30

    if family == "distribution_box":
        if "箱体安装" in candidate_text or "半周长" in candidate_text:
            score -= 35
        if any(term in candidate_text for term in ("墙上", "柱上", "明装", "悬挂", "嵌入")):
            score += 8
    elif family == "switch":
        if "单控" in candidate_text:
            score += 4

    return {
        "rank": rank,
        "quota_id": _text(candidate.get("quota_id")),
        "text": candidate_text,
        "param": candidate_param if has_param else None,
        "score": score,
        "candidate": candidate,
    }


def apply_c4_count_tier_guard(
    item: dict,
    ltr_ranked: list[dict],
    context: dict | None = None,
) -> tuple[bool, str, dict, list[dict]]:
    if len(ltr_ranked) < 2:
        return False, "", {}, ltr_ranked

    item_text = _item_text(item, context)
    item_params = text_parser.parse(item_text)
    family = ""
    param_name = ""
    item_param = None
    if _is_distribution_box_text(item_text) and _is_dual_power_lighting_box_text(item_text):
        family = "dual_power_lighting_box"
        param_name = "circuits"
        item_param = int(item_params["circuits"]) if item_params.get("circuits") is not None else 8
    elif _is_distribution_box_text(item_text) and item_params.get("circuits") is not None:
        family = "distribution_box"
        param_name = "circuits"
        item_param = int(item_params["circuits"])
    elif _is_control_box_install_only_text(item_text):
        family = "control_box_install_only"
        param_name = "control_box_install_only"
        item_param = 4
    elif _is_switch_text(item_text) and item_params.get("switch_gangs") is not None:
        family = "switch"
        param_name = "switch_gangs"
        item_param = int(item_params["switch_gangs"])
    else:
        return False, "", {"item_text": item_text, "intent": "no_c4_count_intent"}, ltr_ranked

    inspected: list[dict] = []
    best: dict | None = None
    for rank, candidate in enumerate(ltr_ranked[:10], start=1):
        scored = _rank_param_candidate(
            rank=rank,
            candidate=candidate,
            item_param=item_param,
            param_name=param_name,
            family=family,
        )
        if scored is None:
            continue
        inspected.append({key: value for key, value in scored.items() if key != "candidate"})
        if best is None or scored["score"] > best["score"] or (
            scored["score"] == best["score"] and scored["rank"] < best["rank"]
        ):
            best = scored

    if best is None or best["score"] < 60:
        return False, "", {
            "item_text": item_text,
            "family": family,
            "param_name": param_name,
            "item_param": item_param,
            "inspected": inspected,
        }, ltr_ranked

    top = ltr_ranked[0]
    top_id = _text(top.get("quota_id"))
    if best["candidate"] is top:
        return True, "c4_count_tier_confirmed", {
            "item_text": item_text,
            "family": family,
            "param_name": param_name,
            "item_param": item_param,
            "rescued_rank": best["rank"],
            "rescued_quota_id": best["quota_id"],
            "top_quota_id": top_id,
            "inspected": inspected,
        }, ltr_ranked

    rescued = [best["candidate"]] + [candidate for candidate in ltr_ranked if candidate is not best["candidate"]]
    return True, "c4_count_tier_rescued", {
        "item_text": item_text,
        "family": family,
        "param_name": param_name,
        "item_param": item_param,
        "rescued_rank": best["rank"],
        "rescued_quota_id": best["quota_id"],
        "top_quota_id": top_id,
        "inspected": inspected,
    }, rescued


def apply_registered_ranking_guards(
    item: dict,
    ltr_ranked: list[dict],
    context: dict | None = None,
) -> tuple[bool, str, dict, list[dict]]:
    return apply_c4_count_tier_guard(item, ltr_ranked, context)
