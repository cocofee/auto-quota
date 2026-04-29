"""Shared material price write-back decision helpers."""

from __future__ import annotations

from typing import Any


LOW_CONFIDENCE_VALUES = {"低", "low", "LOW", "Low"}
MEDIUM_CONFIDENCE_VALUES = {"中", "medium", "MEDIUM", "Medium"}
RISKY_SOURCE_TOKENS = (
    "近似",
    "低置信",
    "估算",
    "跨地区",
    "全国",
    "过期",
    "换算",
    "->",
    "→",
    "用户贡献",
    "user_contribute",
)
RISKY_SOURCE_TYPES = {"user_contribute"}
STRONG_OBJECT_TYPES = {
    "pipe",
    "pipe_fitting",
    "valve",
    "equipment",
    "device",
    "wire",
    "cable",
    "cable_tray",
    "waterproof",
}

_UNIT_COMPAT_GROUPS = [
    {"kg", "千克", "公斤"},
    {"t", "吨"},
    {"个", "只", "套", "台", "件", "组", "块"},
    {"m", "米"},
    {"m²", "㎡", "平方米", "m2"},
    {"m³", "立方米", "m3"},
    {"根", "条", "支"},
    {"桶", "瓶"},
    {"卷", "盘"},
]


def parse_positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return round(price, 2)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _split_reasons(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.replace("；", ";").split(";") if part.strip()]


def _is_risky_source(source: str) -> bool:
    return any(token in source for token in RISKY_SOURCE_TOKENS)


def _normalize_token(value: Any) -> str:
    import re

    return re.sub(r"\s+", "", str(value or "").strip().upper())


def _spec_matches(requested: Any, matched: Any) -> bool:
    import re

    req = _normalize_token(requested)
    hit = _normalize_token(matched)
    if not req or not hit:
        return True
    req = req.replace("×", "X").replace("*", "X")
    hit = hit.replace("×", "X").replace("*", "X")
    if req == hit:
        return True
    pattern = re.escape(req)
    if req[0].isdigit():
        pattern = rf"(?<!\d){pattern}"
    if req[-1].isdigit():
        pattern = rf"{pattern}(?!\d)"
    return re.search(pattern, hit) is not None


def _unit_compatible(requested: Any, matched: Any) -> bool:
    req = str(requested or "").strip().lower()
    hit = str(matched or "").strip().lower()
    if not req or not hit:
        return True
    if req == hit:
        return True
    for group in _UNIT_COMPAT_GROUPS:
        normalized = {str(item).lower() for item in group}
        if req in normalized and hit in normalized:
            return True
    return False


def _object_type_compatible(requested: Any, matched: Any) -> bool:
    req = str(requested or "").strip()
    hit = str(matched or "").strip()
    if not req or not hit:
        return True
    if req == hit:
        return True
    if req not in STRONG_OBJECT_TYPES or hit not in STRONG_OBJECT_TYPES:
        return True
    return False


def _is_quantity_order_outlier(price: float, reference: Any) -> bool:
    ref_price = parse_positive_price(reference)
    if ref_price is None:
        return False
    ratio = price / ref_price
    return ratio >= 100 or ratio <= 0.01


