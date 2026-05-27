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
DEFAULT_S1_REVIEW = AGENT_STATE / "goal_10x_s1_recall_route_evidence_inventory_reentry_review_summary.json"
DEFAULT_S1_REQUESTS = AGENT_STATE / "goal_10x_s1_recall_route_evidence_inventory_reentry_review_evidence_requests.csv"
DEFAULT_S1_ROUTES = AGENT_STATE / "goal_10x_s1_recall_route_evidence_inventory_reentry_review_route_options.csv"
DEFAULT_S1_BLOCKED = AGENT_STATE / "goal_10x_s1_recall_route_evidence_inventory_reentry_review_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s1_independent_recall_evidence_request_broader_strategy_closure"


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
    closure_decisions: list[dict[str, Any]],
    evidence_package: list[dict[str, Any]],
    next_route: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.44 S1 Independent Recall Evidence Request / Broader Strategy Closure",
        "",
        "Read-only closure for the current S1 lane, with a future evidence package contract.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s1_lane_status", metrics["s1_lane_status"]],
                ["s1_learning_reentry_allowed_now", metrics["s1_learning_reentry_allowed_now"]],
                ["s1_internal_learning_lane_exists_now", metrics["s1_internal_learning_lane_exists_now"]],
                ["future_s1_evidence_request_count", metrics["future_s1_evidence_request_count"]],
                ["return_to_broader_strategy_now", metrics["return_to_broader_strategy_now"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Closure Decisions",
        "",
        _md_table(
            [["decision_item", "decision", "rationale"]]
            + [[row["decision_item"], row["decision"], row["rationale"]] for row in closure_decisions]
        ),
        "",
        "## Future S1 Evidence Package",
        "",
        _md_table(
            [["package_item", "required_content", "acceptance_check", "forbidden"]]
            + [
                [row["package_item"], row["required_content"], row["acceptance_check"], row["forbidden"]]
                for row in evidence_package
            ]
        ),
        "",
        "## Next Route",
        "",
        _md_table(
            [["route", "status", "rationale"]]
            + [[row["route"], row["status"], row["rationale"]] for row in next_route]
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
    parser = argparse.ArgumentParser(description="Close S1 current lane and request independent accepted-OSS recall evidence")
    parser.add_argument("--s1-review", default=str(DEFAULT_S1_REVIEW))
    parser.add_argument("--s1-requests", default=str(DEFAULT_S1_REQUESTS))
    parser.add_argument("--s1-routes", default=str(DEFAULT_S1_ROUTES))
    parser.add_argument("--s1-blocked", default=str(DEFAULT_S1_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s1_review = _read_json(Path(args.s1_review))
    s1_metrics = s1_review["metrics"]
    s1_requests = _read_csv(Path(args.s1_requests))
    s1_routes = _read_csv(Path(args.s1_routes))
    s1_blocked = _read_csv(Path(args.s1_blocked))

    evidence_package = [
        {
            "package_item": row["request_id"],
            "required_content": row["required_content"],
            "acceptance_check": row["acceptance_check"],
            "forbidden": "generated/global repair-decision rows; heldout/hard selection; DQ backlog rows counted as recall gain",
        }
        for row in s1_requests
    ]
    closure_decisions = [
        {
            "decision_item": "current_s1_learning_reentry",
            "decision": "CLOSE_CURRENT_REENTRY",
            "rationale": "10.43 found no current learnable or executable S1 lane; independent non-generated recall evidence is absent.",
        },
        {
            "decision_item": "future_s1_reopen_condition",
            "decision": "REQUEST_ACCEPTED_OSS_RECALL_EVIDENCE_PACKAGE",
            "rationale": "S1 can be reconsidered only after accepted human OSS, non-generated dev/OOF recall evidence is supplied.",
        },
        {
            "decision_item": "owner_row_mapping_dependency",
            "decision": "NOT_REQUIRED_FOR_S1",
            "rationale": "The 64 owner row mappings block DQ implementation, not S1 evidence intake.",
        },
        {
            "decision_item": "broader_strategy_return",
            "decision": "RETURN_TO_BROADER_10X_STRATEGY",
            "rationale": "No valid S1 evidence package exists now, so continuing the S1 learning lane would be automatic drift.",
        },
    ]
    next_route = [
        {
            "route": "S1_wait_for_evidence_package",
            "status": "parked_pending_external_or_upstream_evidence",
            "rationale": "Future S1 review needs the evidence package defined in this stage.",
        },
        {
            "route": "broader_10x_strategy_review",
            "status": "selected_next_read_only_route",
            "rationale": "Current S1/S2/S3/DQ implementation lanes are parked or blocked, so next work should choose a non-execution strategy lane.",
        },
        {
            "route": "S1_training_or_rule_implementation",
            "status": "blocked",
            "rationale": "No accepted-OSS positive recall slice exists and no explicit execution/implementation go exists.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "continue_s1_learning_without_new_evidence",
            "reason": "10.43 found learnable_slice_count=0 and independent_non_generated_recall_evidence_available_now=false.",
            "allowed_after": "future accepted-OSS non-generated recall evidence package passes intake checks",
        },
        {
            "blocked_action": "train_tune_or_expand_recall_candidates",
            "reason": "10.44 is a read-only closure/request stage.",
            "allowed_after": "separate future dev/OOF execution authorization after re-entry gates pass",
        },
        {
            "blocked_action": "write_recall_rules_or_change_goal_searcher",
            "reason": "No implementation authorization exists and current evidence is not transferable.",
            "allowed_after": "validated offline plan plus explicit implementation go",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard cannot be used for selection or tuning in this loop.",
            "allowed_after": "future validation-only gate after candidate freeze, not for selection",
        },
        {
            "blocked_action": "resume_dq_implementation",
            "reason": "DQ implementation remains parked behind explicit go plus 64 owner row mappings.",
            "allowed_after": "explicit implementation go plus complete owner row mapping package",
        },
    ] + s1_blocked

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_closure_decisions.csv")),
        "future_evidence_package_csv": str(output_prefix.with_name(output_prefix.name + "_future_evidence_package.csv")),
        "next_route_csv": str(output_prefix.with_name(output_prefix.name + "_next_route.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
        "carried_forward_route_options_csv": str(output_prefix.with_name(output_prefix.name + "_carried_forward_route_options.csv")),
    }
    metrics = {
        "source_stage": s1_review["stage"],
        "s1_lane_status": "parked_pending_independent_evidence",
        "s1_learning_reentry_allowed_now": False,
        "s1_internal_learning_lane_exists_now": s1_metrics["s1_internal_learning_lane_exists_now"],
        "independent_non_generated_recall_evidence_available_now": s1_metrics["independent_non_generated_recall_evidence_available_now"],
        "dev_top80_missing": s1_metrics["dev_top80_missing"],
        "stage_9_30_generated_rows": s1_metrics["stage_9_30_generated_rows"],
        "stage_9_30_non_global_rows": s1_metrics["stage_9_30_non_global_rows"],
        "learnable_slice_count": s1_metrics["learnable_slice_count"],
        "future_s1_evidence_request_count": len(evidence_package),
        "owner_row_mappings_required_for_s1": False,
        "return_to_broader_strategy_now": True,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.44 S1 independent recall evidence request and broader strategy closure",
        "read_only": True,
        "closure_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Close the current S1 learning re-entry and park S1 pending a future accepted-OSS, non-generated recall evidence package. "
            "The package must include accepted OSS top80_missing rows, true recall-failure labels after DQ/taxonomy exclusions, a positive support bucket, and a loss-audit boundary. "
            "Because no such package exists now, return to broader 10.x strategy review instead of continuing S1 automatically."
        ),
        "anti_drift_conclusion": (
            "10.44 only closes the current S1 lane and writes a future evidence request package. It does not train, tune, expand recall candidates, run heldout/hard selection, "
            "write recall rules, change GoalSearcher, edit feature whitelists, implement DQ fixes, or convert DQ backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.45 broader 10.x strategy review after S1 closure",
            "goal": "Read-only choose the next non-execution strategy lane now that S1 is parked pending independent recall evidence.",
            "default": "do_not_execute_or_train; choose only a read-only strategy review lane unless new evidence or explicit go is supplied",
        },
    }

    _write_csv(Path(artifacts["closure_decisions_csv"]), closure_decisions, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["future_evidence_package_csv"]), evidence_package, ["package_item", "required_content", "acceptance_check", "forbidden"])
    _write_csv(Path(artifacts["next_route_csv"]), next_route, ["route", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_csv(Path(artifacts["carried_forward_route_options_csv"]), s1_routes, ["route_option", "status", "rationale"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_decisions, evidence_package, next_route)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
