from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REDESIGN = AGENT_STATE / "goal_13x_top1_loss_guarded_reranker_redesign_gate_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_top1_loss_guarded_experiment_plan_definition"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _candidate_rows() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "T1G_A_low_conf_margin_guard",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "application_gate": "apply only if baseline_rank != 1 OR top1_confidence below dev q35 OR top1_top2_score_margin below dev q25",
            "rank1_veto": "preserve baseline rank_1 unless low_confidence_or_small_margin is true",
            "purpose": "recover near-miss positives while protecting confident top1",
        },
        {
            "candidate_id": "T1G_B_conflict_guard",
            "objective_variant": "OBJ_F_conflict_only_top1_guard",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "application_gate": "apply only if baseline top1 has family_conflict/book_conflict/numeric_conflict and challenger has matching query signal",
            "rank1_veto": "preserve baseline rank_1 unless explicit conflict evidence exists",
            "purpose": "fix wrong-domain or wrong-spec top1 without touching clean top1",
        },
        {
            "candidate_id": "T1G_C_non_rank1_only",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_SAFE_CORE_ONLY",
            "application_gate": "apply only when baseline positive rank is not rank_1 in dev/OOF group diagnostics",
            "rank1_veto": "never override baseline rank_1",
            "purpose": "measure upper bound of pure no-demotion gated reranking",
        },
        {
            "candidate_id": "T1G_D_near_miss_only",
            "objective_variant": "OBJ_G_pairwise_near_miss_promotion",
            "feature_toggle": "FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT",
            "application_gate": "apply only to baseline rank_2_5/rank_6_10 positive groups in dev/OOF diagnostics",
            "rank1_veto": "never override baseline rank_1",
            "purpose": "target near-miss movement without global top80 reshuffle",
        },
        {
            "candidate_id": "T1G_E_taxonomy_empty_guard",
            "objective_variant": "OBJ_F_conflict_only_top1_guard",
            "feature_toggle": "FT_EXCLUDE_TAXONOMY_FAMILY_AND_ACTION",
            "application_gate": "apply only when query_family or top1_family is empty and baseline confidence/margin is weak",
            "rank1_veto": "preserve confident baseline rank_1",
            "purpose": "handle taxonomy-empty slices without overtrusting family features",
        },
        {
            "candidate_id": "T1G_F_hit5_rescue_with_top1_veto",
            "objective_variant": "OBJ_H_hit5_rescue_top1_veto",
            "feature_toggle": "FT_ALL_CURRENT_WHITELIST",
            "application_gate": "apply only when model moves a positive into top5 and does not demote baseline rank_1",
            "rank1_veto": "hard veto rank_1 demotion",
            "purpose": "use the observed Hit5 signal as recall-within-top80 support, not a Top1 override",
        },
    ]


def _objective_rows() -> list[dict[str, Any]]:
    return [
        {
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "definition": "LambdaRank plus high group weight on baseline rank_1 groups and explicit demotion penalty in scorecard.",
            "selection_rule": "candidate must have positive hit1_net and rank1_loss_count <= max(3, hit1_gain_count / 2) on dev/OOF.",
        },
        {
            "objective_variant": "OBJ_F_conflict_only_top1_guard",
            "definition": "Train standard loss-budgeted model but evaluate only conflict-gated overrides.",
            "selection_rule": "candidate must be positive inside conflict-gated rows and neutral or better overall.",
        },
        {
            "objective_variant": "OBJ_G_pairwise_near_miss_promotion",
            "definition": "Pairwise positive promotion objective scoped to rank_2_5/rank_6_10 near-miss rows.",
            "selection_rule": "candidate must improve rank_2_5/rank_6_10 to rank_1 without any rank_1 demotion outside gate.",
        },
        {
            "objective_variant": "OBJ_H_hit5_rescue_top1_veto",
            "definition": "Use the previous Hit5-positive signal only when a top1 veto says baseline rank_1 is preserved.",
            "selection_rule": "hit5_net must be positive and hit1_loss_count must be zero or near-zero.",
        },
    ]


def _gate_feature_rows() -> list[dict[str, Any]]:
    return [
        {"feature_family": "baseline_strength", "fields": "baseline_rank, top1_confidence, top1_top2_score_margin, top1_reason_count", "purpose": "decide whether baseline top1 should be protected"},
        {"feature_family": "conflict_signals", "fields": "family_conflict, book_conflict, numeric_conflict, unit_conflict, domain_conflict_count", "purpose": "allow override only when top1 is visibly suspicious"},
        {"feature_family": "near_miss_position", "fields": "positive_rank_bucket, challenger_rank, challenger_score_margin", "purpose": "scope reranking to rank_2_5/rank_6_10 recovery"},
        {"feature_family": "taxonomy_coverage", "fields": "query_family_present, candidate_family_present, query_family, top1_family", "purpose": "avoid overtrusting empty taxonomy slices"},
        {"feature_family": "source_robustness", "fields": "source_family, source_file, province, oof_fold", "purpose": "audit concentration; not training features"},
    ]


