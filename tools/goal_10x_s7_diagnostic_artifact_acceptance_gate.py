from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1058_SUMMARY = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_summary.json"
DEFAULT_RANK_POSITION = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_rank_position_distribution.csv"
DEFAULT_POOL_BOUNDARY = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_candidate_pool_boundary.csv"
DEFAULT_LOSS_MAP = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_loss_concentration_map.csv"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_diagnostic_readiness_checks.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_acceptance_gate"


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


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
    acceptance_results: list[dict[str, Any]],
    strategy_support: list[dict[str, Any]],
    next_options: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.59 S7 Diagnostic Artifact Acceptance Gate",
        "",
        "Read-only acceptance gate for S7 diagnostic artifacts as future strategy support.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
                ["s7_artifacts_accepted_for_strategy_support", metrics["s7_artifacts_accepted_for_strategy_support"]],
                ["satisfies_learning_reentry", metrics["satisfies_learning_reentry"]],
                ["training_allowed", metrics["training_allowed"]],
                ["selected_next_route", metrics["selected_next_route"]],
            ]
        ),
        "",
        "## Acceptance Results",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_results]
        ),
        "",
        "## Strategy Support",
        "",
        _md_table(
            [["support_item", "accepted_use", "not_allowed"]]
            + [[row["support_item"], row["accepted_use"], row["not_allowed"]] for row in strategy_support]
        ),
        "",
        "## Next Options",
        "",
        _md_table(
            [["option", "status", "rationale"]]
            + [[row["option"], row["status"], row["rationale"]] for row in next_options]
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
    parser = argparse.ArgumentParser(description="Accept S7 diagnostic artifacts as future strategy support")
    parser.add_argument("--summary-1058", default=str(DEFAULT_1058_SUMMARY))
    parser.add_argument("--rank-position", default=str(DEFAULT_RANK_POSITION))
    parser.add_argument("--pool-boundary", default=str(DEFAULT_POOL_BOUNDARY))
    parser.add_argument("--loss-map", default=str(DEFAULT_LOSS_MAP))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1058 = _read_json(Path(args.summary_1058))
    rank_position = _read_csv(Path(args.rank_position))
    pool_boundary = _read_csv(Path(args.pool_boundary))
    loss_map = _read_csv(Path(args.loss_map))
    readiness = _read_csv(Path(args.readiness))
    blocked_input = _read_csv(Path(args.blocked_actions))
    m1058 = summary_1058["metrics"]

    readiness_fail = [row for row in readiness if row.get("status") != "pass"]
    rank_input_rows = _int(m1058.get("wrong_rank_input_rows"))
    rank_bucket_count_sum = sum(_int(row.get("count")) for row in rank_position)
    loss_visible_rows = [row for row in loss_map if row.get("diagnostic_flag") == "loss_visible"]
    net_only_risky_rows = [row for row in loss_map if "loss" not in row or row.get("loss") == ""]
    boundary_classes = Counter(row.get("boundary_class", "") for row in pool_boundary)
    dev_boundary_rows = [
        row
        for row in pool_boundary
        if row.get("source") == "dev_oof_recall_boundary" and row.get("split") == "dev"
    ]
    has_dev_boundary = any(_int(row.get("top80_present_groups")) > 0 and _int(row.get("top80_missing_groups")) > 0 for row in dev_boundary_rows)
    has_no_selection_flag = all(row.get("forbidden_use") == "do_not_select_or_freeze_candidate_from_diagnostic" for row in loss_map)
    top_loss = loss_map[0] if loss_map else {}
    top_rank = rank_position[0] if rank_position else {}
    top_pool_dev = next((row for row in pool_boundary if row.get("source") == "9x_gap_rows" and row.get("split") == "dev"), {})

    acceptance_results = [
        {
            "check_id": "AC01_ARTIFACTS_PRESENT",
            "status": "pass" if rank_position and pool_boundary and loss_map and readiness else "fail",
            "evidence": f"rank_position_rows={len(rank_position)}; pool_boundary_rows={len(pool_boundary)}; loss_rows={len(loss_map)}; readiness_rows={len(readiness)}",
            "decision": "All four S7 artifact families are present.",
        },
        {
            "check_id": "AC02_READINESS_PASSED",
            "status": "pass" if not readiness_fail else "fail",
            "evidence": f"readiness_fail_count={len(readiness_fail)}",
            "decision": "10.58 readiness checks all passed.",
        },
        {
            "check_id": "AC03_RANK_POSITION_COVERAGE",
            "status": "pass" if rank_bucket_count_sum == rank_input_rows else "fail",
            "evidence": f"rank_bucket_count_sum={rank_bucket_count_sum}; wrong_rank_input_rows={rank_input_rows}",
            "decision": "Every wrong-rank input row is represented in rank-position distribution.",
        },
        {
            "check_id": "AC04_POOL_BOUNDARY_PRESERVED",
            "status": "pass" if boundary_classes.get("top80_present_wrong_rank", 0) and boundary_classes.get("top80_missing", 0) and has_dev_boundary else "fail",
            "evidence": f"boundary_classes={dict(boundary_classes)}; has_dev_boundary={has_dev_boundary}",
            "decision": "Ranking-position failures remain separate from candidate-pool/recall ceiling failures.",
        },
        {
            "check_id": "AC05_LOSS_VISIBLE",
            "status": "pass" if loss_visible_rows and not net_only_risky_rows else "fail",
            "evidence": f"loss_visible_rows={len(loss_visible_rows)}; net_only_risky_rows={len(net_only_risky_rows)}",
            "decision": "Loss rows are visible and diagnostics avoid net-only claims.",
        },
        {
            "check_id": "AC06_NON_EXECUTION_CONTRACT",
            "status": "pass" if has_no_selection_flag and not m1058.get("training_allowed") and not m1058.get("implementation_allowed") else "fail",
            "evidence": f"has_no_selection_flag={has_no_selection_flag}; training_allowed={m1058.get('training_allowed')}; implementation_allowed={m1058.get('implementation_allowed')}",
            "decision": "Artifacts are accepted as diagnostics only, not candidate selection or implementation authority.",
        },
    ]
    fail_count = sum(1 for row in acceptance_results if row["status"] != "pass")

    strategy_support = [
        {
            "support_item": "rank_position_distribution",
            "accepted_use": "Use to identify whether failures are near-miss rank_2_5, deeper rank errors, same-family wrong-rank, query_family_empty, or book-relation issues.",
            "not_allowed": "Do not convert rank buckets directly into thresholds, boosts, or rules.",
        },
        {
            "support_item": "candidate_pool_boundary",
            "accepted_use": "Use to preserve top80_present vs top80_missing boundary and avoid claiming ranking can fix missing positives.",
            "not_allowed": "Do not count top80_missing rows as ranking-objective wins.",
        },
        {
            "support_item": "loss_concentration_map",
            "accepted_use": "Use to locate source/taxonomy/family/book slices where losses concentrate before any future strategy proposal.",
            "not_allowed": "Do not freeze or select a candidate from diagnostic loss slices alone.",
        },
        {
            "support_item": "future_strategy_prioritization",
            "accepted_use": "Use as support for selecting a future non-execution strategy lane or defining a new evidence requirement.",
            "not_allowed": "Do not claim Top1 gain, train, tune, expand candidate matrix, or change GoalSearcher.",
        },
    ]

    diagnostic_implications = [
        {
            "finding": "top_dev_wrong_rank_boundary",
            "evidence": f"{top_pool_dev.get('boundary_class', '')}:{top_pool_dev.get('reason', '')} groups={top_pool_dev.get('groups', '')}",
            "implication": "S7 can support deciding whether future work should target ranking-position failure modes or candidate-pool/recall ceiling first.",
            "execution_status": "strategy_support_only",
        },
        {
            "finding": "top_rank_position_bucket",
            "evidence": f"{top_rank.get('split', '')}/{top_rank.get('rank_bucket', '')}/{top_rank.get('reason', '')} count={top_rank.get('count', '')}",
            "implication": "High-volume rank buckets are diagnosable, but still mixed with taxonomy/source dispositions.",
            "execution_status": "strategy_support_only",
        },
        {
            "finding": "top_loss_concentration",
            "evidence": f"{top_loss.get('candidate_id', '')} {top_loss.get('slice_dimension', '')}={top_loss.get('slice_key', '')} loss={top_loss.get('loss', '')}",
            "implication": "Loss concentration remains visible and source/taxonomy risk must be separated before any learning re-entry.",
            "execution_status": "strategy_support_only",
        },
    ]

    next_options = [
        {
            "option": "10.60 S7 diagnostic implications and next-lane selection",
            "status": "selected_next_read_only_route",
            "rationale": "Artifacts are acceptable as support; next read-only step should decide what strategy lane they justify, without execution.",
        },
        {
            "option": "train_or_tune_from_s7",
            "status": "blocked",
            "rationale": "S7 artifacts are diagnostics, not a learning objective or execution authorization.",
        },
        {
            "option": "candidate_pool_or_retrieval_change",
            "status": "blocked",
            "rationale": "Candidate-pool boundary is explanatory only; implementation would need a separate plan and explicit go.",
        },
        {
            "option": "heldout_or_hard_validation",
            "status": "blocked",
            "rationale": "No candidate is frozen or selected; heldout/hard remain validation-only.",
        },
    ]

    blocked_actions = blocked_input + [
        {
            "blocked_action": "use_s7_acceptance_as_learning_reentry",
            "reason": "Acceptance is for strategy support only and satisfies_learning_reentry=false.",
            "allowed_after": "future lane-specific re-entry review passes with valid evidence",
        },
        {
            "blocked_action": "turn_diagnostics_into_rules",
            "reason": "Rank-position and pool diagnostics do not define implementation mappings.",
            "allowed_after": "future implementation plan with explicit go",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_results_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_results.csv")),
        "strategy_support_scope_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_support_scope.csv")),
        "diagnostic_implications_csv": str(output_prefix.with_name(output_prefix.name + "_diagnostic_implications.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1058["stage"],
        "acceptance_pass_count": len(acceptance_results) - fail_count,
        "acceptance_fail_count": fail_count,
        "rank_position_rows": len(rank_position),
        "candidate_pool_boundary_rows": len(pool_boundary),
        "loss_concentration_rows": len(loss_map),
        "rank_bucket_count_sum": rank_bucket_count_sum,
        "wrong_rank_input_rows": rank_input_rows,
        "loss_visible_rows": len(loss_visible_rows),
        "net_only_risky_rows": len(net_only_risky_rows),
        "s7_artifacts_accepted_for_strategy_support": fail_count == 0,
        "satisfies_learning_reentry": False,
        "selected_next_route": "10.60 S7 diagnostic implications and next-lane selection",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.59 S7 diagnostic artifact acceptance gate",
        "read_only": True,
        "acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the S7 diagnostic artifacts as future strategy support only. They preserve rank-position, candidate-pool, loss visibility, and non-execution boundaries. "
            "They do not satisfy learning re-entry, authorize training, change retrieval/candidate pools, select candidates, run heldout/hard validation, or change GoalSearcher."
        ),
        "anti_drift_conclusion": (
            "10.59 only accepts S7 artifacts as diagnostic support. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.60 S7 diagnostic implications and next-lane selection",
            "goal": "Read-only interpret accepted S7 diagnostics and choose the next non-execution strategy lane or stop condition.",
            "default": "strategy implication review only; no training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["acceptance_results_csv"]), acceptance_results, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["strategy_support_scope_csv"]), strategy_support, ["support_item", "accepted_use", "not_allowed"])
    _write_csv(Path(artifacts["diagnostic_implications_csv"]), diagnostic_implications, ["finding", "evidence", "implication", "execution_status"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_results, strategy_support, next_options)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
