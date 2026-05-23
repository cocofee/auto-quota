from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_MANIFEST = DEFAULT_AGENT_STATE / "goal_family_compatibility_eval_only_package_manifest.json"
DEFAULT_RUNBOOK = DEFAULT_AGENT_STATE / "goal_family_compatibility_eval_only_package_runbook.md"
DEFAULT_OUTPUT_PREFIX = DEFAULT_AGENT_STATE / "goal_family_compatibility_eval_only_package_validation"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _artifact_row(kind: str, name: str, path_value: str, required: bool, purpose: str = "") -> dict[str, Any]:
    path = _resolve(path_value)
    return {
        "kind": kind,
        "name": name,
        "path": path_value,
        "required": required,
        "exists": path.exists(),
        "non_empty": path.exists() and path.stat().st_size > 0,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "purpose": purpose,
    }


def _artifact_checks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in manifest.get("input_artifacts") or []:
        rows.append(_artifact_row("input", _clean(item.get("name")), _clean(item.get("path")), _to_bool(item.get("required")), _clean(item.get("purpose"))))
    for path_value in manifest.get("output_artifacts") or []:
        rows.append(_artifact_row("output", Path(_clean(path_value)).name, _clean(path_value), True, "declared package output"))
    for command in manifest.get("commands") or []:
        for path_value in command.get("writes") or []:
            rows.append(_artifact_row("command_write", f"{command.get('name')} write", _clean(path_value), True, f"output of {command.get('name')}"))
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (_clean(row.get("kind")), _clean(row.get("name")), _clean(row.get("path")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _command_checks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    commands = manifest.get("commands") or []
    rows: list[dict[str, Any]] = []
    orders = [_to_int(command.get("order")) for command in commands]
    expected_orders = list(range(1, len(commands) + 1))
    disallowed_patterns = ["train", "tune", "lightgbm", "GoalSearcher", "goal_searcher", "production"]
    for command in commands:
        text = _clean(command.get("command"))
        script_path = ""
        script_exists = False
        match = re.search(r"python\s+([^\s]+)", text)
        if match:
            script_path = match.group(1)
            script_exists = _resolve(script_path).exists()
        rows.append(
            {
                "order": _to_int(command.get("order")),
                "name": _clean(command.get("name")),
                "command": text,
                "order_sequence_valid": orders == expected_orders,
                "script_path": script_path,
                "script_exists": script_exists,
                "writes_count": len(command.get("writes") or []),
                "pass_gates_count": len(command.get("pass_gates") or []),
                "disallowed_token_found": ",".join(pattern for pattern in disallowed_patterns if pattern.lower() in text.lower()),
                "dry_run_only": True,
                "executed": False,
            }
        )
    return rows


def _split_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return list(summary.get("split_metrics") or [])


def _hard_check(summary: dict[str, Any], name: str) -> bool:
    for row in summary.get("hard_checks") or []:
        if _clean(row.get("gate")) == name:
            return _to_bool(row.get("passed"))
    return False


def _gate_row(source: str, gate: str, passed: bool, value: Any, expected: str) -> dict[str, Any]:
    return {"source": source, "gate": gate, "passed": bool(passed), "value": value, "expected": expected}


def _gate_checks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = {item["name"]: _resolve(item["path"]) for item in manifest.get("input_artifacts") or []}
    files.update(
        {
            "default_off_summary_output": _resolve("reports/agent_state/goal_family_compatibility_switch_skeleton_default_off_summary.json"),
            "explicit_shadow_summary_output": _resolve("reports/agent_state/goal_family_compatibility_switch_skeleton_explicit_enabled_eval_only_summary.json"),
            "contract_audit_summary_output": _resolve("reports/agent_state/goal_family_compatibility_switch_contract_audit_summary.json"),
            "readiness_summary_output": _resolve("reports/agent_state/goal_family_compatibility_default_off_readiness_summary.json"),
        }
    )
    config = _read_json(files.get("switch_config", Path()))
    default_summary = _read_json(files["default_off_summary_output"])
    enabled_summary = _read_json(files["explicit_shadow_summary_output"])
    contract_audit = _read_json(files["contract_audit_summary_output"])
    readiness = _read_json(files["readiness_summary_output"])
    default_rows = _split_rows(default_summary)
    enabled_rows = _split_rows(enabled_summary)
    checklist = readiness.get("checklist") or []
    rows = [
        _gate_row("manifest", "eval_only", manifest.get("eval_only") is True, manifest.get("eval_only"), "true"),
        _gate_row("manifest", "default_off", manifest.get("default_off") is True, manifest.get("default_off"), "true"),
        _gate_row("manifest", "production_ready_false", manifest.get("production_ready") is False, manifest.get("production_ready"), "false"),
        _gate_row("manifest", "online_switch_allowed_false", manifest.get("online_switch_allowed") is False, manifest.get("online_switch_allowed"), "false"),
        _gate_row("manifest", "no_training", manifest.get("no_training") is True, manifest.get("no_training"), "true"),
        _gate_row("manifest", "no_goal_searcher_change", manifest.get("no_goal_searcher_change") is True, manifest.get("no_goal_searcher_change"), "true"),
        _gate_row("switch_config", "default_switch_disabled", config.get("goal_family_compatibility_switch_enabled") is False, config.get("goal_family_compatibility_switch_enabled"), "false"),
        _gate_row("default_off_summary", "contract_passed", default_summary.get("contract_passed") is True, default_summary.get("contract_passed"), "true"),
        _gate_row("default_off_summary", "runtime_active_false", default_summary.get("runtime_active") is False, default_summary.get("runtime_active"), "false"),
        _gate_row(
            "default_off_summary",
            "effective_allowed_count_zero",
            all(_to_int(row.get("effective_allowed_count")) == 0 for row in default_rows) and bool(default_rows),
            {row.get("split"): row.get("effective_allowed_count") for row in default_rows},
            "0 for every split",
        ),
        _gate_row(
            "default_off_summary",
            "net_vs_gated_zero",
            all(_to_int(row.get("effective_net_vs_gated")) == 0 for row in default_rows) and bool(default_rows),
            {row.get("split"): row.get("effective_net_vs_gated") for row in default_rows},
            "0 for every split",
        ),
        _gate_row("explicit_shadow_summary", "contract_passed", enabled_summary.get("contract_passed") is True, enabled_summary.get("contract_passed"), "true"),
        _gate_row("explicit_shadow_summary", "eval_only", enabled_summary.get("eval_only") is True, enabled_summary.get("eval_only"), "true"),
        _gate_row("explicit_shadow_summary", "no_search_integration", enabled_summary.get("no_search_integration") is True, enabled_summary.get("no_search_integration"), "true"),
        _gate_row(
            "explicit_shadow_summary",
            "new_residual_loss_zero",
            all(_to_int(row.get("new_residual_loss_count")) == 0 for row in enabled_rows) and bool(enabled_rows),
            {row.get("split"): row.get("new_residual_loss_count") for row in enabled_rows},
            "0 for every split",
        ),
        _gate_row("contract_audit", "hard_gate_passed", contract_audit.get("hard_gate_passed") is True, contract_audit.get("hard_gate_passed"), "true"),
        _gate_row("contract_audit", "schema_warning_count_zero", _to_int(contract_audit.get("schema_warning_count")) == 0, contract_audit.get("schema_warning_count"), "0"),
        _gate_row("contract_audit", "default_off_no_effective_overrides_all_rows", _hard_check(contract_audit, "default_off_no_effective_overrides_all_rows"), "hard_check", "passed"),
        _gate_row("contract_audit", "heldout_not_used_for_policy_selection", _hard_check(contract_audit, "heldout_not_used_for_policy_selection"), "hard_check", "passed"),
        _gate_row("readiness", "readiness_passed", (readiness.get("decision") or {}).get("readiness_passed") is True, (readiness.get("decision") or {}).get("readiness_passed"), "true"),
        _gate_row("readiness", "blocker_count_zero", _to_int((readiness.get("decision") or {}).get("blocker_count")) == 0, (readiness.get("decision") or {}).get("blocker_count"), "0"),
        _gate_row("readiness", "production_ready_false", (readiness.get("decision") or {}).get("production_ready") is False, (readiness.get("decision") or {}).get("production_ready"), "false"),
        _gate_row("readiness", "online_switch_allowed_false", (readiness.get("decision") or {}).get("online_switch_allowed") is False, (readiness.get("decision") or {}).get("online_switch_allowed"), "false"),
        _gate_row(
            "readiness",
            "all_readiness_blockers_passed",
            all(_to_bool(row.get("passed")) for row in checklist if _clean(row.get("severity")) == "blocker") and bool(checklist),
            sum(1 for row in checklist if _clean(row.get("severity")) == "blocker" and not _to_bool(row.get("passed"))),
            "0 failed blocker gates",
        ),
    ]
    return rows


def _hard_fail_checks(manifest: dict[str, Any], gate_rows: list[dict[str, Any]], artifact_rows: list[dict[str, Any]], command_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed_gates = [row for row in gate_rows if not _to_bool(row.get("passed"))]
    missing_artifacts = [row for row in artifact_rows if _to_bool(row.get("required")) and (not _to_bool(row.get("exists")) or not _to_bool(row.get("non_empty")))]
    disallowed_commands = [row for row in command_rows if _clean(row.get("disallowed_token_found"))]
    forbidden = manifest.get("forbidden_actions") or []
    required_forbidden = {
        "do_not_train",
        "do_not_tune",
        "do_not_modify_GoalSearcher",
        "do_not_change_default_search_chain",
        "do_not_enable_online_switch",
        "do_not_use_heldout_for_selection",
    }
    return [
        {
            "condition": "any_gate_failed",
            "present": bool(failed_gates),
            "count": len(failed_gates),
            "detail": ",".join(row["gate"] for row in failed_gates),
        },
        {
            "condition": "required_artifact_missing_or_empty",
            "present": bool(missing_artifacts),
            "count": len(missing_artifacts),
            "detail": ",".join(row["name"] for row in missing_artifacts),
        },
        {
            "condition": "disallowed_command_token",
            "present": bool(disallowed_commands),
            "count": len(disallowed_commands),
            "detail": ",".join(f"{row['name']}:{row['disallowed_token_found']}" for row in disallowed_commands),
        },
        {
            "condition": "forbidden_actions_incomplete",
            "present": not required_forbidden.issubset(set(forbidden)),
            "count": len(required_forbidden - set(forbidden)),
            "detail": ",".join(sorted(required_forbidden - set(forbidden))),
        },
        {
            "condition": "manifest_allows_online_or_production",
            "present": bool(manifest.get("production_ready")) or bool(manifest.get("online_switch_allowed")),
            "count": int(bool(manifest.get("production_ready"))) + int(bool(manifest.get("online_switch_allowed"))),
            "detail": f"production_ready={manifest.get('production_ready')}; online_switch_allowed={manifest.get('online_switch_allowed')}",
        },
    ]


def _runbook_checks(runbook: str) -> list[dict[str, Any]]:
    required = [
        "Stage 8.4 writes the runbook only",
        "Command Order",
        "Default-off Check",
        "Explicit Shadow Check",
        "Broader Contract Audit",
        "Readiness Review",
        "Global Pass Gates",
        "Hard Fail Conditions",
        "Stage 8.5 should validate this manifest and runbook",
        "no training",
        "no tuning",
        "No `GoalSearcher` change",
        "production_ready=false",
        "online_switch_allowed=false",
        "75.00%",
    ]
    return [{"requirement": item, "present": item in runbook} for item in required]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Eval-only Package Manifest Validation",
        "",
        "Stage 8.5 validates the manifest and runbook without executing training, tuning, GoalSearcher changes, or online integration.",
        "",
        "## Decision",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["validation_passed", report["validation_passed"]],
                ["hard_fail_present", report["hard_fail_present"]],
                ["artifact_fail_count", report["artifact_fail_count"]],
                ["gate_fail_count", report["gate_fail_count"]],
                ["command_fail_count", report["command_fail_count"]],
                ["runbook_fail_count", report["runbook_fail_count"]],
                ["recommendation", report["recommendation"]],
            ]
        ),
        "",
        "## Command Dry-run",
        "",
        _md_table(
            [["order", "name", "script_exists", "writes", "pass_gates", "executed"]]
            + [[row["order"], row["name"], row["script_exists"], row["writes_count"], row["pass_gates_count"], row["executed"]] for row in report["command_validation"]]
        ),
        "",
        "## Gate Summary",
        "",
        _md_table([["source", "gate", "passed", "expected"]] + [[row["source"], row["gate"], row["passed"], row["expected"]] for row in report["gate_validation"]]),
        "",
        "## Hard Fail Conditions",
        "",
        _md_table([["condition", "present", "count", "detail"]] + [[row["condition"], row["present"], row["count"], row["detail"]] for row in report["hard_fail_validation"]]),
        "",
        "## Artifacts",
        "",
        _md_table([["name", "kind", "exists", "non_empty", "path"]] + [[row["name"], row["kind"], row["exists"], row["non_empty"], row["path"]] for row in report["artifact_validation"]]),
        "",
        "## Next",
        "",
        report["recommended_next_stage"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    result = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        result.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 8.5 manifest static validation / dry-run")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--runbook", default=str(DEFAULT_RUNBOOK))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    manifest_path = Path(args.manifest)
    runbook_path = Path(args.runbook)
    manifest = _read_json(manifest_path)
    runbook = runbook_path.read_text(encoding="utf-8") if runbook_path.exists() else ""
    artifact_rows = _artifact_checks(manifest)
    command_rows = _command_checks(manifest)
    gate_rows = _gate_checks(manifest)
    hard_fail_rows = _hard_fail_checks(manifest, gate_rows, artifact_rows, command_rows)
    runbook_rows = _runbook_checks(runbook)

    artifact_fail_count = sum(1 for row in artifact_rows if _to_bool(row.get("required")) and (not _to_bool(row.get("exists")) or not _to_bool(row.get("non_empty"))))
    command_fail_count = sum(
        1
        for row in command_rows
        if not _to_bool(row.get("order_sequence_valid")) or not _to_bool(row.get("script_exists")) or _to_int(row.get("writes_count")) == 0 or _to_int(row.get("pass_gates_count")) == 0 or _clean(row.get("disallowed_token_found"))
    )
    gate_fail_count = sum(1 for row in gate_rows if not _to_bool(row.get("passed")))
    hard_fail_present = any(_to_bool(row.get("present")) for row in hard_fail_rows)
    runbook_fail_count = sum(1 for row in runbook_rows if not _to_bool(row.get("present")))
    validation_passed = artifact_fail_count == 0 and command_fail_count == 0 and gate_fail_count == 0 and not hard_fail_present and runbook_fail_count == 0
    recommendation = "allow_stage_8_6_close_8x_and_return_to_accuracy_gap_plan" if validation_passed else "fix_manifest_validation_failures_before_next_stage"

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "artifact_validation_csv": str(output_prefix.with_name(output_prefix.name + "_artifacts.csv")),
        "command_validation_csv": str(output_prefix.with_name(output_prefix.name + "_commands.csv")),
        "gate_validation_csv": str(output_prefix.with_name(output_prefix.name + "_gates.csv")),
        "hard_fail_validation_csv": str(output_prefix.with_name(output_prefix.name + "_hard_fails.csv")),
        "runbook_validation_csv": str(output_prefix.with_name(output_prefix.name + "_runbook.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 8.5 manifest static validation / dry-run",
        "eval_only": True,
        "dry_run_only": True,
        "commands_executed_from_manifest": False,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "production_ready": False,
        "online_switch_allowed": False,
        "validation_passed": validation_passed,
        "artifact_fail_count": artifact_fail_count,
        "command_fail_count": command_fail_count,
        "gate_fail_count": gate_fail_count,
        "hard_fail_present": hard_fail_present,
        "runbook_fail_count": runbook_fail_count,
        "recommendation": recommendation,
        "artifact_validation": artifact_rows,
        "command_validation": command_rows,
        "gate_validation": gate_rows,
        "hard_fail_validation": hard_fail_rows,
        "runbook_validation": runbook_rows,
        "recommended_next_stage": "Stage 8.6: close out the 8.x default-off package and write the handoff back to the 9.x accuracy-gap track; do not train, tune, or modify GoalSearcher.",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "inputs": {"manifest": str(manifest_path), "runbook": str(runbook_path)},
        "artifacts": artifacts,
        "anti_drift_conclusion": "This validation reads existing files and output fields only. It does not execute package commands, train, tune, modify GoalSearcher, change ranking, or connect anything online.",
    }

    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["artifact_validation_csv"]), artifact_rows, ["kind", "name", "path", "required", "exists", "non_empty", "size_bytes", "purpose"])
    _write_csv(Path(artifacts["command_validation_csv"]), command_rows, ["order", "name", "command", "order_sequence_valid", "script_path", "script_exists", "writes_count", "pass_gates_count", "disallowed_token_found", "dry_run_only", "executed"])
    _write_csv(Path(artifacts["gate_validation_csv"]), gate_rows, ["source", "gate", "passed", "value", "expected"])
    _write_csv(Path(artifacts["hard_fail_validation_csv"]), hard_fail_rows, ["condition", "present", "count", "detail"])
    _write_csv(Path(artifacts["runbook_validation_csv"]), runbook_rows, ["requirement", "present"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "validation_passed": validation_passed,
                    "artifact_fail_count": artifact_fail_count,
                    "command_fail_count": command_fail_count,
                    "gate_fail_count": gate_fail_count,
                    "hard_fail_present": hard_fail_present,
                    "runbook_fail_count": runbook_fail_count,
                    "recommendation": recommendation,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
