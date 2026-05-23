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
DEFAULT_9X_CLOSURE = AGENT_STATE / "goal_9x_mining_closure_next_strategy_gate_9x_summary.json"
DEFAULT_9X_DECOMPOSITION = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_COMPAT_WHATIF = AGENT_STATE / "goal_family_compatibility_whatif_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_accuracy_strategy_definition"


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


def _split_metrics(decomp: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row.get("split"): row for row in decomp.get("splits", [])}


def _compat_split_metrics(compat: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row.get("split"): row for row in compat.get("split_metrics", [])}


def _strategy_candidates(decomp: dict[str, Any], compat: dict[str, Any]) -> list[dict[str, Any]]:
    splits = _split_metrics(decomp)
    dev = splits.get("dev", {})
    heldout = splits.get("heldout", {})
    hard = splits.get("hard", {})
    compat_splits = _compat_split_metrics(compat)
    compat_dev = compat_splits.get("dev_oof", {})
    return [
        {
            "strategy_id": "S1_recall_route_evidence_inventory",
            "candidate_lever": "recall_route_expansion",
            "priority": "P0_read_only_first",
            "why_now": (
                f"top80_missing exists across splits: dev={dev.get('top80_missing', 0)}, "
                f"heldout={heldout.get('top80_missing', 0)}, hard={hard.get('top80_missing', 0)}; "
                "9.x bucket mining showed current labels/provenance are not learnable without a cleaner route definition."
            ),
            "first_action": "read_only_inventory_independent_recall_evidence",
            "evidence_requirement": "independent non-generated traces, province/source diversity, query/top1 taxonomy coverage, and no direct use of global_repair_decision_table as learning evidence",
            "blocked_until": "taxonomy/provenance boundaries from 9.32 are respected",
            "not_allowed": "do not add recall rules, do not alter GoalSearcher, do not train from generated repair rows",
        },
        {
            "strategy_id": "S2_ranking_objective_and_feature_strategy",
            "candidate_lever": "ranking_ltr_feature_strategy",
            "priority": "P0_read_only_first",
            "why_now": (
                f"wrong-rank dominates non-hit rows: dev_wrong_rank={dev.get('top80_present_but_wrong_rank', 0)}, "
                f"heldout_wrong_rank={heldout.get('top80_present_but_wrong_rank', 0)}, hard_wrong_rank={hard.get('top80_present_but_wrong_rank', 0)}; "
                "9.x bucket mining exhausted family buckets, so the next strategy must be broad objective/feature framing."
            ),
            "first_action": "read_only_define_feature_objective_loss_audit",
            "evidence_requirement": "OOF-only selection, feature leakage audit, gain/loss slices by split, and explicit fallback/safety-gate interaction",
            "blocked_until": "candidate feature families and loss audit plan are specified before any training",
            "not_allowed": "do not retrain LTR in 10.0, do not use sample/source/expected_id as features, do not tune on heldout",
        },
        {
            "strategy_id": "S3_safety_gate_calibration_v2_plan",
            "candidate_lever": "safety_gate_or_compatibility_recalibration",
            "priority": "P1_read_only_after_S1_S2",
            "why_now": (
                f"7.5 compatibility what-if rescued OOF blocked gain={compat_dev.get('rescued_blocked_gain', 0)} "
                f"with new_residual_loss={compat_dev.get('new_residual_loss', 0)}; remaining blocked gain still needs a broader safety strategy."
            ),
            "first_action": "read_only_residual_and_threshold_policy_plan",
            "evidence_requirement": "OOF calibration only, heldout/hard single validation after freeze, residual loss buckets, and relation-level audit",
            "blocked_until": "a loss budget and rescue/loss acceptance rule are defined",
            "not_allowed": "do not change thresholds in 10.0, do not tune on heldout, do not create family-specific patches",
        },
        {
            "strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "candidate_lever": "taxonomy_data_quality_prerequisite",
            "priority": "P0_parallel_backlog_not_learning",
            "why_now": "9.32 handed off source_provenance, query_family_empty, top1_family coverage, and label mixture as data-quality work.",
            "first_action": "read_only_backlog_contract_and_acceptance_plan",
            "evidence_requirement": "clear ownership, acceptance checks, and re-entry criteria before cleaned data can be used by any learning lane",
            "blocked_until": "P0 provenance/query_family_empty items are documented or fixed",
            "not_allowed": "do not count data-quality backlog rows as learning evidence or Top1 improvement",
        },
    ]


def _split_policy() -> list[dict[str, Any]]:
    return [
        {
            "policy_area": "strategy_selection",
            "allowed_split": "dev + OOF only",
            "heldout_policy": "not_allowed_for_selection",
            "rule": "Use dev/OOF to compare candidate 10.x levers; heldout is reserved for frozen validation.",
        },
        {
            "policy_area": "threshold_or_gate_selection",
            "allowed_split": "OOF only",
            "heldout_policy": "single_validation_after_freeze",
            "rule": "Any future threshold, gate, or compatibility policy must be selected on OOF, then frozen before heldout/hard.",
        },
        {
            "policy_area": "implementation_acceptance",
            "allowed_split": "dev/OOF first, heldout/hard after freeze",
            "heldout_policy": "validation_only",
            "rule": "No implementation is accepted without split-level gains, loss buckets, and regression review.",
        },
        {
            "policy_area": "data_quality_backlog",
            "allowed_split": "not_a_learning_split",
            "heldout_policy": "not_applicable",
            "rule": "Taxonomy/provenance backlog can unblock future evidence, but is not itself training or ranking evidence.",
        },
    ]


