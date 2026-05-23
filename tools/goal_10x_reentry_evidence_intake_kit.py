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
DEFAULT_REQUIRED_INPUTS = AGENT_STATE / "goal_10x_evidence_wait_closure_pause_request_gate_required_user_evidence_inputs.csv"
DEFAULT_SCHEMA = AGENT_STATE / "goal_10x_strategy_confidence_loophole_audit_evidence_intake_schema.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_reentry_evidence_intake_kit"


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.x Re-entry Evidence Intake Kit",
        "",
        "This kit is only a set of templates and acceptance rules. It does not satisfy re-entry by itself.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["required_input_count", metrics["required_input_count"]],
                ["template_count", metrics["template_count"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
                ["heldout_selection_allowed", metrics["heldout_selection_allowed"]],
            ]
        ),
        "",
        "## How To Use",
        "",
        "Fill the template rows with external evidence artifacts, then open a future read-only re-entry evidence intake review. Do not train, tune, validate on heldout/hard, or change ranking from these templates.",
        "",
        "## Required Inputs",
        "",
        _md_table(
            [["input_id", "input_type", "current_status", "future_review_use"]]
            + [[row["input_id"], row["input_type"], row["current_status"], row["future_review_use"]] for row in report["required_inputs"]]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _intake_checklist(required_inputs: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in required_inputs:
        rows.append(
            {
                "input_id": row["input_id"],
                "input_type": row["input_type"],
                "minimum_content": row["minimum_content"],
                "acceptance_check": row["acceptance_check"],
                "required_artifact_template": _template_name_for(row["input_id"]),
                "current_status": row["current_status"],
                "ready_for_reentry": False,
            }
        )
    return rows


def _template_name_for(input_id: str) -> str:
    if input_id.startswith("S2_"):
        return "goal_10x_reentry_evidence_intake_kit_s2_independent_evidence_template.csv"
    mapping = {
        "DQ_ACCEPT_source_provenance": "goal_10x_reentry_evidence_intake_kit_dq_source_provenance_registry_template.csv",
        "DQ_ACCEPT_query_family_empty": "goal_10x_reentry_evidence_intake_kit_dq_query_family_empty_coverage_template.csv",
        "DQ_ACCEPT_top1_family_coverage": "goal_10x_reentry_evidence_intake_kit_dq_top1_family_coverage_audit_template.csv",
        "DQ_ACCEPT_label_or_taxonomy_mixture": "goal_10x_reentry_evidence_intake_kit_dq_label_taxonomy_mixture_separation_template.csv",
    }
    return mapping.get(input_id, "unknown")


def _sample_s2_template() -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": "example_s2_001",
            "source_file": "",
            "source_family": "",
            "producer": "",
            "collection_method": "",
            "row_id": "",
            "query_id": "",
            "expected_id": "",
            "candidate_id": "OBJ_A_current_lambda_rank_baseline__FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT",
            "split": "dev_oof",
            "gain": "",
            "loss": "",
            "net": "",
            "taxonomy_disposition": "true_learning_signal|taxonomy_cleanup|exclude|evidence_only",
            "provenance_hash": "",
            "is_generated_repair_source": "false",
            "source_independence_note": "",
            "eligible_for_reentry_review": "false_until_completed",
        }
    ]


def _dq_source_provenance_template() -> list[dict[str, Any]]:
    return [
        {
            "source_file": "",
            "source_family": "",
            "producer": "",
            "collection_method": "",
            "generated_source_flag": "",
            "generated_source_reason": "",
            "row_id": "",
            "provenance_hash": "",
            "learning_disposition": "exclude|evidence_only|candidate_independent_evidence",
            "reviewer": "",
            "acceptance_status": "pending",
        }
    ]


def _dq_query_family_empty_template() -> list[dict[str, Any]]:
    return [
        {
            "row_id": "",
            "query_text_or_id": "",
            "current_query_family": "<empty>",
            "proposed_query_family": "",
            "taxonomy_empty_reason": "",
            "source_file": "",
            "source_family": "",
            "provenance_hash": "",
            "learning_disposition": "taxonomy_cleanup|exclude|candidate_independent_evidence",
            "acceptance_status": "pending",
        }
    ]


