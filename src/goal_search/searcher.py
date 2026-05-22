from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import config
from db.sqlite import connect as _db_connect
from src.goal_search.national_index import (
    QuotaSignal,
    _apply_structured_values,
    clean_text,
    extract_signal,
    is_pipe_device_false_trigger,
    key_text,
    query_same_cluster,
    tokenize,
)

try:
    from rank_bm25 import BM25Okapi
except Exception:  # pragma: no cover
    BM25Okapi = None


@dataclass(slots=True)
class GoalSearchItem:
    text: str
    unit: str = ""
    bill_name: str = ""
    specialty: str = ""
    no_answer_priors: bool = True


@dataclass(slots=True)
class GoalSearchHit:
    quota_id: str
    name: str
    unit: str
    score: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    source_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class _QuotaRecord:
    rowid: int
    quota_id: str
    name: str
    unit: str
    work_type: str = ""
    specialty: str = ""
    chapter: str = ""
    book: str = ""
    search_text: str = ""
    tokens: list[str] = field(default_factory=list)
    signal: QuotaSignal = field(default_factory=QuotaSignal)


@dataclass(slots=True)
class _PriorRecord:
    bill_text: str
    bill_name: str
    quota_ids: list[str]
    source: str
    confidence: float
    source_file: str = ""
    project_name: str = ""
    sample_id: str = ""
    tokens: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ShadowPriorRecord:
    bill_name: str
    quota_scores: dict[str, float]
    reasons: dict[str, list[str]] = field(default_factory=dict)
    source_file: str = ""
    project_name: str = ""
    sample_id: str = ""
    tokens: list[str] = field(default_factory=list)


def _safe_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(v) for v in value if clean_text(v)]
    text = clean_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [text]
    if isinstance(parsed, list):
        return [clean_text(v) for v in parsed if clean_text(v)]
    if isinstance(parsed, str) and "|" in parsed:
        return [part.strip() for part in parsed.split("|") if part.strip()]
    return [clean_text(parsed)] if clean_text(parsed) else []


def _quota_book(quota_id: str) -> str:
    qid = clean_text(quota_id)
    match = re.match(r"([A-Z]\d+)-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"([A-Z])-\d+-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"2-(\d+)-", qid)
    if match:
        return "2"
    match = re.match(r"(\d+)-", qid)
    if match:
        return match.group(1)
    return ""


def _book_matches(requested_book: str, candidate_book: str) -> bool:
    requested = clean_text(requested_book).upper()
    candidate = clean_text(candidate_book).upper()
    if not requested or not candidate:
        return False
    if requested == candidate:
        return True
    if requested.startswith("C") and requested[1:].isdigit() and candidate == requested[1:]:
        return True
    if candidate.startswith("C") and candidate[1:].isdigit() and requested == candidate[1:]:
        return True
    return False


def _bm25_scores(model: Any, tokens: list[str], count: int) -> list[float]:
    if not tokens or model is None:
        return [0.0] * count
    return [float(score) for score in model.get_scores(tokens)]


def _top_indices(scores: list[float], limit: int) -> list[int]:
    return sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:limit] if scores else []


def _normalize_score(value: float, max_value: float) -> float:
    if value <= 0 or max_value <= 0:
        return 0.0
    return min(1.0, value / max_value)


def _numeric_match_score(query_signal: QuotaSignal, quota: _QuotaRecord) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for key, weight in (
        ("dn", 0.18),
        ("cable_section", 0.15),
        ("circuits", 0.12),
        ("concrete_grade", 0.12),
        ("thickness", 0.10),
    ):
        qv = getattr(query_signal, key)
        cv = getattr(quota.signal, key)
        if qv is None or cv is None:
            continue
        qf = float(qv)
        cf = float(cv)
        if math.isclose(qf, cf, rel_tol=0.0, abs_tol=0.01):
            score += weight
            reasons.append(f"{key} exact")
        elif key in {"dn", "cable_section", "circuits", "thickness"} and cf >= qf:
            gap = (cf - qf) / max(qf, 1.0)
            if gap <= 1.0:
                score += weight * max(0.25, 1.0 - gap)
                reasons.append(f"{key} tier_up")
            else:
                score -= weight * min(1.0, gap / 2.0)
                reasons.append(f"{key} loose_tier")
        else:
            distance = abs(qf - cf) / max(qf, cf, 1.0)
            if distance <= 0.20:
                score += weight * (1.0 - distance)
                reasons.append(f"{key} near")
            else:
                score -= weight * min(1.0, distance)
                reasons.append(f"{key} conflict")
    if query_signal.cable_cores is not None and quota.signal.cable_cores is not None:
        if query_signal.cable_cores == quota.signal.cable_cores:
            score += 0.14
            reasons.append("cable_cores exact")
        elif query_signal.cable_cores == 3 and quota.signal.cable_cores == 4:
            score += 0.08
            reasons.append("cable_cores three_as_four")
        else:
            score -= 0.14 if query_signal.cable_cores > 1 and quota.signal.cable_cores == 1 else 0.08
            reasons.append("cable_cores conflict")
    return score, reasons


def _field_match_score(query_signal: QuotaSignal, quota: _QuotaRecord) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for key, weight in (
        ("family", 0.18),
        ("action", 0.06),
        ("material", 0.08),
        ("connection", 0.08),
        ("install_method", 0.05),
    ):
        qv = clean_text(getattr(query_signal, key))
        cv = clean_text(getattr(quota.signal, key))
        if not qv:
            continue
        if not cv:
            if key == "family":
                score -= 0.28
                reasons.append(f"family missing:{qv}")
            continue
        if qv == cv:
            score += weight
            reasons.append(f"{key}:{qv}")
        elif key == "family":
            score -= 0.22
            reasons.append(f"family conflict:{qv}!={cv}")
    return score, reasons


def _overlap_score(left: Iterable[str], right: Iterable[str]) -> float:
    a = {x for x in left if x}
    b = {x for x in right if x}
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), 1)


DOMAIN_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bridge_steel", ("钢制", "金属", "槽式")),
    ("bridge_tray", ("桥架",)),
    ("wire_in_conduit", ("管内", "穿线", "穿管", "铜芯", "照明线路")),
    ("wire_panel", ("盘柜", "配线架", "车间配线", "钢索")),
    ("wire_single_lighting", ("byj", "铜芯线", "照明线路")),
    ("wire_soft_multi", ("rvs", "kvv", "多芯", "软导线", "二芯", "四芯", "六芯")),
    ("sanitary_floor_drain", ("地漏",)),
    ("sanitary_sink", ("水槽", "洗脸盆", "洗手盆", "拖布池")),
    ("sanitary_faucet", ("水龙头",)),
    ("sanitary_urinal", ("小便器", "小便斗")),
    ("sanitary_squat", ("蹲便器", "蹲式", "大便器")),
    ("sanitary_closet", ("坐便器", "座便器", "坐式")),
    ("sanitary_mop_sink", ("拖布池", "拖把池")),
    ("lamp_downlight", ("筒灯", "射灯", "点光源", "嵌入")),
    ("lamp_panel", ("平板灯", "吸顶")),
    ("lamp_linear", ("线形灯", "线型灯", "长条led灯", "led线形", "灯带", "荧光", "天棚")),
    ("lamp_track", ("轨道", "滑轨", "射灯")),
    ("fan", ("风机", "通风机", "排风机", "换气扇", "排气扇", "风扇")),
    ("socket", ("插座", "单相", "二三极", "三孔", "五孔")),
    ("switch", ("开关", "单控", "双联", "三联", "跷板")),
)


def _domain_labels(text: str) -> set[str]:
    compact = key_text(text)
    labels: set[str] = set()
    for label, terms in DOMAIN_TERMS:
        if any(term in compact for term in terms):
            labels.add(label)
    return labels


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _book_of_record(quota: _QuotaRecord) -> str:
    return clean_text(quota.book).upper() or _quota_book(quota.quota_id)


def _first_number_after(text: str, markers: Iterable[str]) -> float | None:
    for marker in markers:
        pos = text.find(marker)
        if pos < 0:
            continue
        match = re.search(r"(\d+(?:\.\d+)?)", text[pos + len(marker) :])
        if match:
            return float(match.group(1))
    return None


def _first_pair_sum(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[x*×]\s*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1)) + float(match.group(2))


