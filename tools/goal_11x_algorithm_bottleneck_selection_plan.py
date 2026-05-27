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
DEFAULT_S7 = AGENT_STATE / "goal_10x_s7_diagnostic_implications_next_lane_selection_summary.json"
DEFAULT_S6 = AGENT_STATE / "goal_10x_s6_inventory_artifact_acceptance_gate_summary.json"
DEFAULT_S6_PLAN = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_summary.json"
DEFAULT_S9 = AGENT_STATE / "goal_10x_s9_non_global_repair_evidence_inventory_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_algorithm_bottleneck_selection_plan"


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


def _candidate_lanes(
    s7: dict[str, Any],
    s6: dict[str, Any],
    s6_plan: dict[str, Any],
    s9: dict[str, Any],
) -> list[dict[str, Any]]:
    s7m = s7["metrics"]
    s6m = s6["metrics"]
    s6pm = s6_plan["metrics"]
    s9m = s9["metrics"]
    return [
        {
            "lane_id": "A_parser_query_normalization_candidate_recall",
            "layer": "parser_query_normalization_and_candidate_recall",
            "evidence": (
                f"dev_top80_missing_groups={s7m['dev_top80_missing_groups']}; "
                f"dev_top80_recall_rate={s7m['dev_top80_recall_rate']}; "
                f"dev_top80_missing_query_family_empty={s7m['dev_top80_missing_query_family_empty']}; "
                f"dev_top80_missing_top1_family_empty={s7m['dev_top80_missing_top1_family_empty']}; "
                f"s6_future_fix_candidate_rows={s6m['future_fix_candidate_rows']}; "
                f"s6_parser_planning_rows={s6pm['parser_planning_rows']}"
            ),
            "implementation_dependency": "existing parser/query/search code paths",
            "owner_mapping_dependency": "no for dev-only what-if; yes only for taxonomy row edits, which are excluded",
            "heldout_dependency": "no",
            "estimated_blast_radius": "bounded",
            "risk": "medium",
            "score": 8,
            "decision": "select",
            "why": "Only lane with concrete upstream bottleneck, code entry points, and a reversible dev/OOF-only what-if path after S2/S3 no-go.",
        },
        {
            "lane_id": "B_ranking_or_LTR_retry",
            "layer": "ranking_objective",
            "evidence": f"s9_reentry_candidate_count={s9m['reentry_candidate_count']}; s2_best_non_global_net={s9m['s2_best_non_global_net']}",
            "implementation_dependency": "training or ranking objective execution",
            "owner_mapping_dependency": "no",
            "heldout_dependency": "no for dev/OOF, but no re-entry evidence exists",
            "estimated_blast_radius": "medium",
            "risk": "high",
            "score": 1,
            "decision": "block",
            "why": "S9 showed no non-global positive S2 evidence; retrying ranking would be evidence-free.",
        },
        {
            "lane_id": "C_safety_gate_S3_retry",
            "layer": "safety_gate",
            "evidence": f"s3_best_non_global_gain={s9m['s3_best_non_global_gain']}; s3_best_non_global_net={s9m['s3_best_non_global_net']}",
            "implementation_dependency": "threshold/policy execution",
            "owner_mapping_dependency": "no",
            "heldout_dependency": "blocked before validation",
            "estimated_blast_radius": "medium",
            "risk": "high",
            "score": 0,
            "decision": "block",
            "why": "S3 positive signal disappeared after excluding global_repair_decision_table.csv.",
        },
        {
            "lane_id": "D_taxonomy_row_mapping",
            "layer": "taxonomy_data_quality",
            "evidence": f"s6_future_fix_candidate_rows={s6m['future_fix_candidate_rows']}; implementation_ready_rows={s6m['implementation_ready_rows']}",
            "implementation_dependency": "owner-approved row mappings",
            "owner_mapping_dependency": "yes",
            "heldout_dependency": "no",
            "estimated_blast_radius": "bounded",
            "risk": "medium",
            "score": 2,
            "decision": "defer",
            "why": "Useful but blocked by owner mappings; not the best route when user wants Codex to proceed without manual mappings.",
        },
    ]


