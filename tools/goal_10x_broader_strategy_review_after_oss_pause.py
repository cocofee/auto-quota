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
DEFAULT_1055_SUMMARY = AGENT_STATE / "goal_10x_oss_provenance_gap_closure_pause_summary.json"
DEFAULT_1055_NEXT = AGENT_STATE / "goal_10x_oss_provenance_gap_closure_pause_next_options.csv"
DEFAULT_LANE_STATUS = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure_lane_status.csv"
DEFAULT_CANDIDATE_LANES = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_candidate_lanes.csv"
DEFAULT_S5_SUMMARY = AGENT_STATE / "goal_10x_s5_artifact_acceptance_gate_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_review_after_oss_pause"


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
    route_decisions: list[dict[str, Any]],
    lane_recheck: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.56 Broader Strategy Review After OSS Pause",
        "",
        "Read-only broader strategy review after the OSS provenance expansion lane was paused.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["oss_lane_paused", metrics["oss_lane_paused"]],
                ["owner_package_present", metrics["owner_package_present"]],
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],
                ["selected_next_lane", metrics["selected_next_lane"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Route Decisions",
        "",
        _md_table(
            [["route", "decision", "rationale"]]
            + [[row["route"], row["decision"], row["rationale"]] for row in route_decisions]
        ),
        "",
        "## Lane Recheck",
        "",
        _md_table(
            [["lane", "status", "blocking_condition", "review_decision"]]
            + [[row["lane"], row["status"], row["blocking_condition"], row["review_decision"]] for row in lane_recheck]
        ),
        "",
        "## Next Gate",
        "",
        _md_table(
            [["gate_item", "requirement", "not_allowed"]]
            + [[row["gate_item"], row["requirement"], row["not_allowed"]] for row in next_gate]
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
    parser = argparse.ArgumentParser(description="Select broader strategy route after OSS lane pause")
    parser.add_argument("--summary-1055", default=str(DEFAULT_1055_SUMMARY))
    parser.add_argument("--next-1055", default=str(DEFAULT_1055_NEXT))
    parser.add_argument("--lane-status", default=str(DEFAULT_LANE_STATUS))
    parser.add_argument("--candidate-lanes", default=str(DEFAULT_CANDIDATE_LANES))
    parser.add_argument("--s5-summary", default=str(DEFAULT_S5_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1055 = _read_json(Path(args.summary_1055))
    next_1055 = _read_csv(Path(args.next_1055))
    lane_status = _read_csv(Path(args.lane_status))
    candidate_lanes = _read_csv(Path(args.candidate_lanes))
    s5_summary = _read_json(Path(args.s5_summary))
    m1055 = summary_1055["metrics"]

    lane_recheck = []
    for row in lane_status:
        lane_recheck.append(
            {
                "lane": row["lane"],
                "status": row["status"],
                "blocking_condition": row["blocking_condition"],
                "review_decision": "remain_parked_or_blocked",
            }
        )
    lane_recheck.append(
        {
            "lane": "OSS_expansion_provenance_lane",
            "status": "paused",
            "blocking_condition": "owner/source provenance package absent; effect_gate_pass_count=0",
            "review_decision": "do_not_continue_without_owner_package",
        }
    )
    lane_recheck.append(
        {
            "lane": "S5_measurement_integrity_slice_telemetry",
            "status": "accepted_as_support_contract",
            "blocking_condition": "satisfies_lane_reentry=false; implementation_allowed=false",
            "review_decision": "keep_as_support_contract_not_execution_lane",
        }
    )

    selected_lane = "S7_rank_position_distribution_diagnostics"
    candidate_by_id = {row["lane_id"]: row for row in candidate_lanes}
    s7 = candidate_by_id.get(selected_lane, {})
    route_decisions = [
        {
            "route": "future_owner_provenance_intake",
            "decision": "not_selected_now",
            "rationale": "No owner/source provenance package is present, so this route has no actionable input.",
        },
        {
            "route": "continue_oss_expansion",
            "decision": "blocked",
            "rationale": "10.55 paused the lane; best candidate hit1 net is negative and provenance is unaccepted.",
        },
        {
            "route": "return_to_broader_strategy_review",
            "decision": "selected",
            "rationale": "The user asked Codex to choose based on the evidence; broader review is the only unblocked read-only route.",
        },
        {
            "route": selected_lane,
            "decision": "selected_next_read_only_lane",
            "rationale": (
                s7.get("why_now")
                or "Rank-position and candidate-pool diagnostics can use existing reports without external provenance, owner mappings, training, or implementation."
            ),
        },
    ]

    next_gate = [
        {
            "gate_item": "scope",
            "requirement": "Use existing dev/OOF rank buckets, candidate-pool size, top80_present/top80_missing separation, and loss slices.",
            "not_allowed": "No ranking objective changes, no candidate matrix expansion, no LTR training.",
        },
        {
            "gate_item": "split_boundary",
            "requirement": "Stay dev/OOF-only for diagnostics; heldout/hard remain validation-only and unused for selection.",
            "not_allowed": "No heldout/hard selection or threshold choice.",
        },
        {
            "gate_item": "evidence_boundary",
            "requirement": "Use S5 support contract fields for source/split/effect decomposition where available.",
            "not_allowed": "Do not convert diagnostics into a Top1 gain claim.",
        },
        {
            "gate_item": "stop_condition",
            "requirement": "If diagnostics require training, new candidate matrices, owner mappings, or provenance packages, stop and report blockers.",
            "not_allowed": "No silent expansion into implementation.",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "continue_oss_expansion_without_owner_package",
            "reason": "10.55 paused OSS expansion and no owner/source provenance package exists.",
            "allowed_after": "owner/source provenance package arrives and passes future read-only intake",
        },
        {
            "blocked_action": "reopen_s1_s2_s3_or_dq_execution",
            "reason": "All known execution/learning lanes remain parked or blocked.",
            "allowed_after": "lane-specific re-entry requirements pass plus explicit go where required",
        },
        {
            "blocked_action": "train_or_tune_from_s7_diagnostics",
            "reason": "S7 is selected only as a read-only diagnostic lane.",
            "allowed_after": "future strategy gate and explicit execution authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard are validation-only.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "route_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_route_decisions.csv")),
        "lane_recheck_csv": str(output_prefix.with_name(output_prefix.name + "_lane_recheck.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1055["stage"],
        "oss_lane_paused": m1055["pause_oss_expansion_lane_now"],
        "owner_package_present": False,
        "broader_strategy_redirect_selected": True,
        "active_learning_lane_count": 0,
        "parked_or_blocked_lane_count": len(lane_recheck),
        "s5_support_contract_accepted": s5_summary["metrics"]["s5_support_contract_accepted"],
        "s5_satisfies_lane_reentry": s5_summary["metrics"]["satisfies_lane_reentry"],
        "selected_next_lane": selected_lane,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.56 broader strategy review after OSS pause",
        "read_only": True,
        "broader_strategy_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Choose return_to_broader_strategy_review because no owner/source provenance package is present and OSS expansion is paused. "
            "For the next read-only lane, select S7 rank-position and candidate-pool diagnostics because it uses existing dev/OOF artifacts and does not depend on owner provenance, owner row mappings, training, implementation, or heldout/hard selection."
        ),
        "anti_drift_conclusion": (
            "10.56 only selects a read-only broader strategy route after OSS pause. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, reopen OSS expansion, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.57 S7 rank-position and candidate-pool diagnostics design gate",
            "goal": "Read-only decide whether S7 diagnostics are concrete enough to inventory rank-position/candidate-pool failure modes from existing dev/OOF artifacts only.",
            "default": "diagnostic design gate only; no training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["route_decisions_csv"]), route_decisions, ["route", "decision", "rationale"])
    _write_csv(Path(artifacts["lane_recheck_csv"]), lane_recheck, ["lane", "status", "blocking_condition", "review_decision"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["gate_item", "requirement", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, route_decisions, lane_recheck, next_gate)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
