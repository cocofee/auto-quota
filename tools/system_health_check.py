# -*- coding: utf-8 -*-
"""
System health check runner for the auto-quota project.

Modes:
  - quick: core syntax/import/regression checks
  - full: quick + full pytest + quota db init + experience dry-run
  - ci: strict gate (required checks only)
"""

from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "output" / "health_reports"


@dataclass
class CheckResult:
    name: str
    required: bool
    passed: bool
    duration_sec: float
    command: str
    detail: str


def _tail_text(text: str, max_lines: int = 40) -> str:
    if not text:
        return ""
    lines = text.strip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _collect_python_files() -> list[Path]:
    files: list[Path] = []
    for folder in ("src", "tools", "tests"):
        base = PROJECT_ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    for root_file in ("main.py", "config.py"):
        path = PROJECT_ROOT / root_file
        if path.exists():
            files.append(path)
    return sorted(set(files))


def run_syntax_check(required: bool = True) -> CheckResult:
    start = time.perf_counter()
    files = _collect_python_files()
    errors: list[str] = []
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{path.relative_to(PROJECT_ROOT)}: {exc.msg}")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{path.relative_to(PROJECT_ROOT)}: {exc}")
    duration = time.perf_counter() - start
    if errors:
        return CheckResult(
            name="Python syntax compile",
            required=required,
            passed=False,
            duration_sec=duration,
            command="py_compile (in-process)",
            detail=_tail_text("\n".join(errors), max_lines=80),
        )
    return CheckResult(
        name="Python syntax compile",
        required=required,
        passed=True,
        duration_sec=duration,
        command="py_compile (in-process)",
        detail=f"compiled {len(files)} files",
    )


