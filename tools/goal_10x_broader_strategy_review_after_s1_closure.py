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
DEFAULT_S1_CLOSURE = AGENT_STATE / "goal_10x_s1_independent_recall_evidence_request_broader_strategy_closure_summary.json"
DEFAULT_S2_CLOSURE = AGENT_STATE / "goal_10x_s2_lane_park_evidence_request_closure_summary.json"
DEFAULT_S3_PARKING = AGENT_STATE / "goal_10x_s3_execution_lane_parking_strategy_return_gate_summary.json"
DEFAULT_DQ_PARKING = AGENT_STATE / "goal_10x_dq_implementation_parked_broader_strategy_return_gate_summary.json"
DEFAULT_STRATEGY_INVENTORY = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure"


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
    lane_status: list[dict[str, Any]],
    route_selection: list[dict[str, Any]],
    evidence_wait: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.45 Broader 10.x Strategy Review After S1 Closure",
        "",
        "Read-only strategy review after S1/S2/S3/DQ lanes have been parked or blocked.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],
                ["parked_or_blocked_lane_count", metrics["parked_or_blocked_lane_count"]],
                ["selected_next_route", metrics["selected_next_route"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Lane Status",
        "",
        _md_table(
            [["lane", "status", "blocking_condition", "reopen_condition"]]
            + [[row["lane"], row["status"], row["blocking_condition"], row["reopen_condition"]] for row in lane_status]
        ),
        "",
        "## Route Selection",
        "",
        _md_table(
            [["route", "decision", "rationale"]]
            + [[row["route"], row["decision"], row["rationale"]] for row in route_selection]
        ),
        "",
        "## Evidence Wait Contract",
        "",
        _md_table(
            [["evidence_track", "required_before_reentry", "owner_or_source"]]
            + [[row["evidence_track"], row["required_before_reentry"], row["owner_or_source"]] for row in evidence_wait]
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
    parser = argparse.ArgumentParser(description="Review broader 10.x strategy after S1 closure")
    parser.add_argument("--s1-closure", default=str(DEFAULT_S1_CLOSURE))
    parser.add_argument("--s2-closure", default=str(DEFAULT_S2_CLOSURE))
    parser.add_argument("--s3-parking", default=str(DEFAULT_S3_PARKING))
    parser.add_argument("--dq-parking", default=str(DEFAULT_DQ_PARKING))
    parser.add_argument("--strategy-inventory", default=str(DEFAULT_STRATEGY_INVENTORY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s1 = _read_json(Path(args.s1_closure))
    s2 = _read_json(Path(args.s2_closure))
    s3 = _read_json(Path(args.s3_parking))
    dq = _read_json(Path(args.dq_parking))
    inventory = _read_json(Path(args.strategy_inventory))

    lane_status = [
        {
            "lane": "S1_recall_route_expansion",
            "status": s1["metrics"]["s1_lane_status"],
            "blocking_condition": "no accepted-OSS non-generated recall evidence package; current learnable_slice_count=0",
            "reopen_condition": "accepted OSS top80_missing rows + true recall-failure labels + positive support bucket + loss-audit boundary",
        },
        {
            "lane": "S2_ranking_objective_and_feature_strategy",
            "status": "parked_pending_independent_accepted_oss_evidence",
            "blocking_condition": f"accepted_oss_s2_net={s2['metrics']['accepted_oss_s2_net']}; generated_positive_net_still_blocking={s2['metrics']['generated_positive_net_still_blocking']}",
            "reopen_condition": "accepted OSS positive net > 0, at least two positive accepted source families, generated-source dominance removed, explicit loss audit",
        },
        {
            "lane": "S3_safety_gate_calibration_v2",
            "status": "parked_pending_explicit_go",
            "blocking_condition": f"go_no_go_decision={s3['metrics']['go_no_go_decision']}; explicit_go_present={s3['metrics']['explicit_go_present']}",
            "reopen_condition": "explicit user go plus separate dev/OOF-only S3 what-if execution stage",
        },
        {
            "lane": "DQ_implementation",
            "status": dq["metrics"]["dq_lane_status"],
            "blocking_condition": f"owner_after_values_missing={dq['metrics']['owner_after_values_missing']}; implementation_allowed={dq['metrics']['implementation_allowed']}",
            "reopen_condition": "explicit implementation go plus complete 64-row owner mapping package",
        },
    ]
    route_selection = [
        {
            "route": "continue_existing_learning_lane",
            "decision": "do_not_select",
            "rationale": "S1/S2/S3 have no current re-entry evidence or explicit go; continuing would auto-advance a blocked lane.",
        },
        {
            "route": "restart_training_or_expand_candidate_matrix",
            "decision": "blocked",
            "rationale": "No lane has passed re-entry gates; training or expansion would violate the current evidence boundary.",
        },
        {
            "route": "dq_implementation",
            "decision": "blocked",
            "rationale": "DQ implementation remains parked behind explicit go plus complete owner row mappings.",
        },
        {
            "route": "no_active_learning_lane_evidence_wait_checkpoint",
            "decision": "select_next_read_only_route",
            "rationale": "All known 10.x lanes are parked or blocked; the only valid next step is a read-only checkpoint that consolidates evidence waits and stop conditions.",
        },
    ]
    evidence_wait = [
        {
            "evidence_track": "S1_recall",
            "required_before_reentry": "accepted-OSS non-generated recall evidence package from 10.44",
            "owner_or_source": "accepted human OSS/dev/OOF recall evidence provider",
        },
        {
            "evidence_track": "S2_ranking",
            "required_before_reentry": "accepted OSS positive net > 0 across at least two source families without generated dominance",
            "owner_or_source": "independent accepted OSS evidence provider",
        },
        {
            "evidence_track": "S3_safety_gate",
            "required_before_reentry": "explicit user go for dev/OOF-only what-if execution from locked scope",
            "owner_or_source": "user authorization",
        },
        {
            "evidence_track": "DQ_implementation",
            "required_before_reentry": "explicit implementation go plus complete 64-row owner row mapping package",
            "owner_or_source": "DQ owner",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "auto_advance_learning_stage",
            "reason": "active_learning_lane_count=0 after S1/S2/S3/DQ closure/parking.",
            "allowed_after": "new accepted evidence package or explicit go satisfies a lane-specific re-entry condition",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "10.45 is a read-only broader strategy review and no lane passed re-entry.",
            "allowed_after": "future explicit dev/OOF execution authorization after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "heldout/hard remain validation-only and no candidate is frozen for validation.",
            "allowed_after": "future validation gate, not for selection",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "no implementation lane is open.",
            "allowed_after": "separate validated implementation plan plus explicit go",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "current candidate gains are source-dominated or not re-entry eligible.",
            "allowed_after": "independent accepted-OSS evidence and loss audit pass",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "lane_status_csv": str(output_prefix.with_name(output_prefix.name + "_lane_status.csv")),
        "route_selection_csv": str(output_prefix.with_name(output_prefix.name + "_route_selection.csv")),
        "evidence_wait_contract_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_wait_contract.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    active_learning_lane_count = 0
    metrics = {
        "source_stage": s1["stage"],
        "strategy_inventory_candidate_count": inventory["metrics"]["candidate_count"],
        "active_learning_lane_count": active_learning_lane_count,
        "parked_or_blocked_lane_count": len(lane_status),
        "s1_lane_status": s1["metrics"]["s1_lane_status"],
        "s2_lane_parked": s2["metrics"]["s2_lane_parked"],
        "s3_execution_lane_parked": s3["metrics"]["s3_execution_lane_parked"],
        "dq_lane_status": dq["metrics"]["dq_lane_status"],
        "selected_next_route": "no_active_learning_lane_evidence_wait_checkpoint",
        "return_to_broader_strategy_now": True,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.45 broader 10.x strategy review after S1 closure",
        "read_only": True,
        "strategy_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Do not reopen S1, S2, S3, or DQ implementation. All current 10.x lanes are parked or blocked, and active_learning_lane_count=0. "
            "Select a read-only no-active-learning-lane evidence-wait checkpoint as the next route to consolidate the required inputs and stop conditions."
        ),
        "anti_drift_conclusion": (
            "10.45 only reviews broader strategy status after S1 closure. It does not train, tune, expand candidates, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, or convert DQ backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.46 no-active-learning-lane evidence-wait checkpoint",
            "goal": "Read-only consolidate S1/S2/S3/DQ re-entry requirements and decide whether to pause the 10.x loop until new evidence or explicit go arrives.",
            "default": "pause/evidence-wait unless new accepted evidence package or explicit go is supplied",
        },
    }

    _write_csv(Path(artifacts["lane_status_csv"]), lane_status, ["lane", "status", "blocking_condition", "reopen_condition"])
    _write_csv(Path(artifacts["route_selection_csv"]), route_selection, ["route", "decision", "rationale"])
    _write_csv(Path(artifacts["evidence_wait_contract_csv"]), evidence_wait, ["evidence_track", "required_before_reentry", "owner_or_source"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, lane_status, route_selection, evidence_wait)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
