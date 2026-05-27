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

INPUT_DECISION_JSON = AGENT_STATE / "goal_13x_validation_failed_strategy_decision_summary.json"
FEATURE_WHITELIST_JSON = (
    AGENT_STATE
    / "goal_13x_oss_xml_source_aware_training_matrix_expanded"
    / "ltr_feature_whitelist_oss_source_aware_v1.json"
)
DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_rewrite_plan"

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


def safe_rel(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT))


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
    decision = read_json(INPUT_DECISION_JSON)
    whitelist = read_json(FEATURE_WHITELIST_JSON)
    training_features = set(whitelist.get("training_features", []))

    field_manifest = [
        {
            "field": "confidence",
            "role": "top1 low-confidence gate",
            "observable_online": True,
            "source": "baseline scorer output / LTR matrix feature",
            "normalization_requirement": "verify scale on dev/OOF; current matrix examples use 0-100 scale, so do not reuse 0.55 blindly",
            "allowed_use": "compute dev/OOF-only quantile threshold, then freeze before validation",
        },
        {
            "field": "current_score",
            "role": "top1-top2 margin gate",
            "observable_online": True,
            "source": "baseline scorer output / LTR matrix feature",
            "normalization_requirement": "margin = top1.current_score - top2.current_score within each candidate group",
            "allowed_use": "small-margin uncertainty gate only",
        },
        {
            "field": "reason_count",
            "role": "weak evidence separation support",
            "observable_online": True,
            "source": "baseline reason generation",
            "normalization_requirement": "use only as support or slice audit; do not make it the sole intervention gate in first pass",
            "allowed_use": "audit whether low confidence/margin is caused by sparse reasons",
        },
        {
            "field": "family_conflict/book_conflict/unit_conflict/domain_conflict_count",
            "role": "observable conflict support",
            "observable_online": True,
            "source": "existing conflict features",
            "normalization_requirement": "boolean/count features already present in feature whitelist",
            "allowed_use": "optional precision guard, not required for low-confidence/margin variants",
        },
        {
            "field": "family_match/book_match/numeric_score",
            "role": "challenger quality check",
            "observable_online": True,
            "source": "candidate feature rows",
            "normalization_requirement": "compare challenger rows against baseline top1 in the same group",
            "allowed_use": "avoid applying reranker when no challenger has plausible support",
        },
    ]

    forbidden_fields = [
        {
            "field": "baseline_rank != 1 / positive_rank / baseline_positive_rank",
            "reason": "depends on known answer position in the evaluation group",
            "decision": "remove from deployable T1G_A rewrite",
        },
        {
            "field": "expected_id / expected_ids / positive_id / label",
            "reason": "answer-derived leakage",
            "decision": "forbidden for gate and features",
        },
        {
            "field": "heldout/hard validation result",
            "reason": "validation cannot be used for candidate or threshold selection",
            "decision": "dev/OOF-only calibration before any freeze",
        },
    ]

    candidate_matrix = [
        {
            "candidate_id": "T1G_A1_low_conf_q25",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "gate_formula": "top1_confidence <= dev_oof_q25(top1_confidence)",
            "threshold_policy": "compute q25 on dev/OOF only and write threshold manifest",
            "expected_tradeoff": "highest precision low-confidence intervention, lower coverage",
            "must_remove": "baseline_rank != 1",
        },
        {
            "candidate_id": "T1G_A2_small_margin_q25",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "gate_formula": "top1_score_margin <= dev_oof_q25(top1_score_margin)",
            "threshold_policy": "compute q25 on dev/OOF only and write threshold manifest",
            "expected_tradeoff": "targets ambiguous top1/top2 ranking, may miss low-confidence wide-margin errors",
            "must_remove": "baseline_rank != 1",
        },
        {
            "candidate_id": "T1G_A3_low_conf_or_small_margin_q25",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "gate_formula": "low_conf_q25 OR small_margin_q25",
            "threshold_policy": "freeze both q25 thresholds from dev/OOF before scoring",
            "expected_tradeoff": "closest deployable replacement for old mixed T1G_A, broader coverage",
            "must_remove": "baseline_rank != 1",
        },
        {
            "candidate_id": "T1G_A4_low_conf_or_margin_plus_challenger",
            "objective_variant": "OBJ_E_top1_demote_penalty",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "gate_formula": "(low_conf_q25 OR small_margin_q25) AND challenger_has_family_or_book_or_numeric_support",
            "threshold_policy": "same frozen q25 thresholds; challenger support uses observable candidate rows",
            "expected_tradeoff": "stricter release-oriented gate, likely fewer rank1 losses",
            "must_remove": "baseline_rank != 1",
        },
        {
            "candidate_id": "T1G_A5_low_conf_margin_conflict_precision",
            "objective_variant": "OBJ_F_conflict_only_top1_guard",
            "feature_toggle": "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES",
            "gate_formula": "(low_conf_q35 OR small_margin_q35) AND top1_has_observable_conflict AND challenger_has_support",
            "threshold_policy": "dev/OOF q35 thresholds allowed only if q25 coverage is too thin; report both",
            "expected_tradeoff": "more coverage than T1G_B conflict-only while still requiring uncertainty",
            "must_remove": "baseline_rank != 1",
        },
    ]

    loss_budget = [
        {"metric": "dev_oof_hit1_net", "gate": "> 0", "reason": "must improve Top1 before freeze consideration"},
        {"metric": "rank1_loss_count", "gate": "<= max(3, hit1_gain * 0.08)", "reason": "Top1 protection is the core purpose"},
        {"metric": "baseline_rank1_demotion_rate", "gate": "<= 0.005 preferred, hard stop > 0.01", "reason": "avoid repeating hard validation rank1 loss pattern"},
        {"metric": "source_family_net_concentration", "gate": "largest source_family share of net <= 0.35", "reason": "avoid single OSS source domination"},
        {"metric": "fold_net", "gate": "no negative fold with material losses", "reason": "must be robust across source-aware OOF folds"},
        {"metric": "heldout_hard_used_for_selection", "gate": "False", "reason": "validation remains final A/B only"},
    ]

    command_contract = [
        {
            "future_stage": "13.24",
            "command": "authorization only; no execution",
            "allowed": "decide whether to run dev/OOF-only rewritten T1G_A variants",
            "forbidden": "heldout/hard, release, GoalSearcher edit, threshold tuning from validation",
        },
        {
            "future_stage": "13.25_if_go",
            "command": "python tools/goal_13x_observable_low_conf_margin_dev_oof_execute.py --plan reports/agent_state/goal_13x_observable_low_conf_margin_rewrite_plan_candidate_matrix.csv",
            "allowed": "dev/OOF-only execution and loss audit for the candidate matrix above",
            "forbidden": "heldout/hard validation, online integration, new label-derived gates",
        },
    ]

    required_artifacts = [
        {"artifact": "candidate_scorecard", "required_fields": "gain/loss/net, applied groups, rank1 losses, demotion rate"},
        {"artifact": "threshold_manifest", "required_fields": "confidence scale, q25/q35 values, margin q25/q35 values, calibration split"},
        {"artifact": "gating_coverage", "required_fields": "gate_reason, applied/vetoed, gain/loss/net"},
        {"artifact": "rank1_preservation", "required_fields": "all baseline rank1 groups, demotions, veto status"},
        {"artifact": "source_fold_robustness", "required_fields": "source_family/province/fold net and loss concentration"},
        {"artifact": "leakage_check", "required_fields": "forbidden feature/gate scan, heldout/hard unused flag"},
    ]

    gate_checks = [
        {
            "gate": "13.22_input_available",
            "status": "pass" if decision.get("decision") else "fail",
            "evidence": decision.get("decision", ""),
        },
        {
            "gate": "observable_fields_available",
            "status": "pass"
            if {"confidence", "current_score", "reason_count", "family_conflict", "book_conflict", "numeric_score"}.issubset(training_features)
            else "fail",
            "evidence": "confidence/current_score/reason_count/conflict/challenger support fields checked in whitelist",
        },
        {
            "gate": "label_branch_removed_in_plan",
            "status": "pass",
            "evidence": "all candidate rows list baseline_rank != 1 as must_remove",
        },
        {
            "gate": "validation_boundary_preserved",
            "status": "pass",
            "evidence": "next step is explicit dev/OOF execution authorization; heldout/hard remains forbidden",
        },
    ]

    artifacts = {
        "summary_json": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")),
        "summary_md": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")),
        "candidate_matrix_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_candidate_matrix.csv")),
        "field_manifest_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_field_manifest.csv")),
        "forbidden_fields_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_forbidden_fields.csv")),
        "loss_budget_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_loss_budget.csv")),
        "command_contract_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_command_contract.csv")),
        "required_artifacts_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_required_artifacts.csv")),
        "gate_checks_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_gate_checks.csv")),
    }

    return {
        "stage": "13.23 observable low-confidence/margin gate rewrite plan",
        "read_only_review": True,
        "decision": "plan_ready_for_explicit_dev_oof_execution_go_no_go",
        "input_decision": decision.get("decision"),
        "source_signal": decision.get("dev_oof_rewrite_signal", {}),
        "field_manifest": field_manifest,
        "forbidden_fields": forbidden_fields,
        "candidate_matrix": candidate_matrix,
        "loss_budget": loss_budget,
        "command_contract": command_contract,
        "required_artifacts": required_artifacts,
        "gate_checks": gate_checks,
        "next_stage": {
            "id": "13.24",
            "name": "observable low-confidence/margin dev/OOF execution authorization gate",
            "recommended": (
                "13.24：只读判断是否授权 dev/OOF-only 执行 T1G_A observable low-confidence/margin rewrite variants；"
                "默认无明确 go 就 do_not_execute。"
            ),
        },
        "anti_drift_conclusion": (
            "13.23 is read-only. It defines deployable T1G_A rewrite variants, field boundaries, loss budget, "
            "required artifacts, and future command contract. It does not execute training, tune thresholds, use "
            "heldout/hard, release, edit GoalSearcher, or keep any label-derived baseline_rank branch."
        ),
        "artifacts": artifacts,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    signal = report["source_signal"]
    lines = [
        "# 13.23 Observable Low-Confidence/Margin Gate Rewrite Plan",
        "",
        "Read-only plan for rewriting T1G_A into deployable gates that use only online-observable uncertainty signals.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Starting Signal",
        "",
        md_table(
            [
                ["candidate", "dev/OOF gain/loss/net", "rank1 loss", "status", "rewrite reason"],
                [
                    signal.get("candidate_id", "T1G_A_low_conf_margin_guard"),
                    f"{signal.get('hit1_gain')}/{signal.get('hit1_loss')}/{signal.get('hit1_net')}",
                    signal.get("rank1_loss_count"),
                    signal.get("deployability"),
                    signal.get("rewrite_reason"),
                ],
            ]
        ),
        "",
        "## Candidate Matrix",
        "",
        md_table(
            [["candidate_id", "gate_formula", "threshold_policy", "expected_tradeoff"]]
            + [
                [row["candidate_id"], row["gate_formula"], row["threshold_policy"], row["expected_tradeoff"]]
                for row in report["candidate_matrix"]
            ]
        ),
        "",
        "## Observable Fields",
        "",
        md_table(
            [["field", "role", "normalization_requirement"]]
            + [[row["field"], row["role"], row["normalization_requirement"]] for row in report["field_manifest"]]
        ),
        "",
        "## Loss Budget",
        "",
        md_table(
            [["metric", "gate", "reason"]]
            + [[row["metric"], row["gate"], row["reason"]] for row in report["loss_budget"]]
        ),
        "",
        "## Gate Checks",
        "",
        md_table(
            [["gate", "status", "evidence"]]
            + [[row["gate"], row["status"], row["evidence"]] for row in report["gate_checks"]]
        ),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.23 observable low-confidence/margin gate rewrite plan 已完成。\n"
        "结论：plan_ready_for_explicit_dev_oof_execution_go_no_go。T1G_A 已改写成只用线上可观测信号的候选计划：confidence 分位数、top1/top2 current_score margin、可选 challenger support；明确删除 label-derived baseline_rank != 1 分支。\n"
        "下一步建议：13.24 observable low-confidence/margin dev/OOF execution authorization gate。只读判断是否授权执行这些 T1G_A rewrite variants；默认无明确 go 就 do_not_execute。\n"
        "禁止：执行训练、用 heldout/hard、release、改 GoalSearcher、继续使用 baseline_rank/positive_rank/expected_id/label-derived gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.23 observable low-confidence/margin gate rewrite plan</td>
            <td>Read-only deployable T1G_A rewrite plan with observable gate fields, candidate matrix, loss budget, artifacts, and command contract.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
    if "13.23 observable low-confidence/margin gate rewrite plan" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    report = build_report()
    summary_json = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")
    summary_md = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")
    write_json(summary_json, report)
    write_markdown(summary_md, report)
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_candidate_matrix.csv"),
        report["candidate_matrix"],
        ["candidate_id", "objective_variant", "feature_toggle", "gate_formula", "threshold_policy", "expected_tradeoff", "must_remove"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_field_manifest.csv"),
        report["field_manifest"],
        ["field", "role", "observable_online", "source", "normalization_requirement", "allowed_use"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_forbidden_fields.csv"),
        report["forbidden_fields"],
        ["field", "reason", "decision"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_loss_budget.csv"),
        report["loss_budget"],
        ["metric", "gate", "reason"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_command_contract.csv"),
        report["command_contract"],
        ["future_stage", "command", "allowed", "forbidden"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_required_artifacts.csv"),
        report["required_artifacts"],
        ["artifact", "required_fields"],
    )
    write_csv(
        OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_gate_checks.csv"),
        report["gate_checks"],
        ["gate", "status", "evidence"],
    )
    update_dashboard(DASHBOARD, report)
    print(json.dumps({"decision": report["decision"], "summary": safe_rel(summary_json)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
