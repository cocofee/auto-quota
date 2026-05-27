from __future__ import annotations

import argparse
import csv
import json
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
DEFAULT_S3_EXECUTION = AGENT_STATE / "goal_10x_s3_offline_whatif_execution_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_source_artifact_stop_closure_strategy_return"


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


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _closure_decisions(s3: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = s3["metrics"]
    return [
        {
            "decision_id": "CLOSE_S3_EXECUTION",
            "decision": "close_as_diagnostic_only",
            "evidence": f"best_candidate={metrics['best_candidate_id']}; net={metrics['best_candidate_net_vs_selected_gate']}; new_loss={metrics['best_candidate_new_residual_loss']}; top_source={metrics['best_candidate_top_source_file']}; source_share={metrics['best_candidate_top_source_gain_share']}",
            "effect": "do_not_validate_or_implement",
        },
        {
            "decision_id": "DO_NOT_PROMOTE_POL_C",
            "decision": "block_validation_and_implementation",
            "evidence": "POL_C has positive dev/OOF net but source_or_taxonomy_artifact stop fired.",
            "effect": "no heldout/hard validation, no threshold change, no GoalSearcher integration",
        },
        {
            "decision_id": "RETURN_TO_BROADER_STRATEGY",
            "decision": "return_now",
            "evidence": "S2 is closed, S3 is stopped, and DQ/S6 implementation lanes require owner mappings.",
            "effect": "select next read-only lane that excludes global_repair_decision_table.csv dependency",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_heldout_or_hard_validation_for_s3",
            "reason": "S3 candidate selection stopped on source artifact before validation.",
            "allowed_after": "only after a new dev/OOF package shows positive effect not dominated by global_repair_decision_table.csv",
        },
        {
            "blocked_action": "implement_POL_B_or_POL_C",
            "reason": "Both future S3 candidates are diagnostic-only after source dominance stop.",
            "allowed_after": "separate implementation review after independent non-source-dominated evidence",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "S3 what-if was artifact execution, not calibration implementation.",
            "allowed_after": "new frozen plan plus passing dev/OOF evidence and explicit implementation go",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "The apparent +26 dev/OOF gain is 100% from one source file.",
            "allowed_after": "cross-source, non-dominated dev/OOF evidence and later validation-only confirmation",
        },
        {
            "blocked_action": "continue_S2_or_DQ_S6_implementation_by_default",
            "reason": "S2 lacks accepted-source independent support; DQ/S6 need owner mappings.",
            "allowed_after": "new evidence or complete owner mappings",
        },
    ]


def _strategy_return_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "S9_NON_GLOBAL_REPAIR_EVIDENCE_INVENTORY",
            "route": "read_only_inventory",
            "depends_on_global_repair_decision_table": "no",
            "depends_on_owner_mappings": "no",
            "needs_training_now": "no",
            "rationale": "Exclude the stopped source and inventory remaining dev/OOF evidence for any non-source-dominated accuracy lever.",
            "decision": "select_next",
        },
        {
            "option_id": "PAUSE_AWAIT_NEW_EVIDENCE",
            "route": "pause",
            "depends_on_global_repair_decision_table": "no",
            "depends_on_owner_mappings": "no",
            "needs_training_now": "no",
            "rationale": "Conservative, but does not actively search for a viable route.",
            "decision": "defer",
        },
        {
            "option_id": "REOPEN_S3_WITH_SAME_SOURCE",
            "route": "blocked",
            "depends_on_global_repair_decision_table": "yes",
            "depends_on_owner_mappings": "no",
            "needs_training_now": "no",
            "rationale": "Would violate the source-artifact stop condition.",
            "decision": "block",
        },
        {
            "option_id": "DQ_S6_IMPLEMENTATION",
            "route": "blocked",
            "depends_on_global_repair_decision_table": "no",
            "depends_on_owner_mappings": "yes",
            "needs_training_now": "no",
            "rationale": "Still requires explicit go plus complete owner mappings.",
            "decision": "block",
        },
    ]