def _loss_audit_plan() -> list[dict[str, Any]]:
    return [
        {
            "audit_area": "top1_gain_loss_balance",
            "required_output": "net_gain, rescued_gain, new_loss, retained_saved_loss by split",
            "minimum_standard": "new losses must be explicitly enumerated and reviewed before any promotion",
        },
        {
            "audit_area": "bucket_regression_slices",
            "required_output": "loss buckets by query_family, top1_family, expected_book, source_file, province, rank_bucket",
            "minimum_standard": "no single-family or single-source patch may be claimed as general improvement",
        },
        {
            "audit_area": "recall_vs_ranking_separation",
            "required_output": "top80_missing, top80_present_but_wrong_rank, baseline hit, gated hit, proposed hit",
            "minimum_standard": "recall gains and ranking gains must be reported separately",
        },
        {
            "audit_area": "leakage_and_feature_safety",
            "required_output": "forbidden feature/key scan and source-provenance review",
            "minimum_standard": "sample_id, source_file, expected_id, province-specific quota IDs, and generated repair source cannot be direct features or labels",
        },
    ]


def _acceptance_criteria() -> list[dict[str, Any]]:
    return [
        {
            "criterion": "strategy_definition_complete",
            "applies_to": "10.0",
            "threshold": "candidate levers + evidence requirements + split policy + loss audit plan are documented",
            "status": "target_for_this_stage",
        },
        {
            "criterion": "read_only_evidence_inventory_complete",
            "applies_to": "10.1",
            "threshold": "each admissible lever has measurable evidence inventory before implementation",
            "status": "next_required",
        },
        {
            "criterion": "no_heldout_selection",
            "applies_to": "all_10x",
            "threshold": "heldout/hard are used only after strategy and thresholds are frozen",
            "status": "required",
        },
        {
            "criterion": "no_direct_taxonomy_backlog_learning",
            "applies_to": "all_10x",
            "threshold": "taxonomy/data-quality backlog must have re-entry criteria before use as evidence",
            "status": "required",
        },
        {
            "criterion": "large_sample_acceptance",
            "applies_to": "post_implementation",
            "threshold": "split-level Top1 gain with explicit losses, plus final heldout/hard validation; target remains 75% Top1 long-term",
            "status": "future_not_10_0",
        },
    ]


