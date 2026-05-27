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
DEFAULT_GATE_SUMMARY = AGENT_STATE / "goal_10x_s5_measurement_integrity_slice_telemetry_design_gate_summary.json"
DEFAULT_REQUIREMENTS = AGENT_STATE / "goal_10x_s5_measurement_integrity_slice_telemetry_design_gate_future_artifact_requirements.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s5_measurement_integrity_slice_telemetry_design_gate_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition"


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
    field_manifest: list[dict[str, Any]],
    artifact_policy: list[dict[str, Any]],
    acceptance_checks: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.49 S5 Telemetry/Design Artifact Definition",
        "",
        "Read-only definition of the S5 measurement integrity and slice telemetry design artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["field_manifest_count", metrics["field_manifest_count"]],
                ["artifact_policy_count", metrics["artifact_policy_count"]],
                ["effect_category_count", metrics["effect_category_count"]],
                ["acceptance_check_count", metrics["acceptance_check_count"]],
                ["s5_artifact_definition_decision", metrics["s5_artifact_definition_decision"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Field Manifest",
        "",
        _md_table(
            [["field", "type", "source", "forbidden_use"]]
            + [[row["field"], row["type"], row["source_artifact"], row["forbidden_use"]] for row in field_manifest]
        ),
        "",
        "## Artifact Policy",
        "",
        _md_table(
            [["policy_item", "requirement", "fallback"]]
            + [[row["policy_item"], row["requirement"], row["fallback"]] for row in artifact_policy]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "acceptance_check", "failure_action"]]
            + [[row["check_id"], row["acceptance_check"], row["failure_action"]] for row in acceptance_checks]
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
    parser = argparse.ArgumentParser(description="Define S5 telemetry/design artifact without implementation")
    parser.add_argument("--gate-summary", default=str(DEFAULT_GATE_SUMMARY))
    parser.add_argument("--requirements", default=str(DEFAULT_REQUIREMENTS))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    gate_summary = _read_json(Path(args.gate_summary))
    requirements = _read_csv(Path(args.requirements))
    blocked_actions = _read_csv(Path(args.blocked_actions))

    field_manifest = [
        {"field": "telemetry_schema_version", "type": "string", "source_artifact": "S5 artifact definition", "nullable": "no", "forbidden_use": "online feature or ranking signal", "notes": "Version the measurement schema only."},
        {"field": "run_id", "type": "string", "source_artifact": "evaluation run metadata", "nullable": "no", "forbidden_use": "candidate selection key", "notes": "Stable run identifier for provenance."},
        {"field": "split", "type": "enum(dev,oof,heldout,hard)", "source_artifact": "split manifest", "nullable": "no", "forbidden_use": "heldout/hard selection", "notes": "dev/OOF only for analysis; heldout/hard validation-only."},
        {"field": "sample_id", "type": "string", "source_artifact": "eval row", "nullable": "yes", "forbidden_use": "model feature or selection shortcut", "notes": "Traceability only."},
        {"field": "query_id", "type": "string", "source_artifact": "eval row", "nullable": "yes", "forbidden_use": "model feature shortcut", "notes": "Traceability only."},
        {"field": "source_file", "type": "string", "source_artifact": "source provenance registry", "nullable": "no", "forbidden_use": "ranking feature", "notes": "Used to detect source dominance."},
        {"field": "source_family", "type": "string", "source_artifact": "source provenance registry", "nullable": "no", "forbidden_use": "ranking feature", "notes": "Independence unit; filename count is not enough."},
        {"field": "producer", "type": "string", "source_artifact": "source provenance registry", "nullable": "yes", "forbidden_use": "ranking feature", "notes": "Human/system producer."},
        {"field": "collection_method", "type": "string", "source_artifact": "source provenance registry", "nullable": "yes", "forbidden_use": "ranking feature", "notes": "How the evidence was collected."},
        {"field": "provenance_hash", "type": "sha256", "source_artifact": "artifact manifest", "nullable": "no", "forbidden_use": "ranking feature", "notes": "Detect stale or changed artifacts."},
        {"field": "query_family", "type": "string", "source_artifact": "gap decomposition or taxonomy audit", "nullable": "yes", "forbidden_use": "direct rule patch without review", "notes": "Slice field only."},
        {"field": "top1_family", "type": "string", "source_artifact": "gap decomposition or eval output", "nullable": "yes", "forbidden_use": "direct rule patch without review", "notes": "Slice field only."},
        {"field": "expected_book", "type": "string", "source_artifact": "eval row", "nullable": "yes", "forbidden_use": "online feature", "notes": "Audit/slice only."},
        {"field": "top1_book", "type": "string", "source_artifact": "eval output", "nullable": "yes", "forbidden_use": "online feature", "notes": "Audit/slice only."},
        {"field": "rank_bucket", "type": "enum(hit1,rank_2_5,rank_6_10,rank_11_20,rank_21_40,rank_41_80,missing)", "source_artifact": "gap decomposition", "nullable": "no", "forbidden_use": "heldout/hard selection", "notes": "Separate recall vs ranking failures."},
        {"field": "gain", "type": "integer", "source_artifact": "candidate scorecard or future evidence package", "nullable": "yes", "forbidden_use": "net-only claim without loss", "notes": "Must be paired with loss."},
        {"field": "loss", "type": "integer", "source_artifact": "candidate scorecard or future evidence package", "nullable": "yes", "forbidden_use": "hidden regression", "notes": "Loss rows must be audit-visible."},
        {"field": "net", "type": "integer", "source_artifact": "candidate scorecard or future evidence package", "nullable": "yes", "forbidden_use": "general Top1 claim alone", "notes": "Net is gain-loss and must carry slices."},
        {"field": "taxonomy_disposition", "type": "enum(true_learning_signal,taxonomy_cleanup,evidence_only,exclude,unknown)", "source_artifact": "DQ/taxonomy review", "nullable": "no", "forbidden_use": "DQ backlog as learning evidence", "notes": "Separates DQ effects from learning effects."},
        {"field": "effect_category", "type": "enum(taxonomy_cleanup_effect,recall_effect,ranking_effect,safety_gate_effect,unknown_or_evidence_only)", "source_artifact": "S5 effect decomposition contract", "nullable": "no", "forbidden_use": "mixed effect gain claim", "notes": "Required before any re-entry claim."},
    ]
    artifact_policy = [
        {"policy_item": "hash_required", "requirement": "Every source report used in re-entry review must have path, byte size, mtime, and sha256.", "fallback": "regenerate or mark evidence_only_do_not_use"},
        {"policy_item": "freshness_required", "requirement": "A report must either be generated in the current review chain or have an explicit freshness note.", "fallback": "re-run read-only audit before use"},
        {"policy_item": "lineage_required", "requirement": "Every derived table must cite input artifact paths and source stage.", "fallback": "block re-entry until lineage is restored"},
        {"policy_item": "schema_version_required", "requirement": "Telemetry/design artifacts must include telemetry_schema_version.", "fallback": "reject artifact as incomplete"},
        {"policy_item": "generated_source_boundary", "requirement": "global_repair_decision_table or unknown generated provenance remains evidence_only unless separately accepted.", "fallback": "exclude from learning/re-entry claims"},
    ]
    effect_contract = [
        {"effect_category": "taxonomy_cleanup_effect", "definition": "Observed change caused by label, family, book, source provenance, or taxonomy cleanup.", "can_support_learning": "no", "required_evidence": "accepted DQ artifact and separated non-learning disposition"},
        {"effect_category": "recall_effect", "definition": "Expected item moves from top80_missing to candidate set by a recall-route change.", "can_support_learning": "only after S1 re-entry", "required_evidence": "accepted-OSS non-generated recall evidence package"},
        {"effect_category": "ranking_effect", "definition": "Expected item already in candidate set and moves to better rank/top1 by ranking change.", "can_support_learning": "only after S2 re-entry", "required_evidence": "accepted OSS positive net and loss audit"},
        {"effect_category": "safety_gate_effect", "definition": "Safety/compatibility gate rescues gain or prevents loss without changing candidate generation.", "can_support_learning": "only after explicit S3 go", "required_evidence": "dev/OOF what-if and loss budget"},
        {"effect_category": "unknown_or_evidence_only", "definition": "Insufficient provenance, mixed effect, stale artifact, or ambiguous label.", "can_support_learning": "no", "required_evidence": "read-only clarification before any claim"},
    ]
    split_boundary = [
        {"split": "dev", "allowed_use": "analysis, lane definition, diagnostics", "forbidden_use": "final validation claim", "notes": "Can guide read-only strategy, not proof."},
        {"split": "oof", "allowed_use": "candidate/loss-budget selection if a future execution lane is authorized", "forbidden_use": "online feature leakage", "notes": "Primary selection split for future authorized experiments."},
        {"split": "heldout", "allowed_use": "post-freeze validation only", "forbidden_use": "selecting lane, candidate, feature, threshold, field, or policy", "notes": "No selection creep."},
        {"split": "hard", "allowed_use": "post-freeze stress validation only", "forbidden_use": "selecting lane, candidate, feature, threshold, field, or policy", "notes": "No selection creep."},
    ]
    acceptance_checks = [
        {"check_id": "AC01_FIELD_MANIFEST_COMPLETE", "acceptance_check": "All required fields have type, source artifact, nullable policy, and forbidden-use note.", "failure_action": "hold S5 artifact as incomplete"},
        {"check_id": "AC02_ARTIFACT_POLICY_COMPLETE", "acceptance_check": "Hash, freshness, lineage, schema version, and generated-source boundary are defined.", "failure_action": "block future re-entry use"},
        {"check_id": "AC03_EFFECT_DECOMPOSITION_COMPLETE", "acceptance_check": "Every gain/loss claim can be assigned to exactly one effect category or unknown/evidence_only.", "failure_action": "do not allow learning claim"},
        {"check_id": "AC04_SPLIT_BOUNDARY_COMPLETE", "acceptance_check": "Heldout/hard are explicitly validation-only and cannot select anything.", "failure_action": "reject review as selection-contaminated"},
        {"check_id": "AC05_NON_EXECUTION_BOUNDARY", "acceptance_check": "Artifact definition does not write telemetry code, train, tune, or change GoalSearcher.", "failure_action": "stop and require separate implementation go"},
        {"check_id": "AC06_REENTRY_COMPATIBLE", "acceptance_check": "Artifact can be used by future S1/S2/S3/DQ re-entry reviews without satisfying re-entry by itself.", "failure_action": "mark as design-only and do not reopen lanes"},
    ]
    final_blocked_actions = blocked_actions + [
        {"blocked_action": "implement_s5_schema_or_telemetry_code", "reason": "10.49 defines a design artifact only.", "allowed_after": "future implementation scope and explicit go"},
        {"blocked_action": "use_s5_artifact_as_learning_evidence", "reason": "S5 improves measurement discipline but is not a model/result signal.", "allowed_after": "never directly; only future lane-specific evidence can support learning"},
        {"blocked_action": "skip_lane_reentry_requirements", "reason": "S5 does not satisfy S1/S2/S3/DQ re-entry requirements.", "allowed_after": "lane-specific evidence/go/mappings pass read-only re-entry review"},
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "field_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_field_manifest.csv")),
        "artifact_manifest_policy_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest_policy.csv")),
        "effect_decomposition_contract_csv": str(output_prefix.with_name(output_prefix.name + "_effect_decomposition_contract.csv")),
        "split_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_split_boundary.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": gate_summary["stage"],
        "selected_lane": gate_summary["metrics"]["selected_lane"],
        "field_manifest_count": len(field_manifest),
        "artifact_policy_count": len(artifact_policy),
        "effect_category_count": len(effect_contract),
        "split_boundary_count": len(split_boundary),
        "acceptance_check_count": len(acceptance_checks),
        "input_requirement_count": len(requirements),
        "s5_artifact_definition_decision": "defined_read_only_design_artifact",
        "active_learning_lane_count": 0,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.49 S5 telemetry/design artifact definition",
        "read_only": True,
        "artifact_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define the S5 telemetry/design artifact as a read-only measurement contract: field manifest, artifact manifest policy, effect decomposition contract, split boundary, and acceptance checks. "
            "This artifact can support future re-entry reviews, but it does not implement telemetry, satisfy S1/S2/S3/DQ re-entry, train, tune, or authorize GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.49 only defines the S5 design artifact. It does not train, tune, expand candidates, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement telemetry, implement DQ fixes, or claim accuracy gain."
        ),
        "next_stage": {
            "stage": "10.50 S5 artifact acceptance gate",
            "goal": "Read-only check whether the S5 telemetry/design artifact is complete enough to be accepted as a future re-entry support contract.",
            "default": "read-only acceptance gate; no implementation, no training, no heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["field_manifest_csv"]), field_manifest, ["field", "type", "source_artifact", "nullable", "forbidden_use", "notes"])
    _write_csv(Path(artifacts["artifact_manifest_policy_csv"]), artifact_policy, ["policy_item", "requirement", "fallback"])
    _write_csv(Path(artifacts["effect_decomposition_contract_csv"]), effect_contract, ["effect_category", "definition", "can_support_learning", "required_evidence"])
    _write_csv(Path(artifacts["split_boundary_csv"]), split_boundary, ["split", "allowed_use", "forbidden_use", "notes"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check_id", "acceptance_check", "failure_action"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), final_blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, field_manifest, artifact_policy, acceptance_checks)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
