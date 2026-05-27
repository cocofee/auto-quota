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
DEFAULT_S5_SUMMARY = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_summary.json"
DEFAULT_SELECTED_LANE = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_selected_lane.csv"
DEFAULT_GATE_PLAN = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_next_gate_plan.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_new_strategy_lane_definition_after_pause_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s5_measurement_integrity_slice_telemetry_design_gate"


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
    gate_checks: list[dict[str, Any]],
    artifact_requirements: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.48 S5 Measurement Integrity And Slice Telemetry Design Gate",
        "",
        "Read-only gate to decide whether S5 is specific enough for a future telemetry/design artifact definition.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_lane", metrics["selected_lane"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["gate_fail_count", metrics["gate_fail_count"]],
                ["s5_design_gate_decision", metrics["s5_design_gate_decision"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["gate_item", "status", "evidence", "decision"]]
            + [[row["gate_item"], row["status"], row["evidence"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Future Artifact Requirements",
        "",
        _md_table(
            [["artifact_section", "required_content", "acceptance_check"]]
            + [
                [row["artifact_section"], row["required_content"], row["acceptance_check"]]
                for row in artifact_requirements
            ]
        ),
        "",
        "## Next Gate",
        "",
        _md_table(
            [["next_stage", "goal", "boundary"]]
            + [[row["next_stage"], row["goal"], row["boundary"]] for row in next_gate]
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
    parser = argparse.ArgumentParser(description="Gate S5 measurement integrity and slice telemetry design")
    parser.add_argument("--s5-summary", default=str(DEFAULT_S5_SUMMARY))
    parser.add_argument("--selected-lane", default=str(DEFAULT_SELECTED_LANE))
    parser.add_argument("--gate-plan", default=str(DEFAULT_GATE_PLAN))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s5_summary = _read_json(Path(args.s5_summary))
    selected_lane = _read_csv(Path(args.selected_lane))
    gate_plan = _read_csv(Path(args.gate_plan))
    blocked_actions = _read_csv(Path(args.blocked_actions))

    required_gate_items = {
        "observability_field_manifest",
        "artifact_integrity_boundary",
        "effect_decomposition_boundary",
        "split_policy_boundary",
        "next_action_boundary",
    }
    present_gate_items = {row["gate_item"] for row in gate_plan}
    missing_gate_items = sorted(required_gate_items - present_gate_items)
    gate_checks = []
    for row in gate_plan:
        gate_checks.append(
            {
                "gate_item": row["gate_item"],
                "status": "pass",
                "evidence": row["required_output"],
                "decision": row["acceptance_check"],
            }
        )
    for item in missing_gate_items:
        gate_checks.append(
            {
                "gate_item": item,
                "status": "fail_missing",
                "evidence": "missing from 10.47 next_gate_plan",
                "decision": "cannot proceed until defined",
            }
        )

    artifact_requirements = [
        {
            "artifact_section": "field_manifest",
            "required_content": "column definitions for split/source/provenance/query/top1/book/rank/gain-loss/taxonomy fields",
            "acceptance_check": "every field has type, source artifact, nullable policy, and forbidden-use note",
        },
        {
            "artifact_section": "artifact_manifest_policy",
            "required_content": "hash/freshness requirements for report inputs used in future re-entry reviews",
            "acceptance_check": "stale or hash-mismatched artifacts default to regenerate-before-use",
        },
        {
            "artifact_section": "effect_decomposition_contract",
            "required_content": "taxonomy_cleanup_effect, recall_effect, ranking_effect, safety_gate_effect, and unknown/evidence_only categories",
            "acceptance_check": "DQ cleanup and generated/source-dominated artifacts cannot be counted as rank/recall gain",
        },
        {
            "artifact_section": "split_boundary_contract",
            "required_content": "dev/OOF-only strategy analysis and heldout/hard validation-only boundary",
            "acceptance_check": "no candidate, threshold, field, lane, or score can be selected with heldout/hard",
        },
        {
            "artifact_section": "non_execution_boundary",
            "required_content": "explicit statement that the artifact is design/spec only",
            "acceptance_check": "training_allowed=false; implementation_allowed=false; goal_searcher_change_allowed=false",
        },
    ]
    next_gate = [
        {
            "next_stage": "10.49 S5 telemetry/design artifact definition",
            "goal": "Read-only define the concrete S5 field manifest, artifact manifest policy, effect decomposition contract, split boundary, and acceptance checks.",
            "boundary": "No telemetry implementation, no training, no GoalSearcher change, no heldout/hard selection.",
        }
    ]
    final_blocked_actions = blocked_actions + [
        {
            "blocked_action": "implement_s5_telemetry",
            "reason": "10.48 only passes a design gate; it does not authorize implementation.",
            "allowed_after": "future implementation plan plus explicit go, if ever requested",
        },
        {
            "blocked_action": "treat_s5_as_accuracy_gain",
            "reason": "Measurement integrity improves evidence quality, not model output directly.",
            "allowed_after": "future validated algorithm candidate with split-level gain and loss audit",
        },
    ]

    gate_fail_count = sum(1 for row in gate_checks if not str(row["status"]).startswith("pass"))
    decision_label = "pass_to_read_only_artifact_definition" if gate_fail_count == 0 else "hold_until_gate_items_complete"
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "future_artifact_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_future_artifact_requirements.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": s5_summary["stage"],
        "selected_lane": s5_summary["metrics"]["selected_lane"],
        "selected_lane_row_count": len(selected_lane),
        "required_gate_item_count": len(required_gate_items),
        "present_gate_item_count": len(present_gate_items),
        "gate_pass_count": sum(1 for row in gate_checks if row["status"] == "pass"),
        "gate_fail_count": gate_fail_count,
        "future_artifact_requirement_count": len(artifact_requirements),
        "s5_design_gate_decision": decision_label,
        "active_learning_lane_count": 0,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.48 S5 measurement integrity and slice telemetry design gate",
        "read_only": True,
        "design_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pass S5 to a future read-only telemetry/design artifact definition. The 10.47 gate plan covers observability fields, artifact integrity, "
            "effect decomposition, split policy, and non-execution boundaries. This does not authorize telemetry implementation, training, GoalSearcher changes, or heldout/hard selection."
            if gate_fail_count == 0
            else "Hold S5 because required gate items are missing; do not proceed to artifact definition until the gate plan is complete."
        ),
        "anti_drift_conclusion": (
            "10.48 only gates S5 design readiness. It does not train, tune, expand candidates, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement telemetry, implement DQ fixes, or claim accuracy gain."
        ),
        "next_stage": {
            "stage": "10.49 S5 telemetry/design artifact definition",
            "goal": "Read-only define the S5 field manifest, artifact manifest policy, effect decomposition contract, split boundary, and acceptance checks.",
            "default": "read-only artifact definition; no implementation, no training, no heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate_item", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["future_artifact_requirements_csv"]), artifact_requirements, ["artifact_section", "required_content", "acceptance_check"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "goal", "boundary"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), final_blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, artifact_requirements, next_gate)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
