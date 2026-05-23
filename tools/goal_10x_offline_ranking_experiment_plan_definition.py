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
DEFAULT_STAGE_10_2 = AGENT_STATE / "goal_10x_ranking_objective_feature_evidence_review_summary.json"
DEFAULT_STAGE_10_3 = AGENT_STATE / "goal_10x_ranking_feature_objective_design_gate_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_offline_ranking_experiment_plan_definition"


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
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _objective_variants(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_2.get("metrics", {})
    raw_loss = metrics.get("raw_oof_loss", 29)
    selected_loss = metrics.get("selected_gate_loss", 18)
    return [
        {
            "variant_id": "OBJ_A_current_lambda_rank_baseline",
            "objective_family": "top1_net_gain_with_loss_budget",
            "priority": "P0",
            "definition": "Current query-anchored LambdaRank objective as the frozen comparator.",
            "selection_metric": "OOF hit1 net gain vs baseline, with loss slices reported but no promotion decision in 10.4.",
            "loss_budget": "comparator_only",
            "requires_training_later": "no_for_10_4",
        },
        {
            "variant_id": "OBJ_B_loss_budgeted_top1_net",
            "objective_family": "top1_net_gain_with_loss_budget",
            "priority": "P0",
            "definition": "Future candidate objective may optimize Top1 net gain only if new-loss ceiling is explicit before the run.",
            "selection_metric": "OOF net gain, new_loss <= selected_gate_loss, and retained raw gain reported.",
            "loss_budget": f"new_loss_ceiling <= {selected_loss}; raw_loss_reference={raw_loss}",
            "requires_training_later": "yes_after_explicit_future_stage",
        },
        {
            "variant_id": "OBJ_C_recall_separated_top80_present",
            "objective_family": "recall_vs_ranking_separation",
            "priority": "P0",
            "definition": "Evaluate ranking only on Top80-present rows; top80_missing stays outside ranking-objective claims.",
            "selection_metric": "Top80-present hit1 gain/loss; top80_missing reported as unchanged recall boundary.",
            "loss_budget": "ranking_claims_must_exclude_top80_missing",
            "requires_training_later": "yes_after_explicit_future_stage",
        },
        {
            "variant_id": "OBJ_D_fallback_preserving_override",
            "objective_family": "fallback_and_safety_interaction",
            "priority": "P1",
            "definition": "Future candidate must preserve baseline fallback and show whether blocked raw gain is recovered without gate relaxation.",
            "selection_metric": "OOF allowed gain/loss plus blocked-gain recovery and prevented-loss retention.",
            "loss_budget": "prevented_loss_retention_required; no_gate_relaxation",
            "requires_training_later": "yes_after_explicit_future_stage",
        },
    ]


def _feature_toggles(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    feature_rows = stage_10_2.get("feature_families", [])
    rows = [
        {
            "toggle_id": "FT_ALL_CURRENT_WHITELIST",
            "feature_family": "all_reviewed_families",
            "mode": "include_all_current_whitelist",
            "feature_count": stage_10_2.get("metrics", {}).get("training_feature_count", 69),
            "purpose": "Frozen current-feature comparator for later offline experiments.",
            "not_allowed": "no whitelist edit in 10.4",
        },
    ]
    for feature in feature_rows:
        family = feature["feature_family"]
        rows.append(
            {
                "toggle_id": f"FT_EXCLUDE_{family.upper()}",
                "feature_family": family,
                "mode": "ablation_exclude_one_family",
                "feature_count": feature.get("present_feature_count", 0),
                "purpose": f"Future ablation to quantify whether {family} contributes gain or loss.",
                "not_allowed": "define only; no feature deletion or training in 10.4",
            }
        )
    rows.append(
        {
            "toggle_id": "FT_SAFE_CORE_ONLY",
            "feature_family": "base_retrieval_score + book_and_chapter_alignment + conflict_reason_flags",
            "mode": "safe_core_subset_candidate",
            "feature_count": "defined_by_current_whitelist_only",
            "purpose": "Future conservative comparator that keeps broad retrieval and conflict signals while isolating taxonomy/numeric risk.",
            "not_allowed": "no new feature engineering in 10.4",
        }
    )
    return rows


def _split_policy() -> list[dict[str, Any]]:
    return [
        {
            "policy_item": "selection_source",
            "definition": "Use dev/OOF only for candidate scoring, objective comparison, and loss-budget checks.",
            "allowed": "dev and OOF summaries",
            "forbidden": "heldout or hard threshold/strategy selection",
        },
        {
            "policy_item": "heldout_hard_use",
            "definition": "Heldout/hard may be opened only after candidate, objective, features, gate policy, and loss budget are frozen.",
            "allowed": "validation-only after freeze",
            "forbidden": "iterative tuning, threshold picking, or candidate selection",
        },
        {
            "policy_item": "recall_boundary",
            "definition": "Separate Top80-present ranking rows from top80_missing recall rows in every metric table.",
            "allowed": "ranking claims on Top80-present rows",
            "forbidden": "claiming ranking changes fix recall-missing rows",
        },
        {
            "policy_item": "source_boundary",
            "definition": "Report generated repair source separately from independent traces before any improvement claim.",
            "allowed": "diagnostic grouping by source_file/province",
            "forbidden": "single-source dominated promotion",
        },
    ]


def _leakage_gates(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": f"LG_{row['forbidden_key']}",
            "forbidden_key": row["forbidden_key"],
            "current_status": "pass" if not row.get("in_training_features") else "fail",
            "required_check_before_run": "must remain absent from training features; diagnostics only",
            "failure_action": "block experiment execution",
        }
        for row in stage_10_2.get("leakage_checks", [])
    ]


def _fallback_contract(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_2.get("metrics", {})
    return [
        {
            "contract_item": "baseline_first_safety",
            "definition": "Baseline remains the fallback whenever a future ranking candidate is not explicitly allowed by the frozen gate.",
            "oof_reference": f"prevented_raw_hit1_loss={metrics.get('prevented_raw_hit1_loss', 11)}",
            "must_report": "baseline_hit1_saved_loss and candidate_new_loss",
        },
        {
            "contract_item": "raw_ltr_upside_accounting",
            "definition": "Raw LTR upside remains a comparator, but cannot be promoted without loss budget and slices.",
            "oof_reference": f"raw_oof_net={metrics.get('raw_oof_net', 89)}; raw_oof_loss={metrics.get('raw_oof_loss', 29)}",
            "must_report": "raw gain/loss by query_family, top1_family, book/rank, source, province",
        },
        {
            "contract_item": "selected_gate_comparator",
            "definition": "Current selected safety gate remains the conservative comparator for future candidates.",
            "oof_reference": f"selected_gate_net={metrics.get('selected_gate_net', 48)}; selected_gate_loss={metrics.get('selected_gate_loss', 18)}",
            "must_report": "delta vs selected gate and retained prevented-loss",
        },
        {
            "contract_item": "blocked_gain_recovery_without_relaxation",
            "definition": "Blocked raw gain may be studied through objective/features, not through gate relaxation in the plan.",
            "oof_reference": f"blocked_raw_hit1_gain={metrics.get('blocked_raw_hit1_gain', 52)}",
            "must_report": "blocked gain recovered and newly introduced loss",
        },
    ]


def _loss_budget(stage_10_2: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_2.get("metrics", {})
    selected_loss = metrics.get("selected_gate_loss", 18)
    raw_net = metrics.get("raw_oof_net", 89)
    selected_net = metrics.get("selected_gate_net", 48)
    blocked_gain = metrics.get("blocked_raw_hit1_gain", 52)
    prevented_loss = metrics.get("prevented_raw_hit1_loss", 11)
    return [
        {
            "budget_item": "new_loss_ceiling",
            "required_threshold": f"candidate_new_loss <= selected_gate_loss_reference ({selected_loss})",
            "rationale": "No future candidate can be promoted on net gain while increasing unsafe baseline overrides beyond the current selected gate reference.",
            "blocking_condition": "candidate_new_loss_missing_or_above_ceiling",
        },
        {
            "budget_item": "retained_net_gain_target",
            "required_threshold": f"candidate_net_gain > selected_gate_net_reference ({selected_net}); raw_net_reference={raw_net}",
            "rationale": "A future candidate should beat the conservative selected gate before heldout validation is even considered.",
            "blocking_condition": "net_gain_not_better_than_selected_gate",
        },
        {
            "budget_item": "blocked_gain_recovery_target",
            "required_threshold": f"report recovery from blocked_raw_hit1_gain_reference ({blocked_gain}); no minimum promotion threshold set in 10.4",
            "rationale": "10.4 defines the measurement, not the final tuning target.",
            "blocking_condition": "blocked_gain_recovery_not_reported",
        },
        {
            "budget_item": "saved_loss_retention",
            "required_threshold": f"report retention of prevented_raw_hit1_loss_reference ({prevented_loss}); no gate relaxation",
            "rationale": "Safety improvements are not acceptable if they silently discard the current fallback protection.",
            "blocking_condition": "prevented_loss_retention_not_reported",
        },
    ]


def _required_outputs() -> list[dict[str, Any]]:
    return [
        {
            "output_id": "candidate_scorecard",
            "required_content": "objective variant, feature toggle, comparator, OOF hit1 gain/loss, net gain, new loss, blocked-gain recovery, saved-loss retention",
            "format": "csv + summary json",
            "promotion_dependency": "required before any heldout/hard validation",
        },
        {
            "output_id": "loss_audit_by_slice",
            "required_content": "gain/loss by query_family, top1_family, book/rank_bucket, source_file, and province",
            "format": "csv tables per slice",
            "promotion_dependency": "must show no single-source or single-family artifact",
        },
        {
            "output_id": "leakage_gate_report",
            "required_content": "feature whitelist diff, forbidden identifier scan, diagnostics-only field check",
            "format": "json + csv",
            "promotion_dependency": "any failure blocks experiment execution",
        },
        {
            "output_id": "fallback_contract_report",
            "required_content": "baseline fallback, raw LTR, selected safety gate, and candidate override decisions",
            "format": "csv + markdown",
            "promotion_dependency": "candidate cannot bypass baseline fallback",
        },
        {
            "output_id": "recall_boundary_report",
            "required_content": "Top80-present ranking rows separated from top80_missing rows",
            "format": "csv + summary json",
            "promotion_dependency": "ranking claims only use Top80-present rows",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "train_ltr_model",
            "reason": "10.4 defines the offline experiment plan only.",
            "allowed_after": "a later explicit execution stage approves a frozen plan",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "objective variants and loss budgets are specified, not optimized.",
            "allowed_after": "future offline experiment execution stage using dev/OOF only",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "feature-family toggles are plan definitions; whitelist remains frozen.",
            "allowed_after": "future leakage-gated feature change proposal",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no online or ranking implementation is part of 10.4.",
            "allowed_after": "post-validation integration stage, if ever approved",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    objective_variants: list[dict[str, Any]],
    feature_toggles: list[dict[str, Any]],
    split_policy: list[dict[str, Any]],
    leakage_gates: list[dict[str, Any]],
    fallback_contract: list[dict[str, Any]],
    loss_budget: list[dict[str, Any]],
    required_outputs: list[dict[str, Any]],
    stage_10_2: dict[str, Any],
    stage_10_3: dict[str, Any],
) -> dict[str, Any]:
    leakage_fail_count = sum(1 for row in leakage_gates if row["current_status"] != "pass")
    return {
        "objective_variant_count": len(objective_variants),
        "feature_toggle_count": len(feature_toggles),
        "split_policy_count": len(split_policy),
        "leakage_gate_count": len(leakage_gates),
        "leakage_gate_fail_count": leakage_fail_count,
        "fallback_contract_count": len(fallback_contract),
        "loss_budget_item_count": len(loss_budget),
        "required_output_count": len(required_outputs),
        "design_gate_passed": stage_10_3.get("metrics", {}).get("design_gate_passed") is True,
        "training_feature_count": stage_10_2.get("metrics", {}).get("training_feature_count", 0),
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.4 Offline Ranking Experiment Plan Definition",
        "",
        "Read-only plan definition for S2. This freezes what a later offline experiment would have to compare and report. It does not train, tune, change ranking, or edit features.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["objective_variant_count", metrics["objective_variant_count"]],
                ["feature_toggle_count", metrics["feature_toggle_count"]],
                ["split_policy_count", metrics["split_policy_count"]],
                ["leakage_gate_count", metrics["leakage_gate_count"]],
                ["leakage_gate_fail_count", metrics["leakage_gate_fail_count"]],
                ["fallback_contract_count", metrics["fallback_contract_count"]],
                ["loss_budget_item_count", metrics["loss_budget_item_count"]],
                ["required_output_count", metrics["required_output_count"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Objective Variants",
        "",
        _md_table(
            [["variant_id", "objective_family", "priority", "loss_budget"]]
            + [
                [row["variant_id"], row["objective_family"], row["priority"], row["loss_budget"]]
                for row in report["objective_variants"]
            ]
        ),
        "",
        "## Required Outputs",
        "",
        _md_table(
            [["output_id", "required_content", "promotion_dependency"]]
            + [[row["output_id"], row["required_content"], row["promotion_dependency"]] for row in report["required_outputs"]]
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 10.4 offline ranking experiment plan definition")
    parser.add_argument("--stage-10-2", default=str(DEFAULT_STAGE_10_2))
    parser.add_argument("--stage-10-3", default=str(DEFAULT_STAGE_10_3))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_2 = _read_json(Path(args.stage_10_2))
    stage_10_3 = _read_json(Path(args.stage_10_3))

    objective_variants = _objective_variants(stage_10_2)
    feature_toggles = _feature_toggles(stage_10_2)
    split_policy = _split_policy()
    leakage_gates = _leakage_gates(stage_10_2)
    fallback_contract = _fallback_contract(stage_10_2)
    loss_budget = _loss_budget(stage_10_2)
    required_outputs = _required_outputs()
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        objective_variants,
        feature_toggles,
        split_policy,
        leakage_gates,
        fallback_contract,
        loss_budget,
        required_outputs,
        stage_10_2,
        stage_10_3,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "objective_variants_csv": str(output_prefix.with_name(output_prefix.name + "_objective_variants.csv")),
        "feature_toggles_csv": str(output_prefix.with_name(output_prefix.name + "_feature_toggles.csv")),
        "split_policy_csv": str(output_prefix.with_name(output_prefix.name + "_split_policy.csv")),
        "leakage_gates_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gates.csv")),
        "fallback_contract_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract.csv")),
        "loss_budget_csv": str(output_prefix.with_name(output_prefix.name + "_loss_budget.csv")),
        "required_outputs_csv": str(output_prefix.with_name(output_prefix.name + "_required_outputs.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.4 offline ranking experiment plan definition",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_2_summary": str(Path(args.stage_10_2)),
            "stage_10_3_summary": str(Path(args.stage_10_3)),
        },
        "metrics": metrics,
        "objective_variants": objective_variants,
        "feature_toggles": feature_toggles,
        "split_policy": split_policy,
        "leakage_gates": leakage_gates,
        "fallback_contract": fallback_contract,
        "loss_budget": loss_budget,
        "required_outputs": required_outputs,
        "blocked_actions": blocked_actions,
        "decision": (
            "Define the S2 offline ranking experiment plan and keep it read-only. The plan is concrete enough to support a later execution gate: "
            "it names objective variants, feature-family toggles, split policy, leakage gates, fallback contract, loss budget, and required outputs. "
            "This still does not permit training, tuning, ranking changes, feature whitelist edits, heldout selection, or online integration."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.4 defines an offline experiment plan only. It does not train, tune, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, "
            "use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.5 offline ranking experiment execution gate review",
            "goal": (
                "Read-only review whether the 10.4 plan is complete enough to authorize a later offline experiment execution stage, including command boundaries, "
                "expected artifacts, stop conditions, and approval criteria."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
            ],
        },
    }

    _write_csv(
        Path(artifacts["objective_variants_csv"]),
        objective_variants,
        ["variant_id", "objective_family", "priority", "definition", "selection_metric", "loss_budget", "requires_training_later"],
    )
    _write_csv(
        Path(artifacts["feature_toggles_csv"]),
        feature_toggles,
        ["toggle_id", "feature_family", "mode", "feature_count", "purpose", "not_allowed"],
    )
    _write_csv(Path(artifacts["split_policy_csv"]), split_policy, ["policy_item", "definition", "allowed", "forbidden"])
    _write_csv(
        Path(artifacts["leakage_gates_csv"]),
        leakage_gates,
        ["gate_id", "forbidden_key", "current_status", "required_check_before_run", "failure_action"],
    )
    _write_csv(
        Path(artifacts["fallback_contract_csv"]),
        fallback_contract,
        ["contract_item", "definition", "oof_reference", "must_report"],
    )
    _write_csv(Path(artifacts["loss_budget_csv"]), loss_budget, ["budget_item", "required_threshold", "rationale", "blocking_condition"])
    _write_csv(
        Path(artifacts["required_outputs_csv"]),
        required_outputs,
        ["output_id", "required_content", "format", "promotion_dependency"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": metrics,
                "decision": report["decision"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