def _implementation_plan() -> list[dict[str, Any]]:
    return [
        {
            "step_id": "IMPL_1_HINT_TABLE",
            "action": "Add a small allowlisted query-intent hint table for parser/query-family empty cases.",
            "target_files": "src/goal_search/national_index.py; optionally src/canonical_dictionary.py",
            "exact_boundary": "Start with aliases already present in S6: 电箱->electrical_box; LED屏/视频/摄像/扩声/读卡/目标识别->weak-current device bucket for dev-only what-if.",
            "success_evidence": "Unit tests prove hints set family only for exact terms and do not affect pipe/valve/lamp existing cases.",
            "rollback": "Remove hint table and tests; no DB or model artifact rollback.",
        },
        {
            "step_id": "IMPL_2_QUERY_BUILDER_ALIAS",
            "action": "Normalize selected short query names into quota-searchable phrases before candidate collection.",
            "target_files": "src/query_builder.py",
            "exact_boundary": "Map 电箱 to 配电箱 query wording; preserve original weak-current subject terms instead of replacing them with generic installation text.",
            "success_evidence": "build_quota_query tests for S6 examples keep decisive subject terms near the front of the query.",
            "rollback": "Remove helper and test cases; no ranking or online wiring rollback.",
        },
        {
            "step_id": "IMPL_3_DEV_OOF_WHATIF",
            "action": "Run a dev/OOF-only candidate recall what-if on S6 examples and top80_missing slices.",
            "target_files": "tools/goal_11x_parser_recall_dev_oof_whatif.py",
            "exact_boundary": "Report top80 recall delta, top1 delta as diagnostic, new wrong-rank/loss slices, and source-family distribution; exclude heldout/hard selection.",
            "success_evidence": "What-if emits scorecard, hit/loss examples, source-family slices, and no heldout/hard contamination report.",
            "rollback": "Delete what-if outputs; no product code rollback if run in dry-run mode.",
        },
        {
            "step_id": "IMPL_4_FREEZE_GATE",
            "action": "If dev/OOF what-if passes, freeze only the tiny hint set before any validation.",
            "target_files": "reports/agent_state/* only at this stage",
            "exact_boundary": "No GoalSearcher production switch, no threshold change, no LTR retrain, no feature whitelist edit.",
            "success_evidence": "Freeze report names exact hints, source distribution, loss budget, rollback, and validation-only boundary.",
            "rollback": "Do not freeze; keep implementation branch unselected.",
        },
    ]