def _reentry_requirements() -> list[dict[str, Any]]:
    return [
        {
            "lane": "S3",
            "requirement": "new dev/OOF evidence not dominated by global_repair_decision_table.csv",
            "minimum_check": "top_source_gain_share < 0.8 and no source/taxonomy artifact stop",
        },
        {
            "lane": "S3",
            "requirement": "positive net with explicit loss budget pass",
            "minimum_check": "net_vs_selected_gate > 0 and new_residual_loss <= frozen ceiling",
        },
        {
            "lane": "S3",
            "requirement": "fallback/default-off preserved",
            "minimum_check": "no GoalSearcher change, no switch enablement, no threshold implementation",
        },
        {
            "lane": "broader_strategy",
            "requirement": "next lane must exclude stopped source dependency",
            "minimum_check": "global_repair_decision_table.csv excluded or reported separately before any claim",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# S3 Source-artifact Stop Closure / Strategy Return",
        "",
        "S3 is closed as diagnostic-only because the apparent dev/OOF gain is source dominated.",
        "",
        "## Metrics",
        "",
        _md_table([["metric", "value"]] + [[key, value] for key, value in report["metrics"].items()]),
        "",
        "## Closure Decisions",
        "",
        _md_table(
            [["decision_id", "decision", "effect"]]
            + [[row["decision_id"], row["decision"], row["effect"]] for row in report["closure_decisions"]]
        ),
        "",
        "## Selected Next Route",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    text = text.replace('<div class="value">S3 stopped</div>', '<div class="value">Strategy return</div>', 1)
    text = text.replace(
        "S3 dev/OOF-only what-if 已执行并触发 source/taxonomy artifact stop；产物完整，但不能进入 validation/implementation。",
        "S3 已只读收口为 diagnostic-only；当前转回 broader strategy，下一条选 non-global-repair evidence inventory。",
        1,
    )
    text = text.replace('<div class="value">S3 诊断收口</div>', '<div class="value">非单源路线</div>', 1)
    text = text.replace(
        "POL_C dev_oof net=26、new_loss=0，但 rescued gain 100% 来自 global_repair_decision_table.csv，按 stop condition 停止。",
        "下一步只读盘点排除 global_repair_decision_table.csv 后，是否还有不依赖 owner mappings 的 dev/OOF 精度证据。",
        1,
    )
    text = text.replace(
        '<td><span class="pill current">current</span></td>\n            <td>Codex selects the next viable route after the user delegated the choice among new evidence, new direction, or owner mappings.</td>',
        '<td><span class="pill done">done</span></td>\n            <td>Codex selects the next viable route after the user delegated the choice among new evidence, new direction, or owner mappings.</td>',
        1,
    )
    marker = """          <tr>
            <td class="stage">10.x learning loop paused awaiting external evidence</td>"""
    row = """          <tr>
            <td class="stage">S3 source-artifact stop closure / strategy return</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only close S3 as diagnostic-only because source/taxonomy artifact stop fired, then return to broader strategy.</td>
            <td>closure_decision=close_as_diagnostic_only; selected_next_lane=S9_NON_GLOBAL_REPAIR_EVIDENCE_INVENTORY; blocked_validation=true; blocked_implementation=true.</td>
            <td>Next: inventory existing dev/OOF evidence excluding global_repair_decision_table.csv dependency. Still no training, no implementation, no heldout/hard selection.</td>
          </tr>
"""
    if "S3 source-artifact stop closure / strategy return" not in text:
        text = text.replace(marker, row + marker, 1)
    text = text.replace(
        "当前状态：S3 dev/OOF-only offline what-if execution 已执行并停止。best_candidate=POL_C_FREEZE_PLUS_NARROW_CANDIDATES；dev_oof net_vs_selected_gate=26；new_residual_loss=0；但 POL_B/POL_C rescued gain 100% 来自 global_repair_decision_table.csv，触发 source_or_taxonomy_artifact stop。",
        "当前状态：S3 source-artifact stop closure / strategy return 已完成。S3 +26 dev/OOF 只能作为 diagnostic-only，因为收益 100% 来自 global_repair_decision_table.csv；禁止进入 heldout/hard validation、实现、改阈值或 GoalSearcher。下一步选择 S9 non-global-repair evidence inventory。",
        1,
    )
    text = text.replace(
        "下一步只允许从 frozen 10.14 S3 scope 进入 dev/OOF-only offline what-if package，并必须产出全部 6 类 artifact：candidate_policy_scorecard、relation_level_audit、loss_budget_gate_report、residual_slice_report、fallback_default_off_report、selection_boundary_report。",
        "下一步只允许只读盘点排除 global_repair_decision_table.csv 依赖后的现有 dev/OOF evidence，判断是否还有非单源、非 owner-mapping 依赖的 accuracy strategy lane。",
        1,
    )
    text = text.replace(
        "如果 S3 what-if 出现 heldout/hard contamination、缺失产物、loss-budget failure、fallback break、source/taxonomy artifact 或 single-relation dominance，必须停止并报告。",
        "如果排除 global_repair_decision_table.csv 后没有 positive dev/OOF net、或只剩单 source/relation/taxonomy artifact，必须停止并报告无可推进路线。",
        1,
    )
    index_marker = """          <tr>
            <td>S3 route selection after S2 stop</td>"""
    index_row = """          <tr>
            <td>S3 source-artifact stop closure / strategy return</td>
            <td>当前最新收口产物：S3 诊断-only，转回 broader strategy 并选择 non-global-repair evidence inventory。</td>
            <td><code>reports/agent_state/goal_10x_s3_source_artifact_stop_closure_strategy_return_summary.json</code></td>
          </tr>
"""
    if "goal_10x_s3_source_artifact_stop_closure_strategy_return_summary.json" not in text:
        text = text.replace(index_marker, index_row + index_marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3-execution", type=Path, default=DEFAULT_S3_EXECUTION)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    s3 = _read_json(args.s3_execution)
    metrics = s3["metrics"]
    closure_decisions = _closure_decisions(s3)
    blocked_actions = _blocked_actions()
    strategy_return_options = _strategy_return_options()
    reentry_requirements = _reentry_requirements()

    report_metrics = {
        "closure_decision": "close_as_diagnostic_only_and_return_to_broader_strategy",
        "best_candidate_id": metrics["best_candidate_id"],
        "best_candidate_net_vs_selected_gate": metrics["best_candidate_net_vs_selected_gate"],
        "best_candidate_new_residual_loss": metrics["best_candidate_new_residual_loss"],
        "best_candidate_top_source_file": metrics["best_candidate_top_source_file"],
        "best_candidate_top_source_gain_share": metrics["best_candidate_top_source_gain_share"],
        "approved_candidate_count": metrics["approved_candidate_count"],
        "selected_next_lane": "S9_NON_GLOBAL_REPAIR_EVIDENCE_INVENTORY",
        "heldout_validation_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "closure_decisions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_closure_decisions.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
        "strategy_return_options_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_strategy_return_options.csv")),
        "reentry_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_reentry_requirements.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / S3 source-artifact stop closure and strategy return",
        "read_only": True,
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "heldout_not_used_for_selection": True,
        "source_artifacts": {
            "s3_execution_summary": str(args.s3_execution),
        },
        "metrics": report_metrics,
        "closure_decisions": closure_decisions,
        "blocked_actions": blocked_actions,
        "strategy_return_options": strategy_return_options,
        "reentry_requirements": reentry_requirements,
        "decision": "Close S3 as diagnostic-only and return to broader strategy. The best S3 dev/OOF candidate has +26 net and zero new loss, but the gain is 100% dominated by global_repair_decision_table.csv, so it cannot enter heldout/hard validation or implementation. Select S9_NON_GLOBAL_REPAIR_EVIDENCE_INVENTORY as the next read-only lane to search existing dev/OOF evidence after excluding the stopped source dependency.",
        "anti_drift_conclusion": "This closure performs no training, tuning, threshold change, rule patch, ranking change, GoalSearcher change, feature whitelist edit, heldout/hard selection, switch enablement, or online integration. It explicitly blocks S3 promotion and selects only a read-only broader-strategy inventory lane.",
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
        "next_stage": {
            "stage": "S9 non-global-repair evidence inventory",
            "goal": "Read-only inventory existing dev/OOF evidence excluding global_repair_decision_table.csv dependency, looking for a non-source-dominated lane that does not require owner mappings or immediate training/implementation.",
            "prohibited": [
                "training",
                "implementation",
                "threshold changes",
                "GoalSearcher changes",
                "heldout/hard selection",
                "claiming source-dominated Top1 gain",
            ],
        },
    }

    _write_csv(Path(artifacts["closure_decisions_csv"]), closure_decisions, list(closure_decisions[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_csv(Path(artifacts["strategy_return_options_csv"]), strategy_return_options, list(strategy_return_options[0].keys()))
    _write_csv(Path(artifacts["reentry_requirements_csv"]), reentry_requirements, list(reentry_requirements[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": report_metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
