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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_family_compatibility_8x_handoff"

INPUTS = {
    "manifest": AGENT_STATE / "goal_family_compatibility_eval_only_package_manifest.json",
    "validation": AGENT_STATE / "goal_family_compatibility_eval_only_package_validation_summary.json",
    "readiness": AGENT_STATE / "goal_family_compatibility_default_off_readiness_summary.json",
    "contract_audit": AGENT_STATE / "goal_family_compatibility_switch_contract_audit_summary.json",
    "default_off": AGENT_STATE / "goal_family_compatibility_switch_skeleton_default_off_summary.json",
    "explicit_shadow": AGENT_STATE / "goal_family_compatibility_switch_skeleton_explicit_enabled_eval_only_summary.json",
    "design_draft": AGENT_STATE / "goal_family_compatibility_default_off_switch_design_draft.md",
    "runbook": AGENT_STATE / "goal_family_compatibility_eval_only_package_runbook.md",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return ""


def _split_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("split")): row for row in summary.get("split_metrics") or summary.get("split_snapshot") or []}


def _hard_check(summary: dict[str, Any], name: str) -> bool:
    for row in summary.get("hard_checks") or []:
        if row.get("gate") == name:
            return _to_bool(row.get("passed"))
    return False


def _gate(name: str, passed: bool, evidence: Any, required: str, category: str = "blocker") -> dict[str, Any]:
    return {
        "gate": name,
        "passed": bool(passed),
        "category": category,
        "evidence": evidence,
        "required": required,
    }


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
    if not rows:
        return ""
    table = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        table.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(table)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    split_rows = report["split_snapshot"]
    lines = [
        "# Stage 8.6 Closeout / Handoff",
        "",
        "This handoff closes the 8.x default-off eval-only package and returns the roadmap to the 9.x accuracy-gap track.",
        "",
        "## Decision",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closeout_passed", report["closeout_passed"]],
                ["production_ready", report["production_ready"]],
                ["online_switch_allowed", report["online_switch_allowed"]],
                ["next_stage", report["recommended_next_stage"]],
                ["default_off_heldout_top1", _pct(report["accuracy_context"]["default_off_heldout_top1"])],
                ["explicit_shadow_heldout_top1", _pct(report["accuracy_context"]["explicit_shadow_heldout_top1"])],
                ["gap_to_75_top1", _pct(report["accuracy_context"]["heldout_gap_to_75_top1"])],
            ]
        ),
        "",
        "## Split Snapshot",
        "",
        _md_table(
            [["split", "default_top1", "shadow_top1", "shadow_net_vs_gated", "shadow_new_loss", "gap_to_75"]]
            + [
                [
                    row["split"],
                    _pct(row["default_top1_matrix"]),
                    _pct(row["shadow_top1_matrix"]),
                    row["shadow_net_vs_gated"],
                    row["shadow_new_loss"],
                    _pct(row["gap_to_75_top1"]),
                ]
                for row in split_rows
            ]
        ),
        "",
        "## Gates",
        "",
        _md_table([["gate", "passed", "category", "required"]] + [[row["gate"], row["passed"], row["category"], row["required"]] for row in report["closeout_gates"]]),
        "",
        "## What 8.x Solved",
        "",
    ]
    lines.extend(f"- {item}" for item in report["what_8x_solved"])
    lines.extend(["", "## What 8.x Did Not Solve", ""])
    lines.extend(f"- {item}" for item in report["what_8x_did_not_solve"])
    lines.extend(["", "## Stage 9.0 Entry Contract", ""])
    lines.extend(f"- {item}" for item in report["stage_9_entry_contract"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_report(output_prefix: Path) -> dict[str, Any]:
    manifest = _read_json(INPUTS["manifest"])
    validation = _read_json(INPUTS["validation"])
    readiness = _read_json(INPUTS["readiness"])
    contract = _read_json(INPUTS["contract_audit"])
    default_off = _read_json(INPUTS["default_off"])
    explicit_shadow = _read_json(INPUTS["explicit_shadow"])

    readiness_decision = readiness.get("decision") or {}
    default_rows = _split_map(default_off)
    shadow_rows = _split_map(explicit_shadow)
    readiness_rows = list(readiness.get("split_snapshot") or [])
    heldout = next((row for row in readiness_rows if row.get("split") == "heldout"), {})

    gates = [
        _gate("manifest_validation_passed", validation.get("validation_passed") is True, validation.get("validation_passed"), "true"),
        _gate("manifest_artifacts_clean", _to_int(validation.get("artifact_fail_count")) == 0, validation.get("artifact_fail_count"), "0"),
        _gate("manifest_commands_clean", _to_int(validation.get("command_fail_count")) == 0, validation.get("command_fail_count"), "0"),
        _gate("manifest_gates_clean", _to_int(validation.get("gate_fail_count")) == 0, validation.get("gate_fail_count"), "0"),
        _gate("manifest_hard_fails_absent", validation.get("hard_fail_present") is False, validation.get("hard_fail_present"), "false"),
        _gate("readiness_passed", readiness_decision.get("readiness_passed") is True, readiness_decision.get("readiness_passed"), "true"),
        _gate("blocker_count_zero", _to_int(readiness_decision.get("blocker_count")) == 0, readiness_decision.get("blocker_count"), "0"),
        _gate("contract_hard_gate_passed", contract.get("hard_gate_passed") is True, contract.get("hard_gate_passed"), "true"),
        _gate("schema_warning_count_zero", _to_int(contract.get("schema_warning_count")) == 0, contract.get("schema_warning_count"), "0"),
        _gate("heldout_not_used_for_policy_selection", _hard_check(contract, "heldout_not_used_for_policy_selection"), "contract hard check", "passed"),
        _gate("default_off_runtime_inactive", default_off.get("runtime_active") is False, default_off.get("runtime_active"), "false"),
        _gate(
            "default_off_no_effective_override",
            bool(default_rows) and all(_to_int(row.get("effective_allowed_count")) == 0 for row in default_rows.values()),
            {split: row.get("effective_allowed_count") for split, row in default_rows.items()},
            "0 for every split",
        ),
        _gate(
            "default_off_no_top1_change",
            bool(default_rows) and all(_to_int(row.get("effective_net_vs_gated")) == 0 for row in default_rows.values()),
            {split: row.get("effective_net_vs_gated") for split, row in default_rows.items()},
            "0 for every split",
        ),
        _gate("explicit_shadow_eval_only", explicit_shadow.get("eval_only") is True, explicit_shadow.get("eval_only"), "true"),
        _gate("explicit_shadow_no_search_integration", explicit_shadow.get("no_search_integration") is True, explicit_shadow.get("no_search_integration"), "true"),
        _gate(
            "explicit_shadow_no_new_loss",
            bool(shadow_rows) and all(_to_int(row.get("new_residual_loss_count")) == 0 for row in shadow_rows.values()),
            {split: row.get("new_residual_loss_count") for split, row in shadow_rows.items()},
            "0 for every split",
        ),
        _gate("production_not_ready", manifest.get("production_ready") is False and readiness_decision.get("production_ready") is False, {"manifest": manifest.get("production_ready"), "readiness": readiness_decision.get("production_ready")}, "false"),
        _gate("online_switch_not_allowed", manifest.get("online_switch_allowed") is False and readiness_decision.get("online_switch_allowed") is False, {"manifest": manifest.get("online_switch_allowed"), "readiness": readiness_decision.get("online_switch_allowed")}, "false"),
        _gate("accuracy_target_not_claimed", float(heldout.get("default_top1_matrix") or 0) < 0.75, heldout.get("default_top1_matrix"), "<0.75", "guardrail"),
    ]
    closeout_passed = all(_to_bool(row["passed"]) for row in gates if row["category"] == "blocker")

    artifact_rows = []
    for name, path in INPUTS.items():
        artifact_rows.append(
            {
                "artifact": name,
                "path": str(path.relative_to(PROJECT_ROOT)),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            }
        )

    next_stage_contract = [
        {"item": "stage", "value": "9.0 accuracy gap restart / decomposition", "status": "allowed_after_8x_closeout"},
        {"item": "allowed", "value": "read anchor-clean heldout/hard/dev artifacts and split Top80_missing vs Top80_present_but_wrong_rank", "status": "allowed"},
        {"item": "allowed", "value": "produce object-family/province/book/source-file gap tables", "status": "allowed"},
        {"item": "forbidden", "value": "do not train or tune in 9.0", "status": "forbidden"},
        {"item": "forbidden", "value": "do not modify GoalSearcher or connect eval-only switch online", "status": "forbidden"},
        {"item": "forbidden", "value": "do not use heldout to choose policies or thresholds", "status": "forbidden"},
        {"item": "exit_gate", "value": "a ranked gap table exists and one high-yield general bucket is selected for later design", "status": "required"},
    ]

    output_artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gates_csv": str(output_prefix.with_name(output_prefix.name + "_gates.csv")),
        "split_snapshot_csv": str(output_prefix.with_name(output_prefix.name + "_split_snapshot.csv")),
        "artifact_index_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_index.csv")),
        "stage_9_entry_contract_csv": str(output_prefix.with_name(output_prefix.name + "_stage_9_entry_contract.csv")),
    }

    return {
        "stage": "Goal LTR v1 / stage 8.6 close 8.x and return-to-9.x accuracy-gap handoff",
        "eval_only": True,
        "closeout_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "production_ready": False,
        "online_switch_allowed": False,
        "closeout_passed": closeout_passed,
        "recommended_next_stage": "9.0 accuracy gap restart / decomposition",
        "accuracy_context": {
            "target_heldout_top1": 0.75,
            "default_off_heldout_top1": heldout.get("default_top1_matrix"),
            "explicit_shadow_heldout_top1": heldout.get("shadow_top1_matrix"),
            "heldout_gap_to_75_top1": heldout.get("gap_to_75_top1"),
            "speed_target": "100 online queries <= 120 seconds after index exists",
            "note": "8.x closes an eval-only package boundary; it does not close the 75% accuracy gap.",
        },
        "package_status": {
            "manifest_validation_passed": validation.get("validation_passed"),
            "readiness_passed": readiness_decision.get("readiness_passed"),
            "contract_hard_gate_passed": contract.get("hard_gate_passed"),
            "schema_warning_count": contract.get("schema_warning_count"),
            "selected_policy_rows": contract.get("selected_policy_rows"),
            "selected_policy": contract.get("selected_policy"),
        },
        "split_snapshot": readiness_rows,
        "closeout_gates": gates,
        "artifact_index": artifact_rows,
        "stage_9_entry_contract": [row["value"] for row in next_stage_contract],
        "stage_9_entry_contract_rows": next_stage_contract,
        "what_8x_solved": [
            "Default-off eval-only package boundary is documented and validated.",
            "Switch config, fallback behavior, required log fields, command order, and hard fail conditions are auditable.",
            "Explicit shadow run shows possible compatibility benefit with no new residual loss in current splits.",
            "Heldout was not used for policy selection.",
        ],
        "what_8x_did_not_solve": [
            "It does not improve the default search Top1 because the package remains default-off.",
            "It does not close the gap to 75% heldout Top1.",
            "It does not solve Top80 recall gaps or wrong-rank cases.",
            "It does not authorize online integration or production readiness.",
        ],
        "anti_drift_conclusion": "Stage 8.6 is a closeout/handoff only. The next work must return to gap decomposition before any new training, tuning, or search-chain changes.",
        "artifacts": output_artifacts,
        "inputs": {name: str(path) for name, path in INPUTS.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 8.6 closeout / handoff for the default-off eval-only compatibility package.")
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    output_prefix = Path(args.output_prefix)
    report = build_report(output_prefix)
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)

    artifacts = report["artifacts"]
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["gates_csv"]), report["closeout_gates"], ["gate", "passed", "category", "evidence", "required"])
    _write_csv(Path(artifacts["split_snapshot_csv"]), report["split_snapshot"], ["split", "default_top1_matrix", "default_net_vs_gated", "default_effective_allowed", "audit_default_fallback", "audit_default_rows", "shadow_top1_matrix", "shadow_net_vs_gated", "shadow_effective_allowed", "shadow_new_loss", "gap_to_75_top1"])
    _write_csv(Path(artifacts["artifact_index_csv"]), report["artifact_index"], ["artifact", "path", "exists", "size_bytes"])
    _write_csv(Path(artifacts["stage_9_entry_contract_csv"]), report["stage_9_entry_contract_rows"], ["item", "value", "status"])

    print(json.dumps({"summary": {key: report[key] for key in ["stage", "closeout_passed", "production_ready", "online_switch_allowed", "recommended_next_stage", "elapsed_sec"]}, "accuracy_context": report["accuracy_context"], "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
