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
DEFAULT_11X_CLOSURE = AGENT_STATE / "goal_11x_closure_decision_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_accuracy_strategy_definition"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
        "# 12.0 Accuracy Strategy Definition",
        "",
        "Read-only strategy entry after 11.x closure.",
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
        "当前状态：12.0 accuracy strategy definition 已完成。"
        f"selected_lane={report['metrics']['selected_lane']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}；"
        f"training_allowed_now={str(report['metrics']['training_allowed_now']).lower()}；"
        "11.x 保持收口，不重开 9 条 hints。"
    )
    next_text = (
        "下一步：12.1 candidate-pool/rank-position loss decomposition evidence inventory。"
        "只读盘点现有 dev/OOF evidence，确认是否存在不依赖新增外部 evidence、且可进入最小实现计划的瓶颈。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：重开 11.x 自动推进、扩展 11.x hints、训练、调参、改阈值、改 GoalSearcher、"
            "使用 heldout/hard 做选择、或把 11.x scoped release 宣称为通用 Top1 gain。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>11.x closure decision</td>"
    row = (
        "          <tr>\n"
        "            <td>12.0 accuracy strategy definition</td>\n"
        "            <td>在 11.x 收口后开启独立 12.x，只读选择下一条 accuracy strategy lane。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_accuracy_strategy_definition_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_accuracy_strategy_definition_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--closure-summary", type=Path, default=DEFAULT_11X_CLOSURE)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    closure = _read_json(args.closure_summary)
    lane_candidates = [
        {
            "lane_id": "12A_candidate_pool_rank_position_loss_decomposition",
            "objective": "Find whether remaining misses are candidate-pool absence, low-rank placement, or post-recall ranking loss.",
            "uses_existing_inputs": True,
            "needs_owner_mappings": False,
            "needs_online_integration": False,
            "needs_training_now": False,
            "implementation_now": False,
            "evidence_required": "dev/OOF top80_present/missing, rank bucket, candidate pool size, loss slices, source/province/query_family slices",
            "risk": "diagnostic only; may reveal no implementable lever",
            "score": 5,
            "decision": "select",
        },
        {
            "lane_id": "12B_goal_searcher_integration",
            "objective": "Wire released 11.x hints into broader online GoalSearcher behavior.",
            "uses_existing_inputs": True,
            "needs_owner_mappings": False,
            "needs_online_integration": True,
            "needs_training_now": False,
            "implementation_now": False,
            "evidence_required": "explicit online integration request, rollback boundary, runtime trace, production-safe monitoring",
            "risk": "premature without online integration demand",
            "score": 2,
            "decision": "defer",
        },
        {
            "lane_id": "12C_ranking_training_or_objective",
            "objective": "Train or tune ranking objective/features.",
            "uses_existing_inputs": True,
            "needs_owner_mappings": False,
            "needs_online_integration": False,
            "needs_training_now": True,
            "implementation_now": False,
            "evidence_required": "fresh offline experiment authorization and leakage/loss budget gates",
            "risk": "too broad immediately after 11.x release; would mix attribution",
            "score": 1,
            "decision": "defer",
        },
        {
            "lane_id": "12D_taxonomy_or_owner_mapping_fix",
            "objective": "Fix taxonomy/data-quality rows found in previous DQ lanes.",
            "uses_existing_inputs": False,
            "needs_owner_mappings": True,
            "needs_online_integration": False,
            "needs_training_now": False,
            "implementation_now": False,
            "evidence_required": "owner accepted row mappings or DQ acceptance package",
            "risk": "blocked by missing owner mappings",
            "score": 1,
            "decision": "defer",
        },
    ]
    selected_lane = next(row for row in lane_candidates if row["decision"] == "select")
    evidence_requirements = [
        {"requirement": "dev_oof_only", "meaning": "Use dev/OOF evidence for strategy selection; heldout/hard cannot be used for new selection."},
        {"requirement": "loss_budget", "meaning": "Any future implementation plan must define new-loss budget and rollback conditions before code changes."},
        {"requirement": "attribution_boundary", "meaning": "Keep 11.x released hints separate from any 12.x candidate attribution."},
        {"requirement": "implementation_gate", "meaning": "12.0 does not authorize implementation; 12.1/12.2 must produce concrete file/input/output/test boundaries first."},
    ]
    forbidden_actions = [
        {"action": "expand_11x_hints", "reason": "11.x is closed"},
        {"action": "train_or_tune_now", "reason": "12.0 is read-only strategy definition"},
        {"action": "change_thresholds_now", "reason": "no approved threshold plan"},
        {"action": "wire_goal_searcher_now", "reason": "requires separate integration gate"},
        {"action": "use_heldout_hard_for_selection", "reason": "heldout/hard cannot select new strategy candidates"},
    ]
    metrics = {
        "selected_lane": selected_lane["lane_id"],
        "lane_candidates": len(lane_candidates),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "owner_mapping_dependency": False,
        "11x_lane_status": closure["current_state"]["lane_status"],
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "lane_candidates_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_candidates.csv")),
        "evidence_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_evidence_requirements.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
    }
    decision = (
        "Open independent 12.x with lane 12A: candidate-pool/rank-position loss decomposition. "
        "This is the best next route because it uses existing dev/OOF artifacts, does not require owner mappings, "
        "does not require online integration, and keeps 11.x attribution clean. 12.0 does not authorize code changes."
    )
    report = {
        "stage": "Goal LTR v1 / 12.0 accuracy strategy definition",
        "read_only": True,
        "source_artifacts": {"11x_closure_summary": str(args.closure_summary)},
        "metrics": metrics,
        "decision": decision,
        "selected_lane": selected_lane,
        "anti_drift_conclusion": (
            "12.0 starts a new strategy lane without reopening 11.x. It does not train, tune, implement, change thresholds, "
            "edit taxonomy rows, edit feature whitelists, wire GoalSearcher, use heldout/hard for selection, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "12.1 candidate-pool/rank-position loss decomposition evidence inventory",
            "default": "read_only_inventory_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["lane_candidates_csv"]), lane_candidates, list(lane_candidates[0].keys()))
    _write_csv(Path(artifacts["evidence_requirements_csv"]), evidence_requirements, list(evidence_requirements[0].keys()))
    _write_csv(Path(artifacts["forbidden_actions_csv"]), forbidden_actions, list(forbidden_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