def _code_targets() -> list[dict[str, Any]]:
    return [
        {
            "file": "src/goal_search/national_index.py",
            "current_entry": "infer_family(value) and extract_signal(value)",
            "planned_change": "Add tiny exact-token family hints used by local family candidate collection.",
            "risk_control": "No sample_id/source_file/expected_id; no learned weights; unit tests for negative terms.",
        },
        {
            "file": "src/query_builder.py",
            "current_entry": "build_quota_query(parser, name, description, ...)",
            "planned_change": "Add minimal query aliasing for selected parser-empty S6 terms while preserving primary subject.",
            "risk_control": "Keep helper allowlisted and reversible; do not change LTR/ranking.",
        },
        {
            "file": "tests/test_query_builder_fixed_aliases.py",
            "current_entry": "existing fixed alias query tests",
            "planned_change": "Add tests for 电箱/LED屏/视频/摄像/扩声 examples from S6.",
            "risk_control": "Test exact output tokens, not broad benchmark claims.",
        },
        {
            "file": "tests/test_query_router.py",
            "current_entry": "routing profile tests",
            "planned_change": "Add route tests only if query-route behavior changes.",
            "risk_control": "No test churn if route layer is untouched.",
        },
        {
            "file": "tools/goal_11x_parser_recall_dev_oof_whatif.py",
            "current_entry": "new dev/OOF dry-run tool",
            "planned_change": "Evaluate recall/candidate-pool effect without heldout/hard selection.",
            "risk_control": "Report source dominance and stop if single-source gain dominates.",
        },
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {
            "check_id": "AC_DEV_OOF_ONLY",
            "required": "Use dev/OOF only for selection and what-if scoring.",
            "pass_condition": "heldout/hard rows used for selection = 0",
        },
        {
            "check_id": "AC_RECALL_OR_CANDIDATE_POOL_GAIN",
            "required": "Top80 recall or candidate-pool presence improves on parser-empty slices.",
            "pass_condition": "positive dev/OOF delta with examples, not just query text change",
        },
        {
            "check_id": "AC_LOSS_AUDIT",
            "required": "Report new losses/wrong-rank regressions by source/query_family/top1_family.",
            "pass_condition": "no hidden loss; source dominance stop if max source gain share >= 0.8",
        },
        {
            "check_id": "AC_MINIMAL_CODE",
            "required": "Only touch parser/query/search hint paths and tests.",
            "pass_condition": "No LTR, threshold, GoalSearcher online wiring, feature whitelist, or DB row mapping changes.",
        },
        {
            "check_id": "AC_ROLLBACK",
            "required": "Every hint is removable independently.",
            "pass_condition": "Rollback is delete hint row/helper only; no model or DB rollback needed.",
        },
    ]


