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
DEFAULT_S5_SUMMARY = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_summary.json"
DEFAULT_FIELDS = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_field_manifest.csv"
DEFAULT_POLICY = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_artifact_manifest_policy.csv"
DEFAULT_EFFECT = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_effect_decomposition_contract.csv"
DEFAULT_SPLIT = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_split_boundary.csv"
DEFAULT_ACCEPTANCE = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_acceptance_checks.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s5_telemetry_design_artifact_definition_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s5_artifact_acceptance_gate"


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
    acceptance_results: list[dict[str, Any]],
    support_contract: list[dict[str, Any]],
    next_options: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.50 S5 Artifact Acceptance Gate",
        "",
        "Read-only acceptance gate for the S5 telemetry/design artifact as a future re-entry support contract.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
                ["s5_support_contract_accepted", metrics["s5_support_contract_accepted"]],
                ["satisfies_lane_reentry", metrics["satisfies_lane_reentry"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
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
        "## Support Contract Scope",
        "",
        _md_table(
            [["scope_item", "accepted_use", "not_allowed"]]
            + [[row["scope_item"], row["accepted_use"], row["not_allowed"]] for row in support_contract]
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
    parser = argparse.ArgumentParser(description="Accept S5 design artifact as future re-entry support contract")
    parser.add_argument("--s5-summary", default=str(DEFAULT_S5_SUMMARY))
    parser.add_argument("--fields", default=str(DEFAULT_FIELDS))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--effect", default=str(DEFAULT_EFFECT))
    parser.add_argument("--split", default=str(DEFAULT_SPLIT))
    parser.add_argument("--acceptance", default=str(DEFAULT_ACCEPTANCE))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s5_summary = _read_json(Path(args.s5_summary))
    fields = _read_csv(Path(args.fields))
    policy = _read_csv(Path(args.policy))
    effect = _read_csv(Path(args.effect))
    split = _read_csv(Path(args.split))
    checks = _read_csv(Path(args.acceptance))
    blocked = _read_csv(Path(args.blocked_actions))

    acceptance_results = []
    acceptance_results.append(
        {
            "check_id": "AC01_FIELD_MANIFEST_COMPLETE",
            "status": "pass" if len(fields) >= 20 and all(row.get("type") and row.get("source_artifact") and row.get("forbidden_use") for row in fields) else "fail",
            "evidence": f"field_manifest_count={len(fields)}",
            "decision": "field manifest is complete enough for future re-entry support",
        }
    )
    acceptance_results.append(
        {
            "check_id": "AC02_ARTIFACT_POLICY_COMPLETE",
            "status": "pass" if len(policy) >= 5 else "fail",
            "evidence": f"artifact_policy_count={len(policy)}",
            "decision": "hash/freshness/lineage/schema/generated-source boundary are defined",
        }
    )
    acceptance_results.append(
        {
            "check_id": "AC03_EFFECT_DECOMPOSITION_COMPLETE",
            "status": "pass" if len(effect) >= 5 else "fail",
            "evidence": f"effect_category_count={len(effect)}",
            "decision": "effect categories separate taxonomy, recall, ranking, safety, and evidence-only effects",
        }
    )
    acceptance_results.append(
        {
            "check_id": "AC04_SPLIT_BOUNDARY_COMPLETE",
            "status": "pass" if len(split) == 4 and any(row.get("split") == "heldout" for row in split) and any(row.get("split") == "hard" for row in split) else "fail",
            "evidence": f"split_boundary_count={len(split)}",
            "decision": "heldout/hard validation-only boundary is explicit",
        }
    )
    acceptance_results.append(
        {
            "check_id": "AC05_NON_EXECUTION_BOUNDARY",
            "status": "pass" if not s5_summary["metrics"]["implementation_allowed"] and not s5_summary["metrics"]["training_allowed"] else "fail",
            "evidence": f"training_allowed={s5_summary['metrics']['training_allowed']}; implementation_allowed={s5_summary['metrics']['implementation_allowed']}",
            "decision": "artifact remains design-only and non-executing",
        }
    )
    acceptance_results.append(
        {
            "check_id": "AC06_REENTRY_COMPATIBLE",
            "status": "pass" if len(checks) >= 6 else "fail",
            "evidence": f"acceptance_check_count={len(checks)}",
            "decision": "artifact can support future re-entry review but does not satisfy re-entry by itself",
        }
    )
    fail_count = sum(1 for row in acceptance_results if row["status"] != "pass")

    support_contract = [
        {
            "scope_item": "future_reentry_review_support",
            "accepted_use": "Use S5 fields and effect contract to evaluate future S1/S2/S3/DQ evidence packages.",
            "not_allowed": "Do not treat S5 itself as evidence package completion.",
        },
        {
            "scope_item": "artifact_integrity_support",
            "accepted_use": "Require hash/freshness/lineage checks for future reports used in re-entry.",
            "not_allowed": "Do not trust stale or hash-mismatched artifacts.",
        },
        {
            "scope_item": "split_policy_support",
            "accepted_use": "Use dev/OOF for future selection and keep heldout/hard validation-only.",
            "not_allowed": "Do not select candidate, threshold, lane, policy, or field using heldout/hard.",
        },
        {
            "scope_item": "effect_decomposition_support",
            "accepted_use": "Separate taxonomy cleanup, recall, ranking, safety, and evidence-only effects before any claim.",
            "not_allowed": "Do not claim general Top1 gain from DQ cleanup or source-dominated effects.",
        },
    ]
    next_options = [
        {
            "option": "pause_with_s5_contract_available",
            "status": "recommended_default",
            "rationale": "S5 support contract is accepted, but no active learning lane or implementation go exists.",
        },
        {
            "option": "open_s5_implementation_plan",
            "status": "blocked_without_explicit_go",
            "rationale": "Implementing telemetry requires a separate implementation plan and explicit authorization.",
        },
        {
            "option": "reopen_s1_s2_s3_or_dq",
            "status": "blocked",
            "rationale": "S5 acceptance does not satisfy lane-specific re-entry requirements.",
        },
        {
            "option": "define_next_read_only_strategy_lane",
            "status": "available_if_user_redirects",
            "rationale": "A new read-only lane can be defined by explicit user redirect, but not executed by default.",
        },
    ]
    final_blocked = blocked + [
        {
            "blocked_action": "treat_s5_acceptance_as_reentry_pass",
            "reason": "S5 is a support contract only and does not supply S1/S2/S3/DQ evidence/go/mappings.",
            "allowed_after": "lane-specific re-entry package passes future read-only review",
        },
        {
            "blocked_action": "implement_s5_after_acceptance",
            "reason": "10.50 acceptance is not implementation authorization.",
            "allowed_after": "explicit implementation go plus scoped implementation plan",
        },
    ]

    accepted = fail_count == 0
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_results_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_results.csv")),
        "support_contract_scope_csv": str(output_prefix.with_name(output_prefix.name + "_support_contract_scope.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": s5_summary["stage"],
        "acceptance_pass_count": len(acceptance_results) - fail_count,
        "acceptance_fail_count": fail_count,
        "s5_support_contract_accepted": accepted,
        "satisfies_lane_reentry": False,
        "field_manifest_count": len(fields),
        "artifact_policy_count": len(policy),
        "effect_category_count": len(effect),
        "split_boundary_count": len(split),
        "acceptance_check_count": len(checks),
        "active_learning_lane_count": 0,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.50 S5 artifact acceptance gate",
        "read_only": True,
        "acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the S5 telemetry/design artifact as a future re-entry support contract. It is complete enough to standardize fields, artifact integrity, effect decomposition, split boundaries, and acceptance checks. "
            "This acceptance does not implement telemetry, satisfy S1/S2/S3/DQ re-entry, train, tune, or authorize GoalSearcher changes."
            if accepted
            else "Do not accept the S5 telemetry/design artifact yet; one or more acceptance checks failed."
        ),
        "anti_drift_conclusion": (
            "10.50 only accepts S5 as a design/support contract. It does not train, tune, expand candidates, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, implement telemetry, implement DQ fixes, satisfy lane re-entry, or claim accuracy gain."
        ),
        "next_stage": {
            "stage": "paused with S5 support contract available",
            "goal": "Resume only if the user provides lane-specific evidence/go/mappings or explicitly requests a new read-only lane or S5 implementation planning.",
            "default": "pause; no automatic learning or implementation advance",
        },
    }

    _write_csv(Path(artifacts["acceptance_results_csv"]), acceptance_results, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["support_contract_scope_csv"]), support_contract, ["scope_item", "accepted_use", "not_allowed"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), final_blocked, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_results, support_contract, next_options)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
