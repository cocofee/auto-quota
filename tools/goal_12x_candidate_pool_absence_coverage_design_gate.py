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
DEFAULT_STRATEGY = AGENT_STATE / "goal_12x_broader_strategy_review_after_numeric_spec_pause_summary.json"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_candidate_pool_absence_coverage_design_gate"


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
        "# 12.9 Candidate-Pool Absence / Query-Family Coverage Design Gate",
        "",
        "Read-only design gate for top1_family_empty / query_family_empty candidate-pool absence.",
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


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：12.9 candidate-pool absence / query-family coverage diagnostics design gate 已完成。"
        f"gate_decision={report['metrics']['gate_decision']}；"
        f"global_repair_source_share={report['metrics']['global_repair_source_share']}；"
        f"non_global_diagnostic_rows={report['metrics']['non_global_diagnostic_rows']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.10 non-global candidate-pool absence coverage row audit。只读审计 19 条非 global-repair "
        "top1_family_empty/query_family_empty，拆分 parser hint、index family coverage、taxonomy/DQ 和阻断项。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现 parser/query-family 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "忽略 global_repair 单源支配、或需要 owner mappings 时继续硬推。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.8 broader strategy review after numeric/spec pause</td>"
    row = (
        "          <tr>\n"
        "            <td>12.9 candidate-pool absence coverage design gate</td>\n"
        "            <td>只读判断 top1_family_empty/query_family_empty 是否存在非 owner-mapping 诊断路线。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_candidate_pool_absence_coverage_design_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_candidate_pool_absence_coverage_design_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _slice(rows: list[dict[str, str]], dimension: str, label: str, limit: int = 20) -> list[dict[str, Any]]:
    total = len(rows)
    return [
        {
            "slice": label,
            "dimension": dimension,
            "key": key or "<empty>",
            "rows": count,
            "share": round(count / total, 6) if total else 0,
        }
        for key, count in Counter(row.get(dimension) or "<empty>" for row in rows).most_common(limit)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_STRATEGY)
    parser.add_argument("--top80-missing", type=Path, default=DEFAULT_TOP80_MISSING)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy = _read_json(args.strategy_summary)
    top80_missing = _read_csv(args.top80_missing)
    target_rows = [row for row in top80_missing if row.get("reason") in {"top1_family_empty", "query_family_empty"}]
    global_rows = [row for row in target_rows if row.get("source_file") == "global_repair_decision_table.csv"]
    non_global_rows = [row for row in target_rows if row.get("source_file") != "global_repair_decision_table.csv"]
    non_global_query_empty = [row for row in non_global_rows if row.get("reason") == "query_family_empty"]
    non_global_top1_empty = [row for row in non_global_rows if row.get("reason") == "top1_family_empty"]
    global_share = len(global_rows) / len(target_rows) if target_rows else 0.0

    gate_checks = [
        {
            "gate": "global_source_dominance",
            "status": "fail_for_direct_implementation" if global_share > 0.5 else "pass",
            "evidence": f"{len(global_rows)}/{len(target_rows)}={global_share:.6f}",
        },
        {
            "gate": "non_global_diagnostic_support",
            "status": "pass_for_audit_only" if len(non_global_rows) >= 10 else "weak",
            "evidence": str(len(non_global_rows)),
        },
        {
            "gate": "query_family_empty_support",
            "status": "pass_for_audit_only" if len(non_global_query_empty) >= 5 else "weak",
            "evidence": str(len(non_global_query_empty)),
        },
        {
            "gate": "top1_family_empty_support",
            "status": "pass_for_audit_only" if len(non_global_top1_empty) >= 5 else "weak",
            "evidence": str(len(non_global_top1_empty)),
        },
        {
            "gate": "implementation_boundary",
            "status": "blocked",
            "evidence": "row-level diagnosis required; owner-mapping dependency must be separated",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass",
            "evidence": "read-only dev evidence; no heldout/hard selection",
        },
    ]
    candidate_lanes = [
        {
            "lane": "non_global_candidate_pool_absence_row_audit",
            "support_rows": len(non_global_rows),
            "status": "allow_next_read_only_audit",
            "why": "small but cross-source/cross-province diagnostic subset remains after excluding global_repair",
            "not_allowed": "implementation, training, threshold change",
        },
        {
            "lane": "query_family_empty_parser_hint_diagnostics",
            "support_rows": len(non_global_query_empty),
            "status": "audit_only",
            "why": "may reveal parser/query-family hints, but includes 11.x-overlap and taxonomy-like rows",
            "not_allowed": "direct parser rule without row audit and loss gate",
        },
        {
            "lane": "top1_family_empty_index_coverage_diagnostics",
            "support_rows": len(non_global_top1_empty),
            "status": "audit_only",
            "why": "may reveal index family coverage gaps or book/taxonomy artifacts",
            "not_allowed": "owner-mapping or taxonomy fix without accepted package",
        },
        {
            "lane": "global_repair_candidate_absence_rule",
            "support_rows": len(global_rows),
            "status": "blocked",
            "why": "single-source dominated; cannot generalize",
            "not_allowed": "direct implementation or broad recall claim",
        },
    ]
    slice_summary: list[dict[str, Any]] = []
    for dimension in ("source_file", "province", "reason", "query_family"):
        slice_summary.extend(_slice(non_global_rows, dimension, "non_global_target_rows"))
    design_requirements = [
        {
            "requirement": "row_level_disposition",
            "definition": "Each row must be classified as parser hint, index family coverage, taxonomy/DQ, source artifact, 11.x overlap, or blocked.",
        },
        {
            "requirement": "owner_mapping_boundary",
            "definition": "Rows requiring taxonomy row edits or owner acceptance cannot enter implementation from this lane.",
        },
        {
            "requirement": "loss_gate_before_implementation",
            "definition": "Any future parser/query hint needs dev/OOF what-if, zero unexplained new loss, and source/province/family slices.",
        },
        {
            "requirement": "global_repair_exclusion",
            "definition": "Global repair dominated rows stay diagnostic only unless independent evidence appears.",
        },
    ]
    metrics = {
        "gate_decision": "allow_non_global_row_audit_block_direct_implementation",
        "target_rows": len(target_rows),
        "global_repair_rows": len(global_rows),
        "global_repair_source_share": round(global_share, 6),
        "non_global_diagnostic_rows": len(non_global_rows),
        "non_global_query_family_empty_rows": len(non_global_query_empty),
        "non_global_top1_family_empty_rows": len(non_global_top1_empty),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "slice_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_slice_summary.csv")),
        "design_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_design_requirements.csv")),
    }
    decision = (
        "Do not implement from the full top1_family_empty/query_family_empty bucket because it is dominated by global_repair. "
        "Allow only a read-only 12.10 row audit of the 19 remaining non-global rows to separate parser hints, index-family "
        "coverage gaps, taxonomy/DQ issues, source artifacts, and 11.x overlap."
    )
    report = {
        "stage": "Goal LTR v1 / 12.9 candidate-pool absence / query-family coverage diagnostics design gate",
        "read_only": True,
        "source_artifacts": {
            "strategy_summary": str(args.strategy_summary),
            "top80_missing": str(args.top80_missing),
        },
        "metrics": metrics,
        "decision": decision,
        "strategy_context": {
            "selected_next_lane": strategy["metrics"]["selected_next_lane"],
            "numeric_spec_lane_status": strategy["metrics"]["numeric_spec_lane_status"],
        },
        "anti_drift_conclusion": (
            "12.9 is read-only. It blocks direct implementation due to global_repair source dominance and does not train, tune, "
            "change thresholds, edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, use heldout/hard "
            "for selection, or continue rows that require owner mappings."
        ),
        "next_stage": {
            "stage": "12.10 non-global candidate-pool absence coverage row audit",
            "default": "read_only_audit_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["candidate_lanes_csv"]), candidate_lanes, list(candidate_lanes[0].keys()))
    _write_csv(Path(artifacts["slice_summary_csv"]), slice_summary, list(slice_summary[0].keys()) if slice_summary else ["slice"])
    _write_csv(Path(artifacts["design_requirements_csv"]), design_requirements, list(design_requirements[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