def _artifact_rows() -> list[dict[str, Any]]:
    prefix = "reports/agent_state/goal_13x_top1_loss_guarded_dev_oof"
    return [
        {"artifact": "execution_summary_json", "path": f"{prefix}_execution_summary.json", "required": True},
        {"artifact": "candidate_scorecard_csv", "path": f"{prefix}_candidate_scorecard.csv", "required": True},
        {"artifact": "rank1_preservation_report_csv", "path": f"{prefix}_rank1_preservation_report.csv", "required": True},
        {"artifact": "gating_coverage_report_csv", "path": f"{prefix}_gating_coverage_report.csv", "required": True},
        {"artifact": "loss_audit_by_slice_csv", "path": f"{prefix}_loss_audit_by_slice.csv", "required": True},
        {"artifact": "source_fold_report_csv", "path": f"{prefix}_source_fold_report.csv", "required": True},
        {"artifact": "leakage_gate_report_csv", "path": f"{prefix}_leakage_gate_report.csv", "required": True},
        {"artifact": "fallback_contract_report_csv", "path": f"{prefix}_fallback_contract_report.csv", "required": True},
        {"artifact": "hit1_flips_jsonl", "path": f"{prefix}_hit1_flips.jsonl", "required": True},
    ]


def _loss_budget_rows() -> list[dict[str, Any]]:
    return [
        {"budget": "overall_hit1_net", "target": "> 0", "stop_if_failed": True},
        {"budget": "overall_rank1_loss_count", "target": "<= max(3, hit1_gain_count / 2)", "stop_if_failed": True},
        {"budget": "gated_rows_hit1_net", "target": "> 0 for each selected gate family", "stop_if_failed": True},
        {"budget": "baseline_rank1_demotion_rate", "target": "<= 1% of baseline rank_1 groups unless explicitly conflict-gated", "stop_if_failed": True},
        {"budget": "source_family_net_share", "target": "<= 35% of positive net", "stop_if_failed": True},
        {"budget": "hit5_net", "target": "> 0 as secondary evidence only", "stop_if_failed": False},
    ]


def _stop_rows() -> list[dict[str, Any]]:
    return [
        {"condition": "heldout_or_hard_access_attempted", "action": "stop_and_reject_run"},
        {"condition": "candidate_releases_or_edits_goal_searcher", "action": "stop_and_reject_run"},
        {"condition": "rank1_loss_budget_failed", "action": "do_not_freeze_candidate"},
        {"condition": "global_rerank_all_top80_attempted", "action": "stop_and_reject_run"},
        {"condition": "required_artifact_missing", "action": "stop_and_report_missing_artifact"},
        {"condition": "source_family_dominates_positive_net", "action": "do_not_freeze_source_dominated_candidate"},
        {"condition": "hit5_positive_but_top1_non_positive", "action": "treat_as_diagnostic_only_do_not_freeze"},
    ]


def _command_rows() -> list[dict[str, Any]]:
    return [
        {
            "order": 1,
            "phase": "plan_review",
            "command": "python tools/goal_13x_top1_loss_guarded_experiment_plan_definition.py",
            "status": "completed_in_13_17",
        },
        {
            "order": 2,
            "phase": "future_dev_oof_execution",
            "command": "python tools/goal_13x_top1_loss_guarded_dev_oof_execute.py --data-dir reports/agent_state/goal_13x_oss_xml_source_aware_training_matrix_expanded --candidate-plan reports/agent_state/goal_13x_top1_loss_guarded_experiment_plan_definition_candidate_matrix.csv --dev-oof-only --no-heldout-selection --emit-loss-audit",
            "status": "not_executed_requires_explicit_go",
        },
        {
            "order": 3,
            "phase": "future_freeze_gate",
            "command": "python tools/goal_13x_top1_loss_guarded_freeze_gate.py",
            "status": "not_executed_requires_successful_dev_oof",
        },
    ]