def _dq_top1_family_coverage_template() -> list[dict[str, Any]]:
    return [
        {
            "row_id": "",
            "query_family": "",
            "top1_family": "",
            "coverage_issue": "",
            "accepted_family_disposition": "",
            "domain": "pipe|valve|lamp|weak_current|other",
            "source_file": "",
            "source_family": "",
            "provenance_hash": "",
            "learning_disposition": "taxonomy_cleanup|exclude|candidate_independent_evidence",
            "acceptance_status": "pending",
        }
    ]


def _dq_label_mixture_template() -> list[dict[str, Any]]:
    return [
        {
            "row_id": "",
            "original_label_or_family": "",
            "mixture_type": "overbroad_valve|water_meter|sanitary|instrument|civil|other",
            "separated_label_or_family": "",
            "source_file": "",
            "source_family": "",
            "provenance_hash": "",
            "learning_disposition": "taxonomy_cleanup|exclude|candidate_independent_evidence",
            "acceptance_status": "pending",
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create 10.x re-entry evidence intake templates")
    parser.add_argument("--required-inputs", default=str(DEFAULT_REQUIRED_INPUTS))
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    required_inputs = _read_csv(Path(args.required_inputs))
    schema = _read_csv(Path(args.schema))
    output_prefix = Path(args.output_prefix)

    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "intake_checklist_csv": str(output_prefix.with_name(output_prefix.name + "_intake_checklist.csv")),
        "schema_copy_csv": str(output_prefix.with_name(output_prefix.name + "_schema.csv")),
        "s2_independent_evidence_template_csv": str(output_prefix.with_name(output_prefix.name + "_s2_independent_evidence_template.csv")),
        "dq_source_provenance_registry_template_csv": str(output_prefix.with_name(output_prefix.name + "_dq_source_provenance_registry_template.csv")),
        "dq_query_family_empty_coverage_template_csv": str(output_prefix.with_name(output_prefix.name + "_dq_query_family_empty_coverage_template.csv")),
        "dq_top1_family_coverage_audit_template_csv": str(output_prefix.with_name(output_prefix.name + "_dq_top1_family_coverage_audit_template.csv")),
        "dq_label_taxonomy_mixture_separation_template_csv": str(output_prefix.with_name(output_prefix.name + "_dq_label_taxonomy_mixture_separation_template.csv")),
    }

    _write_csv(Path(artifacts["intake_checklist_csv"]), _intake_checklist(required_inputs), ["input_id", "input_type", "minimum_content", "acceptance_check", "required_artifact_template", "current_status", "ready_for_reentry"])
    _write_csv(Path(artifacts["schema_copy_csv"]), schema, ["field", "requirement", "required"])
    _write_csv(Path(artifacts["s2_independent_evidence_template_csv"]), _sample_s2_template(), list(_sample_s2_template()[0].keys()))
    _write_csv(Path(artifacts["dq_source_provenance_registry_template_csv"]), _dq_source_provenance_template(), list(_dq_source_provenance_template()[0].keys()))
    _write_csv(Path(artifacts["dq_query_family_empty_coverage_template_csv"]), _dq_query_family_empty_template(), list(_dq_query_family_empty_template()[0].keys()))
    _write_csv(Path(artifacts["dq_top1_family_coverage_audit_template_csv"]), _dq_top1_family_coverage_template(), list(_dq_top1_family_coverage_template()[0].keys()))
    _write_csv(Path(artifacts["dq_label_taxonomy_mixture_separation_template_csv"]), _dq_label_mixture_template(), list(_dq_label_mixture_template()[0].keys()))

    metrics = {
        "required_input_count": len(required_inputs),
        "schema_field_count": len(schema),
        "template_count": 5,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "evidence_package_completed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.x re-entry evidence intake kit",
        "read_only": True,
        "templates_only": True,
        "metrics": metrics,
        "required_inputs": required_inputs,
        "artifacts": artifacts,
        "decision": (
            "Create evidence intake templates only. These templates do not reopen learning, do not satisfy re-entry, and do not authorize training, heldout/hard validation, "
            "ranking changes, GoalSearcher changes, or feature whitelist edits."
        ),
        "anti_drift_conclusion": (
            "This kit is preparation for a future read-only evidence intake review. Blank or partially completed templates remain non-evidence."
        ),
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
