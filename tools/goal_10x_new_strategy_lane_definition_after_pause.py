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
DEFAULT_PAUSE = AGENT_STATE / "goal_10x_no_active_learning_lane_evidence_wait_checkpoint_summary.json"
DEFAULT_GAP = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_STRATEGY = AGENT_STATE / "goal_10x_accuracy_strategy_definition_summary.json"
DEFAULT_CONFIDENCE = AGENT_STATE / "goal_10x_strategy_confidence_loophole_audit_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause"


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


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    gate_plan: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.47 New 10.x Strategy Lane Definition After Pause",
        "",
        "Read-only definition of a new strategy lane without reopening S1/S2/S3/DQ implementation.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_lane_count", metrics["candidate_lane_count"]],
                ["selected_lane", metrics["selected_lane"]],
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Candidate Lanes",
        "",
        _md_table(
            [["lane_id", "lane_name", "decision", "why_now"]]
            + [[row["lane_id"], row["lane_name"], row["decision"], row["why_now"]] for row in candidates]
        ),
        "",
        "## Selected Lane",
        "",
        _md_table(
            [["lane_id", "first_gate", "evidence_requirement", "not_allowed"]]
            + [[row["lane_id"], row["first_gate"], row["evidence_requirement"], row["not_allowed"]] for row in selected]
        ),
        "",
        "## Next Gate Plan",
        "",
        _md_table(
            [["gate_item", "required_output", "acceptance_check"]]
            + [[row["gate_item"], row["required_output"], row["acceptance_check"]] for row in gate_plan]
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Define a new read-only 10.x strategy lane after pause")
    parser.add_argument("--pause-summary", default=str(DEFAULT_PAUSE))
    parser.add_argument("--gap-summary", default=str(DEFAULT_GAP))
    parser.add_argument("--strategy-summary", default=str(DEFAULT_STRATEGY))
    parser.add_argument("--confidence-summary", default=str(DEFAULT_CONFIDENCE))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    pause = _read_json(Path(args.pause_summary))
    gap = _read_json(Path(args.gap_summary))
    strategy = _read_json(Path(args.strategy_summary))
    confidence = _read_json(Path(args.confidence_summary))
    dev_split = next(row for row in gap["splits"] if row["split"] == "dev")

    candidates = [
        {
            "lane_id": "S5_measurement_integrity_slice_telemetry",
            "lane_name": "measurement_integrity_and_slice_telemetry_design",
            "decision": "select_next_read_only_lane",
            "why_now": "The current loop is paused because evidence provenance, source dominance, and DQ/learning separation repeatedly blocked S1/S2/S3. A read-only telemetry lane can improve future decision quality without training or implementation.",
            "does_not_depend_on": "new external evidence; owner row mappings; heldout/hard selection; training; GoalSearcher changes",
            "evidence_requirement": "existing dev/OOF reports, artifact hashes, split declarations, source_family/provenance fields, and loss-slice schemas",
            "not_allowed": "no algorithm change, no threshold change, no feature whitelist edit, no heldout/hard selection, no Top1 gain claim",
        },
        {
            "lane_id": "S6_parser_query_normalization_inventory",
            "lane_name": "parser_and_query_normalization_gap_inventory",
            "decision": "defer_read_only_candidate",
            "why_now": "query_family_empty remains visible in gap reports, but this borders the DQ backlog and could be mistaken for learning evidence.",
            "does_not_depend_on": "owner row mappings if kept inventory-only",
            "evidence_requirement": "existing query text, parser outputs, query_family_empty slices, and taxonomy disposition labels",
            "not_allowed": "no parser rule edits, no taxonomy edits, no recall/rank learning claim",
        },
        {
            "lane_id": "S7_rank_position_distribution_diagnostics",
            "lane_name": "rank_position_and_candidate_pool_observability",
            "decision": "defer_read_only_candidate",
            "why_now": f"wrong-rank remains large in dev={dev_split['top80_present_but_wrong_rank']}, but S2 ranking is parked pending independent accepted-OSS evidence.",
            "does_not_depend_on": "training if restricted to diagnostics",
            "evidence_requirement": "existing rank buckets, candidate-pool size, top80_present/top80_missing separation, and loss slices",
            "not_allowed": "no ranking objective changes, no candidate matrix expansion, no LTR training",
        },
        {
            "lane_id": "S8_source_family_independence_registry_design",
            "lane_name": "source_family_independence_registry_design",
            "decision": "defer_as_supporting_contract",
            "why_now": "Source-family independence repeatedly blocked re-entry; however, source provenance already has a DQ route and should not become a new learning lane.",
            "does_not_depend_on": "owner row mappings for design only",
            "evidence_requirement": "source_file, source_family, producer, collection_method, provenance_hash schema",
            "not_allowed": "no source acceptance claim, no evidence re-entry, no training",
        },
    ]
    selected = [
        {
            "lane_id": "S5_measurement_integrity_slice_telemetry",
            "first_gate": "10.48 S5 measurement integrity and slice telemetry design gate",
            "evidence_requirement": "Define required observability fields and artifact manifest using existing dev/OOF artifacts only; prove no heldout/hard selection and no algorithm change.",
            "not_allowed": "Do not train, tune, implement telemetry code, edit GoalSearcher, edit feature whitelist, change rules/thresholds, or claim accuracy gain.",
        }
    ]
    gate_plan = [
        {
            "gate_item": "observability_field_manifest",
            "required_output": "fields for split, source_file, source_family, provenance_hash, query_family, top1_family, expected_book, top1_book, rank_bucket, gain/loss/net, taxonomy_disposition",
            "acceptance_check": "all fields are definitions only and can be populated from existing reports or future evidence packages without changing ranking",
        },
        {
            "gate_item": "artifact_integrity_boundary",
            "required_output": "manifest/hash requirements for reports used in future re-entry reviews",
            "acceptance_check": "freshness/hash policy defined; no stale artifact trusted without regeneration or hash check",
        },
        {
            "gate_item": "effect_decomposition_boundary",
            "required_output": "separate taxonomy_cleanup_effect, recall_effect, ranking_effect, and safety_gate_effect",
            "acceptance_check": "DQ backlog rows cannot count as ranking/recall gain; source-dominated gains cannot become general Top1 claims",
        },
        {
            "gate_item": "split_policy_boundary",
            "required_output": "dev/OOF-only analysis contract and heldout/hard validation-only reminder",
            "acceptance_check": "no field, score, lane, or candidate is selected using heldout/hard",
        },
        {
            "gate_item": "next_action_boundary",
            "required_output": "go/no-go for whether S5 can proceed to a read-only design artifact, not implementation",
            "acceptance_check": "implementation_allowed=false; training_allowed=false; goal_searcher_change_allowed=false",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "reopen_s1_s2_s3_or_dq_implementation",
            "reason": "10.47 defines a new lane only; 10.46 re-entry requirements remain unsatisfied.",
            "allowed_after": "lane-specific evidence package, explicit go, or owner mappings pass read-only re-entry review",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "S5 is measurement/design only and active_learning_lane_count remains 0.",
            "allowed_after": "separate future execution authorization after a valid learning lane is reopened",
        },
        {
            "blocked_action": "implement_telemetry_or_change_goal_searcher",
            "reason": "10.47 only defines the lane; it does not authorize code changes or integration.",
            "allowed_after": "separate implementation plan and explicit go",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_accuracy_gain",
            "reason": "strategy definition and telemetry design are not algorithm changes.",
            "allowed_after": "future validated candidate shows split-level gain with loss audit",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_lanes_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_lanes.csv")),
        "selected_lane_csv": str(output_prefix.with_name(output_prefix.name + "_selected_lane.csv")),
        "next_gate_plan_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate_plan.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": pause["stage"],
        "pause_10x_loop_before_user_redirect": pause["metrics"]["pause_10x_loop_now"],
        "user_redirect_to_new_read_only_strategy_lane": True,
        "candidate_lane_count": len(candidates),
        "selected_lane": "S5_measurement_integrity_slice_telemetry",
        "active_learning_lane_count": 0,
        "dev_wrong_rank": dev_split["top80_present_but_wrong_rank"],
        "dev_top80_missing": dev_split["top80_missing"],
        "known_loophole_count_from_confidence_audit": confidence["metrics"]["loophole_count"],
        "legacy_strategy_candidate_count": strategy["metrics"]["strategy_candidate_count"],
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.47 new 10.x strategy lane definition after pause",
        "read_only": True,
        "strategy_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define a new read-only S5 measurement_integrity_and_slice_telemetry lane. It does not reopen S1/S2/S3/DQ implementation and does not authorize training, "
            "implementation, or heldout/hard selection. S5 is selected because it uses existing artifacts to harden future evidence quality, provenance, split discipline, and effect decomposition before any future learning re-entry."
        ),
        "anti_drift_conclusion": (
            "10.47 only defines a new strategy lane and next gate. It does not train, tune, expand candidates, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement telemetry, implement DQ fixes, or convert DQ backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.48 S5 measurement integrity and slice telemetry design gate",
            "goal": "Read-only decide whether S5 is specific enough to define a future telemetry/design artifact using existing dev/OOF reports only.",
            "default": "read-only design gate; no implementation, no training, no heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["candidate_lanes_csv"]), candidates, ["lane_id", "lane_name", "decision", "why_now", "does_not_depend_on", "evidence_requirement", "not_allowed"])
    _write_csv(Path(artifacts["selected_lane_csv"]), selected, ["lane_id", "first_gate", "evidence_requirement", "not_allowed"])
    _write_csv(Path(artifacts["next_gate_plan_csv"]), gate_plan, ["gate_item", "required_output", "acceptance_check"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, candidates, selected, gate_plan)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
