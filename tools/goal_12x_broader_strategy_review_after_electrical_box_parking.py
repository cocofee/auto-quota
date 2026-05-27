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
DEFAULT_12X_STRATEGY = AGENT_STATE / "goal_12x_accuracy_strategy_definition_summary.json"
DEFAULT_LANE_CANDIDATES = AGENT_STATE / "goal_12x_accuracy_strategy_definition_lane_candidates.csv"
DEFAULT_ELECTRICAL_CLOSURE = AGENT_STATE / "goal_12x_electrical_box_lane_no_go_closure_broader_return_summary.json"
DEFAULT_NUMERIC_CLOSURE = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_closure_gate_summary.json"
DEFAULT_MICRO_HINT_NOGO = AGENT_STATE / "goal_12x_parser_query_micro_hint_feasibility_no_go_gate_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_broader_strategy_review_after_electrical_box_parking"


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
        "# 12.18 Broader 12.x Strategy Review After Electrical-Box Parking",
        "",
        "Read-only broader 12.x review after parking the electrical_box lane.",
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
        "当前状态：12.18 broader 12.x strategy review after electrical-box parking 已完成。"
        f"active_no_go_or_parked_lanes={metrics['active_no_go_or_parked_lanes']}；"
        f"available_no_go_default={metrics['available_no_go_default']}；"
        f"recommended_default={metrics['recommended_default']}；"
        f"training_allowed_now={str(metrics['training_allowed_now']).lower()}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.19 12.x no-active-lane pause / explicit-go intake gate。"
        "只读确认默认暂停等待新证据；若用户要继续实质推进，只能明确选择一个新入口："
        "12C offline training/objective gate、12B GoalSearcher integration gate，或 DQ/owner-mapping route。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：自动训练、自动集成 GoalSearcher、自动实现规则、调参、改阈值、使用 heldout/hard 做选择、"
            "重开 electrical_box/numeric-spec/micro-hint 旧 lane，或把 parked evidence 宣称为已验证收益。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.17 electrical-box lane no-go closure and broader 12.x return</td>"
    row = (
        "          <tr>\n"
        "            <td>12.18 broader 12.x strategy review after electrical-box parking</td>\n"
        "            <td>只读回到整体 12.x，判断默认暂停，或等待用户 explicit go 进入训练/集成/数据路线 gate。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_broader_strategy_review_after_electrical_box_parking_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_broader_strategy_review_after_electrical_box_parking_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_12X_STRATEGY)
    parser.add_argument("--lane-candidates", type=Path, default=DEFAULT_LANE_CANDIDATES)
    parser.add_argument("--electrical-closure-summary", type=Path, default=DEFAULT_ELECTRICAL_CLOSURE)
    parser.add_argument("--numeric-closure-summary", type=Path, default=DEFAULT_NUMERIC_CLOSURE)
    parser.add_argument("--micro-hint-nogo-summary", type=Path, default=DEFAULT_MICRO_HINT_NOGO)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy = _read_json(args.strategy_summary)
    lane_candidates = _read_csv(args.lane_candidates)
    electrical_closure = _read_json(args.electrical_closure_summary)
    numeric_closure = _read_json(args.numeric_closure_summary)
    micro_hint_nogo = _read_json(args.micro_hint_nogo_summary)

    lane_status = [
        {
            "lane_id": "12A_candidate_pool_rank_position_loss_decomposition",
            "status": "parked_no_active_sublane",
            "requires_explicit_go": False,
            "evidence": (
                "numeric/spec no-go; parser/query micro-hint no-go; electrical_box parked; "
                "candidate-pool/query-family path global-repair dominated or too thin"
            ),
            "next_if_reopened": "requires new evidence that passes lane-specific reentry requirements",
        },
        {
            "lane_id": "12B_goal_searcher_integration",
            "status": "available_only_with_explicit_integration_go",
            "requires_explicit_go": True,
            "evidence": "11.x scoped parser/query hints are released, but online GoalSearcher integration was explicitly kept separate.",
            "next_if_reopened": "open GoalSearcher integration design/validation gate; no heldout/hard selection without gate",
        },
        {
            "lane_id": "12C_ranking_training_or_objective",
            "status": "available_only_with_explicit_training_go",
            "requires_explicit_go": True,
            "evidence": "Training/tuning/objective changes are outside the read-only/no-implementation boundary used so far.",
            "next_if_reopened": "open offline training/objective authorization gate with split/leakage/loss-audit contract",
        },
        {
            "lane_id": "12D_taxonomy_or_owner_mapping_fix",
            "status": "blocked_without_owner_inputs",
            "requires_explicit_go": True,
            "evidence": "Earlier DQ/S6/DQ implementation paths require accepted owner mappings or provenance package.",
            "next_if_reopened": "provide accepted mappings/provenance, then open DQ implementation authorization gate",
        },
    ]
    default_options = [
        {
            "option": "pause_12x_until_new_evidence_or_explicit_go",
            "recommendation": "default",
            "why": "All no-training/no-implementation 12A sublanes have been exhausted, parked, or no-go.",
            "allowed_now": True,
            "requires_user_input": False,
        },
        {
            "option": "ask_user_for_12C_offline_training_objective_go",
            "recommendation": "most_algorithmic_progress_if_user_wants_more_accuracy",
            "why": "This is the clearest route to another substantive algorithmic change, but it changes the boundary from read-only to execution.",
            "allowed_now": False,
            "requires_user_input": True,
        },
        {
            "option": "ask_user_for_12B_goal_searcher_integration_go",
            "recommendation": "best_if_user_wants_online_product_integration",
            "why": "This wires already released 11.x scoped hints into GoalSearcher, but it is integration rather than new learning.",
            "allowed_now": False,
            "requires_user_input": True,
        },
        {
            "option": "ask_user_for_DQ_owner_mapping_package",
            "recommendation": "best_if_owner_data_is_available",
            "why": "DQ routes remain blocked without accepted owner mappings/provenance.",
            "allowed_now": False,
            "requires_user_input": True,
        },
    ]
    reentry_requirements = [
        {
            "lane": "12A_electrical_box",
            "requirement": "unique positive links plus same-province top1 negative guards",
            "current_status": f"{electrical_closure['metrics']['blocking_gap_rows']} blocking gap rows; parked",
        },
        {
            "lane": "12A_numeric_spec",
            "requirement": "query/bill_text explicit numeric/spec evidence",
            "current_status": numeric_closure["metrics"].get("closure_decision", "paused"),
        },
        {
            "lane": "12A_parser_micro_hint",
            "requirement": "repeated same-family support and negative guards",
            "current_status": f"{micro_hint_nogo['metrics']['micro_hint_candidate_rows']} mixed single-row candidates; no-go",
        },
        {
            "lane": "12B_goal_searcher_integration",
            "requirement": "explicit user go plus integration validation boundary",
            "current_status": "deferred",
        },
        {
            "lane": "12C_training_or_objective",
            "requirement": "explicit user go plus split/leakage/loss-audit plan",
            "current_status": "deferred",
        },
    ]
    forbidden_actions = [
        {"action": "auto_train_or_tune", "status": "forbidden", "reason": "requires explicit 12C go"},
        {"action": "auto_wire_goal_searcher", "status": "forbidden", "reason": "requires explicit 12B integration go"},
        {"action": "auto_implement_rules", "status": "forbidden", "reason": "no active implementation-ready lane"},
        {"action": "reopen_electrical_box_now", "status": "forbidden", "reason": "guard/linkage gaps remain"},
        {"action": "use_heldout_hard_for_selection", "status": "forbidden", "reason": "selection boundary remains dev/OOF-only"},
    ]
    active_no_go_or_parked_lanes = 4
    metrics = {
        "selected_12x_lane": strategy["metrics"]["selected_lane"],
        "active_no_go_or_parked_lanes": active_no_go_or_parked_lanes,
        "available_no_go_default": True,
        "recommended_default": "pause_12x_until_new_evidence_or_explicit_go",
        "explicit_go_routes": 3,
        "training_allowed_now": False,
        "implementation_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "heldout_hard_selection_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "lane_status_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_status.csv")),
        "default_options_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_default_options.csv")),
        "reentry_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_reentry_requirements.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
    }
    decision = (
        "Default to pausing 12.x until new evidence or explicit user go. The read-only/no-implementation 12A mining routes are now parked or no-go. "
        "The only meaningful next progress paths require an explicit boundary change: 12C offline training/objective, 12B GoalSearcher integration, "
        "or a DQ/owner-mapping package."
    )
    report = {
        "stage": "Goal LTR v1 / 12.18 broader 12.x strategy review after electrical-box parking",
        "read_only": True,
        "source_artifacts": {
            "strategy_summary": str(args.strategy_summary),
            "lane_candidates": str(args.lane_candidates),
            "electrical_closure_summary": str(args.electrical_closure_summary),
            "numeric_closure_summary": str(args.numeric_closure_summary),
            "micro_hint_nogo_summary": str(args.micro_hint_nogo_summary),
        },
        "metrics": metrics,
        "decision": decision,
        "lane_candidate_context": {
            "defined_lanes": len(lane_candidates),
            "selected_initial_lane": strategy["metrics"]["selected_lane"],
        },
        "anti_drift_conclusion": (
            "12.18 is read-only. It does not train, tune, change thresholds, implement rules, wire GoalSearcher, run what-if, "
            "use heldout/hard for selection, reopen parked 12A sublanes, or claim parked evidence as validated accuracy gain."
        ),
        "next_stage": {
            "stage": "12.19 12.x no-active-lane pause / explicit-go intake gate",
            "default": "read_only_pause_or_collect_explicit_go",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["lane_status_csv"]),
        lane_status,
        ["lane_id", "status", "requires_explicit_go", "evidence", "next_if_reopened"],
    )
    _write_csv(
        Path(artifacts["default_options_csv"]),
        default_options,
        ["option", "recommendation", "why", "allowed_now", "requires_user_input"],
    )
    _write_csv(
        Path(artifacts["reentry_requirements_csv"]),
        reentry_requirements,
        ["lane", "requirement", "current_status"],
    )
    _write_csv(Path(artifacts["forbidden_actions_csv"]), forbidden_actions, ["action", "status", "reason"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
