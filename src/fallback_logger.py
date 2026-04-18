from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Callable

from loguru import logger as _default_logger

try:
    from prometheus_client import Counter as _PrometheusCounter
except Exception:  # pragma: no cover - optional dependency
    _PrometheusCounter = None


class ErrorSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    DEBUG = "debug"


PagerDutyNotifier = Callable[[BaseException, dict[str, str]], None]
MetricHook = Callable[[str, str], None]


class FallbackLogger:
    def __init__(self, base_logger=_default_logger):
        self._logger = base_logger
        self._counts: Counter[tuple[str, str]] = Counter()
        self._pagerduty_notifier: PagerDutyNotifier | None = None
        self._metric_hook: MetricHook | None = None
        self._prom_counter = None
        if _PrometheusCounter is not None:
            try:
                self._prom_counter = _PrometheusCounter(
                    "jarvis_fallback_events_total",
                    "Fallback handling events by severity and component.",
                    ("severity", "component"),
                )
            except Exception:
                self._prom_counter = None

    def configure_pagerduty(self, notifier: PagerDutyNotifier | None) -> None:
        self._pagerduty_notifier = notifier

    def configure_metric_hook(self, hook: MetricHook | None) -> None:
        self._metric_hook = hook

    def snapshot_counts(self) -> dict[tuple[str, str], int]:
        return dict(self._counts)

    def maybe_alert(
        self,
        exception: BaseException | str,
        *,
        severity: ErrorSeverity | str,
        component: str,
        message: str | None = None,
    ) -> None:
        severity_name = ErrorSeverity(severity).value
        component_name = str(component or "unknown")
        exc = exception if isinstance(exception, BaseException) else RuntimeError(str(exception))
        summary = message or f"{component_name} failed: {type(exc).__name__}: {exc}"

        if severity_name == ErrorSeverity.DEBUG.value:
            self._logger.debug(f"[{component_name}] {summary}: {exc}")
            return

        self._record_metric(severity_name, component_name)

        if severity_name == ErrorSeverity.WARNING.value:
            self._logger.warning(f"[{component_name}] {summary}: {exc}")
            return

        self._notify_pagerduty(exc, severity_name, component_name, summary)
        self._logger.opt(exception=exc).error(f"[{component_name}] {summary}")
        raise exc

    def _record_metric(self, severity: str, component: str) -> None:
        self._counts[(severity, component)] += 1
        if self._prom_counter is not None:
            try:
                self._prom_counter.labels(severity=severity, component=component).inc()
            except Exception:
                pass
        if self._metric_hook is not None:
            try:
                self._metric_hook(severity, component)
            except Exception as hook_exc:
                self._logger.warning(
                    f"[fallback_logger.metric_hook] failed for severity={severity} "
                    f"component={component}: {hook_exc}"
                )

    def _notify_pagerduty(
        self,
        exception: BaseException,
        severity: str,
        component: str,
        summary: str,
    ) -> None:
        if self._pagerduty_notifier is None:
            return
        payload = {
            "severity": severity,
            "component": component,
            "summary": summary,
            "exception_type": type(exception).__name__,
        }
        try:
            self._pagerduty_notifier(exception, payload)
        except Exception as notifier_exc:
            self._logger.warning(
                f"[fallback_logger.pagerduty] notify failed for component={component}: {notifier_exc}"
            )


fallback_logger = FallbackLogger()
