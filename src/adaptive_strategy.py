# -*- coding: utf-8 -*-
"""Adaptive strategy selection for bill matching."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from loguru import logger

from src.accuracy_tracker import AccuracyTracker

_SPECIAL_CHAR_PATTERN = re.compile(r"[（）\(\)【】\[\]]")
_AMBIGUOUS_WORDS = ("或", "及", "含", "包括")
_DEFAULT_PARAM_TARGET = 3


class AdaptiveStrategy:
    """根据清单特征自动选择匹配策略。"""

    def __init__(
        self,
        historical_hit_rates: Mapping[str, float] | None = None,
        accuracy_tracker: AccuracyTracker | None = None,
        default_hit_rate: float = 0.65,
        tracker_days: int = 30,
    ) -> None:
        self._historical_hit_rates = {
            str(key).strip().upper(): self._normalize_rate(value, default_hit_rate)
            for key, value in (historical_hit_rates or {}).items()
        }
        self._accuracy_tracker = accuracy_tracker
        self._default_hit_rate = self._normalize_rate(default_hit_rate, 0.65)
        self._tracker_days = max(int(tracker_days or 0), 1)
        self._cached_global_hit_rate: float | None = None

    def select_strategy(self, item: Any) -> str:
        """返回 fast / standard / deep 三档策略。"""
        return self.evaluate(item)["strategy"]

    def evaluate(self, item: Any) -> dict[str, float | str]:
        """输出策略决策及关键特征，便于后续接入监控或追踪。"""
        complexity = self._compute_complexity(item)
        hit_rate = self._get_historical_hit_rate(self._item_get(item, "specialty", ""))
        param_completeness = self._compute_param_completeness(item)

        simple_item = complexity < 0.3 and param_completeness >= 0.6
        complex_item = complexity > 0.7
        sparse_params = param_completeness < 0.3
        very_low_hit_rate = hit_rate < 0.15

        if simple_item and hit_rate > 0.8:
            strategy = "fast"
        elif complex_item or sparse_params:
            strategy = "deep"
        elif very_low_hit_rate and complexity >= 0.35:
            strategy = "deep"
        else:
            strategy = "standard"

        return {
            "strategy": strategy,
            "complexity": complexity,
            "hit_rate": hit_rate,
            "param_completeness": param_completeness,
        }

    def _compute_complexity(self, item: Any) -> float:
        """计算清单复杂度（0-1）。"""
        score = 0.0
        name = str(self._item_get(item, "name", "") or "").strip()
        params = self._normalize_params(self._item_get(item, "params", {}))

        if len(name) > 50:
            score += 0.3

        if len(params) > 5:
            score += 0.3

        if _SPECIAL_CHAR_PATTERN.search(name):
            score += 0.2

        if any(word in name for word in _AMBIGUOUS_WORDS):
            score += 0.2

        return min(score, 1.0)

    def _compute_param_completeness(self, item: Any) -> float:
        """计算参数完整度（0-1）。"""
        params = self._normalize_params(self._item_get(item, "params", {}))
        if not params:
            return 0.0

        meaningful_count = sum(1 for value in params.values() if self._is_meaningful_value(value))
        if meaningful_count <= 0:
            return 0.0

        return min(meaningful_count / _DEFAULT_PARAM_TARGET, 1.0)

    def _get_historical_hit_rate(self, specialty: Any) -> float:
        """优先读取专业历史命中率，缺失时回落到全局经验库命中率。"""
        specialty_key = str(specialty or "").strip().upper()
        if specialty_key and specialty_key in self._historical_hit_rates:
            return self._historical_hit_rates[specialty_key]

        for fallback_key in ("DEFAULT", "*", ""):
            if fallback_key in self._historical_hit_rates:
                return self._historical_hit_rates[fallback_key]

        return self._get_global_hit_rate()

    def _get_global_hit_rate(self) -> float:
        if self._cached_global_hit_rate is not None:
            return self._cached_global_hit_rate

        tracker = self._accuracy_tracker or AccuracyTracker()
        try:
            report = tracker.get_knowledge_hit_report(days=self._tracker_days)
        except Exception as exc:
            logger.debug(f"读取历史命中率失败，回退默认值: {exc}")
            self._cached_global_hit_rate = self._default_hit_rate
            return self._cached_global_hit_rate

        for metric in report.get("layer_metrics", []):
            if str(metric.get("layer", "")).strip() != "ExperienceDB":
                continue
            self._cached_global_hit_rate = self._normalize_rate(
                metric.get("hit_rate", self._default_hit_rate),
                self._default_hit_rate,
            )
            return self._cached_global_hit_rate

        self._cached_global_hit_rate = self._default_hit_rate
        return self._cached_global_hit_rate

    @staticmethod
    def _normalize_params(raw_params: Any) -> dict[str, Any]:
        if isinstance(raw_params, Mapping):
            return {str(key): value for key, value in raw_params.items()}

        if isinstance(raw_params, Sequence) and not isinstance(raw_params, (str, bytes, bytearray)):
            return {
                f"param_{index}": value
                for index, value in enumerate(raw_params)
            }

        return {}

    @staticmethod
    def _item_get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, Mapping):
            return item.get(key, default)
        return getattr(item, key, default)

    @staticmethod
    def _is_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (bytes, bytearray)):
            return bool(value)
        if isinstance(value, Mapping):
            return bool(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return bool(value)
        if isinstance(value, (int, float)):
            return value != 0
        return True

    @staticmethod
    def _normalize_rate(value: Any, default: float) -> float:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            rate = float(default)

        if rate > 1.0:
            rate /= 100.0
        return min(max(rate, 0.0), 1.0)


def summarize_adaptive_strategy_metrics(results: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Aggregate per-result adaptive strategy usage and latency."""
    total_results = len(results or [])
    strategy_order = ("fast", "standard", "deep", "unknown")
    stats: dict[str, dict[str, float | int | None]] = {
        strategy: {
            "count": 0,
            "matched": 0,
            "total_time_sec": 0.0,
            "observed_time_count": 0,
            "avg_time_sec": None,
            "rate": 0.0,
            "matched_rate": 0.0,
        }
        for strategy in strategy_order
    }

    observed_total_time = 0.0
    observed_total_count = 0

    for result in results or []:
        if not isinstance(result, Mapping):
            continue

        bill_item = result.get("bill_item")
        strategy = "unknown"
        if isinstance(bill_item, Mapping):
            strategy = str(bill_item.get("adaptive_strategy") or "").strip().lower() or "unknown"
        if strategy not in stats:
            strategy = "unknown"

        row = stats[strategy]
        row["count"] += 1
        if result.get("quotas"):
            row["matched"] += 1

        performance = result.get("performance")
        if not isinstance(performance, Mapping):
            continue
        try:
            elapsed = float(performance.get("total", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if elapsed < 0:
            continue

        row["total_time_sec"] += elapsed
        row["observed_time_count"] += 1
        observed_total_time += elapsed
        observed_total_count += 1

    for strategy, row in stats.items():
        count = int(row["count"])
        matched = int(row["matched"])
        observed = int(row["observed_time_count"])
        row["rate"] = round(count / total_results * 100, 1) if total_results else 0.0
        row["matched_rate"] = round(matched / count * 100, 1) if count else 0.0
        row["avg_time_sec"] = round(float(row["total_time_sec"]) / observed, 3) if observed else None
        row["total_time_sec"] = round(float(row["total_time_sec"]), 3)

    distribution = {
        strategy: stats[strategy]
        for strategy in strategy_order
        if stats[strategy]["count"] or strategy == "unknown"
    }
    with_strategy_count = sum(
        int(distribution.get(strategy, {}).get("count", 0))
        for strategy in ("fast", "standard", "deep")
    )

    return {
        "total": total_results,
        "with_strategy_count": with_strategy_count,
        "missing_strategy_count": int(distribution.get("unknown", {}).get("count", 0)),
        "observed_time_count": observed_total_count,
        "overall_avg_time_sec": round(observed_total_time / observed_total_count, 3) if observed_total_count else None,
        "distribution": distribution,
    }
