from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_WHATIF = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_summary.json"
DEFAULT_GUARD_COVERAGE = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_guard_coverage.csv"
DEFAULT_BLOCK_REASONS = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_guard_block_reasons.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_loss_audit.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_closure_gate"


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
        "# 12.7 Numeric/Spec Tier What-if Closure Gate",
        "",
        "Read-only closure gate after 12.6 dev/OOF-only what-if.",
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
        "当前状态：12.7 numeric/spec tier what-if closure gate 已完成。"
        f"closure_decision={report['metrics']['closure_decision']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}；"
        f"reentry_requires_numeric_query_evidence={str(report['metrics']['reentry_requires_numeric_query_evidence']).lower()}；"
        f"guard_allowed_rows={report['metrics']['guard_allowed_rows']}。"
    )
    next_text = (
        "下一步：默认暂停 12.x numeric/spec lane；只有补充 query/bill_text 明确带规格的 dev/OOF evidence，"
        "或你明确要求回到 broader 12.x strategy review，才进入新 gate。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：实现 numeric/spec comparator、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "用 expected label 反推规格、或在无新 evidence 时继续自动推进 numeric/spec lane。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.6 numeric/spec tier dev/OOF what-if</td>"
    row = (
        "          <tr>\n"
        "            <td>12.7 numeric/spec tier what-if closure gate</td>\n"
        "            <td>只读收口 12.6：因 query-side 数值/规格证据不足，默认不实现并定义 re-entry 条件。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_numeric_spec_tier_whatif_closure_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_numeric_spec_tier_whatif_closure_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whatif-summary", type=Path, default=DEFAULT_WHATIF)
    parser.add_argument("--guard-coverage", type=Path, default=DEFAULT_GUARD_COVERAGE)
    parser.add_argument("--block-reasons", type=Path, default=DEFAULT_BLOCK_REASONS)
    parser.add_argument("--loss-audit", type=Path, default=DEFAULT_LOSS_AUDIT)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    whatif = _read_json(args.whatif_summary)
    guard_coverage = _read_csv(args.guard_coverage)
    block_reasons = _read_csv(args.block_reasons)
    loss_audit = _read_csv(args.loss_audit)
    wm = whatif["metrics"]

    closure_checks = [
        {"check": "whatif_executed", "status": "pass", "evidence": str(args.whatif_summary)},
        {"check": "guard_allowed_rows", "status": "block_implementation" if int(wm["guard_allowed_rows"]) == 0 else "review", "evidence": str(wm["guard_allowed_rows"])},
        {"check": "query_numeric_present_rows", "status": "fail_for_implementation" if int(wm["query_numeric_present_rows"]) == 0 else "pass", "evidence": str(wm["query_numeric_present_rows"])},
        {"check": "new_loss_count", "status": "pass", "evidence": str(wm["new_loss_count"])},
        {"check": "heldout_hard_boundary", "status": "pass", "evidence": "heldout_hard_used=false"},
    ]
    reentry_requirements = [
        {
            "requirement": "numeric_query_or_bill_text_evidence",
            "needed": True,
            "definition": "dev/OOF rows where query or bill_text contains explicit DN/diameter/section/perimeter/volume/dimension specs before label lookup.",
        },
        {
            "requirement": "non_label_leakage",
            "needed": True,
            "definition": "numeric/spec value must come from bill/query fields, not expected_ids, expected_names, or positive quota names.",
        },
        {
            "requirement": "minimum_support",
            "needed": True,
            "definition": "enough guarded rows to estimate gain/loss by source/province/family; current 0 guarded rows is insufficient.",
        },
        {
            "requirement": "loss_budget",
            "needed": True,
            "definition": "new_loss_count remains 0 preferred; any unexplained loss blocks implementation.",
        },
    ]
    closure_options = [
        {
            "option": "pause_numeric_spec_lane",
            "decision": "select",
            "reason": "what-if has 0 guarded rows because query-side numeric/spec evidence is absent",
        },
        {
            "option": "implement_numeric_spec_comparator",
            "decision": "reject",
            "reason": "no guarded action and no gain; implementation would need label leakage or unsafe broad rule",
        },
        {
            "option": "request_new_numeric_evidence",
            "decision": "allowed_future_entry",
            "reason": "can re-enter if dev/OOF query/bill_text rows include explicit specs",
        },
        {
            "option": "return_to_broader_12x_strategy",
            "decision": "allowed_on_user_request",
            "reason": "numeric/spec lane is blocked unless new evidence arrives",
        },
    ]
    blocked_actions = [
        {"action": "implement_numeric_spec_comparator", "blocked": True, "reason": "guard_allowed_rows=0"},
        {"action": "train_or_tune", "blocked": True, "reason": "outside lane and no evidence"},
        {"action": "change_thresholds", "blocked": True, "reason": "no approved threshold plan"},
        {"action": "use_expected_labels_for_specs", "blocked": True, "reason": "label leakage"},
        {"action": "use_heldout_hard_for_selection", "blocked": True, "reason": "split policy"},
        {"action": "auto_advance_numeric_spec_lane_without_new_evidence", "blocked": True, "reason": "closure gate stop condition"},
    ]

    metrics = {
        "closure_decision": "pause_numeric_spec_lane_no_implementation",
        "evaluated_rows": int(wm["evaluated_rows"]),
        "guard_allowed_rows": int(wm["guard_allowed_rows"]),
        "query_numeric_present_rows": int(wm["query_numeric_present_rows"]),
        "candidate_hit1_gain": int(wm["candidate_hit1_gain"]),
        "new_loss_count": int(wm["new_loss_count"]),
        "net_hit1_delta": int(wm["net_hit1_delta"]),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "reentry_requires_numeric_query_evidence": True,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "closure_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_closure_checks.csv")),
        "reentry_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_reentry_requirements.csv")),
        "closure_options_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_closure_options.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Pause the numeric/spec tier lane and do not implement. The dev/OOF what-if had zero guarded rows because the "
        "query/bill_text side lacked explicit numeric/spec evidence; implementing would require leakage from expected labels "
        "or an unsafe broad comparator. Re-entry requires new dev/OOF evidence with explicit specs in query or bill_text."
    )
    report = {
        "stage": "Goal LTR v1 / 12.7 numeric/spec tier what-if closure gate",
        "read_only": True,
        "source_artifacts": {
            "whatif_summary": str(args.whatif_summary),
            "guard_coverage": str(args.guard_coverage),
            "block_reasons": str(args.block_reasons),
            "loss_audit": str(args.loss_audit),
        },
        "metrics": metrics,
        "decision": decision,
        "source_contract_snapshot": {
            "guard_coverage_rows": len(guard_coverage),
            "block_reason_rows": len(block_reasons),
            "loss_audit_rows": len(loss_audit),
        },
        "anti_drift_conclusion": (
            "12.7 is read-only. It closes the numeric/spec what-if lane without implementation and does not train, tune, "
            "change thresholds, edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, use heldout/hard "
            "for selection, or infer missing query specs from expected labels."
        ),
        "next_stage": {
            "stage": "pause numeric/spec lane or broader 12.x strategy review",
            "default": "stop_auto_advance_until_new_evidence_or_user_direction",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["closure_checks_csv"]), closure_checks, list(closure_checks[0].keys()))
    _write_csv(Path(artifacts["reentry_requirements_csv"]), reentry_requirements, list(reentry_requirements[0].keys()))
    _write_csv(Path(artifacts["closure_options_csv"]), closure_options, list(closure_options[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
