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
DEFAULT_1062_SUMMARY = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_summary.json"
DEFAULT_INVENTORY = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_failure_mode_inventory.csv"
DEFAULT_ROLLUP = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_failure_mode_rollup.csv"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_candidate_fix_readiness.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_inventory_artifact_acceptance_gate"


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
    acceptance_checks: list[dict[str, Any]],
    planning_candidates: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.63 S6 Inventory Artifact Acceptance Gate",
        "",
        "Read-only acceptance gate for S6 parser/query normalization inventory artifacts.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s6_inventory_accepted_for_fix_support", metrics["s6_inventory_accepted_for_fix_support"]],
                ["future_planning_gate_allowed", metrics["future_planning_gate_allowed"]],
                ["planning_candidate_rows", metrics["planning_candidate_rows"]],
                ["source_generated_or_risky_rows", metrics["source_generated_or_risky_rows"]],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
            ]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_checks]
        ),
        "",
        "## Planning Candidate Preview",
        "",
        _md_table(
            [["planning_bucket", "rows", "dominant_failure_modes", "planning_boundary"]]
            + [[row["planning_bucket"], row["rows"], row["dominant_failure_modes"], row["planning_boundary"]] for row in planning_candidates]
        ),
        "",
        "## Next Gate",
        "",
        _md_table(
            [["next_stage", "status", "scope", "not_allowed"]]
            + [[row["next_stage"], row["status"], row["scope"], row["not_allowed"]] for row in next_gate]
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
    parser = argparse.ArgumentParser(description="Accept S6 inventory artifacts as future parser/taxonomy fix support")
    parser.add_argument("--summary-1062", default=str(DEFAULT_1062_SUMMARY))
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--rollup", default=str(DEFAULT_ROLLUP))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1062 = _read_json(Path(args.summary_1062))
    inventory = _read_csv(Path(args.inventory))
    rollup = _read_csv(Path(args.rollup))
    readiness = _read_csv(Path(args.readiness))
    blocked_input = _read_csv(Path(args.blocked_actions))
    m1062 = summary_1062["metrics"]

    future_candidates = [row for row in inventory if row.get("future_fix_candidate") == "true"]
    future_candidate_modes = Counter(row.get("failure_mode", "") for row in future_candidates)
    future_candidate_sources = Counter(row.get("source_family", "") for row in future_candidates)
    blocked_source_rows = next((row for row in readiness if row.get("readiness_bucket") == "blocked_source_generated_or_dominated"), {})
    future_owner_rows = next((row for row in readiness if row.get("readiness_bucket") == "future_owner_review_candidate"), {})
    all_rows_non_learning = all(row.get("learning_use_allowed") == "false" for row in inventory)
    all_rows_non_implementation = all(row.get("implementation_allowed") == "false" for row in inventory)
    has_required_columns = bool(inventory) and {
        "inventory_id",
        "failure_mode",
        "future_fix_candidate",
        "review_status",
        "learning_use_allowed",
        "implementation_allowed",
    }.issubset(inventory[0].keys())
    rollup_rows_sum = sum(_int(row.get("rows")) for row in rollup)
    readiness_rows_sum = sum(_int(row.get("rows")) for row in readiness)
    source_risky_rows = _int(m1062.get("source_generated_or_risky_rows"))
    inventory_rows = _int(m1062.get("inventory_rows"))

    acceptance_checks = [
        {
            "check_id": "AC01_ARTIFACTS_PRESENT",
            "status": "pass" if inventory and rollup and readiness and has_required_columns else "fail",
            "evidence": f"inventory_rows={len(inventory)}; rollup_rows={len(rollup)}; readiness_rows={len(readiness)}; required_columns={has_required_columns}",
            "decision": "Required S6 inventory artifacts are present.",
        },
        {
            "check_id": "AC02_ROW_ACCOUNTING_COMPLETE",
            "status": "pass" if len(inventory) == inventory_rows and rollup_rows_sum == inventory_rows and readiness_rows_sum == inventory_rows else "fail",
            "evidence": f"summary_inventory_rows={inventory_rows}; inventory_rows={len(inventory)}; rollup_sum={rollup_rows_sum}; readiness_sum={readiness_rows_sum}",
            "decision": "Inventory, rollup, and readiness buckets reconcile.",
        },
        {
            "check_id": "AC03_FAILURE_MODES_VISIBLE",
            "status": "pass" if _int(m1062.get("failure_mode_count")) >= 6 and any(row.get("failure_mode") == "parser_unrecognized_taxonomy_empty" for row in rollup) else "fail",
            "evidence": f"failure_mode_count={m1062.get('failure_mode_count')}; parser_unrecognized_present={any(row.get('failure_mode') == 'parser_unrecognized_taxonomy_empty' for row in rollup)}",
            "decision": "Parser/taxonomy/source/label failure modes are separated enough for support review.",
        },
        {
            "check_id": "AC04_SOURCE_RISK_NOT_HIDDEN",
            "status": "pass" if source_risky_rows > len(future_candidates) and _int(blocked_source_rows.get("rows")) == source_risky_rows else "fail",
            "evidence": f"source_generated_or_risky_rows={source_risky_rows}; blocked_source_readiness_rows={blocked_source_rows.get('rows', '')}; future_candidate_rows={len(future_candidates)}",
            "decision": "Source/generated dominance remains visible and blocks direct implementation/learning.",
        },
        {
            "check_id": "AC05_PLANNING_CANDIDATES_EXTRACTABLE",
            "status": "pass" if len(future_candidates) > 0 and _int(future_owner_rows.get("rows")) == len(future_candidates) else "fail",
            "evidence": f"future_fix_candidate_rows={len(future_candidates)}; future_owner_review_rows={future_owner_rows.get('rows', '')}; candidate_modes={dict(future_candidate_modes)}",
            "decision": "A small candidate package can enter a later read-only planning gate.",
        },
        {
            "check_id": "AC06_NON_EXECUTION_CONTRACT",
            "status": "pass" if all_rows_non_learning and all_rows_non_implementation and not m1062.get("implementation_allowed") else "fail",
            "evidence": f"all_rows_non_learning={all_rows_non_learning}; all_rows_non_implementation={all_rows_non_implementation}; implementation_allowed={m1062.get('implementation_allowed')}",
            "decision": "10.63 accepts support artifacts only, not implementation or learning.",
        },
    ]
    fail_count = sum(1 for row in acceptance_checks if row["status"] != "pass")

    planning_candidates = [
        {
            "planning_bucket": "future_owner_review_candidate",
            "rows": len(future_candidates),
            "dominant_failure_modes": "; ".join(f"{key}:{value}" for key, value in future_candidate_modes.most_common()),
            "dominant_source_families": "; ".join(f"{key}:{value}" for key, value in future_candidate_sources.most_common()),
            "planning_boundary": "eligible only for future read-only parser/taxonomy fix planning; not implementation-ready",
        },
        {
            "planning_bucket": "blocked_source_generated_or_dominated",
            "rows": source_risky_rows,
            "dominant_failure_modes": "source/generated or source-dominated risk remains dominant",
            "dominant_source_families": "",
            "planning_boundary": "blocked from learning and implementation until accepted provenance/owner review exists",
        },
    ]

    support_scope = [
        {
            "support_item": "failure_mode_inventory",
            "accepted_use": "Use as row-level evidence for future read-only parser/taxonomy fix planning.",
            "not_allowed": "Do not implement parser/taxonomy changes directly from this artifact.",
        },
        {
            "support_item": "failure_mode_rollup",
            "accepted_use": "Use to see which failure modes dominate and which are source-risky.",
            "not_allowed": "Do not use rollup counts as Top1 gain or training labels.",
        },
        {
            "support_item": "candidate_fix_readiness",
            "accepted_use": "Use the 16 future_owner_review_candidate rows as a planning input only.",
            "not_allowed": "Do not treat planning candidates as accepted mappings or implementation authorization.",
        },
    ]

    next_gate = [
        {
            "next_stage": "10.64 S6 parser/taxonomy fix planning scope definition",
            "status": "allowed_read_only_planning_gate" if fail_count == 0 and future_candidates else "hold",
            "scope": "Read-only define owner-reviewable parser/taxonomy fix planning package for the 16 future candidates, including exact evidence, risks, acceptance checks, and explicit go requirements.",
            "not_allowed": "no parser edit, no taxonomy edit, no data edit, no training, no implementation, no GoalSearcher change, no heldout/hard selection",
        }
    ]

    blocked_actions = blocked_input + [
        {
            "blocked_action": "treat_16_candidates_as_implementation_ready",
            "reason": "10.63 only allows a future read-only planning gate; candidates still need owner review, exact mappings, rollback, and explicit go.",
            "allowed_after": "future implementation authorization with owner-accepted mappings",
        },
        {
            "blocked_action": "use_inventory_as_learning_reentry",
            "reason": "Rows are DQ/parser/taxonomy support and most inventory is source/generated risky.",
            "allowed_after": "future learning re-entry review with accepted non-generated effect evidence",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "support_scope_csv": str(output_prefix.with_name(output_prefix.name + "_support_scope.csv")),
        "planning_candidates_csv": str(output_prefix.with_name(output_prefix.name + "_planning_candidates.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1062["stage"],
        "inventory_rows": inventory_rows,
        "failure_mode_count": _int(m1062.get("failure_mode_count")),
        "source_generated_or_risky_rows": source_risky_rows,
        "normalization_signal_rows": _int(m1062.get("normalization_signal_rows")),
        "future_fix_candidate_rows": len(future_candidates),
        "future_fix_candidate_failure_modes": len(future_candidate_modes),
        "future_fix_candidate_source_family_count": len(future_candidate_sources),
        "planning_candidate_rows": len(future_candidates),
        "s6_inventory_accepted_for_fix_support": fail_count == 0,
        "future_planning_gate_allowed": fail_count == 0 and len(future_candidates) > 0,
        "implementation_ready_rows": 0,
        "learning_reentry_allowed": False,
        "acceptance_pass_count": len(acceptance_checks) - fail_count,
        "acceptance_fail_count": fail_count,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.63 S6 inventory artifact acceptance gate",
        "read_only": True,
        "acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the S6 inventory artifacts as future parser/taxonomy fix support only. The artifacts are complete enough to support a later read-only planning gate, "
            "and the 16 future fix candidate rows may be carried forward for owner-reviewable planning. They are not implementation-ready, not learning evidence, and do not authorize parser edits, taxonomy edits, training, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.63 only accepts read-only support artifacts and selects a future planning gate. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.64 S6 parser/taxonomy fix planning scope definition",
            "goal": "Read-only define the planning scope for the 16 future parser/taxonomy fix candidates, including exact evidence, risk, acceptance checks, rollback boundary, and explicit go requirements.",
            "default": "planning scope only; no parser edit, taxonomy edit, implementation, training, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["support_scope_csv"]), support_scope, ["support_item", "accepted_use", "not_allowed"])
    _write_csv(
        Path(artifacts["planning_candidates_csv"]),
        planning_candidates,
        ["planning_bucket", "rows", "dominant_failure_modes", "dominant_source_families", "planning_boundary"],
    )
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "status", "scope", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_checks, planning_candidates, next_gate)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