def decide_material_price(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized decision for one material price candidate.

    The helper intentionally errs toward suggested/review when evidence is weak.
    Manual user prices are allowed into the formal column, but automated low
    confidence, approximate, converted, stale or cross-region prices are not.
    """
    user_price = parse_positive_price(candidate.get("user_price"))
    lookup_price = parse_positive_price(
        candidate.get("lookup_price")
        if candidate.get("lookup_price") is not None
        else candidate.get("gldjc_price")
    )
    price = user_price if user_price is not None else lookup_price
    source = str(
        candidate.get("lookup_source")
        or candidate.get("gldjc_source")
        or candidate.get("source")
        or ""
    ).strip()
    source_type = str(candidate.get("lookup_source_type") or candidate.get("source_type") or "").strip()
    confidence = str(
        candidate.get("lookup_confidence")
        or candidate.get("gldjc_confidence")
        or candidate.get("confidence")
        or ""
    ).strip()
    normalization_confidence = str(candidate.get("normalization_confidence") or "").strip()
    risk_reasons = _split_reasons(
        candidate.get("risk_reasons") or candidate.get("gldjc_risk_reasons")
    )

    if price is None:
        return {
            "decision_type": "rejected",
            "write_target": "none",
            "final_price": None,
            "suggested_price": None,
            "confidence": confidence or "低",
            "risk_level": "high",
            "risk_reasons": risk_reasons or ["无可写入价格"],
        }

    if price > 1_000_000:
        _append_reason(risk_reasons, "价格数量级异常")
        return {
            "decision_type": "auto_suggested",
            "write_target": "suggested_column",
            "final_price": None,
            "suggested_price": price,
            "confidence": confidence or "低",
            "risk_level": "high",
            "risk_reasons": risk_reasons,
        }

    if user_price is not None:
        return {
            "decision_type": "manual_approved",
            "write_target": "formal_column",
            "final_price": price,
            "suggested_price": None,
            "confidence": "manual",
            "risk_level": "low",
            "risk_reasons": risk_reasons,
        }

    risk_level = "low"
    if _is_risky_source(source):
        risk_level = "medium"
        _append_reason(risk_reasons, f"风险来源: {source}")
    if source_type in RISKY_SOURCE_TYPES:
        risk_level = "medium"
        _append_reason(risk_reasons, "用户贡献价未审核，不能自动写正式价")
    if confidence in LOW_CONFIDENCE_VALUES:
        risk_level = "high"
        _append_reason(risk_reasons, "低置信度价格")
    if normalization_confidence in LOW_CONFIDENCE_VALUES:
        risk_level = "high"
        _append_reason(risk_reasons, "主材抽取低置信")
    if not _object_type_compatible(candidate.get("object_type"), candidate.get("matched_object_type")):
        risk_level = "high"
        _append_reason(risk_reasons, "对象类型不一致")
    requested_spec = candidate.get("final_spec") or candidate.get("spec") or candidate.get("normalized_spec")
    if not _spec_matches(requested_spec, candidate.get("matched_spec")):
        risk_level = "high"
        _append_reason(risk_reasons, "关键规格不一致")
    requested_unit = candidate.get("unit") or candidate.get("raw_unit")
    if not _unit_compatible(requested_unit, candidate.get("matched_unit")):
        risk_level = "high"
        _append_reason(risk_reasons, "单位不能安全换算")
    target_tax_mode = str(candidate.get("target_tax_mode") or "").strip()
    tax_mode = str(candidate.get("tax_mode") or candidate.get("matched_tax_mode") or "").strip()
    if target_tax_mode and tax_mode and target_tax_mode != tax_mode:
        risk_level = "high"
        _append_reason(risk_reasons, "含税/除税口径不一致")
    if target_tax_mode and not tax_mode:
        risk_level = "medium"
        _append_reason(risk_reasons, "含税/除税口径不明")
    price_scope = str(candidate.get("price_scope") or candidate.get("price_kind") or "").strip()
    if any(token in price_scope or token in source for token in ("综合单价", "安装费", "成套价")):
        risk_level = "high"
        _append_reason(risk_reasons, "价格口径不是主材单价")
    if _is_quantity_order_outlier(price, candidate.get("existing_price")):
        risk_level = "high"
        _append_reason(risk_reasons, "相对原价存在数量级异常")

    if risk_level in {"medium", "high"}:
        return {
            "decision_type": "auto_suggested",
            "write_target": "suggested_column",
            "final_price": None,
            "suggested_price": price,
            "confidence": confidence or ("中" if risk_level == "medium" else "低"),
            "risk_level": risk_level,
            "risk_reasons": risk_reasons,
        }

    return {
        "decision_type": "auto_formal",
        "write_target": "formal_column",
        "final_price": price,
        "suggested_price": None,
        "confidence": confidence or "高",
        "risk_level": "low",
        "risk_reasons": risk_reasons,
    }
