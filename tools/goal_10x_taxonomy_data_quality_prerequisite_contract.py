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
DEFAULT_STAGE_10_20 = AGENT_STATE / "goal_10x_broader_strategy_reentry_after_s3_parking_summary.json"
DEFAULT_STAGE_9_32 = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_taxonomy_data_quality_prerequisite_contract"


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


def _owner_contracts(stage_9_32: dict[str, Any]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for item in stage_9_32.get("handoff_items", []):
        priority = item.get("priority")
        owner_lane = item.get("handoff_owner_lane")
        backlog_area = item.get("backlog_area")
        if backlog_area == "source_provenance":
            evidence_output = "provenance boundary registry and generated-source exclusion list"
            reentry_gate = "all candidate rows have non-generated provenance or are explicitly marked evidence_only"
        elif backlog_area == "query_family_empty":
            evidence_output = "query family coverage table with empty rows labeled or taxonomy-empty classified"
            reentry_gate = "query_family_empty rows are resolved or excluded before recall/ranking learning review"
        elif backlog_area == "top1_family_coverage":
            evidence_output = "top1 family coverage audit for same-domain pipe/valve/lamp/weak-current cases"
            reentry_gate = "top1_family coverage gaps are reviewed and tagged with accepted coverage disposition"
        else:
            evidence_output = "label quality separation table for overbroad/mixed taxonomy labels"
            reentry_gate = "mixed labels are split, accepted as mixed, or excluded from learning evidence"
        contracts.append(
            {
                "backlog_area": backlog_area,
                "priority": priority,
                "owner_lane": owner_lane,
                "row_count": item.get("count"),
                "route_boundary": item.get("route_boundary"),
                "required_evidence_output": evidence_output,
                "acceptance_check": item.get("acceptance_check"),
                "reentry_gate": reentry_gate,
                "learning_boundary": item.get("learning_lane_disposition"),
                "sla_class": "P0_before_any_learning_reentry" if priority == "P0" else "P1_before_generalization_claim",
            }
        )
    return contracts


def _acceptance_checks(owner_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contract in owner_contracts:
        rows.append(
            {
                "check_id": f"ACCEPT_{contract['backlog_area']}",
                "backlog_area": contract["backlog_area"],
                "priority": contract["priority"],
                "owner_lane": contract["owner_lane"],
                "minimum_artifact": contract["required_evidence_output"],
                "pass_condition": contract["acceptance_check"],
                "fail_action": "remain_blocked_from_rank_recall_learning",
                "review_split_policy": "not_a_learning_split; dev/OOF/heldout labels must not be selected from these rows",
            }
        )
    rows.append(
        {
            "check_id": "ACCEPT_NO_DIRECT_LEARNING",
            "backlog_area": "all_backlog_areas",
            "priority": "P0",
            "owner_lane": "learning_boundary",
            "minimum_artifact": "explicit non-learning declaration in any re-entry report",
            "pass_condition": "backlog rows are not counted as Top1 gain, training labels, recall rules, ranking features, or safety-gate thresholds",
            "fail_action": "invalidate_reentry_claim",
            "review_split_policy": "heldout/hard remain validation-only and cannot be used to select fixes",
        }
    )
    return rows


def _reentry_criteria(owner_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for contract in owner_contracts:
        rows.append(
            {
                "reentry_id": f"REENTRY_{contract['backlog_area']}",
                "backlog_area": contract["backlog_area"],
                "eligible_future_lane": "S1_recall_evidence_inventory" if contract["backlog_area"] in {"query_family_empty", "top1_family_coverage"} else "data_quality_reference_only",
                "required_before_reentry": contract["reentry_gate"],
                "evidence_allowed_after_pass": "independent non-generated evidence inventory only",
                "still_forbidden_after_pass": "automatic training, direct rule patch, direct GoalSearcher change, heldout selection, or counting fixed rows as gain",
            }
        )
    return rows


def _learning_boundaries(stage_10_20: dict[str, Any], stage_9_32: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_10_20 = stage_10_20.get("metrics", {})
    metrics_9_32 = stage_9_32.get("metrics", {})
    return [
        {
            "boundary": "s2_s3_execution_lanes",
            "status": "parked",
            "evidence": f"s2_execution_lane_parked={metrics_10_20.get('s2_execution_lane_parked')}; s3_execution_lane_parked={metrics_10_20.get('s3_execution_lane_parked')}",
            "decision": "do_not_execute_from_data_quality_contract",
            "not_allowed": "no S2/S3 what-if, training, tuning, or implementation",
        },
        {
            "boundary": "taxonomy_backlog_not_learning",
            "status": "closed_for_direct_learning",
            "evidence": f"total_priority_backlog_rows={metrics_9_32.get('total_priority_backlog_rows')}; learnable_slice_count={metrics_9_32.get('learnable_slice_count')}",
            "decision": "contract_only_not_learning_evidence",
            "not_allowed": "no Top1 gain claim, no training label, no recall rule, no ranking feature",
        },
        {
            "boundary": "source_provenance",
            "status": "P0_gate_before_reentry",
            "evidence": f"source_provenance_rows={metrics_9_32.get('source_provenance_rows')}",
            "decision": "generated_source_must_be_excluded_or_documented",
            "not_allowed": "no global_repair_decision_table learning evidence",
        },
        {
            "boundary": "taxonomy_coverage",
            "status": "P0_P1_gate_before_reentry",
            "evidence": f"query_family_empty_rows={metrics_9_32.get('query_family_empty_rows')}; top1_family_coverage_rows={metrics_9_32.get('top1_family_coverage_rows')}",
            "decision": "coverage_labels_must_be_audited_before_recall_learning",
            "not_allowed": "no recall expansion rule from empty family buckets",
        },
        {
            "boundary": "heldout_hard",
            "status": "validation_only",
            "evidence": f"heldout_used_for_selection={metrics_10_20.get('heldout_used_for_selection')}",
            "decision": "no_selection_from_validation_splits",
            "not_allowed": "no heldout/hard selection of taxonomy fixes or learning strategy",
        },
    ]


def _route_plan() -> list[dict[str, Any]]:
    return [
        {
            "step_id": "DQ1_CONTRACT",
            "stage_status": "complete_in_10_21",
            "action": "define owners, acceptance checks, learning boundaries, and re-entry gates",
            "exit_condition": "contract tables emitted and dashboard updated",
            "next_default": "10.22 taxonomy/data-quality contract closure and next-lane gate",
        },
        {
            "step_id": "DQ2_CLOSURE_GATE",
            "stage_status": "future_read_only",
            "action": "decide whether contract is enough to close 10.x strategy loop or to open a data-quality backlog route",
            "exit_condition": "no learning or implementation is opened",
            "next_default": "read-only closure or backlog route handoff",
        },
        {
            "step_id": "DQ3_REENTRY_IF_FIXED",
            "stage_status": "future_condition_only",
            "action": "only after acceptance checks pass, revisit S1 recall evidence inventory using independent non-generated traces",
            "exit_condition": "new strategy gate explicitly admits cleaned evidence",
            "next_default": "not eligible in 10.21",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s2_or_s3_execution",
            "reason": "10.21 is taxonomy/data-quality contract only; execution lanes remain parked.",
            "allowed_after": "explicit user go plus separate execution stage",
        },
        {
            "blocked_action": "train_or_tune_model",
            "reason": "contract definition is not model training or tuning.",
            "allowed_after": "separate explicitly opened execution stage with frozen scope",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "S3 execution and implementation are parked.",
            "allowed_after": "future explicit S3 execution evidence plus implementation review, if ever reached",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.21 does not propose feature/ranking changes.",
            "allowed_after": "separate feature/ranking proposal plus leakage preflight",
        },
        {
            "blocked_action": "patch_recall_rules_or_goal_searcher",
            "reason": "taxonomy backlog acceptance does not authorize recall rules or GoalSearcher changes.",
            "allowed_after": "future recall strategy after accepted independent evidence and separate implementation review",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "count_backlog_rows_as_learning_evidence",
            "reason": "10.21 defines prerequisite gates only.",
            "allowed_after": "never directly; only accepted cleaned evidence can be inventoried in a later strategy stage",
        },
    ]


def _metrics(
    owner_contracts: list[dict[str, Any]],
    acceptance_checks: list[dict[str, Any]],
    reentry_criteria: list[dict[str, Any]],
    learning_boundaries: list[dict[str, Any]],
    route_plan: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_9_32: dict[str, Any],
) -> dict[str, Any]:
    metrics_9_32 = stage_9_32.get("metrics", {})
    return {
        "owner_contract_count": len(owner_contracts),
        "acceptance_check_count": len(acceptance_checks),
        "reentry_criteria_count": len(reentry_criteria),
        "learning_boundary_count": len(learning_boundaries),
        "route_plan_step_count": len(route_plan),
        "blocked_action_count": len(blocked_actions),
        "p0_contract_count": sum(1 for row in owner_contracts if row["priority"] == "P0"),
        "p1_contract_count": sum(1 for row in owner_contracts if row["priority"] == "P1"),
        "total_priority_backlog_rows": metrics_9_32.get("total_priority_backlog_rows", 0),
        "source_provenance_rows": metrics_9_32.get("source_provenance_rows", 0),
        "query_family_empty_rows": metrics_9_32.get("query_family_empty_rows", 0),
        "top1_family_coverage_rows": metrics_9_32.get("top1_family_coverage_rows", 0),
        "label_or_taxonomy_mixture_rows": metrics_9_32.get("label_or_taxonomy_mixture_rows", 0),
        "learnable_slice_count": metrics_9_32.get("learnable_slice_count", 0),
        "contract_defined": True,
        "learning_reentry_allowed_now": False,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.21 Taxonomy/Data-quality Prerequisite Acceptance And Re-entry Contract",
        "",
        "Read-only contract for taxonomy/data-quality backlog ownership, acceptance checks, and future learning re-entry criteria.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["owner_contract_count", metrics["owner_contract_count"]],
                ["acceptance_check_count", metrics["acceptance_check_count"]],
                ["reentry_criteria_count", metrics["reentry_criteria_count"]],
                ["total_priority_backlog_rows", metrics["total_priority_backlog_rows"]],
                ["contract_defined", metrics["contract_defined"]],
                ["learning_reentry_allowed_now", metrics["learning_reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Owner Contracts",
        "",
        _md_table(
            [["backlog_area", "priority", "owner_lane", "row_count", "reentry_gate"]]
            + [[row["backlog_area"], row["priority"], row["owner_lane"], row["row_count"], row["reentry_gate"]] for row in report["owner_contracts"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.21 taxonomy/data-quality prerequisite acceptance and re-entry contract")
    parser.add_argument("--stage-10-20", default=str(DEFAULT_STAGE_10_20))
    parser.add_argument("--stage-9-32", default=str(DEFAULT_STAGE_9_32))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_20 = _read_json(Path(args.stage_10_20))
    stage_9_32 = _read_json(Path(args.stage_9_32))
    owner_contracts = _owner_contracts(stage_9_32)
    acceptance_checks = _acceptance_checks(owner_contracts)
    reentry_criteria = _reentry_criteria(owner_contracts)
    learning_boundaries = _learning_boundaries(stage_10_20, stage_9_32)
    route_plan = _route_plan()
    blocked_actions = _blocked_actions()
    metrics = _metrics(owner_contracts, acceptance_checks, reentry_criteria, learning_boundaries, route_plan, blocked_actions, stage_9_32)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "owner_contracts_csv": str(output_prefix.with_name(output_prefix.name + "_owner_contracts.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "reentry_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_criteria.csv")),
        "learning_boundaries_csv": str(output_prefix.with_name(output_prefix.name + "_learning_boundaries.csv")),
        "route_plan_csv": str(output_prefix.with_name(output_prefix.name + "_route_plan.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.21 taxonomy/data-quality prerequisite acceptance and re-entry contract",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_whatif_execution": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_20_reentry": str(Path(args.stage_10_20)),
            "stage_9_32_taxonomy_handoff": str(Path(args.stage_9_32)),
        },
        "metrics": metrics,
        "owner_contracts": owner_contracts,
        "acceptance_checks": acceptance_checks,
        "reentry_criteria": reentry_criteria,
        "learning_boundaries": learning_boundaries,
        "route_plan": route_plan,
        "blocked_actions": blocked_actions,
        "decision": (
            "Define the taxonomy/data-quality prerequisite contract for four backlog areas: source_provenance, query_family_empty, "
            "top1_family_coverage, and label_or_taxonomy_mixture. The contract establishes owners, acceptance checks, and re-entry criteria, "
            "but does not allow learning re-entry now. Backlog rows remain excluded from Top1 gain claims, training labels, recall rules, ranking features, "
            "safety-gate thresholds, and GoalSearcher changes."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.21 only defines a prerequisite contract. It does not run S2/S3 execution, run what-if, train, tune, change thresholds, patch rules, "
            "change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, convert taxonomy backlog rows "
            "into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.22 taxonomy/data-quality contract closure and learning re-entry gate",
            "goal": (
                "Read-only decide whether the 10.21 prerequisite contract is sufficient to close the 10.x strategy loop, open a data-quality backlog route, "
                "or define future learning re-entry conditions. Still no learning, training, tuning, or implementation."
            ),
            "prohibited": [
                "what-if execution",
                "training",
                "tuning",
                "threshold changes",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
                "counting backlog rows as learning evidence",
            ],
        },
    }

    _write_csv(
        Path(artifacts["owner_contracts_csv"]),
        owner_contracts,
        ["backlog_area", "priority", "owner_lane", "row_count", "route_boundary", "required_evidence_output", "acceptance_check", "reentry_gate", "learning_boundary", "sla_class"],
    )
    _write_csv(
        Path(artifacts["acceptance_checks_csv"]),
        acceptance_checks,
        ["check_id", "backlog_area", "priority", "owner_lane", "minimum_artifact", "pass_condition", "fail_action", "review_split_policy"],
    )
    _write_csv(
        Path(artifacts["reentry_criteria_csv"]),
        reentry_criteria,
        ["reentry_id", "backlog_area", "eligible_future_lane", "required_before_reentry", "evidence_allowed_after_pass", "still_forbidden_after_pass"],
    )
    _write_csv(
        Path(artifacts["learning_boundaries_csv"]),
        learning_boundaries,
        ["boundary", "status", "evidence", "decision", "not_allowed"],
    )
    _write_csv(Path(artifacts["route_plan_csv"]), route_plan, ["step_id", "stage_status", "action", "exit_condition", "next_default"])
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
