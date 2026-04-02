from __future__ import annotations

from collections import OrderedDict

from loguru import logger


def _read_value(payload, key: str, default=None):
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _safe_float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class PriceValidator:
    def __init__(self, price_db, *, cache_size: int = 256):
        self.price_db = price_db
        self.cache_size = max(1, int(cache_size or 256))
        self.cache: OrderedDict[str, dict] = OrderedDict()

    def validate(self, bill_item, matched_quota):
        """用历史综合单价验证黄灯匹配结果。"""
        confidence = _safe_int(_read_value(matched_quota, "confidence"), 0)
        if confidence < 60 or confidence > 85:
            return {"status": "skip"}

        bill_name = str(_read_value(bill_item, "name", "") or "").strip()
        quota_code = str(
            _read_value(matched_quota, "code")
            or _read_value(matched_quota, "quota_id")
            or ""
        ).strip()
        specialty = str(_read_value(bill_item, "specialty", "") or "").strip()
        if not bill_name or not quota_code:
            return {"status": "skip"}

        cache_key = f"{bill_name}:{quota_code}:{specialty}"
        price_ref = self._get_cached_reference(
            cache_key,
            query=bill_name,
            quota_code=quota_code,
            specialty=specialty,
        )
        if not price_ref:
            return {"status": "unavailable"}

        summary = price_ref.get("summary") or {}
        sample_count = _safe_int(
            summary.get("count", summary.get("sample_count")),
            0,
        )
        if sample_count < 5:
            return {
                "status": "insufficient_samples",
                "sample_count": sample_count,
            }

        median_price = _safe_float(
            summary.get("median", summary.get("median_composite_unit_price"))
        )
        actual_price = self._extract_bill_price(bill_item)
        if actual_price is None or median_price is None or median_price <= 0:
            return {
                "status": "ok",
                "sample_count": sample_count,
                "median_price": median_price,
            }

        deviation = abs(actual_price - median_price) / median_price
        if deviation > 0.5:
            return {
                "status": "price_mismatch",
                "message": f"单价偏离历史中位数{deviation:.0%}",
                "confidence_penalty": -10,
                "median_price": median_price,
                "actual_price": actual_price,
                "sample_count": sample_count,
                "deviation": deviation,
            }

        return {
            "status": "ok",
            "sample_count": sample_count,
            "median_price": median_price,
            "actual_price": actual_price,
            "deviation": deviation,
        }

    def _get_cached_reference(self, cache_key: str, **query_kwargs) -> dict | None:
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.cache.move_to_end(cache_key)
            return cached

        try:
            value = self.price_db.get_composite_price_reference(
                query=query_kwargs.get("query", ""),
                quota_code=query_kwargs.get("quota_code", ""),
                specialty=query_kwargs.get("specialty", ""),
                top_k=20,
            )
        except Exception as exc:
            logger.debug("price reference lookup failed for {}: {}", cache_key, exc)
            return None

        self.cache[cache_key] = value
        self.cache.move_to_end(cache_key)
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return value

    def _extract_bill_price(self, bill_item) -> float | None:
        for field in ("unit_price", "composite_unit_price", "bill_unit_price"):
            value = _safe_float(_read_value(bill_item, field))
            if value is not None:
                return value

        amount = _safe_float(
            _read_value(bill_item, "amount")
            or _read_value(bill_item, "total_price")
            or _read_value(bill_item, "bill_amount")
        )
        quantity = _safe_float(_read_value(bill_item, "quantity"))
        if amount is None or quantity in (None, 0):
            return None
        return amount / quantity
