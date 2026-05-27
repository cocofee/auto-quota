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
DEFAULT_GAP_REVIEW = AGENT_STATE / "goal_12x_electrical_box_negative_guard_linkage_gap_review_summary.json"
DEFAULT_EVIDENCE_PACKAGE = AGENT_STATE / "goal_12x_electrical_box_bill_text_linkage_evidence_gate_evidence_package.csv"
DEFAULT_GAP_ROWS = AGENT_STATE / "goal_12x_electrical_box_negative_guard_linkage_gap_review_gap_rows.csv"
DEFAULT_REENTRY_REQUIREMENTS = AGENT_STATE / "goal_12x_electrical_box_negative_guard_linkage_gap_review_reentry_requirements.csv"
DEFAULT_12X_STRATEGY = AGENT_STATE / "goal_12x_accuracy_strategy_definition_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_electrical_box_lane_no_go_closure_broader_return"


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
        "# 12.17 Electrical-Box Lane No-Go Closure and Broader 12.x Return",
        "",
        "Read-only closure for the electrical_box lane after negative-guard/linkage gaps blocked what-if.",
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
    metrics = report["metrics"]
    current = (
        "当前状态：12.17 electrical-box lane no-go closure and broader 12.x return 已完成。"
        f"parked_lane=electrical_box_installation_context；"
        f"preserved_positive_evidence_rows={metrics['preserved_positive_evidence_rows']}；"
        f"blocking_gap_rows={metrics['blocking_gap_rows']}；"
        f"whatif_allowed_now={str(metrics['whatif_allowed_now']).lower()}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.18 broader 12.x strategy review after electrical-box parking。"
        "只读回到整体 12.x，判断是暂停 12.x 等新证据，还是请求 explicit go 进入新的训练/集成/数据路线 gate；"
        "仍不自动训练、不实现、不改 GoalSearcher。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：继续推进 electrical_box what-if、直接实现 electrical_box 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "忽略 top1 guard 缺口、忽略福建同名多匹配，或把 parked lane 说成已验证可上线收益。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.16 electrical-box negative guard / linkage gap review</td>"
    row = (
        "          <tr>\n"
        "            <td>12.17 electrical-box lane no-go closure and broader 12.x return</td>\n"
        "            <td>只读正式 park electrical_box lane，保留 13 条 bill_text 正向证据和重开条件，回到 broader 12.x。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_electrical_box_lane_no_go_closure_broader_return_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_electrical_box_lane_no_go_closure_broader_return_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-review-summary", type=Path, default=DEFAULT_GAP_REVIEW)
    parser.add_argument("--evidence-package", type=Path, default=DEFAULT_EVIDENCE_PACKAGE)
    parser.add_argument("--gap-rows", type=Path, default=DEFAULT_GAP_ROWS)
    parser.add_argument("--reentry-requirements", type=Path, default=DEFAULT_REENTRY_REQUIREMENTS)
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_12X_STRATEGY)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    gap_review = _read_json(args.gap_review_summary)
    strategy = _read_json(args.strategy_summary)
    evidence_rows = _read_csv(args.evidence_package)
    gap_rows = _read_csv(args.gap_rows)
    reentry_rows = _read_csv(args.reentry_requirements)

    preserved_evidence_manifest = [
        {
            "artifact": "positive_bill_text_evidence_package",
            "path": str(args.evidence_package),
            "rows": len(evidence_rows),
            "status": "preserve_for_future_reentry_only",
            "use_boundary": "May support a future dev/OOF-only electrical_box what-if only after reentry requirements pass.",
        },
        {
            "artifact": "negative_guard_gap_rows",
            "path": str(args.gap_rows),
            "rows": len(gap_rows),
            "status": "blocking_gap_manifest",
            "use_boundary": "Explains why current lane is parked; not an implementation input.",
        },
        {
            "artifact": "reentry_requirements",
            "path": str(args.reentry_requirements),
            "rows": len(reentry_rows),
            "status": "required_before_reopen",
            "use_boundary": "All requirements must be satisfied or explicitly excluded before any future what-if authorization.",
        },
    ]
    closure_reasons = [
        {
            "reason": "missing_same_province_top1_guard",
            "severity": "blocker",
            "evidence": f"{gap_review['metrics']['missing_top1_guard_rows']} rows still lack accepted same-province top1 guard coverage.",
        },
        {
            "reason": "pole_bucket_zero_guard",
            "severity": "blocker",
            "evidence": f"{gap_review['metrics']['pole_bucket_guard_rows']}/6 pole-equipment rows have accepted guard coverage.",
        },
        {
            "reason": "fujian_plain_box_ambiguous_positive_linkage",
            "severity": "blocker",
            "evidence": f"{gap_review['metrics']['ambiguous_positive_link_rows']} rows have ambiguous exact positive links.",
        },
        {
            "reason": "broad_guard_not_substitute",
            "severity": "blocker",
            "evidence": f"{gap_review['metrics']['diagnostic_broad_guard_rows']} broad candidates remain diagnostic only.",
        },
    ]
    broader_return_options = [
        {
            "option": "pause_12x_until_new_evidence",
            "status": "available",
            "requires_explicit_go": False,
            "why": "All no-training/no-implementation sublanes selected from 12A are now parked or no-go.",
        },
        {
            "option": "request_explicit_go_for_training_or_objective_lane",
            "status": "deferred_requires_user_go",
            "requires_explicit_go": True,
            "why": "12C was deferred because training/tuning is outside the current boundary.",
        },
        {
            "option": "request_goal_searcher_integration_gate",
            "status": "deferred_requires_user_go",
            "requires_explicit_go": True,
            "why": "11.x scoped hints are released, but online GoalSearcher integration is a separate lane.",
        },
        {
            "option": "return_to_data_quality_or_owner_mapping_route",
            "status": "deferred_requires_owner_inputs",
            "requires_explicit_go": True,
            "why": "DQ/owner mapping routes need accepted mappings or provenance package.",
        },
        {
            "option": "continue_electrical_box_lane_now",
            "status": "blocked",
            "requires_explicit_go": True,
            "why": "Blocked by guard/linkage gaps; explicit go alone is insufficient without required evidence.",
        },
    ]
    forbidden_actions = [
        {"action": "run_electrical_box_whatif", "status": "forbidden", "reason": "whatif input package not ready"},
        {"action": "implement_electrical_box_rule", "status": "forbidden", "reason": "negative guard and linkage gaps remain"},
        {"action": "train_or_tune", "status": "forbidden", "reason": "not part of this closure gate"},
        {"action": "change_thresholds", "status": "forbidden", "reason": "not authorized"},
        {"action": "wire_goal_searcher", "status": "forbidden", "reason": "requires separate integration gate"},
        {"action": "use_heldout_hard_for_selection", "status": "forbidden", "reason": "dev/OOF-only boundary"},
    ]

    metrics = {
        "preserved_positive_evidence_rows": len(evidence_rows),
        "blocking_gap_rows": len(gap_rows),
        "reentry_requirement_rows": len(reentry_rows),
        "missing_top1_guard_rows": gap_review["metrics"]["missing_top1_guard_rows"],
        "ambiguous_positive_link_rows": gap_review["metrics"]["ambiguous_positive_link_rows"],
        "selected_return_stage": "12.18 broader 12.x strategy review after electrical-box parking",
        "whatif_allowed_now": False,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "preserved_evidence_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_preserved_evidence_manifest.csv")),
        "closure_reasons_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_closure_reasons.csv")),
        "broader_return_options_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_broader_return_options.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
    }
    decision = (
        "Park the electrical_box lane. Preserve the 13-row positive bill_text package and reentry requirements, but do not run what-if "
        "or implement. Return to broader 12.x strategy review because this lane is blocked by unresolved negative-guard and linkage gaps."
    )
    report = {
        "stage": "Goal LTR v1 / 12.17 electrical-box lane no-go closure and broader 12.x return",
        "read_only": True,
        "source_artifacts": {
            "gap_review_summary": str(args.gap_review_summary),
            "evidence_package": str(args.evidence_package),
            "gap_rows": str(args.gap_rows),
            "reentry_requirements": str(args.reentry_requirements),
            "strategy_summary": str(args.strategy_summary),
        },
        "metrics": metrics,
        "decision": decision,
        "strategy_context": {
            "selected_12x_lane": strategy["metrics"]["selected_lane"],
            "previous_stage": gap_review["stage"],
            "previous_whatif_allowed_now": gap_review["metrics"]["whatif_allowed_now"],
        },
        "anti_drift_conclusion": (
            "12.17 is read-only. It parks the electrical_box lane and writes closure artifacts only. It does not run dev/OOF what-if, "
            "train, tune, change thresholds, implement electrical_box rules, edit parser/query-family rules, wire GoalSearcher, "
            "use heldout/hard for selection, or claim validated Top1 gain from parked evidence."
        ),
        "next_stage": {
            "stage": "12.18 broader 12.x strategy review after electrical-box parking",
            "default": "read_only_strategy_review",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["preserved_evidence_manifest_csv"]),
        preserved_evidence_manifest,
        ["artifact", "path", "rows", "status", "use_boundary"],
    )
    _write_csv(Path(artifacts["closure_reasons_csv"]), closure_reasons, ["reason", "severity", "evidence"])
    _write_csv(
        Path(artifacts["broader_return_options_csv"]),
        broader_return_options,
        ["option", "status", "requires_explicit_go", "why"],
    )
    _write_csv(Path(artifacts["forbidden_actions_csv"]), forbidden_actions, ["action", "status", "reason"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