def _commands() -> list[dict[str, Any]]:
    return [
        {
            "phase": "unit_tests",
            "command": "python -m pytest tests/test_query_builder_fixed_aliases.py tests/test_query_builder_distribution_boxes.py tests/test_query_router.py",
            "purpose": "Verify query construction/routing stays bounded.",
        },
        {
            "phase": "dev_oof_whatif",
            "command": "python tools/goal_11x_parser_recall_dev_oof_whatif.py --dev-oof-only --emit-loss-audit",
            "purpose": "Future authorized dry-run; not executed in 11.0.",
        },
        {
            "phase": "full_dev_regression_if_authorized",
            "command": "python tools/run_benchmark.py --json-only --detail",
            "purpose": "Broad local regression after implementation; still no heldout selection claim.",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "implement_now",
            "reason": "11.0 is selection and plan only.",
            "allowed_after": "explicit implementation go for the selected minimal parser/query recall plan",
        },
        {
            "blocked_action": "train_or_tune_LTR",
            "reason": "S9 found no non-global ranking evidence.",
            "allowed_after": "future evidence and separate execution plan",
        },
        {
            "blocked_action": "change_safety_gate_or_threshold",
            "reason": "S3 was stopped as source-artifact dominated.",
            "allowed_after": "new non-source-dominated dev/OOF S3 evidence",
        },
        {
            "blocked_action": "edit_taxonomy_row_mappings",
            "reason": "Owner mappings are still not available and taxonomy DB edits are outside the selected minimal plan.",
            "allowed_after": "explicit owner mappings plus implementation go",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "Heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 11.0 Algorithm Bottleneck Selection And Minimal Implementation Plan",
        "",
        "Decision: select parser/query normalization + candidate recall as the next algorithm lane.",
        "",
        "## Metrics",
        "",
        _md_table([["metric", "value"]] + [[key, value] for key, value in report["metrics"].items()]),
        "",
        "## Selected Plan",
        "",
        _md_table(
            [["step_id", "action", "target_files"]]
            + [[row["step_id"], row["action"], row["target_files"]] for row in report["implementation_plan"]]
        ),
        "",
        "## Decision",
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
    text = text.replace('<div class="value">S9 no-go</div>', '<div class="value">11.0 plan</div>', 1)
    text = text.replace(
        "S9 已排除 global_repair_decision_table.csv 盘点现有 dev/OOF evidence；没有发现可 re-entry 的正向 lane。",
        "已停止 10.x 证据循环，11.0 选择 parser/query normalization + candidate recall 作为下一条最小算法实现计划。",
        1,
    )
    text = text.replace('<div class="value">等待新证据</div>', '<div class="value">算法计划</div>', 1)
    text = text.replace(
        "S2 最佳 non-global net=0；S3 non-global gain=0；当前不应训练、验证或实现。",
        "下一步需要 explicit implementation go；先做 dev/OOF-only parser recall what-if，不碰 heldout/线上。",
        1,
    )
    text = text.replace(
        '<td class="stage">S9 non-global-repair evidence inventory</td>\n            <td><span class="pill paused">no-go</span></td>',
        '<td class="stage">S9 non-global-repair evidence inventory</td>\n            <td><span class="pill done">done</span></td>',
        1,
    )
    marker = """          <tr>
            <td class="stage">10.x learning loop paused awaiting external evidence</td>"""
    row = """          <tr>
            <td class="stage">11.0 dev/OOF-only algorithm bottleneck selection and minimal implementation plan</td>
            <td><span class="pill current">current</span></td>
            <td>Read-only select a concrete algorithm bottleneck and define a minimal implementation plan that can enter explicit go.</td>
            <td>selected_lane=parser_query_normalization_candidate_recall; dev_top80_missing_groups=330; query_family_empty_missing=104; s6_future_fix_candidate_rows=16; implementation_allowed=false.</td>
            <td>Next: request explicit go for a bounded dev/OOF-only parser/query recall implementation + what-if. Still no heldout/hard selection or online GoalSearcher change.</td>
          </tr>
"""
    if "11.0 dev/OOF-only algorithm bottleneck selection and minimal implementation plan" not in text:
        text = text.replace(marker, row + marker, 1)
    text = text.replace(
        "当前状态：S9 non-global-repair evidence inventory 已完成。排除 global_repair_decision_table.csv 后，S3 gain=0；S2 最佳 non-global net=0，reentry_candidate_count=0。当前没有可训练/验证/实现的 lane。",
        "当前状态：11.0 algorithm bottleneck selection plan 已完成。决定停止 10.x 证据循环，选择 parser/query normalization + candidate recall 作为下一条可实现算法路线；本步仍未实现。",
        1,
    )
    text = text.replace(
        "下一步只能暂停等待新证据/owner mappings/明确新方向，或另开只读 broader strategy review；不能从当前 non-global inventory 自动进入算法改动。",
        "下一步需要 explicit implementation go：只允许最小 parser/query hint + dev/OOF-only recall what-if，产出 recall/candidate-pool delta 和 loss audit；默认无 go 就不实现。",
        1,
    )
    text = text.replace(
        "禁止：训练、调参、heldout/hard selection、改阈值、改 GoalSearcher、上线，或把 global_repair/source-dominated 证据宣称为通用 Top1 gain。",
        "禁止：训练、调参、heldout/hard selection、改阈值、改线上 GoalSearcher、上线、taxonomy row mapping、或把 parser what-if 直接宣称为通用 Top1 gain。",
        1,
    )
    index_marker = """          <tr>
            <td>S9 non-global-repair evidence inventory</td>"""
    index_row = """          <tr>
            <td>11.0 algorithm bottleneck selection plan</td>
            <td>当前最新计划产物：选择 parser/query normalization + candidate recall，并定义最小实现边界。</td>
            <td><code>reports/agent_state/goal_11x_algorithm_bottleneck_selection_plan_summary.json</code></td>
          </tr>
"""
    if "goal_11x_algorithm_bottleneck_selection_plan_summary.json" not in text:
        text = text.replace(index_marker, index_row + index_marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--s7", type=Path, default=DEFAULT_S7)
    parser.add_argument("--s6", type=Path, default=DEFAULT_S6)
    parser.add_argument("--s6-plan", type=Path, default=DEFAULT_S6_PLAN)
    parser.add_argument("--s9", type=Path, default=DEFAULT_S9)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    s7 = _read_json(args.s7)
    s6 = _read_json(args.s6)
    s6_plan = _read_json(args.s6_plan)
    s9 = _read_json(args.s9)
    lanes = _candidate_lanes(s7, s6, s6_plan, s9)
    plan = _implementation_plan()
    code_targets = _code_targets()
    acceptance_checks = _acceptance_checks()
    commands = _commands()
    blocked_actions = _blocked_actions()
    selected = next(row for row in lanes if row["decision"] == "select")

    s7m = s7["metrics"]
    s6m = s6["metrics"]
    s6pm = s6_plan["metrics"]
    s9m = s9["metrics"]
    metrics = {
        "selected_lane": selected["lane_id"],
        "candidate_lane_count": len(lanes),
        "dev_top80_missing_groups": s7m["dev_top80_missing_groups"],
        "dev_top80_recall_rate": s7m["dev_top80_recall_rate"],
        "dev_top80_missing_query_family_empty": s7m["dev_top80_missing_query_family_empty"],
        "dev_top80_missing_top1_family_empty": s7m["dev_top80_missing_top1_family_empty"],
        "s6_future_fix_candidate_rows": s6m["future_fix_candidate_rows"],
        "s6_parser_planning_rows": s6pm["parser_planning_rows"],
        "s6_taxonomy_planning_rows": s6pm["taxonomy_planning_rows"],
        "s9_reentry_candidate_count": s9m["reentry_candidate_count"],
        "implementation_step_count": len(plan),
        "code_target_count": len(code_targets),
        "acceptance_check_count": len(acceptance_checks),
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "implementation_plan_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_implementation_plan.csv")),
        "code_targets_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_code_targets.csv")),
        "acceptance_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_acceptance_checks.csv")),
        "commands_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_commands.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / 11.0 dev/OOF-only algorithm bottleneck selection and minimal implementation plan",
        "read_only": True,
        "plan_only": True,
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
            "s7_diagnostics": str(args.s7),
            "s6_inventory_acceptance": str(args.s6),
            "s6_planning_scope": str(args.s6_plan),
            "s9_non_global_inventory": str(args.s9),
        },
        "metrics": metrics,
        "candidate_lanes": lanes,
        "implementation_plan": plan,
        "code_targets": code_targets,
        "acceptance_checks": acceptance_checks,
        "commands": commands,
        "blocked_actions": blocked_actions,
        "decision": "Select A_parser_query_normalization_candidate_recall as the next algorithm lane. Stop retrying S2/S3 ranking or safety-gate changes because non-global evidence is zero. Do not edit taxonomy rows without owner mappings. The next actionable step, if explicitly approved, is a minimal parser/query hint implementation plus dev/OOF-only recall what-if, with no heldout/hard selection and no online GoalSearcher change.",
        "anti_drift_conclusion": "11.0 is a read-only implementation plan. It does not train, tune, change thresholds, patch ranking, modify GoalSearcher, edit feature whitelists, use heldout/hard for selection, edit taxonomy row mappings, or claim Top1 gain. It converts the strategy back into a bounded algorithm plan that requires explicit implementation go.",
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
        "next_stage": {
            "stage": "11.1 explicit implementation go/no-go for parser/query recall what-if",
            "goal": "Collect explicit go before making the minimal parser/query hint implementation and dev/OOF-only recall what-if.",
            "default_without_go": "do_not_implement",
            "prohibited": [
                "heldout/hard selection",
                "online GoalSearcher change",
                "LTR training",
                "threshold changes",
                "taxonomy row mapping edits",
                "feature whitelist edits",
            ],
        },
    }

    _write_csv(Path(artifacts["candidate_lanes_csv"]), lanes, list(lanes[0].keys()))
    _write_csv(Path(artifacts["implementation_plan_csv"]), plan, list(plan[0].keys()))
    _write_csv(Path(artifacts["code_targets_csv"]), code_targets, list(code_targets[0].keys()))
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, list(acceptance_checks[0].keys()))
    _write_csv(Path(artifacts["commands_csv"]), commands, list(commands[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
