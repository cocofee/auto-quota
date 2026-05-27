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
DEFAULT_DQ_CLOSURE = AGENT_STATE / "goal_10x_dq_implementation_held_request_closure_summary.json"
DEFAULT_DQ_RESUME = AGENT_STATE / "goal_10x_dq_implementation_held_request_closure_resume_requirements.csv"
DEFAULT_STRATEGY_INVENTORY = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_parked_broader_strategy_return_gate"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_md_table(rows: list[list[Any]]) -> str:
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
    parking_rows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.42 DQ Implementation Parked And Broader Strategy Return Gate",
        "",
        "Read-only gate to park DQ implementation and return to broader 10.x strategy review.",
        "",
        "## Metrics",
        "",
        _safe_md_table(
            [
                ["metric", "value"],
                ["dq_lane_status", metrics["dq_lane_status"]],
                ["plan_ready_candidate_count", metrics["plan_ready_candidate_count"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["selected_next_strategy_lane", metrics["selected_next_strategy_lane"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Parking Manifest",
        "",
        _safe_md_table(
            [["parked_item", "status", "preserved_evidence", "resume_condition"]]
            + [
                [row["parked_item"], row["status"], row["preserved_evidence"], row["resume_condition"]]
                for row in parking_rows
            ]
        ),
        "",
        "## Strategy Route Decision",
        "",
        _safe_md_table(
            [["strategy_lane", "decision", "rationale"]]
            + [[row["strategy_lane"], row["decision"], row["rationale"]] for row in route_rows]
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
    parser = argparse.ArgumentParser(description="Park DQ implementation and return to broader strategy review")
    parser.add_argument("--dq-closure", default=str(DEFAULT_DQ_CLOSURE))
    parser.add_argument("--dq-resume", default=str(DEFAULT_DQ_RESUME))
    parser.add_argument("--strategy-inventory", default=str(DEFAULT_STRATEGY_INVENTORY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    dq_closure = _read_json(Path(args.dq_closure))
    dq_resume = _read_csv(Path(args.dq_resume))
    strategy_inventory = _read_json(Path(args.strategy_inventory))
    dq_metrics = dq_closure["metrics"]

    parking_rows = [
        {
            "parked_item": "dq_implementation_lane",
            "status": "parked",
            "preserved_evidence": f"closure_decision={dq_metrics['closure_decision']}; request_package_decision={dq_metrics['request_package_decision_from_10_40']}",
            "resume_condition": "explicit implementation go plus complete owner row mapping package, or explicit user instruction",
        },
        {
            "parked_item": "plan_ready_candidates",
            "status": "preserved",
            "preserved_evidence": f"plan_ready_candidate_count={dq_metrics['plan_ready_candidate_count']}",
            "resume_condition": "candidate row mappings remain available for future DQ implementation package",
        },
        {
            "parked_item": "owner_row_mapping_gap",
            "status": "missing",
            "preserved_evidence": f"owner_after_values_missing={dq_metrics['owner_after_values_missing']}",
            "resume_condition": "owner supplies row_id old/new values, owner notes, and rollback keys",
        },
    ]

    route_rows = [
        {
            "strategy_lane": "S1_recall_route_evidence_inventory",
            "decision": "select_next_read_only_lane",
            "rationale": "S2, S3, and DQ implementation are parked/held; S1 is the remaining non-implementation lane that does not depend on owner row mappings.",
        },
        {
            "strategy_lane": "S2_ranking_objective_and_feature_strategy",
            "decision": "defer_parked",
            "rationale": "Previously held due to source-dominated candidate evidence and lack of independent accepted-OSS support.",
        },
        {
            "strategy_lane": "S3_safety_gate_calibration",
            "decision": "defer_parked",
            "rationale": "Previously parked after what-if/authorization gates; no new go or evidence package provided.",
        },
        {
            "strategy_lane": "DQ_implementation",
            "decision": "parked",
            "rationale": "Closed as pause_keep_held; missing explicit go and 64 owner row mappings.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "resume_dq_implementation_without_owner_package",
            "reason": "10.41 closed DQ implementation as pause_keep_held.",
            "allowed_after": "explicit go plus complete owner row mapping package",
        },
        {
            "blocked_action": "train_or_tune",
            "reason": "10.42 is a read-only strategy return gate.",
            "allowed_after": "future explicit offline experiment authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Strategy routing must not use heldout/hard selection.",
            "allowed_after": "separate validation gate, not for selection",
        },
        {
            "blocked_action": "change_goal_searcher_or_feature_whitelist",
            "reason": "No implementation is authorized.",
            "allowed_after": "separate implementation review, if ever authorized",
        },
        {
            "blocked_action": "treat_dq_backlog_as_learning_evidence",
            "reason": "DQ lane remains parked and not learning evidence.",
            "allowed_after": "future re-entry review with separate accepted evidence",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "parking_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_parking_manifest.csv")),
        "strategy_route_decision_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_route_decision.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": dq_closure["stage"],
        "dq_lane_status": "parked",
        "closure_decision_from_10_41": dq_metrics["closure_decision"],
        "plan_ready_candidate_count": dq_metrics["plan_ready_candidate_count"],
        "owner_after_values_missing": dq_metrics["owner_after_values_missing"],
        "resume_requirement_count": len(dq_resume),
        "selected_next_strategy_lane": "S1_recall_route_evidence_inventory",
        "return_to_broader_strategy_now": True,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.42 DQ implementation parked and broader strategy return gate",
        "read_only": True,
        "dq_implementation_parking_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Park the DQ implementation lane while preserving five plan-ready candidates and the 64-row owner mapping requirement. "
            "Return to broader 10.x strategy review and select S1 recall-route evidence inventory as the next read-only lane because it does not depend on owner row mappings."
        ),
        "anti_drift_conclusion": (
            "10.42 only parks the DQ implementation lane and selects the next read-only strategy lane. It does not train, tune, implement DQ fixes, run heldout/hard selection, "
            "change GoalSearcher, edit feature whitelists, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.43 S1 recall-route evidence inventory re-entry review",
            "goal": "Read-only reassess S1 recall-route evidence using dev/OOF/provenance artifacts and decide whether a non-owner-mapping recall analysis lane is viable.",
            "default": "read-only strategy review; no training, no implementation, no heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["parking_manifest_csv"]), parking_rows, ["parked_item", "status", "preserved_evidence", "resume_condition"])
    _write_csv(Path(artifacts["strategy_route_decision_csv"]), route_rows, ["strategy_lane", "decision", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, parking_rows, route_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
