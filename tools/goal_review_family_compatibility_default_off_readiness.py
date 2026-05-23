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

DEFAULT_AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DEFAULT_SUMMARY = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_default_off_summary.json"
DEFAULT_ENABLED_SUMMARY = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_skeleton_explicit_enabled_eval_only_summary.json"
DEFAULT_CONTRACT_AUDIT = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_contract_audit_summary.json"
DEFAULT_SWITCH_CONFIG = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_config_v1.json"
DEFAULT_LOG_SCHEMA = DEFAULT_AGENT_STATE / "goal_family_compatibility_switch_contract_audit_log_schema.csv"
DEFAULT_OUTPUT_PREFIX = DEFAULT_AGENT_STATE / "goal_family_compatibility_default_off_readiness"

TARGET_TOP1 = 0.75


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def _split_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_clean(row.get("split")): row for row in summary.get("split_metrics") or [] if _clean(row.get("split"))}


def _audit_split_map(audit: dict[str, Any], mode: str) -> dict[str, dict[str, Any]]:
    return {
        _clean(row.get("split")): row
        for row in audit.get("split_metrics") or []
        if _clean(row.get("audit_mode")) == mode and _clean(row.get("split"))
    }


def _gate(stage: str, gate: str, passed: bool, severity: str, value: Any, expected: str, evidence: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "gate": gate,
        "passed": bool(passed),
        "severity": severity,
        "value": value,
        "expected": expected,
        "evidence": evidence,
    }


def _collect_artifacts(*reports: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for report in reports:
        for name, path_value in (report.get("artifacts") or {}).items():
            path = Path(_clean(path_value))
            key = str(path)
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "artifact": name,
                    "path": key,
                    "exists": path.exists(),
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                }
            )
    return rows


def _split_snapshot(default_summary: dict[str, Any], enabled_summary: dict[str, Any], audit: dict[str, Any]) -> list[dict[str, Any]]:
    default_splits = _split_map(default_summary)
    enabled_splits = _split_map(enabled_summary)
    audit_default = _audit_split_map(audit, "default_off_broader")
    audit_shadow = _audit_split_map(audit, "explicit_shadow")
    result: list[dict[str, Any]] = []
    for split in sorted(set(default_splits) | set(enabled_splits) | set(audit_default) | set(audit_shadow)):
        default_row = default_splits.get(split, {})
        enabled_row = enabled_splits.get(split, {})
        audit_default_row = audit_default.get(split, {})
        audit_shadow_row = audit_shadow.get(split, {})
        result.append(
            {
                "split": split,
                "default_top1_matrix": default_row.get("effective_hit1_rate_matrix", ""),
                "default_net_vs_gated": default_row.get("effective_net_vs_gated", ""),
                "default_effective_allowed": default_row.get("effective_allowed_count", ""),
                "audit_default_fallback": audit_default_row.get("fallback_count", ""),
                "audit_default_rows": audit_default_row.get("policy_rows", ""),
                "shadow_top1_matrix": enabled_row.get("effective_hit1_rate_matrix", ""),
                "shadow_net_vs_gated": enabled_row.get("effective_net_vs_gated", ""),
                "shadow_effective_allowed": enabled_row.get("effective_allowed_count", ""),
                "shadow_new_loss": enabled_row.get("new_residual_loss_count", audit_shadow_row.get("new_residual_loss", "")),
                "gap_to_75_top1": round(TARGET_TOP1 - _to_float(default_row.get("effective_hit1_rate_matrix")), 6),
            }
        )
    return result


