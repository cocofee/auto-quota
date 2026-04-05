from __future__ import annotations

from collections import OrderedDict
from typing import Any


class PriceValidator:
    """Validate a matched quota against historical composite price references."""

    def __init__(
        self,
        price_db,
        *,
        cache_size: int = 512,
        min_confidence: float = 60,
        max_confidence: float = 85,
        min_samples: int = 5,
        deviation_threshold: float = 0.5,
        top_k: int = 20,
    ):
        self.price_db = price_db
        self.cache_size = max(int(cache_size or 0), 1)
        self.min_confidence = float(min_confidence)
        self.max_confidence = float(max_confidence)
        self.min_samples = max(int(min_samples or 0), 1)
        self.deviation_threshold = float(deviation_threshold)
        self.top_k = max(int(top_k or 0), 1)
        self.cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    @staticmethod
    def _get(source: Any, key: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(key, default)
        return getattr(source, key, default)

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _cache_get(self, cache_key: str) -> dict[str, Any] | None:
        payload = self.cache.get(cache_key)
        if payload is None:
            return None
        self.cache.move_to_end(cache_key)
        return payload

    def _cache_set(self, cache_key: str, payload: dict[str, Any]) -> None:
        self.cache[cache_key] = payload
        self.cache.move_to_end(cache_key)
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)

    def _extract_bill_unit_price(self, bill_item: Any) -> float | None:
        direct = self._coerce_float(self._get(bill_item, "unit_price"))
        if direct is not None:
            return direct
        direct = self._coerce_float(self._get(bill_item, "composite_unit_price"))
        if direct is not None:
            return direct
        amount = self._coerce_float(self._get(bill_item, "amount"))
        quantity = self._coerce_float(self._get(bill_item, "quantity"))
        if amount is not None and quantity not in (None, 0):
            return amount / quantity
        return None

    def validate(self, bill_item: Any, matched_quota: Any, *, confidence: float | None = None) -> dict[str, Any]:
        match_confidence = self._coerce_float(confidence)
        if match_confidence is None:
            match_confidence = self._coerce_float(self._get(matched_quota, "confidence"))
        match_confidence = float(match_confidence or 0.0)
        if match_confidence < self.min_confidence or match_confidence > self.max_confidence:
            return {"status": "skip"}

        bill_unit_price = self._extract_bill_unit_price(bill_item)
        if bill_unit_price is None:
            return {
                "status": "skip",
                "reason": "missing_bill_price",
            }

        bill_name = str(self._get(bill_item, "name", "") or "").strip()
        specialty = str(self._get(bill_item, "specialty", "") or "").strip()
        quota_code = str(
            self._get(matched_quota, "quota_id", self._get(matched_quota, "code", "")) or ""
        ).strip()
        if not bill_name or not quota_code:
            return {"status": "skip"}

        cache_key = f"{bill_name}:{quota_code}:{specialty}"
        price_ref = self._cache_get(cache_key)
        if price_ref is None:
            price_ref = self.price_db.get_composite_price_reference(
                query=bill_name,
                quota_code=quota_code,
                specialty=specialty,
                top_k=self.top_k,
            )
            self._cache_set(cache_key, price_ref)

        summary = dict(price_ref.get("summary") or {})
        sample_count = int(summary.get("count") or summary.get("sample_count") or 0)
        median_price = self._coerce_float(
            summary.get("median")
            if summary.get("median") is not None
            else summary.get("median_composite_unit_price")
        )

        if sample_count < self.min_samples:
            return {
                "status": "insufficient_samples",
                "sample_count": sample_count,
            }

        if median_price is None or median_price <= 0:
            return {
                "status": "invalid_reference",
                "sample_count": sample_count,
            }

        deviation = abs(bill_unit_price - median_price) / median_price
        if deviation > self.deviation_threshold:
            return {
                "status": "price_mismatch",
                "message": f"单价偏离历史中位数{deviation:.0%}",
                "confidence_penalty": -10,
                "median_price": median_price,
                "actual_price": bill_unit_price,
                "sample_count": sample_count,
                "deviation": deviation,
            }

        return {
            "status": "ok",
            "sample_count": sample_count,
            "median_price": median_price,
            "actual_price": bill_unit_price,
            "deviation": deviation,
        }
