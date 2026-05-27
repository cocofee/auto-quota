from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

DESIGN_SUMMARY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_redesign_definition_summary.json"
DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_experiment_plan_definition"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_rel(path: str | Path) -> str:
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def build_report() -> dict[str, Any]:
    design = read_json(DESIGN_SUMMARY)

    matrix_rebuild_plan = [
        {
            "step": "input_scope",
            "exact_input": "D:\\广联达临时文件\\oss_samples plus existing accepted OSS XML selection manifest",
            "rule": "OSS XML human quantity-surveyor output remains the primary training source",
            "output": "reports/agent_state/goal_14x_rank1_safe_source_robust_matrix",
        },
        {
            "step": "source_family_cap",
            "exact_input": "accepted OSS groups by source_family",
            "rule": "cap each source_family at <=22% preferred; hard stop if post-build share >25%",
            "output": "source_balance_checks.csv",
        },
        {
            "step": "province_book_cap",
            "exact_input": "province, quota_book, source_family metadata",
            "rule": "cap or downweight province/book clusters that would dominate positive dev/OOF net; audit only during matrix build",
            "output": "province_book_balance_checks.csv",
        },
        {
            "step": "fold_assignment",
            "exact_input": "source_family + province + source_top_dir grouping",
            "rule": "province-family stratified 5-fold OOF; same source_file cannot cross folds",
            "output": "source_split_manifest.csv",
        },
        {
            "step": "taxonomy_empty_tagging",
            "exact_input": "query_family/top1_family fields",
            "rule": "emit taxonomy-empty flags and separate audit slices; do not use taxonomy-empty alone as positive learning evidence",
            "output": "taxonomy_empty_slice_manifest.csv",
        },
    ]

    strong_challenger_features = [
        {
            "feature": "challenger_support_score",
            "definition": "sum of independent observable supports: family_match, book_match, action_match, material_match, connection_match, numeric_score_superior",
            "range": "0..6",
            "gate_use": ">=2 for R14_A/R14_B, >=3 for R14_C",
        },
        {
            "feature": "baseline_weak_or_conflicted",
            "definition": "top1 has explicit conflict OR weak evidence: low confidence, small current_score margin, low reason_count",
            "range": "boolean plus reason flags",
            "gate_use": "required for every candidate that can demote baseline rank1",
        },
        {
            "feature": "challenger_margin_delta",
            "definition": "reranker_pred(challenger) - reranker_pred(baseline_top1)",
            "range": "float, dev/OOF calibrated",
            "gate_use": "must exceed frozen dev/OOF q75 or candidate-specific threshold",
        },
        {
            "feature": "clean_rank1_veto",
            "definition": "baseline top1 has no conflict, confidence/margin/reason_count are not weak, and challenger support is below threshold",
            "range": "boolean",
            "gate_use": "hard veto; candidate_order falls back to baseline",
        },
        {
            "feature": "taxonomy_empty_guard",
            "definition": "query_family_empty or top1_family_empty flag",
            "range": "boolean",
            "gate_use": "audit/report; cannot be sole intervention trigger",
        },
    ]

    candidate_matrix = [
        {
            "candidate_id": "R14_A_rank1_veto_strong_challenger",
            "objective_variant": "OBJ_R14_top1_loss_guarded",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CHALLENGER",
            "gate_formula": "baseline_weak_or_conflicted AND challenger_support_score>=2 AND challenger_margin_delta>=q75",
            "rank1_veto": "clean_rank1_veto always protects baseline",
            "expected_tradeoff": "release-oriented balance of coverage and rank1 safety",
        },
        {
            "candidate_id": "R14_B_conflict_plus_challenger_margin",
            "objective_variant": "OBJ_R14_conflict_weighted",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CONFLICT",
            "gate_formula": "baseline_explicit_conflict AND challenger_support_score>=2 AND challenger_margin_delta>=q70",
            "rank1_veto": "clean non-conflict baseline cannot be demoted",
            "expected_tradeoff": "lower coverage, higher precision",
        },
        {
            "candidate_id": "R14_C_low_conf_with_challenger_veto",
            "objective_variant": "OBJ_R14_top1_demote_penalty_high",
            "feature_toggle": "FT_R14_SAFE_CORE_PLUS_CHALLENGER",
            "gate_formula": "low_conf_or_small_margin AND challenger_support_score>=3 AND challenger_margin_delta>=q80",
            "rank1_veto": "low confidence alone cannot bypass veto",
            "expected_tradeoff": "controlled successor to failed low-conf-only lane",
        },
        {
            "candidate_id": "R14_D_near_miss_proxy_no_clean_rank1",
            "objective_variant": "OBJ_R14_pairwise_near_miss_proxy",
            "feature_toggle": "FT_R14_SAFE_CORE_NO_BOOK_ID",
            "gate_formula": "small_margin AND baseline_weak_or_conflicted AND challenger_support_score>=2",
            "rank1_veto": "clean confident rank1 is protected",
            "expected_tradeoff": "tests near-miss rescue using online proxy rather than label rank",
        },
    ]

    command_contract = [
        {
            "stage": "14.2_if_explicit_go",
            "command": "python tools/goal_14x_rank1_safe_source_robust_matrix_build.py --oss-root \"D:\\广联达临时文件\\oss_samples\" --output-dir reports/agent_state/goal_14x_rank1_safe_source_robust_matrix --dev-oof-only --source-family-cap 0.22 --no-heldout",
            "allowed": "build balanced OSS dev/OOF matrix and manifests only",
            "forbidden": "training, heldout/hard, online changes, GoalSearcher edits",
        },
        {
            "stage": "14.3_if_explicit_go",
            "command": "python tools/goal_14x_rank1_safe_source_robust_dev_oof_execute.py --data-dir reports/agent_state/goal_14x_rank1_safe_source_robust_matrix --candidate-plan reports/agent_state/goal_14x_rank1_safe_source_robust_experiment_plan_definition_candidate_matrix.csv --dev-oof-only --emit-loss-audit",
            "allowed": "train/evaluate R14 candidate matrix on dev/OOF only",
            "forbidden": "heldout/hard selection, validation, release, GoalSearcher edits",
        },
        {
            "stage": "14.4_after_execution",
            "command": "python tools/goal_14x_rank1_safe_source_robust_freeze_gate_review.py",
            "allowed": "read-only scorecard/loss/source/fold review and possible freeze decision",
            "forbidden": "heldout/hard unless future explicit validation go",
        },
    ]

    required_artifacts = [
        {"artifact": "balanced_matrix_manifest", "required_at": "14.2", "fields": "group_count, row_count, source_family/province caps, fold assignment"},
        {"artifact": "feature_contract_report", "required_at": "14.2", "fields": "allowed features, forbidden leakage scan, challenger_support_score components"},
        {"artifact": "candidate_scorecard", "required_at": "14.3", "fields": "Top1 gain/loss/net, Hit5, applied/vetoed groups, approval status"},
        {"artifact": "rank1_preservation_report", "required_at": "14.3", "fields": "all baseline rank1 groups, veto status, demotions, clean_rank1_veto reason"},
        {"artifact": "strong_challenger_gate_coverage", "required_at": "14.3", "fields": "gate_reason, challenger_support_score bucket, margin threshold, gain/loss/net"},
        {"artifact": "source_fold_robustness", "required_at": "14.3", "fields": "source_family/province/fold gain/loss/net and concentration share"},
        {"artifact": "taxonomy_empty_separate_audit", "required_at": "14.3", "fields": "taxonomy-empty net, losses, whether excluded from freeze support"},
        {"artifact": "threshold_manifest", "required_at": "14.3", "fields": "challenger_margin_delta q70/q75/q80, confidence/margin weak thresholds, calibration split"},
        {"artifact": "freeze_gate_summary", "required_at": "14.4", "fields": "freeze/no-freeze decision, candidate id, loss budget checks, validation boundary"},
    ]

    stop_conditions = [
        {"condition": "source_family_share_after_rebuild_gt_0.25", "action": "stop before training; fix matrix balance"},
        {"condition": "same_source_file_crosses_oof_folds", "action": "stop before training; fix fold split"},
        {"condition": "candidate_gate_uses_baseline_rank_positive_rank_expected_id_or_label", "action": "invalidate candidate"},
        {"condition": "low_confidence_alone_can_demote_rank1", "action": "invalidate candidate"},
        {"condition": "clean_rank1_veto_missing_or_not_audited", "action": "stop execution review"},
        {"condition": "dev_oof_rank1_loss_count_gt_1", "action": "do not freeze"},
        {"condition": "source_or_province_positive_net_concentration_exceeds_budget", "action": "do not freeze without redesign"},
        {"condition": "heldout_or_hard_used_before_future_validation_go", "action": "invalidate run"},
    ]

    approval_criteria = [
        {"criterion": "matrix_balance", "required": "source_family cap <=0.22 preferred, <=0.25 hard; same source_file single fold"},
        {"criterion": "feature_safety", "required": "no answer IDs, source IDs, positive rank, labels, or heldout/hard-derived thresholds"},
        {"criterion": "rank1_safety", "required": "dev/OOF rank1 loss 0 preferred, hard stop >1"},
        {"criterion": "positive_signal", "required": "dev/OOF Top1 net >0 and not dominated by taxonomy-empty rows"},
        {"criterion": "robustness", "required": "source_family net share <=0.35, province warning above 0.50, no material negative fold"},
        {"criterion": "validation_boundary", "required": "heldout/hard only after future freeze and explicit validation go"},
    ]

    gate_checks = [
        {
            "gate": "14_0_design_ready",
            "status": "pass" if design.get("decision") == "redesign_ready_for_14_1_execution_plan_definition" else "fail",
            "evidence": design.get("decision", ""),
        },
        {
            "gate": "matrix_and_training_separated",
            "status": "pass",
            "evidence": "14.2 matrix build and 14.3 training require separate explicit go",
        },
        {
            "gate": "heldout_hard_blocked",
            "status": "pass",
            "evidence": "no 14.1/14.2/14.3 command uses heldout or hard",
        },
        {
            "gate": "rank1_veto_explicit",
            "status": "pass",
            "evidence": "all candidate rows define rank1_veto",
        },
    ]

    artifacts = {
        "summary_json": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")),
        "summary_md": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")),
        "matrix_rebuild_plan_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_matrix_rebuild_plan.csv")),
        "strong_challenger_features_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_strong_challenger_features.csv")),
        "candidate_matrix_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_candidate_matrix.csv")),
        "command_contract_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_command_contract.csv")),
        "required_artifacts_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_required_artifacts.csv")),
        "stop_conditions_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_stop_conditions.csv")),
        "approval_criteria_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_approval_criteria.csv")),
        "gate_checks_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_gate_checks.csv")),
    }

    return {
        "stage": "14.1 rank1-safe source-robust offline experiment plan definition",
        "read_only_review": True,
        "decision": "plan_ready_for_explicit_14_2_matrix_build_go_no_go",
        "matrix_rebuild_plan": matrix_rebuild_plan,
        "strong_challenger_features": strong_challenger_features,
        "candidate_matrix": candidate_matrix,
        "command_contract": command_contract,
        "required_artifacts": required_artifacts,
        "stop_conditions": stop_conditions,
        "approval_criteria": approval_criteria,
        "gate_checks": gate_checks,
        "next_stage": {
            "id": "14.2",
            "name": "rank1-safe source-robust balanced OSS matrix build authorization",
            "recommended": "14.2：只读判断是否授权重建 source/province-balanced OSS dev/OOF matrix；默认无明确 go 就 do_not_build。",
        },
        "anti_drift_conclusion": (
            "14.1 is read-only. It defines the exact future matrix rebuild, strong challenger gates, rank1 veto, dev/OOF command contract, "
            "required artifacts, stop conditions, and approval criteria. It does not rebuild matrices, train, run heldout/hard, release, edit GoalSearcher, or tune validation thresholds."
        ),
        "artifacts": artifacts,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 14.1 Rank1-Safe Source-Robust Offline Experiment Plan Definition",
        "",
        "Read-only plan for the next OSS reranker iteration after 13.28 validation failure.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Matrix Rebuild",
        "",
        md_table([["step", "rule", "output"]] + [[row["step"], row["rule"], row["output"]] for row in report["matrix_rebuild_plan"]]),
        "",
        "## Strong Challenger Features",
        "",
        md_table([["feature", "definition", "gate_use"]] + [[row["feature"], row["definition"], row["gate_use"]] for row in report["strong_challenger_features"]]),
        "",
        "## Candidate Matrix",
        "",
        md_table([["candidate_id", "gate_formula", "rank1_veto", "expected_tradeoff"]] + [[row["candidate_id"], row["gate_formula"], row["rank1_veto"], row["expected_tradeoff"]] for row in report["candidate_matrix"]]),
        "",
        "## Command Contract",
        "",
        md_table([["stage", "command", "forbidden"]] + [[row["stage"], row["command"], row["forbidden"]] for row in report["command_contract"]]),
        "",
        "## Stop Conditions",
        "",
        md_table([["condition", "action"]] + [[row["condition"], row["action"]] for row in report["stop_conditions"]]),
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


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.1 rank1-safe source-robust offline experiment plan definition 已完成。\n"
        "结论：plan_ready_for_explicit_14_2_matrix_build_go_no_go。已锁定 matrix rebuild、strong challenger feature/gate、rank1 veto、dev/OOF-only command contract、required artifacts、stop conditions。\n"
        "下一步建议：14.2 rank1-safe source-robust balanced OSS matrix build authorization。只读判断是否授权重建 balanced OSS dev/OOF matrix；默认无明确 go 就 do_not_build。\n"
        "禁止：直接训练、跑 heldout/hard、release、改 GoalSearcher、调验证阈值、使用 baseline_rank/positive_rank/expected_id/label gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>14.1 rank1-safe source-robust offline experiment plan definition</td>
            <td>Read-only plan for balanced OSS matrix rebuild, strong challenger gates, rank1 veto, dev/OOF command contract, and artifacts.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
    if "14.1 rank1-safe source-robust offline experiment plan definition" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    report = build_report()
    output_prefix = OUTPUT_PREFIX
    write_json(output_prefix.with_name(output_prefix.name + "_summary.json"), report)
    write_markdown(output_prefix.with_name(output_prefix.name + "_summary.md"), report)
    write_csv(output_prefix.with_name(output_prefix.name + "_matrix_rebuild_plan.csv"), report["matrix_rebuild_plan"], ["step", "exact_input", "rule", "output"])
    write_csv(output_prefix.with_name(output_prefix.name + "_strong_challenger_features.csv"), report["strong_challenger_features"], ["feature", "definition", "range", "gate_use"])
    write_csv(output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv"), report["candidate_matrix"], ["candidate_id", "objective_variant", "feature_toggle", "gate_formula", "rank1_veto", "expected_tradeoff"])
    write_csv(output_prefix.with_name(output_prefix.name + "_command_contract.csv"), report["command_contract"], ["stage", "command", "allowed", "forbidden"])
    write_csv(output_prefix.with_name(output_prefix.name + "_required_artifacts.csv"), report["required_artifacts"], ["artifact", "required_at", "fields"])
    write_csv(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv"), report["stop_conditions"], ["condition", "action"])
    write_csv(output_prefix.with_name(output_prefix.name + "_approval_criteria.csv"), report["approval_criteria"], ["criterion", "required"])
    write_csv(output_prefix.with_name(output_prefix.name + "_gate_checks.csv"), report["gate_checks"], ["gate", "status", "evidence"])
    update_dashboard(DASHBOARD, report)
    print(json.dumps({"decision": report["decision"], "summary": report["artifacts"]["summary_json"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
