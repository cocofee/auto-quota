# -*- coding: utf-8 -*-

from src.performance_monitor import PerformanceMonitor


def test_measure_records_elapsed_time(monkeypatch):
    times = iter([10.0, 10.25, 20.0, 20.5])
    monkeypatch.setattr("src.performance_monitor.time.perf_counter", lambda: next(times))

    monitor = PerformanceMonitor()
    with monitor.measure("阶段A"):
        pass
    with monitor.measure("阶段A"):
        pass

    assert monitor.stages["阶段A"] == 0.75


def test_report_prints_sorted_summary(capsys):
    monitor = PerformanceMonitor()
    monitor.stages = {
        "阶段A": 0.2,
        "阶段B": 0.5,
    }

    report = monitor.report()
    captured = capsys.readouterr().out

    assert "性能报告" in report
    assert "阶段B" in report
    assert "总计" in report
    assert report.strip() == captured.strip()
