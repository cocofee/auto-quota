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
    "用户贡献",
    "user_contribute",
)


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
    if confidence in LOW_CONFIDENCE_VALUES:
        risk_level = "high"
        _append_reason(risk_reasons, "低置信度价格")
    if normalization_confidence in LOW_CONFIDENCE_VALUES:
        risk_level = "high"
        _append_reason(risk_reasons, "主材抽取低置信")
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
