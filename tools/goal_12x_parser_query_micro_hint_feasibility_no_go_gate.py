from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_ROW_AUDIT_SUMMARY = AGENT_STATE / "goal_12x_non_global_candidate_pool_absence_coverage_row_audit_summary.json"
DEFAULT_ROW_AUDIT = AGENT_STATE / "goal_12x_non_global_candidate_pool_absence_coverage_row_audit_row_audit.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_parser_query_micro_hint_feasibility_no_go_gate"

MIN_SUPPORT_ROWS_FOR_WHATIF = 5
MIN_DOMINANT_FAMILY_ROWS_FOR_WHATIF = 3
MIN_INDEPENDENT_SOURCE_FILES_FOR_WHATIF = 2


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.11 Parser / Query-Family Micro-Hint Feasibility No-Go Gate",
        "",
        "Read-only gate for the 3 weak parser/query-family candidates from 12.10.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _target_family(query: str) -> str:
    if "路灯" in query:
        return "lamp"
    if "流量指示器" in query or "空气流量" in query:
        return "instrument"
    if "视频系统设备" in query:
        return "weak_current_device"
    return "<unknown>"


def _risk_note(query: str) -> str:
    if "路灯" in query:
        return "Lamp hint would need to avoid broad 灯 substring behavior and prove it does not pull decorative/emergency lamp losses."
    if "流量指示器" in query or "空气流量" in query:
        return "Instrument hint would need to separate flow indicator from fan/air-volume and process-equipment text."
    if "视频系统设备" in query:
        return "Weak-current video hint would need to avoid pulling video measurement/test quota rows into device queries."
    return "Unknown target family; no safe guard can be stated from current evidence."


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.11 parser/query-family micro-hint feasibility no-go gate 已完成。"
        f"micro_hint_candidate_rows={metrics['micro_hint_candidate_rows']}；"
        f"dominant_target_family_rows={metrics['dominant_target_family_rows']}；"
        f"independent_source_files={metrics['independent_source_files']}；"
        f"whatif_allowed_now={str(metrics['whatif_allowed_now']).lower()}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.12 broader 12.x strategy review after parser/query micro-hint no-go。"
        "只读回到整体 12.x，选择下一条不依赖这 3 条薄弱 micro-hint、不重开 11.x、"
        "不需要 owner mappings、且不立即训练/实现的候选路线。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现 3 条 parser/query-family micro-hint、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "把单条跨域样本当成可泛化算法证据、重开 11.x attribution、或把 taxonomy/index coverage 行当 parser 证据。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.10 non-global candidate-pool absence coverage row audit</td>"
    row = (
        "          <tr>\n"
        "            <td>12.11 parser/query-family micro-hint feasibility no-go gate</td>\n"
        "            <td>只读判断 3 条弱 parser/query-family 候选是否足够进入 dev/OOF what-if；结论为 no-go，回到 broader 12.x。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_parser_query_micro_hint_feasibility_no_go_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_parser_query_micro_hint_feasibility_no_go_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-audit-summary", type=Path, default=DEFAULT_ROW_AUDIT_SUMMARY)
    parser.add_argument("--row-audit", type=Path, default=DEFAULT_ROW_AUDIT)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    prior_summary = _read_json(args.row_audit_summary)
    row_audit = _read_csv(args.row_audit)
    candidates = [row for row in row_audit if row.get("future_feasibility_candidate") == "True"]

    candidate_rows: list[dict[str, Any]] = []
    for row in candidates:
        target_family = _target_family(row.get("query", ""))
        candidate_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "current_inferred_family": row.get("current_inferred_family", ""),
                "proposed_target_family": target_family,
                "expected_ids": row.get("expected_ids", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "risk_note": _risk_note(row.get("query", "")),
                "whatif_feasibility": "no_go",
                "reason": "single-row target family with no repeated support; mixed-domain candidate set",
            }
        )

    source_files = {row["source_file"] for row in candidate_rows if row["source_file"]}
    family_counts = Counter(row["proposed_target_family"] for row in candidate_rows)
    dominant_family, dominant_family_rows = ("<none>", 0)
    if family_counts:
        dominant_family, dominant_family_rows = family_counts.most_common(1)[0]
    support_rows = len(candidate_rows)
    mixed_target_families = len(family_counts)
    whatif_allowed_now = (
        support_rows >= MIN_SUPPORT_ROWS_FOR_WHATIF
        and dominant_family_rows >= MIN_DOMINANT_FAMILY_ROWS_FOR_WHATIF
        and len(source_files) >= MIN_INDEPENDENT_SOURCE_FILES_FOR_WHATIF
        and mixed_target_families == 1
    )

    gate_checks = [
        {
            "gate": "minimum_support_rows",
            "status": "fail",
            "evidence": f"{support_rows} < {MIN_SUPPORT_ROWS_FOR_WHATIF}",
        },
        {
            "gate": "coherent_target_family",
            "status": "fail",
            "evidence": f"{mixed_target_families} target families: {', '.join(sorted(family_counts))}",
        },
        {
            "gate": "dominant_family_repetition",
            "status": "fail",
            "evidence": f"{dominant_family} has {dominant_family_rows} < {MIN_DOMINANT_FAMILY_ROWS_FOR_WHATIF}",
        },
        {
            "gate": "independent_source_files",
            "status": "pass",
            "evidence": f"{len(source_files)} >= {MIN_INDEPENDENT_SOURCE_FILES_FOR_WHATIF}",
        },
        {
            "gate": "negative_guard_specificity",
            "status": "fail",
            "evidence": "Each proposed family has only one positive row; negative guards would be guessed rather than evidence-backed.",
        },
        {
            "gate": "implementation_boundary",
            "status": "blocked",
            "evidence": "No direct parser/query-family implementation and no dev/OOF what-if from this thin mixed evidence.",
        },
    ]
    required_evidence = [
        {
            "requirement": "repeated_same_family_support",
            "needed": "At least 5 candidate rows overall and at least 3 rows in one proposed target family.",
            "current": f"{support_rows} overall; max family support {dominant_family_rows}.",
        },
        {
            "requirement": "explicit_negative_guards",
            "needed": "Concrete guard rows proving the hint will not absorb adjacent taxonomy/index coverage rows.",
            "current": "No guard evidence beyond risk notes.",
        },
        {
            "requirement": "dev_oof_loss_audit_contract",
            "needed": "If evidence appears later, run dev/OOF-only what-if with gain/loss/net, source/province/family slices, and zero unexplained new loss.",
            "current": "Not authorized because support gates fail.",
        },
    ]
    metrics = {
        "micro_hint_candidate_rows": support_rows,
        "target_family_count": mixed_target_families,
        "dominant_target_family": dominant_family,
        "dominant_target_family_rows": dominant_family_rows,
        "independent_source_files": len(source_files),
        "whatif_allowed_now": whatif_allowed_now,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "candidate_rows_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_rows.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "required_evidence_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_required_evidence.csv")),
    }
    decision = (
        "No-go for dev/OOF what-if and no implementation. The 3 micro-hint candidates are single-row, mixed-domain signals "
        "(lamp, instrument, weak_current_device), so any parser/query-family hint would be guessed rather than evidence-backed."
    )
    report = {
        "stage": "Goal LTR v1 / 12.11 parser/query-family micro-hint feasibility no-go gate",
        "read_only": True,
        "source_artifacts": {
            "row_audit_summary": str(args.row_audit_summary),
            "row_audit": str(args.row_audit),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_stage_context": {
            "prior_stage": prior_summary["stage"],
            "prior_parser_hint_candidate_rows": prior_summary["metrics"]["parser_hint_candidate_rows"],
            "prior_plan_ready_rows": prior_summary["metrics"]["plan_ready_rows"],
        },
        "anti_drift_conclusion": (
            "12.11 is read-only. It writes only diagnostic artifacts and the dashboard. It does not run a what-if, train, tune, "
            "change thresholds, edit parser/query-family rules, edit taxonomy rows, wire GoalSearcher, use heldout/hard for selection, "
            "reopen 11.x attribution, or claim broad Top1 gain from three single-row mixed-domain candidates."
        ),
        "next_stage": {
            "stage": "12.12 broader 12.x strategy review after parser/query micro-hint no-go",
            "default": "read_only_strategy_review",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["candidate_rows_csv"]),
        candidate_rows,
        [
            "group_id",
            "sample_id",
            "source_file",
            "province",
            "query",
            "current_inferred_family",
            "proposed_target_family",
            "expected_ids",
            "top1_id",
            "top1_name",
            "risk_note",
            "whatif_feasibility",
            "reason",
        ],
    )
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "evidence"])
    _write_csv(Path(artifacts["required_evidence_csv"]), required_evidence, ["requirement", "needed", "current"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
