import time
from contextlib import contextmanager

from loguru import logger


class PerformanceMonitor:
    def __init__(self):
        self.stages = {}

    @contextmanager
    def measure(self, stage_name):
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.stages[stage_name] = self.stages.get(stage_name, 0.0) + elapsed
            logger.debug(f"⏱️ {stage_name}: {elapsed:.3f}s")

    def snapshot(self) -> dict[str, float]:
        return dict(self.stages)

    def format_report(self, title: str = "性能报告：") -> str:
        total = sum(self.stages.values())
        lines = [f"\n{title}"]
        for stage, elapsed in sorted(self.stages.items(), key=lambda item: -item[1]):
            pct = (elapsed / total * 100.0) if total > 0 else 0.0
            lines.append(f"  {stage:30s} {elapsed:6.3f}s ({pct:5.1f}%)")
        lines.append(f"  {'总计':30s} {total:6.3f}s")
        return "\n".join(lines)

    def report(self) -> str:
        report_text = self.format_report()
        print(report_text)
        return report_text


def measure_call(monitor: PerformanceMonitor | None, stage_name: str, func, *args, **kwargs):
    if monitor is None:
        return func(*args, **kwargs)
    with monitor.measure(stage_name):
        return func(*args, **kwargs)