def _gate_rows(redesign: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "redesign_gate_ready",
            "status": "pass" if redesign.get("decision") == "ready_for_13_17_dev_oof_plan_definition" else "fail",
            "value": redesign.get("decision"),
            "reason": "13.17 can define execution plan only after 13.16 accepts redesign.",
        },
        {
            "gate": "candidate_matrix_bounded",
            "status": "pass",
            "value": 6,
            "reason": "Plan limits candidates to a small matrix rather than open-ended exploration.",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass",
            "value": "blocked",
            "reason": "Execution plan is dev/OOF-only; heldout/hard are not used for selection.",
        },
        {
            "gate": "implementation_not_authorized",
            "status": "hold",
            "value": "explicit_go_required",
            "reason": "13.17 defines the plan; execution requires a separate go.",
        },
    ]
    decision = "execution_plan_ready_waiting_for_explicit_dev_oof_go" if rows[0]["status"] == "pass" else "plan_not_ready"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 13.17 Top1-Loss-Guarded Dev/OOF Experiment Plan Definition",
        "",
        "Read-only experiment plan for the next OSS XML dev/OOF reranker attempt. This stage does not train, validate heldout/hard, tune thresholds, release, or modify GoalSearcher.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Candidate Matrix",
        "",
        _md_table([["candidate_id", "objective_variant", "feature_toggle", "application_gate", "rank1_veto"]] + [[row["candidate_id"], row["objective_variant"], row["feature_toggle"], row["application_gate"], row["rank1_veto"]] for row in report["candidate_rows"]]),
        "",
        "## Loss Budget",
        "",
        _md_table([["budget", "target", "stop_if_failed"]] + [[row["budget"], row["target"], row["stop_if_failed"]] for row in report["loss_budget_rows"]]),
        "",
        "## Required Artifacts",
        "",
        _md_table([["artifact", "path", "required"]] + [[row["artifact"], row["path"], row["required"]] for row in report["artifact_rows"]]),
        "",
        "## Stop Conditions",
        "",
        _md_table([["condition", "action"]] + [[row["condition"], row["action"]] for row in report["stop_rows"]]),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.17 Top1-loss-guarded dev/OOF experiment plan definition 已完成。\n"
        f"结论：{report['decision']}。候选矩阵=6；核心约束：baseline rank_1 默认保护、低置信/近失误/冲突场景才允许 reranker 介入、heldout/hard 不参与选择。\n"
        "下一步：只有明确 go，才进入 13.18 Top1-loss-guarded dev/OOF execution。默认 do_not_execute。\n"
        "禁止：无明确 go 训练、跑 heldout/hard、上线、改 GoalSearcher、改阈值、全局重排 top80、把 Hit5 正收益宣称为 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.17 Top1-loss-guarded dev/OOF experiment plan definition" not in text:
        rows = f"""          <tr>
            <td>13.17 Top1-loss-guarded dev/OOF experiment plan definition</td>
            <td>Read-only execution plan for bounded gated reranker candidates, loss budget, artifacts, and stop conditions.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.16 Top1-loss-guarded reranker redesign gate</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.17 Top1-loss-guarded dev/OOF experiment plan definition")
    parser.add_argument("--redesign-summary", type=Path, default=DEFAULT_REDESIGN)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    redesign = _read_json(args.redesign_summary)
    gate_rows, decision = _gate_rows(redesign)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv")),
        "objective_variants_csv": str(output_prefix.with_name(output_prefix.name + "_objective_variants.csv")),
        "gate_features_csv": str(output_prefix.with_name(output_prefix.name + "_gate_features.csv")),
        "loss_budget_csv": str(output_prefix.with_name(output_prefix.name + "_loss_budget.csv")),
        "artifact_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
    }
    report = {
        "stage": "13.17 Top1-loss-guarded dev/OOF experiment plan definition",
        "read_only": True,
        "decision": decision,
        "candidate_rows": _candidate_rows(),
        "objective_rows": _objective_rows(),
        "gate_feature_rows": _gate_feature_rows(),
        "loss_budget_rows": _loss_budget_rows(),
        "artifact_rows": _artifact_rows(),
        "stop_rows": _stop_rows(),
        "command_rows": _command_rows(),
        "gate_rows": gate_rows,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only plan definition only: no training, no heldout/hard validation, no online integration, no threshold change, no GoalSearcher edit, and no claim of Top1 improvement.",
        "next_stage": {
            "recommended": "13.18 Top1-loss-guarded dev/OOF execution authorization gate: only execute if the user explicitly says go; otherwise keep do_not_execute.",
            "default": "do_not_execute",
        },
    }
    _write_csv(Path(artifacts["candidate_matrix_csv"]), report["candidate_rows"], ["candidate_id", "objective_variant", "feature_toggle", "application_gate", "rank1_veto", "purpose"])
    _write_csv(Path(artifacts["objective_variants_csv"]), report["objective_rows"], ["objective_variant", "definition", "selection_rule"])
    _write_csv(Path(artifacts["gate_features_csv"]), report["gate_feature_rows"], ["feature_family", "fields", "purpose"])
    _write_csv(Path(artifacts["loss_budget_csv"]), report["loss_budget_rows"], ["budget", "target", "stop_if_failed"])
    _write_csv(Path(artifacts["artifact_manifest_csv"]), report["artifact_rows"], ["artifact", "path", "required"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_rows"], ["condition", "action"])
    _write_csv(Path(artifacts["command_contract_csv"]), report["command_rows"], ["order", "phase", "command", "status"])
    _write_csv(Path(artifacts["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "candidate_count": len(report["candidate_rows"]), "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
