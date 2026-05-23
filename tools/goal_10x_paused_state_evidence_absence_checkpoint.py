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
DEFAULT_KIT_SUMMARY = AGENT_STATE / "goal_10x_reentry_evidence_intake_kit_summary.json"
DEFAULT_CHECKLIST = AGENT_STATE / "goal_10x_reentry_evidence_intake_kit_intake_checklist.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_paused_state_evidence_absence_checkpoint"


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.x Paused-state Evidence Absence Checkpoint",
        "",
        "Read-only checkpoint after a plain next-step request while the learning loop is paused.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["evidence_package_completed", metrics["evidence_package_completed"]],
                ["ready_for_reentry_count", metrics["ready_for_reentry_count"]],
                ["missing_or_pending_input_count", metrics["missing_or_pending_input_count"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
                ["heldout_selection_allowed", metrics["heldout_selection_allowed"]],
            ]
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


def _template_status(kit_summary: dict[str, Any], checklist: list[dict[str, str]]) -> list[dict[str, Any]]:
    artifacts = kit_summary.get("artifacts", {})
    template_names = sorted(
        name for name in artifacts
        if name.endswith("_template_csv") or name in {"intake_checklist_csv", "schema_copy_csv"}
    )
    rows: list[dict[str, Any]] = []
    for name in template_names:
        path = Path(artifacts[name])
        row_count = 0
        filled_row_count = 0
        if path.exists() and path.suffix.lower() == ".csv":
            csv_rows = _read_csv(path)
            row_count = len(csv_rows)
            for row in csv_rows:
                values = [str(value).strip() for value in row.values()]
                if values and all(value not in {"", "pending", "false_until_completed", "False"} for value in values):
                    filled_row_count += 1
        rows.append(
            {
                "artifact_key": name,
                "path": str(path),
                "exists": path.exists(),
                "row_count": row_count,
                "filled_row_count": filled_row_count,
                "usable_as_evidence_now": False,
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "open_future_reentry_review",
            "reason": "No completed evidence package or accepted DQ artifacts were found.",
            "allowed_after": "completed evidence intake templates are provided and pass schema checks",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "Paused-state checkpoint is not learning execution.",
            "allowed_after": "separate explicit execution stage after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation",
            "reason": "No re-entry review or frozen validation candidate exists.",
            "allowed_after": "future source/DQ re-entry pass plus explicit validation gate",
        },
        {
            "blocked_action": "change_ranking_goal_searcher_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "auto_advance_learning_stage",
            "reason": "Dashboard and automation pause boundary remain active.",
            "allowed_after": "new external evidence package or accepted DQ artifacts",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check paused 10.x state for completed evidence package")
    parser.add_argument("--kit-summary", default=str(DEFAULT_KIT_SUMMARY))
    parser.add_argument("--checklist", default=str(DEFAULT_CHECKLIST))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    kit_summary = _read_json(Path(args.kit_summary))
    checklist = _read_csv(Path(args.checklist))
    template_status = _template_status(kit_summary, checklist)
    ready_count = sum(1 for row in checklist if str(row.get("ready_for_reentry")).lower() == "true")
    missing_or_pending = sum(1 for row in checklist if str(row.get("ready_for_reentry")).lower() != "true")
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "template_status_csv": str(output_prefix.with_name(output_prefix.name + "_template_status.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "required_input_count": len(checklist),
        "ready_for_reentry_count": ready_count,
        "missing_or_pending_input_count": missing_or_pending,
        "template_artifact_count": len(template_status),
        "evidence_package_completed": False,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.x paused-state evidence absence checkpoint",
        "read_only": True,
        "paused_state_only": True,
        "metrics": metrics,
        "template_status": template_status,
        "blocked_actions": _blocked_actions(),
        "artifacts": artifacts,
        "decision": (
            "Remain paused. No completed external evidence package or accepted DQ artifacts were found, so future re-entry review is not allowed now. "
            "This checkpoint does not authorize training, validation, tuning, ranking changes, GoalSearcher changes, or feature whitelist edits."
        ),
        "anti_drift_conclusion": (
            "Plain next-step while paused is treated as a status/checkpoint only. It does not create a new learning stage or reopen S1/S2/S3."
        ),
    }
    _write_csv(Path(artifacts["template_status_csv"]), template_status, ["artifact_key", "path", "exists", "row_count", "filled_row_count", "usable_as_evidence_now"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), report["blocked_actions"], ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
