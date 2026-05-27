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
DEFAULT_1051_SUMMARY = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review_summary.json"
DEFAULT_1051_CHECKS = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review_reentry_readiness_checks.csv"
DEFAULT_1051_S2 = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review_s2_candidate_readiness.csv"
DEFAULT_1051_S1_GAP = AGENT_STATE / "goal_10x_oss_evidence_package_auto_assembly_review_s1_gap_review.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_oss_evidence_package_no_go_strategy_handoff"


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


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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
    handoff_options: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.52 OSS Evidence Package No-go / Strategy Handoff",
        "",
        "Read-only closure of the assembled OSS evidence package and handoff to the next safe route.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["accepted_oss_source_file_count", metrics["accepted_oss_source_file_count"]],
                ["accepted_oss_source_family_count", metrics["accepted_oss_source_family_count"]],
                ["s2_reentry_ready_candidate_count", metrics["s2_reentry_ready_candidate_count"]],
                ["s1_reentry_ready", metrics["s1_reentry_ready"]],
                ["handoff_decision", metrics["handoff_decision"]],
                ["selected_next_route", metrics["selected_next_route"]],
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
        "## Handoff Options",
        "",
        _md_table(
            [["route", "status", "why", "next_gate"]]
            + [[row["route"], row["status"], row["why"], row["next_gate"]] for row in handoff_options]
        ),
        "",
        "## Re-entry Blockers",
        "",
        _md_table(
            [["lane", "blocker", "evidence", "needed_to_unblock"]]
            + [[row["lane"], row["blocker"], row["evidence"], row["needed_to_unblock"]] for row in blockers]
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
    parser = argparse.ArgumentParser(description="Close 10.51 OSS package as no-go and select safe handoff route")
    parser.add_argument("--summary-1051", default=str(DEFAULT_1051_SUMMARY))
    parser.add_argument("--checks-1051", default=str(DEFAULT_1051_CHECKS))
    parser.add_argument("--s2-readiness-1051", default=str(DEFAULT_1051_S2))
    parser.add_argument("--s1-gap-1051", default=str(DEFAULT_1051_S1_GAP))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1051 = _read_json(Path(args.summary_1051))
    checks_1051 = _read_csv(Path(args.checks_1051))
    s2_readiness = _read_csv(Path(args.s2_readiness_1051))
    s1_gap = _read_csv(Path(args.s1_gap_1051))
    m1051 = summary_1051["metrics"]

    check_pass_count = sum(1 for row in checks_1051 if row.get("status") == "pass")
    check_fail_count = sum(1 for row in checks_1051 if row.get("status") == "fail")
    positive_s2_rows = [row for row in s2_readiness if _int(row.get("accepted_oss_net")) > 0]
    reentry_s2_rows = [row for row in s2_readiness if _bool(row.get("reentry_ready"))]
    best_positive = max(positive_s2_rows, key=lambda row: _int(row.get("accepted_oss_net")), default={})

    closure_decisions = [
        {
            "decision_item": "assembled_oss_package_status",
            "decision": "CLOSE_AS_REENTRY_NO_GO",
            "rationale": "10.51 assembled package rows, but future_reentry_review_allowed=false and no S1/S2 re-entry gate passed.",
        },
        {
            "decision_item": "s2_learning_reentry",
            "decision": "DO_NOT_REENTER_S2",
            "rationale": "s2_reentry_ready_candidate_count=0; the strongest accepted-OSS signal has net=1 and only one positive source_family.",
        },
        {
            "decision_item": "s1_recall_reentry",
            "decision": "DO_NOT_REENTER_S1",
            "rationale": "S1 accepted-OSS rows are taxonomy/coverage cleanup rows, not confirmed true recall failures.",
        },
        {
            "decision_item": "algorithm_execution",
            "decision": "DO_NOT_EXECUTE",
            "rationale": "No training, tuning, implementation, heldout/hard selection, or GoalSearcher change is authorized by this package.",
        },
    ]

    blockers = [
        {
            "lane": "S2 ranking",
            "blocker": "accepted_oss_reentry_gate_failed",
            "evidence": (
                f"s2_reentry_ready_candidate_count={len(reentry_s2_rows)}; "
                f"max_accepted_oss_net={m1051['s2_max_accepted_oss_net']}; "
                f"max_positive_source_family_count={m1051['s2_max_positive_accepted_source_family_count']}"
            ),
            "needed_to_unblock": "accepted OSS positive net across at least two independent source_family, passing loss budget and generated-dominance checks",
        },
        {
            "lane": "S2 diagnostic positive slice",
            "blocker": "weak_single_family_signal",
            "evidence": (
                f"best_candidate={best_positive.get('candidate_id', 'none')}; "
                f"accepted_oss_net={best_positive.get('accepted_oss_net', 0)}; "
                f"overall_hit1_net={best_positive.get('overall_hit1_net', 'n/a')}; "
                f"block_reasons={best_positive.get('block_reasons', 'no_positive_accepted_oss_candidate')}"
            ),
            "needed_to_unblock": "larger accepted-OSS gain with no hidden loss and no single-family/source artifact",
        },
        {
            "lane": "S1 recall",
            "blocker": "taxonomy_cleanup_not_recall_learning",
            "evidence": "; ".join(f"{row['review_item']}={row['value']}" for row in s1_gap),
            "needed_to_unblock": "accepted-OSS non-generated rows proving true missing recall after taxonomy/DQ exclusions",
        },
        {
            "lane": "DQ implementation",
            "blocker": "owner_mapping_package_absent",
            "evidence": "Prior DQ lane remains parked behind explicit go plus complete owner row mappings.",
            "needed_to_unblock": "explicit implementation go plus complete owner-reviewed row mapping package",
        },
    ]

    handoff_options = [
        {
            "route": "10.53 OSS-only evidence expansion inventory",
            "status": "selected_next_read_only_route",
            "why": "User wants Codex to use OSS automatically; this route searches existing OSS/v36 artifacts for additional accepted-source evidence without training or implementing.",
            "next_gate": "Read-only inventory gate: count candidate OSS sources/rows, classify provenance, and decide whether any new evidence can feed future re-entry review.",
        },
        {
            "route": "pause_waiting_for_stronger_accepted_oss_evidence",
            "status": "safe_default_if_no_more_local_oss_artifacts",
            "why": "Current assembled package is insufficient for S1/S2 re-entry.",
            "next_gate": "Resume only when stronger accepted-OSS evidence package exists.",
        },
        {
            "route": "direct_S1_or_S2_execution",
            "status": "blocked",
            "why": "No re-entry-ready S1/S2 evidence package exists.",
            "next_gate": "Requires future read-only re-entry gate before any dev/OOF execution authorization.",
        },
        {
            "route": "DQ_implementation",
            "status": "blocked",
            "why": "Owner row mappings are still missing.",
            "next_gate": "Explicit implementation go plus complete row mapping package.",
        },
    ]

    evidence_requests = [
        {
            "request_id": "REQ_S2_ACCEPTED_OSS_MULTI_FAMILY_GAIN",
            "required_input": "S2 candidate-level dev/OOF gain/loss/net on accepted OSS sources with positive net in at least two independent source_family.",
            "acceptance_check": "positive accepted OSS net, loss budget pass, generated-source dominance false, no heldout/hard selection",
        },
        {
            "request_id": "REQ_S1_TRUE_RECALL_FAILURE",
            "required_input": "Accepted OSS non-generated top80_missing rows that remain true recall failures after taxonomy/DQ exclusions.",
            "acceptance_check": "not taxonomy cleanup, not generated/global artifact, sufficient support, clear loss audit boundary",
        },
        {
            "request_id": "REQ_DQ_OWNER_MAPPING",
            "required_input": "Explicit go plus complete owner after-values for plan-ready DQ row mappings.",
            "acceptance_check": "all affected rows mapped, rollback path defined, validation boundary explicit",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "train_or_tune_ranking_from_10_51_package",
            "reason": "S2 re-entry gate failed.",
            "allowed_after": "future accepted-OSS re-entry review passes",
        },
        {
            "blocked_action": "implement_recall_or_goal_searcher_change",
            "reason": "S1 true recall evidence is absent and no implementation go exists.",
            "allowed_after": "future S1 re-entry plus explicit implementation authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "Heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_general_top1_gain_from_oss_package",
            "reason": "10.51 only produced provenance/package evidence, not validated general accuracy gain.",
            "allowed_after": "future approved candidate passes full validation boundary",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_closure_decisions.csv")),
        "reentry_blockers_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_blockers.csv")),
        "handoff_options_csv": str(output_prefix.with_name(output_prefix.name + "_handoff_options.csv")),
        "evidence_requests_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_requests.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1051["stage"],
        "accepted_oss_source_file_count": m1051["accepted_oss_source_file_count"],
        "accepted_oss_source_family_count": m1051["accepted_oss_source_family_count"],
        "readiness_check_pass_count": check_pass_count,
        "readiness_check_fail_count": check_fail_count,
        "s2_positive_accepted_oss_net_candidate_count": m1051["s2_positive_accepted_oss_net_candidate_count"],
        "s2_max_accepted_oss_net": m1051["s2_max_accepted_oss_net"],
        "s2_max_positive_accepted_source_family_count": m1051["s2_max_positive_accepted_source_family_count"],
        "s2_reentry_ready_candidate_count": len(reentry_s2_rows),
        "s1_accepted_oss_package_row_count": m1051["s1_accepted_oss_package_row_count"],
        "s1_reentry_ready": m1051["s1_reentry_ready"],
        "handoff_decision": "close_package_no_go_and_select_oss_only_inventory",
        "selected_next_route": "10.53 OSS-only evidence expansion inventory",
        "future_reentry_review_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.52 OSS evidence package no-go / strategy handoff",
        "read_only": True,
        "no_go_handoff_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Close the 10.51 assembled OSS evidence package as re-entry no-go for S1 and S2. "
            "Because the user wants Codex to keep using OSS automatically, the selected next read-only route is 10.53 OSS-only evidence expansion inventory: look for more usable local OSS/v36 evidence before asking for training or implementation."
        ),
        "anti_drift_conclusion": (
            "10.52 only closes and hands off the evidence package. It does not train, tune, expand the ranking candidate matrix, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, or claim Top1 gain from package/provenance evidence."
        ),
        "next_stage": {
            "stage": "10.53 OSS-only evidence expansion inventory",
            "goal": "Read-only inventory existing local OSS/v36 artifacts for additional accepted-source evidence candidates, without training, tuning, implementation, or heldout/hard selection.",
            "default": "read-only inventory only",
        },
    }

    _write_csv(Path(artifacts["closure_decisions_csv"]), closure_decisions, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["reentry_blockers_csv"]), blockers, ["lane", "blocker", "evidence", "needed_to_unblock"])
    _write_csv(Path(artifacts["handoff_options_csv"]), handoff_options, ["route", "status", "why", "next_gate"])
    _write_csv(Path(artifacts["evidence_requests_csv"]), evidence_requests, ["request_id", "required_input", "acceptance_check"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_decisions, handoff_options, blockers)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