def _metrics(closure: dict[str, Any], decomp: dict[str, Any], compat: dict[str, Any]) -> dict[str, Any]:
    splits = _split_metrics(decomp)
    dev = splits.get("dev", {})
    heldout = splits.get("heldout", {})
    hard = splits.get("hard", {})
    compat_splits = _compat_split_metrics(compat)
    compat_dev = compat_splits.get("dev_oof", {})
    closure_metrics = closure.get("metrics", {})
    return {
        "all_9x_mining_lanes_closed": closure_metrics.get("all_9x_mining_lanes_closed"),
        "dev_baseline_top1_rate": dev.get("baseline_top1_rate"),
        "dev_top80_recall_rate": dev.get("top80_recall_rate"),
        "dev_top80_missing": dev.get("top80_missing"),
        "dev_wrong_rank": dev.get("top80_present_but_wrong_rank"),
        "heldout_baseline_top1_rate": heldout.get("baseline_top1_rate"),
        "heldout_top80_recall_rate": heldout.get("top80_recall_rate"),
        "hard_baseline_top1_rate": hard.get("baseline_top1_rate"),
        "hard_top80_recall_rate": hard.get("top80_recall_rate"),
        "compat_oof_rescued_blocked_gain": compat_dev.get("rescued_blocked_gain"),
        "compat_oof_new_residual_loss": compat_dev.get("new_residual_loss"),
        "strategy_candidate_count": 4,
        "implementation_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.0 Accuracy Strategy Definition",
        "",
        "Read-only definition of the next accuracy strategy after 9.x mining closure. This stage defines candidate levers, evidence requirements, split policy, loss audit plan, and acceptance criteria; it does not implement any change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["all_9x_mining_lanes_closed", metrics.get("all_9x_mining_lanes_closed")],
                ["dev_baseline_top1_rate", metrics.get("dev_baseline_top1_rate")],
                ["dev_top80_recall_rate", metrics.get("dev_top80_recall_rate")],
                ["dev_top80_missing", metrics.get("dev_top80_missing")],
                ["dev_wrong_rank", metrics.get("dev_wrong_rank")],
                ["compat_oof_rescued_blocked_gain", metrics.get("compat_oof_rescued_blocked_gain")],
                ["compat_oof_new_residual_loss", metrics.get("compat_oof_new_residual_loss")],
                ["strategy_candidate_count", metrics.get("strategy_candidate_count")],
                ["implementation_allowed", metrics.get("implementation_allowed")],
            ]
        ),
        "",
        "## Candidate Levers",
        "",
        _md_table(
            [["strategy_id", "lever", "priority", "first_action", "blocked_until"]]
            + [
                [row["strategy_id"], row["candidate_lever"], row["priority"], row["first_action"], row["blocked_until"]]
                for row in report["strategy_candidates"]
            ]
        ),
        "",
        "## Split Policy",
        "",
        _md_table(
            [["policy_area", "allowed_split", "heldout_policy", "rule"]]
            + [[row["policy_area"], row["allowed_split"], row["heldout_policy"], row["rule"]] for row in report["split_policy"]]
        ),
        "",
        "## Loss Audit Plan",
        "",
        _md_table(
            [["audit_area", "required_output", "minimum_standard"]]
            + [[row["audit_area"], row["required_output"], row["minimum_standard"]] for row in report["loss_audit_plan"]]
        ),
        "",
        "## Acceptance Criteria",
        "",
        _md_table(
            [["criterion", "applies_to", "threshold", "status"]]
            + [[row["criterion"], row["applies_to"], row["threshold"], row["status"]] for row in report["acceptance_criteria"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.0 accuracy strategy definition")
    parser.add_argument("--closure-9x", default=str(DEFAULT_9X_CLOSURE))
    parser.add_argument("--decomposition-9x", default=str(DEFAULT_9X_DECOMPOSITION))
    parser.add_argument("--compat-whatif", default=str(DEFAULT_COMPAT_WHATIF))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    closure = _read_json(Path(args.closure_9x))
    decomp = _read_json(Path(args.decomposition_9x))
    compat = _read_json(Path(args.compat_whatif))
    candidates = _strategy_candidates(decomp, compat)
    split_policy = _split_policy()
    loss_audit = _loss_audit_plan()
    acceptance = _acceptance_criteria()
    metrics = _metrics(closure, decomp, compat)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "strategy_candidates_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_candidates.csv")),
        "evidence_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_requirements.csv")),
        "split_policy_csv": str(output_prefix.with_name(output_prefix.name + "_split_policy.csv")),
        "loss_audit_plan_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_plan.csv")),
        "acceptance_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_criteria.csv")),
    }
    evidence_requirements = [
        {
            "strategy_id": row["strategy_id"],
            "candidate_lever": row["candidate_lever"],
            "evidence_requirement": row["evidence_requirement"],
            "not_allowed": row["not_allowed"],
        }
        for row in candidates
    ]
    report = {
        "stage": "Goal LTR v1 / stage 10.0 accuracy strategy definition",
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
        "source_artifacts": {
            "stage_9_33_closure": str(Path(args.closure_9x)),
            "stage_9_0_decomposition": str(Path(args.decomposition_9x)),
            "stage_7_5_compatibility_whatif": str(Path(args.compat_whatif)),
        },
        "metrics": metrics,
        "strategy_candidates": candidates,
        "evidence_requirements": evidence_requirements,
        "split_policy": split_policy,
        "loss_audit_plan": loss_audit,
        "acceptance_criteria": acceptance,
        "decision": (
            "Define 10.x as a read-only strategy lane before any implementation. The admissible first follow-up is a 10.1 evidence inventory "
            "that scores recall-route, ranking/objective, safety-gate, and taxonomy-prerequisite levers against explicit evidence, split, and loss-audit requirements."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.0 only defines strategy. It does not train, tune, patch rules, change ranking, modify GoalSearcher, use heldout for selection, "
            "connect online, relax gates, or convert taxonomy/data-quality backlog into rank/recall learning evidence."
        ),
        "next_stage": {
            "stage": "10.1 accuracy strategy evidence inventory",
            "goal": (
                "Read-only inventory and score evidence for the 10.0 candidate levers, selecting at most a next analysis lane using dev/OOF evidence only."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
            ],
        },
    }

    _write_csv(
        Path(artifacts["strategy_candidates_csv"]),
        candidates,
        ["strategy_id", "candidate_lever", "priority", "why_now", "first_action", "evidence_requirement", "blocked_until", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["evidence_requirements_csv"]),
        evidence_requirements,
        ["strategy_id", "candidate_lever", "evidence_requirement", "not_allowed"],
    )
    _write_csv(Path(artifacts["split_policy_csv"]), split_policy, ["policy_area", "allowed_split", "heldout_policy", "rule"])
    _write_csv(Path(artifacts["loss_audit_plan_csv"]), loss_audit, ["audit_area", "required_output", "minimum_standard"])
    _write_csv(Path(artifacts["acceptance_criteria_csv"]), acceptance, ["criterion", "applies_to", "threshold", "status"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": {
                    "all_9x_mining_lanes_closed": metrics["all_9x_mining_lanes_closed"],
                    "dev_top80_missing": metrics["dev_top80_missing"],
                    "dev_wrong_rank": metrics["dev_wrong_rank"],
                    "strategy_candidate_count": metrics["strategy_candidate_count"],
                    "implementation_allowed": metrics["implementation_allowed"],
                },
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
