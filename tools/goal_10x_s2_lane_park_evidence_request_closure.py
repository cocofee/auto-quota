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
DEFAULT_GAP_SUMMARY = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review_summary.json"
DEFAULT_GAP_REASONS = AGENT_STATE / "goal_10x_s2_accepted_oss_evidence_gap_review_gap_reasons.csv"
DEFAULT_REENTRY_BLOCKERS = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_reentry_blockers.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_lane_park_evidence_request_closure"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any], park_decisions: list[dict[str, Any]], evidence_requests: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.29 S2 lane park / evidence request closure",
        "",
        "Read-only closure for the S2 lane after accepted-OSS evidence gap review.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s2_lane_parked", metrics["s2_lane_parked"]],
                ["accepted_oss_s2_net", metrics["accepted_oss_s2_net"]],
                ["accepted_oss_positive_source_family_count", metrics["accepted_oss_positive_source_family_count"]],
                ["generated_positive_net_still_blocking", metrics["generated_positive_net_still_blocking"]],
                ["future_evidence_request_count", metrics["future_evidence_request_count"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Park Decisions",
        "",
        _md_table(
            [["decision_item", "decision", "rationale"]]
            + [[row["decision_item"], row["decision"], row["rationale"]] for row in park_decisions]
        ),
        "",
        "## Future Evidence Requests",
        "",
        _md_table(
            [["request_id", "required_content", "acceptance_check"]]
            + [[row["request_id"], row["required_content"], row["acceptance_check"]] for row in evidence_requests]
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
    parser = argparse.ArgumentParser(description="Close S2 lane by parking it and requesting future evidence")
    parser.add_argument("--gap-summary", default=str(DEFAULT_GAP_SUMMARY))
    parser.add_argument("--gap-reasons", default=str(DEFAULT_GAP_REASONS))
    parser.add_argument("--reentry-blockers", default=str(DEFAULT_REENTRY_BLOCKERS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    gap_summary = _read_json(Path(args.gap_summary))
    gap_metrics = gap_summary["metrics"]
    gap_reasons = _read_csv(Path(args.gap_reasons))
    reentry_blockers = _read_csv(Path(args.reentry_blockers))

    park_decisions = [
        {
            "decision_item": "s2_lane_status",
            "decision": "PARK_S2_LANE",
            "rationale": "Accepted OSS source provenance exists, but accepted OSS S2 net is 0 and positive source families are 0.",
        },
        {
            "decision_item": "learning_reentry_now",
            "decision": "DO_NOT_OPEN_REENTRY_REVIEW",
            "rationale": "Source provenance acceptance is not enough; current S2 positive net is generated-source dominated.",
        },
        {
            "decision_item": "next_route",
            "decision": "RETURN_TO_REMAINING_DQ_ARTIFACT_BACKLOG_OR_WAIT_FOR_EVIDENCE",
            "rationale": "The remaining useful work is evidence intake or DQ artifacts, not S2 execution.",
        },
        {
            "decision_item": "future_s2_restart",
            "decision": "REQUIRE_NEW_ACCEPTED_OSS_EVIDENCE_PACKAGE",
            "rationale": "Future S2 review must show accepted OSS positive net > 0 across at least two accepted source families without generated dominance.",
        },
    ]
    evidence_requests = [
        {
            "request_id": "S2_ACCEPTED_OSS_POSITIVE_NET",
            "required_content": "dev/OOF-only evidence where accepted human OSS sources show gain > loss and net > 0",
            "acceptance_check": "accepted_oss_s2_gain > accepted_oss_s2_loss; accepted_oss_s2_net > 0; generated rows excluded",
            "source_scope": "accepted v36_oss_* or newly owner-accepted human OSS source provenance",
            "forbidden": "heldout/hard selection, generated repair-decision rows, taxonomy-empty artifacts counted as gain",
        },
        {
            "request_id": "S2_ACCEPTED_OSS_POSITIVE_SOURCE_FAMILIES",
            "required_content": "positive net appears in at least two independent accepted OSS source families",
            "acceptance_check": "accepted_oss_positive_source_family_count >= 2",
            "source_scope": "source_family independence, not duplicate filenames from one pipeline family",
            "forbidden": "counting v36_oss_r3 variants as multiple independent families without owner acceptance",
        },
        {
            "request_id": "S2_GENERATED_SHARE_NOT_DOMINANT",
            "required_content": "generated-source positive net share no longer dominates the S2 claim",
            "acceptance_check": "generated_positive_net_share <= 0.5 or generated sources fully excluded from the claim",
            "source_scope": "dev/OOF evidence only",
            "forbidden": "using global_repair_decision_table.csv to support general Top1 gain",
        },
        {
            "request_id": "S2_ACCEPTED_OSS_LOSS_AUDIT",
            "required_content": "loss slices for accepted OSS evidence, including source_family/query_family/top1_family/province",
            "acceptance_check": "loss budget explicit; no hidden regression on accepted OSS slices",
            "source_scope": "same dev/OOF accepted OSS evidence package",
            "forbidden": "net-only summary without loss rows",
        },
        {
            "request_id": "REMAINING_DQ_ARTIFACTS",
            "required_content": "query_family_empty coverage, top1_family coverage, and label/taxonomy mixture separation artifacts",
            "acceptance_check": "accepted DQ artifacts are completed before learning re-entry review",
            "source_scope": "DQ backlog route",
            "forbidden": "treating backlog rows as learning evidence before acceptance",
        },
    ]
    route_options = [
        {
            "route_option": "wait_for_s2_evidence_package",
            "status": "allowed_read_only_wait_state",
            "description": "Pause S2 until a future accepted-OSS evidence package satisfies the evidence requests.",
        },
        {
            "route_option": "return_to_remaining_dq_backlog",
            "status": "recommended_if_continuing_now",
            "description": "Continue only with non-learning DQ artifacts: query_family_empty, top1_family coverage, label/taxonomy mixture.",
        },
        {
            "route_option": "execute_or_train_s2",
            "status": "blocked",
            "description": "No accepted-OSS positive evidence exists, so execution/training would be premature.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "reopen_s2_learning_reentry_now",
            "reason": "Accepted OSS S2 net is 0 and positive source family count is 0.",
            "allowed_after": "future accepted-OSS evidence package passes evidence request checks",
        },
        {
            "blocked_action": "train_tune_or_expand_s2_candidates",
            "reason": "10.29 is a closure/evidence-request stage, not execution authorization.",
            "allowed_after": "explicit future execution authorization after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No frozen validation candidate exists and source robustness failed.",
            "allowed_after": "future validation gate after accepted-OSS source robustness pass",
        },
        {
            "blocked_action": "claim_s2_general_top1_gain",
            "reason": "The only positive S2 net remains generated-source dominated.",
            "allowed_after": "future independent accepted-OSS positive-net review",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "park_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_park_decisions.csv")),
        "future_evidence_requests_csv": str(output_prefix.with_name(output_prefix.name + "_future_evidence_requests.csv")),
        "route_options_csv": str(output_prefix.with_name(output_prefix.name + "_route_options.csv")),
        "carry_forward_blockers_csv": str(output_prefix.with_name(output_prefix.name + "_carry_forward_blockers.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "candidate_id": gap_metrics["candidate_id"],
        "s2_lane_parked": True,
        "accepted_oss_s2_groups": gap_metrics["accepted_oss_s2_groups"],
        "accepted_oss_s2_gain": gap_metrics["accepted_oss_s2_gain"],
        "accepted_oss_s2_loss": gap_metrics["accepted_oss_s2_loss"],
        "accepted_oss_s2_net": gap_metrics["accepted_oss_s2_net"],
        "accepted_oss_positive_source_family_count": gap_metrics["accepted_oss_positive_source_family_count"],
        "generated_positive_net_still_blocking": gap_metrics["generated_positive_net_still_blocking"],
        "future_evidence_request_count": len(evidence_requests),
        "carry_forward_blocker_count": len(reentry_blockers),
        "gap_reason_count": len(gap_reasons),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.29 S2 lane park and evidence request closure",
        "read_only": True,
        "closure_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Formally park the S2 lane. Future S2 re-entry requires a new dev/OOF-only accepted-OSS evidence package with positive net > 0, "
            "at least two positive accepted source families, generated-source dominance removed, and explicit loss audit. Until then, continue waiting or work only on remaining DQ artifacts."
        ),
        "anti_drift_conclusion": (
            "10.29 is a closure and evidence-request stage. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or claim S2 general Top1 gain."
        ),
        "next_stage": {
            "stage": "10.30 remaining DQ artifact backlog route selection",
            "goal": "Read-only choose whether to work on query_family_empty coverage, top1_family coverage, or label/taxonomy mixture separation while S2 remains parked.",
            "default": "return_to_remaining_dq_backlog_unless_new_accepted_oss_evidence_arrives",
        },
    }

    _write_csv(Path(artifacts["park_decisions_csv"]), park_decisions, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["future_evidence_requests_csv"]), evidence_requests, ["request_id", "required_content", "acceptance_check", "source_scope", "forbidden"])
    _write_csv(Path(artifacts["route_options_csv"]), route_options, ["route_option", "status", "description"])
    _write_csv(Path(artifacts["carry_forward_blockers_csv"]), reentry_blockers, ["blocker", "status", "why_it_blocks_reentry"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, park_decisions, evidence_requests)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