def run_command_check(
    name: str,
    command: list[str],
    *,
    required: bool = True,
    timeout_sec: int = 600,
) -> CheckResult:
    start = time.perf_counter()
    cmd_text = " ".join(command)
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
        )
        duration = time.perf_counter() - start
        mixed_output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        detail = _tail_text(mixed_output)
        return CheckResult(
            name=name,
            required=required,
            passed=(completed.returncode == 0),
            duration_sec=duration,
            command=cmd_text,
            detail=detail,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start
        mixed_output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        return CheckResult(
            name=name,
            required=required,
            passed=False,
            duration_sec=duration,
            command=cmd_text,
            detail=f"timeout after {timeout_sec}s\n{_tail_text(mixed_output)}".strip(),
        )
    except Exception as exc:  # pragma: no cover - defensive
        duration = time.perf_counter() - start
        return CheckResult(
            name=name,
            required=required,
            passed=False,
            duration_sec=duration,
            command=cmd_text,
            detail=f"runner error: {exc}",
        )


def _build_checks(mode: str) -> list[tuple[str, callable]]:
    py = sys.executable
    quick_checks = [
        ("syntax", lambda: run_syntax_check(required=True)),
        (
            "import_smoke",
            lambda: run_command_check(
                "Import smoke",
                [
                    py,
                    "-c",
                    (
                        "import src.match_core, src.match_pipeline, src.match_engine, src.agent_matcher; "
                        "import src.query_builder, src.quota_db, src.hybrid_searcher, src.param_validator; "
                        "import src.output_writer, src.experience_db, src.rule_validator, src.text_parser; "
                        "import tools.run_install_smoke; "
                        "import src.vector_engine, src.reranker; "
                        "import src.experience_importer, src.experience_manager; "
                        "import src.rule_family, src.rule_post_validator; "
                        "print('import_ok')"
                    ),
                ],
                required=True,
                timeout_sec=120,
            ),
        ),
        (
            "pytest_stability_guards",
            lambda: run_command_check(
                "Pytest stability guards",
                [
                    py,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_circuit_breaker.py",
                    "tests/test_agent_matcher_resilience.py",
                    "tests/test_match_engine_resilience.py",
                ],
                required=True,
                timeout_sec=600,
            ),
        ),
        (
            "pytest_regression",
            lambda: run_command_check(
                "Pytest regression fixes",
                [py, "-m", "pytest", "-q", "tests/test_regression_fixes.py"],
                required=True,
                timeout_sec=600,
            ),
        ),
    ]

    full_extra = [
        (
            "pytest_all",
            lambda: run_command_check(
                "Pytest all",
                [py, "-m", "pytest", "-q"],
                required=True,
                timeout_sec=1800,
            ),
        ),
        (
            "e2e_output_smoke",
            lambda: run_command_check(
                "E2E output writer smoke (merged cell)",
                [py, "-m", "pytest", "-q", "tests/test_output_writer_merged_cell.py"],
                required=True,
                timeout_sec=60,
            ),
        ),
        (
            "pytest_multiuser_smoke",
            lambda: run_command_check(
                "Pytest multiuser smoke",
                [py, "-m", "pytest", "-q", "tests/test_multiuser_smoke.py"],
                required=True,
                timeout_sec=300,
            ),
        ),
        (
            "quota_db_init",
            lambda: run_command_check(
                "Quota DB schema init",
                [py, "-c", "from src.quota_db import QuotaDB; QuotaDB().init_db(); print('quota_db_ok')"],
                required=True,
                timeout_sec=120,
            ),
        ),
        (
            "experience_health",
            lambda: run_command_check(
                "Experience health (dry-run)",
                [py, "tools/experience_manager.py", "health", "--limit", "100"],
                required=False,
                timeout_sec=600,
            ),
        ),
    ]

    if mode == "quick":
        return quick_checks
    if mode == "full":
        return quick_checks + full_extra
    if mode == "ci":
        # CI mode: quick checks + pytest_all + E2E output smoke + multiuser smoke
        return quick_checks + full_extra[:4]
    raise ValueError(f"unsupported mode: {mode}")


def _render_markdown(report: dict) -> str:
    lines = [
        "# System Health Report",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Started: `{report['started_at']}`",
        f"- Finished: `{report['finished_at']}`",
        f"- Required failures: `{report['required_failures']}`",
        f"- Optional failures: `{report['optional_failures']}`",
        "",
        "| Check | Required | Status | Duration(s) |",
        "|---|---:|---:|---:|",
    ]
    for item in report["checks"]:
        status = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"| {item['name']} | {'yes' if item['required'] else 'no'} | {status} | {item['duration_sec']:.2f} |"
        )
    lines.append("")
    lines.append("## Details")
    for item in report["checks"]:
        status = "PASS" if item["passed"] else "FAIL"
        lines.append("")
        lines.append(f"### {item['name']} ({status})")
        lines.append(f"- Command: `{item['command']}`")
        lines.append("```text")
        lines.append(item["detail"] or "(no output)")
        lines.append("```")
    return "\n".join(lines)


def run(mode: str) -> int:
    started_at = datetime.now().isoformat(timespec="seconds")
    checks = _build_checks(mode)
    results: list[CheckResult] = []

    print("=" * 72)
    print(f"System health check started | mode={mode}")
    print("=" * 72)
    for _, fn in checks:
        result = fn()
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        level = "REQ" if result.required else "OPT"
        print(f"[{status}] [{level}] {result.name} ({result.duration_sec:.2f}s)")
        if not result.passed:
            brief = (result.detail or "").splitlines()
            if brief:
                print(f"  -> {brief[-1][:200]}")

    required_failures = sum(1 for r in results if r.required and not r.passed)
    optional_failures = sum(1 for r in results if (not r.required) and (not r.passed))

    finished_at = datetime.now().isoformat(timespec="seconds")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "mode": mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "required_failures": required_failures,
        "optional_failures": optional_failures,
        "checks": [asdict(r) for r in results],
    }
    json_path = REPORT_DIR / f"health_{mode}_{ts}.json"
    md_path = REPORT_DIR / f"health_{mode}_{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print("-" * 72)
    print(f"Required failures: {required_failures} | Optional failures: {optional_failures}")
    print(f"Report JSON: {json_path}")
    print(f"Report Markdown: {md_path}")
    print("=" * 72)
    return 1 if required_failures > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run system health checks.")
    parser.add_argument(
        "--mode",
        choices=("quick", "full", "ci"),
        default="quick",
        help="Check profile to run.",
    )
    args = parser.parse_args()
    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
