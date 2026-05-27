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

VALIDATION_REVIEW = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_package_review_summary.json"
FREEZE_REVIEW = AGENT_STATE / "goal_13x_observable_low_conf_margin_freeze_gate_review_summary.json"
SOURCE_BALANCE = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "source_balance_checks.csv"
DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_redesign_definition"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
    validation = read_json(VALIDATION_REVIEW)
    freeze = read_json(FREEZE_REVIEW)
    source_balance = read_csv(SOURCE_BALANCE)
    heldout = validation["heldout_metrics"]
    hard = validation["hard_metrics"]
    frozen = freeze["frozen_candidate"]

    failure_rows = [
        {
            "finding": "low_conf_not_sufficient",
            "evidence": f"heldout low_conf gate {heldout['hit1_gain']}/{heldout['hit1_loss']}/{heldout['hit1_net']}; hard {hard['hit1_gain']}/{hard['hit1_loss']}/{hard['hit1_net']}",
            "implication": "low confidence is an uncertainty signal, not a safe Top1 override trigger",
            "design_response": "require strong challenger evidence plus rank1 protection veto before reranking",
        },
        {
            "finding": "rank1_loss_reappeared_on_validation",
            "evidence": f"dev/OOF frozen candidate rank1_loss={frozen['rank1_loss_count']}; validation heldout/hard rank1_loss={heldout['rank1_loss_count']}/{hard['rank1_loss_count']}",
            "implication": "zero-loss dev/OOF is not enough when validation distribution shifts",
            "design_response": "make rank1 preservation a hard objective and validation acceptance gate",
        },
        {
            "finding": "source_province_concentration",
            "evidence": f"freeze province top share={frozen['province_top_share']}; source_family top share={frozen['source_family_top_share']}; fold top share={frozen['fold_top_share']}",
            "implication": "OSS signal is useful but needs stronger source/province balancing",
            "design_response": "rebuild training matrix with per-province/source caps and province-family stratified OOF",
        },
        {
            "finding": "validation_source_mismatch",
            "evidence": "heldout/hard validation source_file is global_repair_decision_table.csv while training signal is OSS XML",
            "implication": "OSS should remain the main training source, but validation robustness must be checked through source-invariant features",
            "design_response": "exclude source IDs from features and audit gains by source/province/fold before freeze",
        },
    ]

    source_rebalance_rows = [
        {
            "policy": "source_family_cap",
            "current_evidence": next((row.get("value") for row in source_balance if row.get("check") == "max_source_family_group_share"), "unknown"),
            "target": "<=0.22 preferred, hard stop >0.25",
            "implementation_boundary": "future matrix rebuild only; no heldout/hard use",
        },
        {
            "policy": "province_bucket_cap",
            "current_evidence": "13.26 frozen candidate province share 0.606557 on Zhejiang municipal",
            "target": "no single province/book family contributes >0.35 positive net in dev/OOF",
            "implementation_boundary": "cap or reweight OSS groups by province/book/source_family",
        },
        {
            "policy": "province_family_stratified_oof",
            "current_evidence": "all major source families have 5 folds, but positive net still fold-concentrated",
            "target": "fold positive net share <=0.30 preferred, <=0.35 hard",
            "implementation_boundary": "fold assignment must be frozen before training",
        },
        {
            "policy": "taxonomy_empty_separate_lane",
            "current_evidence": "validation losses include query_family/top1_family <empty>",
            "target": "taxonomy-empty rows are audited separately and not used as sole reranker intervention evidence",
            "implementation_boundary": "no parser/taxonomy rule changes in 14.0",
        },
    ]

    candidate_matrix = [
        {
            "candidate_id": "R14_A_rank1_veto_strong_challenger",
            "objective": "top1_loss_guarded_lambdarank",
            "gate": "apply only if baseline weak/conflicted AND challenger_support_score >= 2 AND challenger_margin_delta >= dev_oof_q75",
            "rank1_protection": "never demote baseline rank1 unless baseline has explicit conflict or weak-evidence flag and challenger has multi-signal support",
            "purpose": "main release-oriented candidate",
        },
        {
            "candidate_id": "R14_B_conflict_plus_challenger_margin",
            "objective": "conflict_weighted_top1_guard",
            "gate": "top1 has family/book/unit/domain conflict AND challenger has family/book/action/material support AND reranker margin clears threshold",
            "rank1_protection": "protect clean baseline rank1; allow only conflict-backed replacement",
            "purpose": "precision candidate for wrong-domain/wrong-family top1",
        },
        {
            "candidate_id": "R14_C_low_conf_with_challenger_veto",
            "objective": "top1_demote_penalty_high",
            "gate": "low confidence is allowed only as supporting evidence, never alone; require challenger_support_score >= 3",
            "rank1_protection": "low confidence alone cannot demote rank1",
            "purpose": "controlled successor to failed 13.28 low-confidence lane",
        },
        {
            "candidate_id": "R14_D_near_miss_rescue_no_clean_rank1",
            "objective": "pairwise_near_miss_promotion_with_online_proxy",
            "gate": "online proxy: small current_score margin + challenger multi-signal support + baseline conflict/weak evidence",
            "rank1_protection": "clean confident rank1 is vetoed",
            "purpose": "approximate diagnostic near-miss gains without label-derived rank",
        },
    ]

    feature_contract = [
        {"feature_family": "baseline_uncertainty", "allowed_fields": "confidence, current_score margin, reason_count", "restriction": "cannot alone trigger Top1 demotion"},
        {"feature_family": "baseline_conflict", "allowed_fields": "family_conflict, book_conflict, unit_conflict, domain_conflict_count", "restriction": "must be observable from current candidate rows"},
        {"feature_family": "challenger_support", "allowed_fields": "family_match, book_match, action_match, material_match, connection_match, numeric_score superiority", "restriction": "must require at least two independent support signals"},
        {"feature_family": "reranker_margin", "allowed_fields": "candidate_pred_score - baseline_pred_score from dev/OOF model", "restriction": "threshold calibrated on dev/OOF only, frozen before validation"},
        {"feature_family": "forbidden", "allowed_fields": "baseline_rank, positive_rank, expected_id, label, source_file/source_family as model features", "restriction": "forbidden for gate and training features"},
    ]

    loss_budget = [
        {"metric": "dev_oof_hit1_net", "gate": "> 0", "why": "must improve before freeze"},
        {"metric": "dev_oof_rank1_loss_count", "gate": "0 preferred; hard stop >1", "why": "rank1 safety is the design objective"},
        {"metric": "heldout_hit1_net", "gate": "> 0 for release gate", "why": "must pass independent validation"},
        {"metric": "hard_hit1_net", "gate": ">= 0 and rank1_loss <= 1", "why": "hard split cannot regress"},
        {"metric": "source_family_positive_net_share", "gate": "<=0.30 preferred, <=0.35 hard", "why": "avoid source artifact"},
        {"metric": "province_positive_net_share", "gate": "<=0.35 preferred, explicit warning above 0.50", "why": "avoid province-specific overfit"},
        {"metric": "taxonomy_empty_net", "gate": "reported separately; cannot drive freeze alone", "why": "taxonomy/DQ rows are not clean ranking evidence"},
    ]

    command_contract = [
        {
            "future_stage": "14.1",
            "command": "read-only execution plan definition",
            "allowed": "define exact matrix rebuild, feature computation, candidate gates, and artifact schema",
            "forbidden": "training, heldout/hard validation, GoalSearcher edit, threshold tuning",
        },
        {
            "future_stage": "14.2_if_go",
            "command": "build source/province-balanced OSS dev/OOF matrix",
            "allowed": "dev/OOF matrix rebuild from OSS XML only with source/province caps",
            "forbidden": "heldout/hard, online changes, owner mapping edits",
        },
        {
            "future_stage": "14.3_if_go",
            "command": "run rank1-safe source-robust dev/OOF reranker experiment",
            "allowed": "train/evaluate candidate matrix on dev/OOF only",
            "forbidden": "heldout/hard selection, release, GoalSearcher edit",
        },
    ]

    gate_checks = [
        {"gate": "prior_validation_failed", "status": "pass" if validation.get("decision") == "do_not_release_low_conf_validation_not_positive" else "fail", "evidence": validation.get("decision", "")},
        {"gate": "solution_changes_failure_mode", "status": "pass", "evidence": "new design adds strong challenger evidence and rank1 veto instead of retuning low_conf q25"},
        {"gate": "oss_kept_as_training_source", "status": "pass", "evidence": "source/province-balanced OSS remains the matrix source"},
        {"gate": "heldout_hard_boundary_preserved", "status": "pass", "evidence": "heldout/hard reserved for one final validation after future freeze"},
    ]

    artifacts = {
        "summary_json": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")),
        "summary_md": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")),
        "failure_analysis_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_failure_analysis.csv")),
        "source_rebalance_policy_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_source_rebalance_policy.csv")),
        "candidate_matrix_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_candidate_matrix.csv")),
        "feature_contract_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_feature_contract.csv")),
        "loss_budget_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_loss_budget.csv")),
        "command_contract_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_command_contract.csv")),
        "gate_checks_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_gate_checks.csv")),
    }

    return {
        "stage": "14.0 rank1-safe / source-robust reranker redesign definition",
        "read_only_review": True,
        "decision": "redesign_ready_for_14_1_execution_plan_definition",
        "prior_validation_decision": validation.get("decision"),
        "failure_rows": failure_rows,
        "source_rebalance_rows": source_rebalance_rows,
        "candidate_matrix": candidate_matrix,
        "feature_contract": feature_contract,
        "loss_budget": loss_budget,
        "command_contract": command_contract,
        "gate_checks": gate_checks,
        "next_stage": {
            "id": "14.1",
            "name": "rank1-safe source-robust offline experiment plan definition",
            "recommended": "14.1：只读定义具体 matrix rebuild、strong challenger feature/gate、rank1 veto、dev/OOF-only command contract 和 required artifacts；仍不训练、不跑 heldout/hard。",
        },
        "anti_drift_conclusion": (
            "14.0 is read-only. It stops the failed low-confidence release path and defines a new rank1-safe/source-robust design. "
            "It does not train, rebuild matrices, run heldout/hard, tune validation thresholds, release, edit GoalSearcher, or use label-derived gates."
        ),
        "artifacts": artifacts,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 14.0 Rank1-Safe / Source-Robust Reranker Redesign Definition",
        "",
        "Read-only redesign after the frozen low-confidence candidate failed heldout/hard validation.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Failure Analysis",
        "",
        md_table([["finding", "evidence", "design_response"]] + [[row["finding"], row["evidence"], row["design_response"]] for row in report["failure_rows"]]),
        "",
        "## Candidate Matrix",
        "",
        md_table([["candidate_id", "gate", "rank1_protection", "purpose"]] + [[row["candidate_id"], row["gate"], row["rank1_protection"], row["purpose"]] for row in report["candidate_matrix"]]),
        "",
        "## Source Rebalance",
        "",
        md_table([["policy", "target", "implementation_boundary"]] + [[row["policy"], row["target"], row["implementation_boundary"]] for row in report["source_rebalance_rows"]]),
        "",
        "## Loss Budget",
        "",
        md_table([["metric", "gate", "why"]] + [[row["metric"], row["gate"], row["why"]] for row in report["loss_budget"]]),
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
        "当前阶段：14.0 rank1-safe / source-robust reranker redesign definition 已完成。\n"
        "结论：redesign_ready_for_14_1_execution_plan_definition。13.28 low-confidence validation 已失败，不能 release；14.x 改为 OSS source/province-balanced、强 challenger 证据、rank1 protection veto 的 reranker 设计。\n"
        "下一步建议：14.1 rank1-safe source-robust offline experiment plan definition。只读定义具体 matrix rebuild、feature/gate、loss budget、command contract；仍不训练、不跑 heldout/hard。\n"
        "禁止：调 heldout/hard 阈值、重选 13.x A3/A4、release、改 GoalSearcher、用 label-derived baseline_rank/positive_rank/expected_id gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>14.0 rank1-safe / source-robust reranker redesign definition</td>
            <td>Read-only redesign after 13.28 validation failure: source/province-balanced OSS, strong challenger evidence, rank1 protection veto.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
    if "14.0 rank1-safe / source-robust reranker redesign definition" not in text:
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
    write_csv(output_prefix.with_name(output_prefix.name + "_failure_analysis.csv"), report["failure_rows"], ["finding", "evidence", "implication", "design_response"])
    write_csv(output_prefix.with_name(output_prefix.name + "_source_rebalance_policy.csv"), report["source_rebalance_rows"], ["policy", "current_evidence", "target", "implementation_boundary"])
    write_csv(output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv"), report["candidate_matrix"], ["candidate_id", "objective", "gate", "rank1_protection", "purpose"])
    write_csv(output_prefix.with_name(output_prefix.name + "_feature_contract.csv"), report["feature_contract"], ["feature_family", "allowed_fields", "restriction"])
    write_csv(output_prefix.with_name(output_prefix.name + "_loss_budget.csv"), report["loss_budget"], ["metric", "gate", "why"])
    write_csv(output_prefix.with_name(output_prefix.name + "_command_contract.csv"), report["command_contract"], ["future_stage", "command", "allowed", "forbidden"])
    write_csv(output_prefix.with_name(output_prefix.name + "_gate_checks.csv"), report["gate_checks"], ["gate", "status", "evidence"])
    update_dashboard(DASHBOARD, report)
    print(json.dumps({"decision": report["decision"], "summary": report["artifacts"]["summary_json"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