def _build_checklist(
    *,
    default_summary: dict[str, Any],
    enabled_summary: dict[str, Any],
    audit: dict[str, Any],
    config: dict[str, Any],
    schema_rows: list[dict[str, Any]],
    artifact_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    default_splits = _split_map(default_summary)
    enabled_splits = _split_map(enabled_summary)
    audit_default = _audit_split_map(audit, "default_off_broader")
    audit_shadow = _audit_split_map(audit, "explicit_shadow")
    schema_missing = [row for row in schema_rows if _clean(row.get("status")) != "present"]
    hard_checks = audit.get("hard_checks") or []
    checklist = [
        _gate(
            "7.9",
            "default_config_disabled",
            config.get("goal_family_compatibility_switch_enabled") is False,
            "blocker",
            config.get("goal_family_compatibility_switch_enabled"),
            "False",
            "goal_family_compatibility_switch_config_v1.json",
        ),
        _gate(
            "7.9",
            "default_off_contract_passed",
            bool(default_summary.get("contract_passed")) and not bool(default_summary.get("runtime_active")),
            "blocker",
            {"contract_passed": default_summary.get("contract_passed"), "runtime_active": default_summary.get("runtime_active")},
            "contract_passed=true and runtime_active=false",
            "default_off_summary.json",
        ),
        _gate(
            "7.9",
            "explicit_shadow_eval_only",
            bool(enabled_summary.get("contract_passed"))
            and bool(enabled_summary.get("eval_only"))
            and bool(enabled_summary.get("no_search_integration"))
            and bool(enabled_summary.get("no_goal_searcher_change")),
            "blocker",
            {
                "contract_passed": enabled_summary.get("contract_passed"),
                "eval_only": enabled_summary.get("eval_only"),
                "no_search_integration": enabled_summary.get("no_search_integration"),
                "no_goal_searcher_change": enabled_summary.get("no_goal_searcher_change"),
            },
            "explicit shadow stays eval-only",
            "explicit_enabled_eval_only_summary.json",
        ),
        _gate(
            "7.9",
            "default_off_no_overrides",
            all(_to_int(row.get("effective_allowed_count")) == 0 for row in default_splits.values()),
            "blocker",
            {split: row.get("effective_allowed_count") for split, row in default_splits.items()},
            "0 for every split",
            "default_off split_metrics",
        ),
        _gate(
            "7.9",
            "default_off_keeps_baseline",
            all(_to_int(row.get("effective_net_vs_gated")) == 0 for row in default_splits.values()),
            "blocker",
            {split: row.get("effective_net_vs_gated") for split, row in default_splits.items()},
            "0 for every split",
            "default_off split_metrics",
        ),
        _gate(
            "8.0",
            "broader_contract_hard_gates_passed",
            bool(audit.get("hard_gate_passed")),
            "blocker",
            audit.get("hard_gate_passed"),
            "true",
            "goal_family_compatibility_switch_contract_audit_summary.json",
        ),
        _gate(
            "8.0",
            "broader_rows_covered",
            _to_int(audit.get("selected_policy_rows")) > 0,
            "blocker",
            audit.get("selected_policy_rows"),
            ">0",
            "selected policy broader rows",
        ),
        _gate(
            "8.0",
            "broader_default_off_all_fallback",
            all(_to_int(row.get("fallback_count")) == _to_int(row.get("policy_rows")) for row in audit_default.values()),
            "blocker",
            {split: f"{row.get('fallback_count')}/{row.get('policy_rows')}" for split, row in audit_default.items()},
            "fallback_count == policy_rows for every split",
            "contract audit split_metrics",
        ),
        _gate(
            "8.0",
            "heldout_not_used_for_selection",
            any(_clean(row.get("gate")) == "heldout_not_used_for_policy_selection" and _to_bool(row.get("passed")) for row in hard_checks),
            "blocker",
            next((row.get("value") for row in hard_checks if _clean(row.get("gate")) == "heldout_not_used_for_policy_selection"), ""),
            "dev_oof selected and OOF gates passed",
            "contract audit hard_checks",
        ),
        _gate(
            "8.0",
            "explicit_shadow_no_new_loss",
            all(_to_int(row.get("new_residual_loss")) == 0 for row in audit_shadow.values())
            and all(_to_int(row.get("new_residual_loss_count")) == 0 for row in enabled_splits.values()),
            "blocker",
            {
                "audit": {split: row.get("new_residual_loss") for split, row in audit_shadow.items()},
                "summary": {split: row.get("new_residual_loss_count") for split, row in enabled_splits.items()},
            },
            "0 for every split",
            "contract audit + explicit shadow summary",
        ),
        _gate(
            "8.1",
            "log_schema_exact",
            len(schema_missing) == 0 and len(schema_rows) > 0,
            "blocker",
            {"missing_or_alias": len(schema_missing), "schema_rows": len(schema_rows)},
            "all required log fields present exactly",
            "goal_family_compatibility_switch_contract_audit_log_schema.csv",
        ),
        _gate(
            "8.1",
            "artifacts_exist",
            all(_to_bool(row.get("exists")) and _to_int(row.get("size_bytes")) > 0 for row in artifact_rows),
            "blocker",
            {"artifact_count": len(artifact_rows), "missing": [row["artifact"] for row in artifact_rows if not _to_bool(row.get("exists"))]},
            "all referenced artifacts exist and are non-empty",
            "artifact manifest",
        ),
        _gate(
            "8.2",
            "production_not_ready_declared",
            True,
            "guardrail",
            "production_ready=false",
            "readiness review only permits later eval-only design",
            "stage policy",
        ),
        _gate(
            "8.2",
            "target_gap_recorded",
            True,
            "guardrail",
            "default-off heldout Top1 remains far below 75%",
            "record residual gap instead of claiming accuracy readiness",
            "split snapshot",
        ),
    ]
    return checklist


def _decision(checklist: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = [row for row in checklist if _clean(row.get("severity")) == "blocker" and not _to_bool(row.get("passed"))]
    guardrails = [row for row in checklist if _clean(row.get("severity")) == "guardrail"]
    return {
        "readiness_passed": len(blockers) == 0,
        "allowed_next_stage": "eval_only_design_draft" if not blockers else "fix_readiness_blockers",
        "production_ready": False,
        "online_switch_allowed": False,
        "blocker_count": len(blockers),
        "guardrail_count": len(guardrails),
        "decision": "allow_next_eval_only_design_only" if not blockers else "do_not_continue_until_blockers_fixed",
        "reason": (
            "All default-off contract, schema, fallback, and no-leakage gates passed. This only allows the next offline design stage; it does not allow production integration."
            if not blockers
            else "One or more blocker gates failed; do not proceed."
        ),
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Default-off Readiness Review",
        "",
        "Stage 8.2 summarizes offline gates only. It does not train, tune, modify GoalSearcher, or connect the switch online.",
        "",
        "## Decision",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["readiness_passed", report["decision"]["readiness_passed"]],
                ["allowed_next_stage", report["decision"]["allowed_next_stage"]],
                ["production_ready", report["decision"]["production_ready"]],
                ["online_switch_allowed", report["decision"]["online_switch_allowed"]],
                ["blocker_count", report["decision"]["blocker_count"]],
                ["recommendation", report["recommendation"]],
            ]
        ),
        "",
        "## Checklist",
        "",
        _md_table(
            [["stage", "gate", "passed", "severity", "value", "expected"]]
            + [[row["stage"], row["gate"], row["passed"], row["severity"], row["value"], row["expected"]] for row in report["checklist"]]
        ),
        "",
        "## Split Snapshot",
        "",
        _md_table(
            [["split", "default_top1", "default_net", "shadow_top1", "shadow_net", "gap_to_75"]]
            + [
                [
                    row["split"],
                    row["default_top1_matrix"],
                    row["default_net_vs_gated"],
                    row["shadow_top1_matrix"],
                    row["shadow_net_vs_gated"],
                    row["gap_to_75_top1"],
                ]
                for row in report["split_snapshot"]
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["name", "path"]] + [[key, value] for key, value in report["artifacts"].items()]),
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
    parser = argparse.ArgumentParser(description="Stage 8.2 default-off readiness review")
    parser.add_argument("--default-summary", default=str(DEFAULT_DEFAULT_SUMMARY))
    parser.add_argument("--enabled-summary", default=str(DEFAULT_ENABLED_SUMMARY))
    parser.add_argument("--contract-audit", default=str(DEFAULT_CONTRACT_AUDIT))
    parser.add_argument("--switch-config", default=str(DEFAULT_SWITCH_CONFIG))
    parser.add_argument("--log-schema", default=str(DEFAULT_LOG_SCHEMA))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    default_summary = _read_json(Path(args.default_summary))
    enabled_summary = _read_json(Path(args.enabled_summary))
    contract_audit = _read_json(Path(args.contract_audit))
    switch_config = _read_json(Path(args.switch_config))
    schema_rows = _read_csv(Path(args.log_schema))
    artifact_rows = _collect_artifacts(default_summary, enabled_summary, contract_audit)
    split_snapshot = _split_snapshot(default_summary, enabled_summary, contract_audit)
    checklist = _build_checklist(
        default_summary=default_summary,
        enabled_summary=enabled_summary,
        audit=contract_audit,
        config=switch_config,
        schema_rows=schema_rows,
        artifact_rows=artifact_rows,
    )
    decision = _decision(checklist)
    recommendation = (
        "allow_stage_8_3_eval_only_design_draft_not_online"
        if decision["readiness_passed"]
        else "fix_readiness_blockers_before_next_stage"
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "checklist_csv": str(output_prefix.with_name(output_prefix.name + "_checklist.csv")),
        "split_snapshot_csv": str(output_prefix.with_name(output_prefix.name + "_split_snapshot.csv")),
        "artifact_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 8.2 default-off readiness review",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "default_off_only": True,
        "readiness_scope": "offline gates only; not production readiness",
        "decision": decision,
        "recommendation": recommendation,
        "checklist": checklist,
        "split_snapshot": split_snapshot,
        "artifact_manifest": artifact_rows,
        "recommended_next_stage": "Stage 8.3: write an eval-only design draft for a default-off switch package; keep it offline and do not modify GoalSearcher.",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "inputs": {
            "default_summary": str(Path(args.default_summary)),
            "enabled_summary": str(Path(args.enabled_summary)),
            "contract_audit": str(Path(args.contract_audit)),
            "switch_config": str(Path(args.switch_config)),
            "log_schema": str(Path(args.log_schema)),
        },
        "artifacts": artifacts,
        "anti_drift_conclusion": "The review is a cross-stage gate summary. It does not add rules, train models, tune thresholds, use heldout for selection, or connect the switch online.",
    }

    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["checklist_csv"]), checklist, ["stage", "gate", "passed", "severity", "value", "expected", "evidence"])
    _write_csv(
        Path(artifacts["split_snapshot_csv"]),
        split_snapshot,
        [
            "split",
            "default_top1_matrix",
            "default_net_vs_gated",
            "default_effective_allowed",
            "audit_default_fallback",
            "audit_default_rows",
            "shadow_top1_matrix",
            "shadow_net_vs_gated",
            "shadow_effective_allowed",
            "shadow_new_loss",
            "gap_to_75_top1",
        ],
    )
    _write_csv(Path(artifacts["artifact_manifest_csv"]), artifact_rows, ["artifact", "path", "exists", "size_bytes"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "readiness_passed": decision["readiness_passed"],
                    "allowed_next_stage": decision["allowed_next_stage"],
                    "production_ready": decision["production_ready"],
                    "online_switch_allowed": decision["online_switch_allowed"],
                    "blocker_count": decision["blocker_count"],
                    "recommendation": recommendation,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_snapshot": split_snapshot,
                "checklist": checklist,
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