def _first_pair_first(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[x*×]\s*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def _first_pair_perimeter(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[x*×]\s*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return 2.0 * (float(match.group(1)) + float(match.group(2)))


def _tier_limit_score(target: float | None, limit: float | None, weight: float) -> tuple[float, str]:
    if target is None or limit is None or target <= 0:
        return 0.0, ""
    if math.isclose(target, limit, rel_tol=0.0, abs_tol=0.01):
        return weight, "exact"
    if limit >= target:
        gap = (limit - target) / max(target, 1.0)
        if gap <= 1.5:
            return weight * max(0.2, 1.0 - gap / 1.5), "tier_up"
        return -weight * 0.45, "loose_tier"
    return -weight * min(1.0, (target - limit) / max(target, 1.0)), "below"


def _bridge_param_score(query_text: str, quota_text: str) -> tuple[float, list[str]]:
    query_target = _first_pair_sum(query_text)
    quota_limit = _first_number_after(quota_text, ("宽+高", "宽 高", "宽高"))
    score, kind = _tier_limit_score(query_target, quota_limit, 0.18)
    return (score, [f"bridge_size {kind}"]) if kind else (0.0, [])


def _duct_param_score(query_text: str, quota_text: str) -> tuple[float, list[str]]:
    query_target = _first_number_after(query_text, ("大边长", "长边长", "直径×壁厚", "直径 壁厚", "直径")) or _first_pair_first(query_text)
    quota_limit = _first_number_after(quota_text, ("长边长", "直径×壁厚", "直径 壁厚", "直径")) or _first_pair_first(quota_text)
    score, kind = _tier_limit_score(query_target, quota_limit, 0.16)
    return (score, [f"duct_size {kind}"]) if kind else (0.0, [])


def _first_air_volume(text: str) -> float | None:
    match = re.search(r"风量(?:\([^)]*\))?[^0-9]*(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def _fan_param_score(query_text: str, quota_text: str) -> tuple[float, list[str]]:
    score, kind = _tier_limit_score(_first_air_volume(query_text), _first_air_volume(quota_text), 0.22)
    return (score, [f"fan_air_volume {kind}"]) if kind else (0.0, [])


def _is_air_duct_run_query(query_compact: str, query_text: str) -> bool:
    if is_pipe_device_false_trigger(query_text):
        return False
    return _has_any(query_compact, ("通风管道", "风管", "碳钢风管", "碳钢通风管道", "复合型风管")) and not _has_any(
        query_compact, ("保温", "绝热", "隔热")
    )


def _apply_strong_name_signal(query_signal: QuotaSignal, bill_name: str) -> QuotaSignal:
    name_compact = key_text(bill_name)
    if not name_compact or is_pipe_device_false_trigger(bill_name):
        return query_signal
    strong_branch_pipe = _has_any(name_compact, ("分歧器", "分歧管"))
    strong_steel = _has_any(name_compact, ("无缝钢管", "焊接钢管", "镀锌钢管"))
    strong_air_duct = _is_air_duct_run_query(name_compact, bill_name)
    if not (strong_branch_pipe or strong_steel or strong_air_duct):
        return query_signal
    name_signal = extract_signal(bill_name)
    if strong_branch_pipe and name_signal.family == "pipe" and query_signal.family in {"", "sleeve", "support"}:
        query_signal.family = "pipe"
        query_signal.material = name_signal.material or "分歧管"
    elif strong_steel and name_signal.family == "pipe" and query_signal.family in {"", "sleeve", "support"}:
        query_signal.family = "pipe"
        query_signal.material = name_signal.material or "钢管"
    elif strong_air_duct and name_signal.family == "duct" and query_signal.family in {"", "sleeve", "support"}:
        query_signal.family = "duct"
        query_signal.material = name_signal.material or "风管"
    return query_signal


def _domain_term_score(query_text: str, query_signal: QuotaSignal, quota: _QuotaRecord) -> tuple[float, list[str]]:
    query_labels = _domain_labels(query_text)
    quota_labels = _domain_labels(quota.search_text) if query_labels else set()
    score = 0.0
    reasons: list[str] = []
    matched = query_labels & quota_labels
    if matched:
        score += min(0.24, 0.08 * len(matched))
        reasons.append("domain:" + ",".join(sorted(matched)[:3]))

    if query_signal.family and quota.signal.family and query_signal.family != quota.signal.family:
        return score, reasons

    query_compact = key_text(query_text)
    quota_compact = key_text(quota.search_text)
    raw_query_compact = re.sub(r"\s+", "", clean_text(query_text).lower())
    raw_quota_compact = re.sub(r"\s+", "", clean_text(quota.search_text).lower())
    if query_signal.family == "bridge":
        param_score, param_reasons = _bridge_param_score(query_text, quota.search_text)
        score += param_score
        reasons.extend(param_reasons)
        if "桥架" in raw_query_compact and "线槽" not in raw_query_compact and "线槽" in raw_quota_compact and "桥架" not in raw_quota_compact:
            score -= 0.36
            reasons.append("domain:line槽 conflict")
        if any(term in query_compact for term in ("普通强电", "桥架")) and "钢制" in quota_compact:
            score += 0.10
            reasons.append("domain:steel bridge")
        if "桥架" in query_compact and "桥架" in quota_compact and not _has_any(quota_compact, ("塑料线槽", "走线槽")):
            score += 0.14
            reasons.append("domain:bridge exact")
        if "桥架" in query_compact and _has_any(quota_compact, ("塑料线槽", "走线槽")):
            score -= 0.18
            reasons.append("domain:plastic line槽")
        if _has_any(query_compact, ("普通强电", "强电桥架", "桥架")) and not _has_any(
            query_compact, ("梯式", "铝合金", "玻璃钢", "网式", "托盘式")
        ):
            if "钢制槽式" in quota_compact:
                score += 0.22
                reasons.append("domain:default steel槽式 bridge")
            elif _has_any(quota_compact, ("梯式", "铝合金", "玻璃钢", "网式")):
                score -= 0.16
                reasons.append("domain:nondefault bridge form")
    elif query_signal.family == "wire":
        wire_in_conduit = any(term in query_compact for term in ("管内穿线", "穿管", "管内"))
        soft_multi_query = _has_any(query_compact, ("rvs", "kvv", "多芯", "软导线", "2x", "2*", "2×"))
        copper_lighting_query = _has_any(query_compact, ("byj", "bv", "bvr", "照明")) or ("铜芯" in query_compact and not soft_multi_query)
        if wire_in_conduit and any(term in quota_compact for term in ("盘柜", "配线架", "车间配线", "钢索")):
            score -= 0.20
            reasons.append("domain:wire route conflict")
        if wire_in_conduit and copper_lighting_query:
            if _has_any(quota_compact, ("管内穿照明线", "管内穿线", "照明线路")) and _has_any(quota_compact, ("铜芯", "铜芯线", "导线")):
                score += 0.36
                reasons.append("domain:wire conduit copper lighting")
            if _has_any(quota_compact, ("多芯软导线", "软导线", "双绞线", "电缆测试", "配线架")):
                score -= 0.24
                reasons.append("domain:wire conduit subtype conflict")
        if copper_lighting_query:
            if any(term in quota_compact for term in ("铜芯线", "照明线路")):
                score += 0.16
                reasons.append("domain:wire copper lighting")
            if any(term in quota_compact for term in ("多芯", "软导线", "配线架")):
                score -= 0.10
                reasons.append("domain:wire subtype conflict")
        if soft_multi_query:
            if any(term in quota_compact for term in ("多芯", "软导线", "二芯")):
                score += 0.16
                reasons.append("domain:wire soft multi")
    elif query_signal.family == "cable":
        quota_book = _book_of_record(quota)
        if "电力电缆" in query_compact:
            if "电力电缆" in quota_compact:
                score += 0.18
                reasons.append("domain:power cable")
            if "控制电缆" in quota_compact:
                score -= 0.28
                reasons.append("domain:control cable conflict")
        query_aluminum = _has_any(query_compact, ("铝芯", "铝合金电缆", "yjlv", "vlv"))
        query_copper = _has_any(query_compact, ("铜芯", "yjv", "yjy", "wdz", "nh-", "zr-", "btly", "bttz")) or (
            "电力电缆" in query_compact and not query_aluminum
        )
        quota_aluminum = _has_any(quota_compact, ("铝芯", "yjlv", "vlv"))
        quota_copper = "铜芯" in quota_compact
        if query_aluminum:
            if quota_aluminum:
                score += 0.16
                reasons.append("domain:aluminum cable")
            elif quota_copper:
                score -= 0.18
                reasons.append("domain:copper cable conflict")
        elif query_copper:
            if quota_copper:
                score += 0.16
                reasons.append("domain:copper cable")
            if quota_aluminum:
                score -= 0.26
                reasons.append("domain:aluminum cable conflict")
        route_specific = ("夹层", "轨旁", "电缆沟", "隧道", "排管", "直埋", "街码", "竖井", "架设")
        query_has_specific_route = _has_any(query_compact, route_specific)
        if "电力电缆" in query_compact and not query_has_specific_route:
            if quota_book in {"C4", "4"} and _has_any(quota_compact, ("电缆沿桥架", "室内敷设电力电缆", "铜芯电力电缆敷设")):
                score += 0.60
                reasons.append("domain:default cable book route")
            if quota_book.startswith("G5") or _has_any(quota_compact, route_specific):
                score -= 0.56
                reasons.append("domain:specific cable route weak")
            if _has_any(quota_compact, ("矿物绝缘电缆", "刚性矿物")) and not _has_any(query_compact, ("矿物", "btly", "bttz")):
                score -= 0.32
                reasons.append("domain:mineral cable conflict")
        if _has_any(query_compact, ("桥架", "线槽")):
            if _has_any(quota_compact, ("电缆沿桥架", "沿桥架", "线槽敷设")):
                score += 0.34
                reasons.append("domain:cable bridge route")
            elif _has_any(quota_compact, ("铜芯电力电缆敷设", "铝芯电力电缆敷设")):
                score -= 0.12
                reasons.append("domain:cable route missing")
    elif query_signal.family == "cable_head":
        query_aluminum = _has_any(query_compact, ("铝芯", "yjlv", "vlv"))
        query_copper = _has_any(query_compact, ("铜芯", "yjv", "yjy", "wdz", "nh-", "zr-", "btly", "bttz", "电力电缆"))
        quota_aluminum = _has_any(quota_compact, ("铝芯", "yjlv", "vlv"))
        quota_copper = "铜芯" in quota_compact
        if "电力电缆" in query_compact and "电力电缆" in quota_compact:
            score += 0.12
            reasons.append("domain:power cable head")
        if not _has_any(query_compact, ("中间头", "中间接头")):
            if "终端头" in quota_compact:
                score += 0.22
                reasons.append("domain:terminal cable head")
            if "中间头" in quota_compact:
                score -= 0.24
                reasons.append("domain:middle cable head conflict")
        if query_aluminum:
            if quota_aluminum:
                score += 0.12
                reasons.append("domain:aluminum cable head")
            elif quota_copper:
                score -= 0.16
                reasons.append("domain:copper cable head conflict")
        elif query_copper:
            if quota_copper:
                score += 0.12
                reasons.append("domain:copper cable head")
            if quota_aluminum:
                score -= 0.22
                reasons.append("domain:aluminum cable head conflict")
        if "铠装" not in query_compact and "铠装" in quota_compact and "非铠装" not in quota_compact:
            score -= 0.08
            reasons.append("domain:armored default weak")
        if "铠装" not in query_compact and "非铠装" in quota_compact:
            score += 0.08
            reasons.append("domain:non-armored default")
        if query_signal.cable_section is None and query_signal.cable_cores is None:
            if "5x16" in quota_compact:
                score += 0.16
                reasons.append("domain:default 5x16 cable head")
            elif "5x" in quota_compact:
                score += 0.08
                reasons.append("domain:default five-core cable head")
            elif "1x" in quota_compact:
                score -= 0.04
                reasons.append("domain:single-core cable head weak")
    elif query_signal.family == "fan":
        param_score, param_reasons = _fan_param_score(query_text, quota.search_text)
        score += param_score
        reasons.extend(param_reasons)
        if _has_any(quota_compact, ("风机", "通风机", "排风机", "换气扇", "排气扇", "风扇")):
            score += 0.34
            reasons.append("domain:fan equipment")
        if "轴流" in query_compact:
            if "轴流" in quota_compact:
                score += 0.18
                reasons.append("domain:axial fan")
            if _has_any(quota_compact, ("离心", "混流", "斜流")):
                score -= 0.12
                reasons.append("domain:fan type conflict")
        if _has_any(query_compact, ("混流", "其他风机")):
            if _has_any(quota_compact, ("轴流", "混流", "斜流", "通风机")):
                score += 0.10
                reasons.append("domain:general ventilation fan")
        if _has_any(query_compact, ("换气扇", "排风机", "排气扇")):
            if _has_any(quota_compact, ("排气扇", "换气扇", "风扇安装")):
                score += 0.44
                reasons.append("domain:exhaust fan")
            if _has_any(quota_compact, ("天花式", "天花板")) and "天花" in query_compact:
                score += 0.16
                reasons.append("domain:ceiling exhaust fan")
            if _has_any(quota_compact, ("电机干燥", "水表", "天花灯", "发电机", "泵安装")):
                score -= 0.36
                reasons.append("domain:fan noise conflict")
    elif query_signal.family == "duct":
        param_score, param_reasons = _duct_param_score(query_text, quota.search_text)
        score += param_score
        reasons.extend(param_reasons)
        duct_run_query = _is_air_duct_run_query(query_compact, query_text)
        if duct_run_query:
            if _book_of_record(quota) == "C7" or _has_any(quota_compact, ("风管安装", "风管制作安装", "制作安装")):
                score += 0.20
                reasons.append("domain:duct fabrication book")
            if _has_any(quota_compact, ("保温板", "保温", "绝热", "聚乙烯", "橡塑", "高发泡")):
                score -= 0.30
                reasons.append("domain:duct insulation conflict")
            if _has_any(quota_compact, ("风口安装", "散流器", "百叶风口")):
                score -= 0.10
                reasons.append("domain:duct outlet weak")
            if "复合型风管" in query_compact and "复合型" in quota_compact:
                score += 0.18
                reasons.append("domain:composite duct")
        if "不锈钢" in query_compact:
            if "不锈钢" in quota_compact:
                score += 0.20
                reasons.append("domain:stainless duct")
            elif _has_any(quota_compact, ("镀锌", "薄钢板", "碳钢")):
                score -= 0.18
                reasons.append("domain:non-stainless duct conflict")
        if "防火阀" in query_compact:
            if "防火阀" in quota_compact:
                score += 0.18
                reasons.append("domain:fire damper")
            if quota.quota_id.upper().startswith("C9-"):
                score -= 0.20
                reasons.append("domain:fire damper book weak")
        if _has_any(query_compact, ("风口", "散流器", "百叶")):
            if "风口安装" in quota_compact and _has_any(quota_compact, ("百叶风口", "散流器", "风口")):
                score += 0.12
                reasons.append("domain:air outlet")
            if "百叶" in query_compact:
                if "百叶风口" in quota_compact:
                    score += 0.28
                    reasons.append("domain:louver outlet")
                if _has_any(quota_compact, ("方形散流器", "圆形散流器", "散流器")) and "散流器" not in query_compact:
                    score -= 0.20
                    reasons.append("domain:diffuser conflict")
                if "玻璃钢" in quota_compact and "玻璃钢" not in query_compact:
                    score -= 0.18
                    reasons.append("domain:frp outlet conflict")
                if "百叶窗" in quota_compact and ("窗" not in query_compact or "防雨百叶" in query_compact):
                    score -= 0.22
                    reasons.append("domain:louver window conflict")
            if "防雨百叶" in query_compact and "百叶风口" in quota_compact:
                score += 0.16
                reasons.append("domain:rainproof louver as outlet")
            if "钢百叶窗" in quota_compact and ("窗" not in query_compact or "防雨百叶" in query_compact):
                score -= 0.24
                reasons.append("domain:louver window conflict")
            outlet_score, outlet_kind = _tier_limit_score(_first_pair_perimeter(query_text), _first_number_after(quota.search_text, ("周长",)), 0.18)
            if outlet_kind:
                score += outlet_score
                reasons.append(f"domain:air outlet perimeter {outlet_kind}")
        if _has_any(query_compact, ("柔性软风管", "帆布")):
            fabric_flex_query = "帆布" in query_compact
            if _has_any(quota_compact, ("柔性接口", "伸缩节", "帆布接口")):
                score += 0.84 if fabric_flex_query else 0.16
                reasons.append("domain:flex duct fabric interface")
            if "有法兰" in quota_compact and _has_any(query_compact, ("法兰", "帆布")):
                score += 0.12
                reasons.append("domain:flex duct flange")
            if "柔性软风管安装" in quota_compact and fabric_flex_query:
                score -= 0.32
                reasons.append("domain:flex duct install weak")
        if "隔热棉" in query_compact:
            if _has_any(quota_compact, ("纤维类", "棉")):
                score += 0.38
                reasons.append("domain:fiber insulation")
            if "其他纤维类制品" in quota_compact:
                score += 0.08
                reasons.append("domain:generic fiber insulation")
            if _has_any(quota_compact, ("聚氨酯", "聚苯乙烯", "橡塑")):
                score -= 0.26
                reasons.append("domain:foam insulation conflict")
            if "高发泡" in quota_compact:
                score -= 0.14
                reasons.append("domain:pef insulation conflict")
        query_rect_duct = "矩形风管" in query_compact or ("形状矩形" in query_compact and "风管" in query_compact)
        query_round_duct = "圆形风管" in query_compact or ("形状圆形" in query_compact and "风管" in query_compact)
        if query_rect_duct:
            if "矩形风管" in quota_compact:
                score += 0.10
                reasons.append("domain:rect duct")
            if "圆形风管" in quota_compact:
                score -= 0.14
                reasons.append("domain:round duct conflict")
        if query_round_duct:
            if "圆形风管" in quota_compact:
                score += 0.10
                reasons.append("domain:round duct")
            if "矩形风管" in quota_compact:
                score -= 0.14
                reasons.append("domain:rect duct conflict")
        if _has_any(query_compact, ("复合型风管", "复合板", "复合一体板", "复合型")):
            if "复合" in quota_compact:
                score += 0.32
                reasons.append("domain:composite duct")
            elif _has_any(quota_compact, ("镀锌薄钢板", "薄钢板", "碳钢")):
                score -= 0.12
                reasons.append("domain:non-composite duct weak")
        if "咬口" in query_compact and not _has_any(query_compact, ("共板法兰", "角钢法兰", "法兰连接")):
            if "咬口" in quota_compact and not _has_any(quota_compact, ("共板法兰", "角钢法兰")):
                score += 0.08
                reasons.append("domain:duct bite joint")
            if _has_any(quota_compact, ("共板法兰", "角钢法兰")):
                score -= 0.12
                reasons.append("domain:duct flange weak")
        if _has_any(query_compact, ("板材厚度", "薄钢板", "厚度")):
            if "制作安装" in quota_compact:
                score += 0.14
                reasons.append("domain:duct fabrication")
            if "装配式" in quota_compact:
                score -= 0.10
                reasons.append("domain:prefab duct weak")
    elif query_signal.family == "lamp":
        if _has_any(query_compact, ("装饰灯", "轨道", "滑轨", "射灯")):
            if _has_any(query_compact, ("轨道", "滑轨")):
                if _has_any(quota_compact, ("滑轨式射灯", "滑轨安装", "轨道灯")):
                    score += 0.68
                    reasons.append("domain:track decorative lamp")
                elif "射灯" in quota_compact:
                    score += 0.24
                    reasons.append("domain:generic track lamp weak")
            elif _has_any(quota_compact, ("滑轨式射灯", "滑轨安装", "轨道灯")):
                score += 0.98 if "装饰灯" in query_compact else 0.42
                reasons.append("domain:track decorative lamp")
            elif "射灯" in quota_compact:
                score += 0.56 if "装饰灯" in query_compact else 0.42
                reasons.append("domain:track decorative lamp")
            if "装饰灯" in query_compact and not _has_any(query_compact, ("水下", "喷水池", "幻光", "彩灯")):
                if _has_any(quota_compact, ("水下艺术装饰", "幻光型灯", "彩灯")):
                    score -= 0.22
                    reasons.append("domain:water decorative lamp conflict")
            if _has_any(quota_compact, ("标志、诱导", "标志诱导")):
                score -= 0.28
                reasons.append("domain:sign decorative lamp conflict")
            if _has_any(quota_compact, ("草坪灯", "楼宇亮化", "霓虹", "路灯")):
                score -= 0.18
                reasons.append("domain:outdoor decor lamp weak")
            if "嵌入式点光源" in quota_compact and not _has_any(query_compact, ("嵌入", "筒灯")):
                score -= 0.10
                reasons.append("domain:embedded decor lamp weak")
        if "普通灯具" in query_compact:
            if _has_any(quota_compact, ("标志、诱导", "标志诱导", "金属软管")):
                score -= 0.30
                reasons.append("domain:ordinary lamp chapter conflict")
            if _has_any(query_compact, ("管吊", "吊杆", "壁装led", "1x18w", "1*18w")):
                if "吊杆式荧光灯" in quota_compact:
                    score += 0.58
                    reasons.append("domain:rod fluorescent lamp")
                    if _has_any(query_compact, ("1x", "1*")) and "单管" in quota_compact:
                        score += 0.08
                        reasons.append("domain:single tube lamp")
                    if "无吊顶处" in quota_compact:
                        score += 0.10
                        reasons.append("domain:no-ceiling rod lamp")
                    if "有吊顶处" in quota_compact:
                        score -= 0.06
                        reasons.append("domain:ceiling rod lamp weak")
                elif _has_any(quota_compact, ("软线吊灯", "吊链灯", "直杆灯", "座灯头")):
                    score -= 0.20
                    reasons.append("domain:generic ordinary lamp weak")
            elif _has_any(query_compact, ("筒灯", "点光源", "led")):
                if "吸顶式点光源艺术装饰灯具" in quota_compact:
                    score += 0.72
                    reasons.append("domain:ceiling point lamp")
                elif "点光源艺术装饰灯具" in quota_compact:
                    score += 0.04
                    reasons.append("domain:point lamp")
            else:
                if _has_any(quota_compact, ("吸顶灯具", "座灯头")):
                    score += 0.10
                    reasons.append("domain:ordinary lamp default")
        if any(term in query_compact for term in ("筒灯", "嵌入", "射灯")) and any(term in quota_compact for term in ("吊链", "直杆", "座灯头")):
            score -= 0.18
            reasons.append("domain:lamp type conflict")
        if _has_any(query_compact, ("筒灯", "嵌入式防眩")):
            if "筒灯" in quota_compact:
                score += 0.20
                reasons.append("domain:downlight exact")
            if _has_any(quota_compact, ("楼宇亮化", "地面射灯", "路灯", "霓虹")):
                score -= 0.24
                reasons.append("domain:outdoor/decor lamp conflict")
        if "平板灯" in query_compact:
            if _has_any(quota_compact, ("平板灯", "格栅灯", "其他普通灯具")):
                score += 0.16
                reasons.append("domain:panel lamp")
            if "吸顶" in quota_compact:
                score += 0.18
                reasons.append("domain:panel as ceiling")
            if "吸顶灯具" in quota_compact:
                score += 0.06
                reasons.append("domain:flat panel ceiling fixture")
            if "吊式" in quota_compact:
                score -= 0.16
                reasons.append("domain:panel吊式 conflict")
            if _has_any(quota_compact, ("艺术装饰", "串珠", "穗", "棒灯", "楼宇亮化", "路灯")):
                score -= 0.34
                reasons.append("domain:decor lamp conflict")
        if _has_any(query_compact, ("线形灯", "长条led灯")):
            if _has_any(quota_compact, ("线形", "线型", "荧光灯", "灯带", "成套型")):
                score += 0.18
                reasons.append("domain:linear lamp")
            if "灯带" in quota_compact and _has_any(query_compact, ("线形灯", "线型灯", "灯带", "顶棚", "天棚")):
                score += 0.08
                reasons.append("domain:linear light strip")
            if _has_any(query_compact, ("顶棚", "天棚")) and "天棚" in quota_compact:
                score += 0.08
                reasons.append("domain:ceiling linear lamp")
            if "长条led灯" in query_compact and "吸顶灯具" in quota_compact:
                score += 0.26
                reasons.append("domain:long led as ceiling")
            if "荧光灯具" in quota_compact and "灯带" not in quota_compact:
                score -= 0.08
                reasons.append("domain:generic fluorescent weak")
            if _has_any(quota_compact, ("霓虹", "楼宇亮化", "路灯", "镇流器", "配件")):
                score -= 0.26
                reasons.append("domain:linear lamp conflict")
    elif query_signal.family in {"socket", "switch"}:
        if query_signal.family == "socket" and "插座" not in quota_compact:
            score -= 0.20
            reasons.append("domain:socket conflict")
        if query_signal.family == "switch" and "开关" not in quota_compact:
            score -= 0.20
            reasons.append("domain:switch conflict")
        if query_signal.family == "socket" and _has_any(query_compact, ("五孔", "三孔", "二三极", "二、三极", "插座")):
            if _has_any(quota_compact, ("地面插座", "地插")) and not _has_any(query_compact, ("地面", "地插")):
                score -= 0.24
                reasons.append("domain:floor socket conflict")
            if _has_any(quota_compact, ("防爆", "插座箱", "组合开关插座", "周边设备", "卡侬", "气插座", "气动辅助")):
                score -= 0.24
                reasons.append("domain:socket subtype conflict")
            if _has_any(quota_compact, ("单相", "二三极", "二、三极", "五孔", "三孔")):
                score += 0.14
                reasons.append("domain:ordinary socket")
            if _has_any(query_compact, ("五孔", "5孔")):
                if _has_any(quota_compact, ("五孔", "5孔")):
                    score += 0.14
                    reasons.append("domain:five-hole socket")
                elif _has_any(quota_compact, ("二孔", "2孔", "三孔", "3孔", "四孔", "4孔", "六孔", "6孔", "七孔", "7孔", "八孔", "8孔")):
                    score -= 0.08
                    reasons.append("domain:hole-count conflict")
            if _has_any(query_compact, ("暗装", "墙面暗装", "顶棚暗装")):
                if _has_any(quota_compact, ("暗插座", "暗装")):
                    score += 0.22
                    reasons.append("domain:dark socket")
                if _has_any(quota_compact, ("明插座", "明装")):
                    score -= 0.22
                    reasons.append("domain:light socket conflict")
            elif _has_any(query_compact, ("明装", "墙面明装")):
                if _has_any(quota_compact, ("明插座", "明装")):
                    score += 0.14
                    reasons.append("domain:light socket")
                if _has_any(quota_compact, ("暗插座", "暗装")):
                    score -= 0.12
                    reasons.append("domain:dark socket conflict")
            else:
                if _has_any(quota_compact, ("暗插座", "暗装")):
                    score += 0.06
                    reasons.append("domain:default dark socket")
                if _has_any(quota_compact, ("明插座", "明装")):
                    score -= 0.04
                    reasons.append("domain:default light socket weak")
            if not _has_any(query_compact, ("二孔", "2孔", "三孔", "3孔", "四孔", "4孔", "五孔", "5孔")):
                if _has_any(quota_compact, ("五孔", "5孔")):
                    score += 0.08
                    reasons.append("domain:default five-hole socket")
                if _has_any(quota_compact, ("二孔", "2孔", "三孔", "3孔", "四孔", "4孔")):
                    score -= 0.03
                    reasons.append("domain:small-hole socket weak")
    elif query_signal.family == "electrical_box":
        if "配电箱" in query_compact and not _has_any(query_compact, ("电涌", "浪涌", "避雷", "防雷")):
            if _has_any(quota_compact, ("电涌保护器", "浪涌保护器", "防雷器")):
                score -= 0.36
                reasons.append("domain:surge protector conflict")
            if "成套配电箱" in quota_compact:
                score += 0.14
                reasons.append("domain:assembled distribution box")
        if "设备自带控制箱" in query_compact:
            if "控制箱" in quota_compact and "配电箱" not in quota_compact:
                score -= 0.30
                reasons.append("domain:equipment control box conflict")
            if _has_any(quota_compact, ("配电箱墙上", "配电箱墙上柱上")) and "明装" in quota_compact:
                score += 0.22
                reasons.append("domain:default wall box")
            if query_signal.circuits is None and quota.signal.circuits is not None:
                if quota.signal.circuits <= 4:
                    score += 0.04
                    reasons.append("domain:small wall box default")
                else:
                    score -= 0.02
                    reasons.append("domain:large wall box weak")
        if "配电间" in query_compact and "配电箱" in query_compact:
            if _has_any(quota_compact, ("配电箱墙上", "配电箱墙上柱上")) and "明装" in quota_compact:
                score += 0.08
                reasons.append("domain:distribution room wall box")
            if _has_any(quota_compact, ("落地安装", "嵌入式安装")):
                score -= 0.05
                reasons.append("domain:distribution room install weak")
        if "明装" in query_compact and "明装" in quota_compact:
            score += 0.08
            reasons.append("domain:box exposed")
    elif query_signal.family == "sleeve":
        if not _has_any(query_compact, ("塑料", "柔性", "人防", "填料")) and "刚性防水套管" in quota_compact:
            score += 0.14
            reasons.append("domain:default rigid sleeve")
        if _has_any(query_compact, ("钢管", "入户套管")):
            if "刚性防水套管" in quota_compact:
                score += 0.26
                reasons.append("domain:rigid waterproof sleeve")
            if "塑料套管" in quota_compact:
                score -= 0.28
                reasons.append("domain:plastic sleeve conflict")
            if _has_any(quota_compact, ("柔性防水套管", "一般填料套管")):
                score -= 0.08
                reasons.append("domain:non-rigid sleeve weak")
        if "人防" not in query_compact and _has_any(quota_compact, ("人防段", "防护密闭")):
            score -= 0.36
            reasons.append("domain:civil-defense sleeve conflict")
    elif query_signal.family == "sanitary":
        sanitary_names = ("地漏", "水龙头", "小便器", "大便器", "坐便器", "拖布池", "洗脸盆", "洗涤盆")
        query_hits = {term for term in sanitary_names if term in query_compact}
        quota_hits = {term for term in sanitary_names if term in quota_compact}
        if query_hits and quota_hits and not (query_hits & quota_hits):
            score -= 0.18
            reasons.append("domain:sanitary type conflict")
        if "地漏" in query_compact:
            if "地漏" in quota_compact:
                score += 0.24
                reasons.append("domain:floor drain")
            if _has_any(quota_compact, ("排水栓", "水龙头", "洗脸盆", "洗涤盆")):
                score -= 0.18
                reasons.append("domain:floor drain accessory conflict")
        if "水槽" in query_compact:
            single_sink_query = _has_any(query_compact, ("单孔", "成品安装"))
            if _has_any(quota_compact, ("气柜", "壁板", "底板", "刷油", "除锈")):
                score -= 0.54
                reasons.append("domain:industrial tank conflict")
            if any(term in quota_compact for term in ("洗脸盆", "洗手盆", "洗涤盆", "水槽")):
                score += 0.14
                reasons.append("domain:sink fixture")
            if single_sink_query and "洗脸盆" in quota_compact and "立柱式" in quota_compact:
                score += 0.62
                reasons.append("domain:single-hole basin default")
            elif single_sink_query and "洗脸盆" in quota_compact and "挂墙式" in quota_compact:
                score -= 0.08
                reasons.append("domain:wall basin weak")
            if single_sink_query and _has_any(quota_compact, ("淋浴", "浴室")):
                score -= 0.24
                reasons.append("domain:shower fixture conflict")
            if "水龙头" in quota_compact:
                score -= 0.22
                reasons.append("domain:faucet accessory conflict")
        if _has_any(query_compact, ("感应蹲便器", "感应式蹲便器")):
            if _has_any(quota_compact, ("蹲式", "蹲便器", "大便器")):
                score += 0.16
                reasons.append("domain:squat fixture")
            if _has_any(quota_compact, ("感应", "自动冲洗", "埋入式")):
                score += 0.18
                reasons.append("domain:sensor flush")
            if _has_any(quota_compact, ("延时器", "周边设备")):
                score -= 0.30
                reasons.append("domain:sanitary accessory conflict")
        if _has_any(query_compact, ("感应式小便", "感应小便")):
            if _has_any(quota_compact, ("小便器", "小便斗")):
                score += 0.16
                reasons.append("domain:urinal fixture")
            if _has_any(quota_compact, ("感应", "自动冲洗", "埋入式")):
                score += 0.18
                reasons.append("domain:sensor urinal")
            if "立式" in query_compact:
                if _has_any(quota_compact, ("落地式", "立式")):
                    score += 0.10
                    reasons.append("domain:standing urinal")
                if _has_any(quota_compact, ("壁挂式", "挂斗式")):
                    score -= 0.10
                    reasons.append("domain:wall urinal conflict")
            elif not _has_any(query_compact, ("壁挂", "挂式", "墙")):
                if _has_any(quota_compact, ("落地式", "立式")):
                    score += 0.06
                    reasons.append("domain:default standing urinal")
                if _has_any(quota_compact, ("壁挂式", "挂斗式")):
                    score -= 0.04
                    reasons.append("domain:default wall urinal weak")
            if "普通式" in quota_compact and not _has_any(quota_compact, ("感应", "自动冲洗")):
                score -= 0.16
                reasons.append("domain:ordinary urinal conflict")
    elif query_signal.family == "pipe":
        if is_pipe_device_false_trigger(query_text):
            return score, reasons
        branch_pipe_query = _has_any(query_compact, ("分歧器", "分歧管"))
        if branch_pipe_query:
            if _has_any(quota_compact, ("分歧管安装", "分歧管", "铜管外径")):
                score += 0.58
                reasons.append("domain:branch pipe")
            if _has_any(quota_compact, ("贮存装置", "容器规格", "水表", "阀门", "钢管", "塑料管", "支架")):
                score -= 0.28
                reasons.append("domain:branch pipe conflict")
        plastic_pipe_query = _has_any(query_compact, ("upvc", "pvc", "塑料管"))
        drainage_query = _has_any(query_compact, ("排水", "雨水", "污水", "冷凝水"))
        adhesive_query = _has_any(query_compact, ("粘接", "胶粘", "胶水", "承插"))
        if plastic_pipe_query and drainage_query:
            if _has_any(quota_compact, ("塑料排水管", "塑料雨水管")):
                score += 0.34
                reasons.append("domain:plastic drainage pipe")
            if _has_any(quota_compact, ("给排水管道", "室内塑料排水管", "室内塑料雨水管")):
                score += 0.16
                reasons.append("domain:plumbing plastic pipe book")
            if "室内" not in query_compact and "室外" not in query_compact:
                if "室内" in quota_compact:
                    score += 0.08
                    reasons.append("domain:default indoor plastic pipe")
                if "室外" in quota_compact:
                    score -= 0.04
                    reasons.append("domain:outdoor plastic pipe weak")
            if _has_any(quota_compact, ("保护管", "采暖管道", "空调冷热水", "地源", "低压管道", "低中压", "管件", "煨弯", "基础")):
                score -= 0.30
                reasons.append("domain:plastic drainage chapter conflict")
            if adhesive_query:
                if _has_any(quota_compact, ("粘接", "胶粘", "承插")):
                    score += 0.16
                    reasons.append("domain:adhesive plastic pipe")
                if _has_any(quota_compact, ("热熔", "电熔", "螺母密封圈")):
                    score -= 0.18
                    reasons.append("domain:plastic connection conflict")
        steel_pipe_query = _has_any(query_compact, ("无缝钢管", "焊接钢管", "镀锌钢管", "钢管", "sc"))
        if steel_pipe_query:
            if _has_any(quota_compact, ("给排水管道", "室内钢管", "室内镀锌钢管", "室内无缝钢管", "无缝钢管", "焊接钢管")):
                score += 0.18
                reasons.append("domain:default indoor plumbing pipe")
            if "无缝钢管" in query_compact:
                if "无缝钢管" in quota_compact:
                    score += 0.24
                    reasons.append("domain:seamless steel pipe")
                if _has_any(quota_compact, ("塑料排水管", "塑料管", "镀锌钢管")):
                    score -= 0.16
                    reasons.append("domain:seamless steel pipe conflict")
            if not _has_any(query_compact, ("燃气", "采暖", "空调", "室外", "不锈钢")):
                if "给排水管道" in quota_compact and "室内" in quota_compact:
                    score += 0.14
                    reasons.append("domain:default indoor water steel pipe")
                if _has_any(quota_compact, ("燃气管道", "采暖管道", "空调冷热水", "室外")):
                    score -= 0.14
                    reasons.append("domain:nondefault steel pipe system")
                if "不锈钢" in quota_compact:
                    score -= 0.08
                    reasons.append("domain:stainless steel pipe weak")
            if "镀锌钢管" in query_compact:
                if _has_any(quota_compact, ("镀锌钢管", "室内钢管")):
                    score += 0.22
                    reasons.append("domain:galvanized steel pipe")
                if _has_any(quota_compact, ("塑料排水管", "塑料管")):
                    score -= 0.32
                    reasons.append("domain:steel plastic pipe conflict")
            if "沟槽" in query_compact:
                if "沟槽" in quota_compact:
                    score += 0.22
                    reasons.append("domain:grooved pipe")
                if _has_any(quota_compact, ("螺纹", "焊接")) and "沟槽" not in quota_compact:
                    score -= 0.12
                    reasons.append("domain:groove connection conflict")
            if _has_any(quota_compact, ("钢管柱", "管柱")):
                score -= 0.36
                reasons.append("domain:steel column conflict")
            if _has_any(quota_compact, ("燃气管道", "空调冷热水", "空调凝结水", "采暖管道")):
                score -= 0.08
                reasons.append("domain:pipe system weak")
        if _has_any(query_compact, ("低压碳钢管", "碳钢管道")) and not _has_any(query_compact, ("管件", "弯头", "煨弯")):
            if _has_any(quota_compact, ("低压碳钢管", "低中压碳钢管", "碳钢管道")) and not _has_any(quota_compact, ("管件", "煨弯")):
                score += 0.28
                reasons.append("domain:carbon steel pipe run")
            if _has_any(quota_compact, ("管件", "煨弯")):
                score -= 0.30
                reasons.append("domain:pipe fitting/bend conflict")
    elif query_signal.family == "support":
        pipe_support_context = _has_any(query_compact, ("给排水", "管道", "支吊架", "支/吊架", "管架"))
        if pipe_support_context:
            if "一般管架" in quota_compact:
                score += 0.46
                reasons.append("domain:general pipe support")
            if _book_of_record(quota) == "C10":
                score += 0.14
                reasons.append("domain:pipe support book")
            elif _has_any(quota_compact, ("设备基础型钢", "金属支架", "设备支架")):
                score -= 0.22
                reasons.append("domain:non-pipe support weak")
            if _has_any(quota_compact, ("模板", "现浇混凝土", "基础梁")):
                score -= 0.34
                reasons.append("domain:formwork support conflict")
        if "铁构件" in query_compact:
            if "一般铁构件" in quota_compact:
                score += 0.36
                reasons.append("domain:general iron member")
            if "配管" in quota_compact:
                score -= 0.22
                reasons.append("domain:conduit support conflict")
    return score, reasons


class _GoalIndex:
    def __init__(self, province: str):
        self.province = province
        self.quotas = self._load_quotas()
        self.by_quota_id = {record.quota_id: record for record in self.quotas}
        self.quota_index_by_id = {record.quota_id: idx for idx, record in enumerate(self.quotas)}
        self.by_family = self._build_family_map()
        corpus = [record.tokens for record in self.quotas]
        self.bm25 = BM25Okapi(corpus) if BM25Okapi is not None and corpus else None
        self.priors = self._load_priors()
        self.exact_prior_by_key = self._build_exact_prior_map()
        prior_corpus = [record.tokens for record in self.priors]
        self.prior_bm25 = BM25Okapi(prior_corpus) if BM25Okapi is not None and prior_corpus else None
        self.shadow_priors = self._load_oss_shadow_priors()
        self.shadow_prior_by_key = self._build_shadow_prior_map()
        shadow_corpus = [record.tokens for record in self.shadow_priors]
        self.shadow_bm25 = BM25Okapi(shadow_corpus) if BM25Okapi is not None and shadow_corpus else None

    def _load_quotas(self) -> list[_QuotaRecord]:
        path = config.get_quota_db_path(self.province)
        if not Path(path).exists():
            raise FileNotFoundError(f"quota db not found for province: {self.province}")
        conn = _db_connect(path)
        conn.row_factory = sqlite3.Row
        try:
            cols = {row["name"] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            optional = [
                "work_type",
                "specialty",
                "chapter",
                "material",
                "connection",
                "dn",
                "cable_section",
                "circuits",
                "book",
                "search_text",
            ]
            select_cols = ["id", "quota_id", "name", "unit"] + [col for col in optional if col in cols]
            rows = conn.execute(f"select {', '.join(select_cols)} from quotas").fetchall()
        finally:
            conn.close()

        records: list[_QuotaRecord] = []
        for row in rows:
            data = dict(row)
            search_text = " ".join(
                clean_text(data.get(key))
                for key in ("quota_id", "name", "unit", "work_type", "specialty", "chapter", "material", "connection", "search_text")
                if clean_text(data.get(key))
            )
            signal = extract_signal(search_text)
            _apply_structured_values(signal, data)
            record = _QuotaRecord(
                rowid=int(data.get("id") or 0),
                quota_id=clean_text(data.get("quota_id")),
                name=clean_text(data.get("name")),
                unit=clean_text(data.get("unit")),
                work_type=clean_text(data.get("work_type")),
                specialty=clean_text(data.get("specialty")),
                chapter=clean_text(data.get("chapter")),
                book=clean_text(data.get("book")),
                search_text=search_text,
                tokens=signal.tokens or tokenize(search_text),
                signal=signal,
            )
            if record.quota_id and record.name:
                records.append(record)
        return records

    def _build_family_map(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for idx, record in enumerate(self.quotas):
            if record.signal.family:
                result.setdefault(record.signal.family, []).append(idx)
        return result

    def _load_priors(self) -> list[_PriorRecord]:
        path = Path(config.DB_DIR) / "common" / "experience.db"
        if not path.exists() or path.stat().st_size <= 0:
            return []
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                select bill_text, bill_name, quota_ids, source, confidence,
                       project_name, '' as source_file, '' as sample_id
                from experiences
                where coalesce(disputed, 0) = 0
                  and coalesce(layer, '') <> 'deleted'
                  and (province = ? or province is null or province = '')
                """,
                (self.province,),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        priors: list[_PriorRecord] = []
        for row in rows:
            quota_ids = [qid for qid in _safe_json_list(row["quota_ids"]) if qid in self.by_quota_id]
            if not quota_ids:
                continue
            text = " ".join([clean_text(row["bill_name"]), clean_text(row["bill_text"])])
            priors.append(
                _PriorRecord(
                    bill_text=clean_text(row["bill_text"]),
                    bill_name=clean_text(row["bill_name"]),
                    quota_ids=quota_ids,
                    source=clean_text(row["source"]),
                    confidence=float(row["confidence"] or 0.0),
                    project_name=clean_text(row["project_name"]),
                    source_file=clean_text(row["source_file"]),
                    sample_id=clean_text(row["sample_id"]),
                    tokens=tokenize(text),
                )
            )
        return priors

    def _build_exact_prior_map(self) -> dict[str, list[_PriorRecord]]:
        result: dict[str, list[_PriorRecord]] = {}
        for prior in self.priors:
            for key in {_prior_key(prior.bill_text), _prior_key(" ".join([prior.bill_name, prior.bill_text]))}:
                if key:
                    result.setdefault(key, []).append(prior)
        return result

    def _load_oss_shadow_priors(self) -> list[_ShadowPriorRecord]:
        paths = [
            Path(config.PROJECT_ROOT) / "reports" / "agent_state" / "v36_oss_r2_canonicalizer_alignment_top.jsonl",
            Path(config.PROJECT_ROOT) / "reports" / "agent_state" / "v36_data_fuel_r4_top50_guarded_shadow_details.jsonl",
        ]
        records: list[_ShadowPriorRecord] = []
        for path in paths:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if clean_text(raw.get("province")) != self.province:
                        continue
                    top_candidates = raw.get("top_candidates")
                    if not isinstance(top_candidates, list):
                        continue
                    max_raw_score = max([float(item.get("score") or 0.0) for item in top_candidates if isinstance(item, dict)] or [0.0])
                    quota_scores: dict[str, float] = {}
                    reasons: dict[str, list[str]] = {}
                    for item in top_candidates[:16]:
                        if not isinstance(item, dict):
                            continue
                        quota_id = clean_text(item.get("quota_id"))
                        if quota_id not in self.by_quota_id:
                            continue
                        normalized = _normalize_score(float(item.get("score") or 0.0), max_raw_score)
                        if normalized <= 0:
                            continue
                        quota_scores[quota_id] = max(quota_scores.get(quota_id, 0.0), normalized)
                        why = item.get("why") if isinstance(item.get("why"), list) else []
                        reasons[quota_id] = [clean_text(value) for value in why if clean_text(value)]
                    bill_name = clean_text(raw.get("bill_name"))
                    if bill_name and quota_scores:
                        records.append(
                            _ShadowPriorRecord(
                                bill_name=bill_name,
                                quota_scores=quota_scores,
                                reasons=reasons,
                                source_file=clean_text(raw.get("source_file") or path.name),
                                project_name=clean_text(raw.get("project_name")),
                                sample_id=clean_text(raw.get("sample_id") or raw.get("bill_id")),
                                tokens=tokenize(bill_name),
                            )
                        )
        return records

    def _build_shadow_prior_map(self) -> dict[str, list[_ShadowPriorRecord]]:
        result: dict[str, list[_ShadowPriorRecord]] = {}
        for prior in self.shadow_priors:
            key = _prior_key(prior.bill_name)
            if key:
                result.setdefault(key, []).append(prior)
        return result


def _prior_key(value: object) -> str:
    return key_text(value)


def _is_excluded_source(row: _PriorRecord | _ShadowPriorRecord, item: dict[str, Any]) -> bool:
    excluded = item.get("goal_excluded_sources") if isinstance(item, dict) else None
    if not isinstance(excluded, dict):
        return False
    for field in ("sample_id", "source_file", "project_name"):
        values = excluded.get(field)
        if not values:
            continue
        if isinstance(values, str):
            values = {values}
        else:
            values = set(values)
        if clean_text(getattr(row, field, "")) in values:
            return True
    return False


class GoalSearcher:
    """Standalone pure-search quota matcher for goal-mode experiments."""

    def __init__(self, province: str):
        self.province = config.resolve_province(province) if province else config.get_current_province()
        self.index = _get_index(self.province)

    def search(self, item: GoalSearchItem | dict[str, Any] | str, top_k: int = 5) -> list[GoalSearchHit]:
        started = time.perf_counter()
        query = self._coerce_item(item)
        raw_item = item if isinstance(item, dict) else {}
        query_text = " ".join(x for x in [query.bill_name, query.text, query.specialty, query.unit] if x)
        query_signal = extract_signal(query_text)
        query_signal = _apply_strong_name_signal(query_signal, query.bill_name)
        query_tokens = query_signal.tokens or tokenize(query_text)

        quota_scores = _bm25_scores(self.index.bm25, query_tokens, len(self.index.quotas))
        max_quota_score = max(quota_scores, default=0.0)
        candidate_indices = set(_top_indices(quota_scores, max(top_k * 12, 80)))

        prior_bonus: dict[str, float] = {}
        prior_reasons: dict[str, list[str]] = {}
        if not query.no_answer_priors:
            self._collect_experience_priors(raw_item, query, query_text, query_tokens, prior_bonus, prior_reasons, candidate_indices)
            self._collect_shadow_priors(raw_item, query, query_text, query_tokens, prior_bonus, prior_reasons, candidate_indices)
        self._collect_local_family_candidates(query_signal, query_text, query_tokens, query.specialty, candidate_indices)
        self._collect_national_index_candidates(query_signal, query.unit, prior_bonus, prior_reasons, candidate_indices)

        hits: list[GoalSearchHit] = []
        query_token_set = set(query_tokens)
        for idx in candidate_indices:
            quota = self.index.quotas[idx]
            bm25_norm = _normalize_score(quota_scores[idx], max_quota_score)
            overlap = _overlap_score(query_token_set, quota.tokens)
            score = 0.58 * bm25_norm + 0.12 * min(1.0, overlap * 2.0)
            reasons = [f"bm25:{bm25_norm:.2f}"]

            if query.unit and quota.unit and query.unit == quota.unit:
                score += 0.06
                reasons.append("unit exact")
            elif query.unit and quota.unit and query.unit != quota.unit:
                score -= 0.05
                reasons.append("unit conflict")

            requested_book = query.specialty.upper()
            candidate_book = _book_of_record(quota)
            if requested_book and candidate_book:
                if _book_matches(requested_book, candidate_book):
                    score += 0.16
                    reasons.append("book exact")
                    if query_signal.family in {"cable", "cable_head", "support"}:
                        score += 0.10
                        reasons.append("domain:family book exact")
                    elif query_signal.family == "lamp":
                        score += 0.06
                        reasons.append("domain:lamp book exact")
                else:
                    score -= 0.30
                    reasons.append("book conflict")
                    if query_signal.family in {"cable", "cable_head", "support"}:
                        score -= 0.18
                        reasons.append("domain:family book conflict")
                    elif query_signal.family == "lamp":
                        score -= 0.10
                        reasons.append("domain:lamp book conflict")

            numeric_score, numeric_reasons = _numeric_match_score(query_signal, quota)
            field_score, field_reasons = _field_match_score(query_signal, quota)
            domain_score, domain_reasons = _domain_term_score(query_text, query_signal, quota)
            score += numeric_score + field_score + domain_score
            reasons.extend(numeric_reasons + field_reasons + domain_reasons)

            bonus = prior_bonus.get(quota.quota_id, 0.0)
            if bonus:
                score += bonus
                reasons.extend(prior_reasons.get(quota.quota_id, []))

            score = max(0.0, score)
            confidence = max(0.0, min(99.0, 100.0 * (1.0 - math.exp(-score))))
            hits.append(
                GoalSearchHit(
                    quota_id=quota.quota_id,
                    name=quota.name,
                    unit=quota.unit,
                    score=round(score, 6),
                    confidence=round(confidence, 2),
                    reasons=reasons[:8],
                    source_scores={
                        "bm25": round(bm25_norm, 6),
                        "prior": round(bonus, 6),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    },
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.quota_id))
        return hits[:top_k]

    def _collect_local_family_candidates(
        self,
        query_signal: QuotaSignal,
        query_text: str,
        query_tokens: list[str],
        requested_book: str,
        candidate_indices: set[int],
    ) -> None:
        if not query_signal.family:
            return
        family_indices = self.index.by_family.get(query_signal.family, [])
        if not family_indices:
            return
        query_token_set = set(query_tokens)
        requested_book = clean_text(requested_book).upper()

        def rank_key(idx: int) -> tuple[float, float, int]:
            quota = self.index.quotas[idx]
            candidate_book = clean_text(quota.book).upper() or _quota_book(quota.quota_id)
            book_score = 1.0 if _book_matches(requested_book, candidate_book) else 0.0
            overlap = _overlap_score(query_token_set, quota.tokens)
            param_score, _ = _numeric_match_score(query_signal, quota)
            domain_score, _ = _domain_term_score(query_text, query_signal, quota)
            return (book_score, overlap + param_score + domain_score, -idx)

        for idx in sorted(family_indices, key=rank_key, reverse=True)[:128]:
            candidate_indices.add(idx)

    def _collect_experience_priors(
        self,
        raw_item: dict[str, Any],
        query: GoalSearchItem,
        query_text: str,
        query_tokens: list[str],
        prior_bonus: dict[str, float],
        prior_reasons: dict[str, list[str]],
        candidate_indices: set[int],
    ) -> None:
        if not self.index.priors:
            return
        exact_keys = {_prior_key(query.text), _prior_key(query_text), _prior_key(" ".join([query.bill_name, query.text]))}
        for key in exact_keys:
            for prior in self.index.exact_prior_by_key.get(key, []):
                if _is_excluded_source(prior, raw_item):
                    continue
                confidence_factor = min(1.0, max(0.55, prior.confidence / 100.0))
                self._apply_quota_bonus(prior.quota_ids[:3], 0.72 * confidence_factor, "exact_prior", prior_bonus, prior_reasons, candidate_indices)

        prior_scores = _bm25_scores(self.index.prior_bm25, query_tokens, len(self.index.priors))
        max_prior_score = max(prior_scores, default=0.0)
        for idx in _top_indices(prior_scores, 12):
            raw = prior_scores[idx]
            if raw <= 0:
                continue
            prior = self.index.priors[idx]
            if _is_excluded_source(prior, raw_item):
                continue
            strength = _normalize_score(raw, max_prior_score)
            confidence_factor = min(1.0, max(0.45, prior.confidence / 100.0))
            self._apply_quota_bonus(prior.quota_ids[:3], 0.30 * strength * confidence_factor, "prior", prior_bonus, prior_reasons, candidate_indices)

    def _collect_shadow_priors(
        self,
        raw_item: dict[str, Any],
        query: GoalSearchItem,
        query_text: str,
        query_tokens: list[str],
        prior_bonus: dict[str, float],
        prior_reasons: dict[str, list[str]],
        candidate_indices: set[int],
    ) -> None:
        if not self.index.shadow_priors:
            return
        shadow_keys = {_prior_key(query.bill_name), _prior_key(query.text), _prior_key(query_text)}
        for key in shadow_keys:
            for shadow in self.index.shadow_prior_by_key.get(key, []):
                if _is_excluded_source(shadow, raw_item):
                    continue
                self._apply_shadow_prior(shadow, prior_bonus, prior_reasons, candidate_indices, exact=True)

        shadow_scores = _bm25_scores(self.index.shadow_bm25, query_tokens, len(self.index.shadow_priors))
        max_shadow_score = max(shadow_scores, default=0.0)
        for idx in _top_indices(shadow_scores, 5):
            raw = shadow_scores[idx]
            if raw <= 0 or _normalize_score(raw, max_shadow_score) < 0.60:
                continue
            shadow = self.index.shadow_priors[idx]
            if _is_excluded_source(shadow, raw_item):
                continue
            self._apply_shadow_prior(shadow, prior_bonus, prior_reasons, candidate_indices, exact=False)

    def _collect_national_index_candidates(
        self,
        query_signal: QuotaSignal,
        unit: str,
        prior_bonus: dict[str, float],
        prior_reasons: dict[str, list[str]],
        candidate_indices: set[int],
    ) -> None:
        for row in query_same_cluster(province=self.province, signal=query_signal, unit=unit, limit=32):
            quota_id = clean_text(row.get("quota_id"))
            quota_index = self.index.quota_index_by_id.get(quota_id)
            if quota_index is None:
                continue
            candidate_indices.add(quota_index)
            bonus = float(row.get("national_bonus") or 0.18)
            label = clean_text(row.get("national_match")) or "national_index"
            prior_bonus[quota_id] = max(prior_bonus.get(quota_id, 0.0), bonus)
            prior_reasons.setdefault(quota_id, []).append(label)

    def _apply_quota_bonus(
        self,
        quota_ids: Iterable[str],
        bonus: float,
        label: str,
        prior_bonus: dict[str, float],
        prior_reasons: dict[str, list[str]],
        candidate_indices: set[int],
    ) -> None:
        for quota_id in quota_ids:
            quota_index = self.index.quota_index_by_id.get(quota_id)
            if quota_index is None:
                continue
            candidate_indices.add(quota_index)
            prior_bonus[quota_id] = max(prior_bonus.get(quota_id, 0.0), bonus)
            prior_reasons.setdefault(quota_id, []).append(label)

    def _apply_shadow_prior(
        self,
        shadow: _ShadowPriorRecord,
        prior_bonus: dict[str, float],
        prior_reasons: dict[str, list[str]],
        candidate_indices: set[int],
        *,
        exact: bool,
    ) -> None:
        base = 0.48 if exact else 0.24
        label = "national_shadow_exact" if exact else "national_shadow"
        for quota_id, strength in shadow.quota_scores.items():
            quota_index = self.index.quota_index_by_id.get(quota_id)
            if quota_index is None:
                continue
            candidate_indices.add(quota_index)
            bonus = base * max(0.0, min(1.0, strength))
            prior_bonus[quota_id] = max(prior_bonus.get(quota_id, 0.0), bonus)
            reason_bits = shadow.reasons.get(quota_id, [])[:3]
            reason_text = f"{label}:{'/'.join(reason_bits)}" if reason_bits else label
            prior_reasons.setdefault(quota_id, []).append(reason_text)

    @staticmethod
    def _coerce_item(item: GoalSearchItem | dict[str, Any] | str) -> GoalSearchItem:
        if isinstance(item, GoalSearchItem):
            return item
        if isinstance(item, str):
            return GoalSearchItem(text=item)
        name = clean_text(item.get("bill_name") or item.get("name"))
        desc = clean_text(item.get("bill_text") or item.get("description") or item.get("feature_text"))
        feature_text = GoalSearcher._feature_text_from_item(item)
        if feature_text:
            desc = " ".join(x for x in [desc, feature_text] if x)
        if desc and name and key_text(desc).startswith(key_text(name)):
            text = desc
        else:
            text = " ".join(x for x in [name, desc] if x)
        return GoalSearchItem(
            text=text,
            unit=clean_text(item.get("unit") or item.get("bill_unit")),
            bill_name=name,
            specialty=clean_text(item.get("specialty")),
            no_answer_priors=GoalSearcher._no_answer_priors_from_item(item),
        )

    @staticmethod
    def _no_answer_priors_from_item(item: dict[str, Any]) -> bool:
        if "goal_no_answer_priors" in item:
            return bool(item.get("goal_no_answer_priors"))
        if "goal_allow_answer_priors" in item:
            return not bool(item.get("goal_allow_answer_priors"))
        return True

    @staticmethod
    def _feature_text_from_item(item: dict[str, Any]) -> str:
        parts: list[str] = []
        feature_sources: list[dict[str, Any]] = []
        for feature_key in ("target_feature_snapshot", "canonical_features"):
            raw = GoalSearcher._coerce_feature_payload(item.get(feature_key))
            if isinstance(raw, dict):
                feature_sources.append(raw)
            elif isinstance(raw, str) and raw:
                parts.append(raw)

        for raw in feature_sources:
            for key in (
                "canonical_name",
                "entity",
                "family",
                "material",
                "connection",
                "install_method",
                "laying_method",
                "system",
                "cable_type",
                "wire_type",
                "bridge_type",
                "lamp_type",
                "sanitary_subtype",
                "support_scope",
                "support_action",
                "valve_type",
            ):
                value = clean_text(raw.get(key))
                if value:
                    parts.append(value)
            for numeric in (raw.get("primary_params"), raw.get("numeric_params")):
                GoalSearcher._append_numeric_feature_parts(parts, numeric)

        for numeric_key in ("target_primary_params", "primary_params", "numeric_params"):
            GoalSearcher._append_numeric_feature_parts(parts, item.get(numeric_key))

        seen: set[str] = set()
        result: list[str] = []
        for part in parts:
            part = clean_text(part)
            if not part or part in seen:
                continue
            seen.add(part)
            result.append(part)
        return " ".join(result)

    @staticmethod
    def _coerce_feature_payload(raw: object) -> dict[str, Any] | str:
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except Exception:
                return raw
            return parsed if isinstance(parsed, dict) else raw
        return raw if isinstance(raw, dict) else ""

    @staticmethod
    def _append_numeric_feature_parts(parts: list[str], numeric: object) -> None:
        if isinstance(numeric, str) and numeric.strip():
            try:
                numeric = json.loads(numeric)
            except Exception:
                parts.append(numeric)
                return
        if not isinstance(numeric, dict):
            return

        lowered = {clean_text(key).lower(): value for key, value in numeric.items()}
        width = GoalSearcher._first_numeric_value(lowered, ("width", "width_mm", "w", "宽", "宽度"))
        height = GoalSearcher._first_numeric_value(lowered, ("height", "height_mm", "h", "高", "高度"))
        if width is not None and height is not None:
            parts.append(f"{width:g}x{height:g}")
            parts.append(f"宽+高{width + height:g}")

        for key, value in numeric.items():
            if value in (None, "", [], {}):
                continue
            key_text_value = clean_text(key)
            value_text = clean_text(value)
            if not value_text:
                continue
            lowered_key = key_text_value.lower()
            if lowered_key in {"dn", "diameter", "pipe_diameter", "nominal_diameter", "公称直径", "管径", "外径"}:
                parts.append(f"DN{value_text}")
                parts.append(f"公称直径{value_text}")
            elif lowered_key in {"de"}:
                parts.append(f"De{value_text}")
            elif lowered_key in {"thickness", "wall_thickness", "板材厚度", "厚度"}:
                parts.append(f"厚度{value_text}")
            elif lowered_key in {"cable_section", "cross_section", "section", "截面"}:
                parts.append(f"{value_text}mm2")
            elif lowered_key in {"cable_cores", "cores", "芯数"}:
                parts.append(f"{value_text}芯")
            elif lowered_key in {"circuits", "circuit_count", "回路", "回路数"}:
                parts.append(f"{value_text}回路")
            elif lowered_key in {"half_perimeter", "半周长"}:
                parts.append(f"半周长{value_text}")
            else:
                parts.append(f"{key_text_value}{value_text}")

    @staticmethod
    def _first_numeric_value(values: dict[str, object], keys: Iterable[str]) -> float | None:
        for key in keys:
            value = values.get(key)
            if value in (None, "", [], {}):
                continue
            match = re.search(r"\d+(?:\.\d+)?", clean_text(value))
            if match:
                return float(match.group(0))
        return None


@lru_cache(maxsize=8)
def _get_index(province: str) -> _GoalIndex:
    return _GoalIndex(province)


def clear_goal_search_cache() -> None:
    _get_index.cache_clear()
