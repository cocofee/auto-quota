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
SPLITS_DIR = PROJECT_ROOT / "data" / "goal_search" / "splits"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_11x_parser_recall_freeze_gate_review_summary.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_validation_boundary_go_no_go"


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


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 11.3 Validation Boundary / Go-No-Go",
        "",
        "Read-only validation boundary definition for the frozen 11.1 parser/query recall candidate.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Anti-drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：11.3 validation boundary definition / explicit validation go-no-go 已完成。"
        f"validation_go_present={str(report['metrics']['explicit_validation_go_present']).lower()}；"
        f"validation_allowed_now={str(report['metrics']['validation_allowed_now']).lower()}；"
        f"heldout_rows={report['metrics']['heldout_rows']}；hard_rows={report['metrics']['hard_rows']}；"
        "默认仍不跑 heldout/hard validation。"
    )
    next_text = (
        "下一步：只有用户明确说 go: run 11.4 heldout/hard A/B validation，才允许实现/运行 validation harness；"
        "否则保持 frozen candidate parked，不验证、不上线、不改 GoalSearcher。"
    )
    if "当前状态：11.2 parser recall scorecard + loss slice freeze gate 已完成。" in text:
        start = text.index("当前状态：11.2 parser recall scorecard + loss slice freeze gate 已完成。")
        end = text.index("禁止：继续 S2、训练、调参", start)
        text = text[:start] + current + "\n" + next_text + "\n" + text[end:]
    marker = "          <tr>\n            <td>11.2 parser recall freeze gate review</td>"
    row = (
        "          <tr>\n"
        "            <td>11.3 validation boundary go/no-go</td>\n"
        "            <td>只读定义 heldout/hard validation 边界、A/B 命令合约、stop conditions 和 explicit go 要求。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_validation_boundary_go_no_go_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_11x_validation_boundary_go_no_go_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    freeze_summary = _read_json(args.freeze_summary)
    frozen_manifest = _read_csv(args.frozen_manifest)
    heldout_path = SPLITS_DIR / "heldout.jsonl"
    hard_path = SPLITS_DIR / "hard.jsonl"
    split_summary_path = SPLITS_DIR / "split_summary.json"
    split_summary = _read_json(split_summary_path) if split_summary_path.exists() else {}
    explicit_go = bool(args.explicit_validation_go)
    frozen_ready = freeze_summary["metrics"].get("freeze_decision") == "freeze_dev_oof_candidate"
    split_ready = heldout_path.exists() and hard_path.exists()
    validation_allowed_now = explicit_go and frozen_ready and split_ready

    boundary_rows = [
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "heldout/hard may not choose, tune, prune, expand, or reorder hints; they only validate the frozen manifest.",
        },
        {
            "boundary": "candidate_scope",
            "decision": "frozen_9_hint_rows_only",
            "details": "Only the 9 rows in goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv are in scope.",
        },
        {
            "boundary": "comparison_design",
            "decision": "requires_ab_baseline_vs_frozen_hints",
            "details": "A valid validation run must compare baseline behavior to the frozen hint behavior; current-code-only output is insufficient.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "No GoalSearcher production wiring, no rollout, and no threshold change are allowed in validation.",
        },
        {
            "boundary": "claim_boundary",
            "decision": "no_general_top1_claim_until_validation_passes",
            "details": "Candidate-pool gains from dev/OOF remain diagnostic until validation artifacts pass.",
        },
    ]
    command_contract = [
        {
            "order": 1,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_11x_parser_recall_validation_ab.py "
                "--split heldout --input data/goal_search/splits/heldout.jsonl "
                "--frozen-manifest reports/agent_state/goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv "
                "--output-prefix reports/agent_state/goal_11x_parser_recall_validation_heldout"
            ),
            "status": "not_executed_in_11_3",
        },
        {
            "order": 2,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_11x_parser_recall_validation_ab.py "
                "--split hard --input data/goal_search/splits/hard.jsonl "
                "--frozen-manifest reports/agent_state/goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv "
                "--output-prefix reports/agent_state/goal_11x_parser_recall_validation_hard"
            ),
            "status": "not_executed_in_11_3",
        },
        {
            "order": 3,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_11x_parser_recall_validation_package_review.py",
            "status": "not_executed_in_11_3",
        },
    ]
    required_artifacts = [
        {"artifact": "heldout_ab_summary_json", "required": True, "purpose": "split-level hit/top80/candidate-pool delta and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row before/after evidence for audit"},
        {"artifact": "hard_ab_summary_json", "required": True, "purpose": "hard split robustness check"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard-split audit"},
        {"artifact": "loss_slices_csv", "required": True, "purpose": "new losses by source/query_family/top1_family/province"},
        {"artifact": "source_slices_csv", "required": True, "purpose": "detect single-source/source-family domination"},
        {"artifact": "package_review_summary_json", "required": True, "purpose": "final go/no-go for post-validation implementation review"},
    ]
    stop_conditions = [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "heldout_or_hard_used_for_selection", "action": "invalidate_run", "triggered_now": False},
        {"condition": "ab_baseline_missing", "action": "invalidate_run", "triggered_now": False},
        {"condition": "new_top1_loss_or_top80_loss_unreviewed", "action": "stop_before_post_validation", "triggered_now": False},
        {"condition": "max_source_gain_share_ge_0_8", "action": "stop_source_dominated", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
    ]
    go_requirements = [
        {
            "requirement": "explicit_validation_go_text",
            "status": "present" if explicit_go else "missing",
            "needed_text": "go: run 11.4 heldout/hard A/B validation for frozen 11.1 parser/query hints",
        },
        {
            "requirement": "frozen_manifest_available",
            "status": "present" if frozen_ready else "missing",
            "needed_text": str(args.frozen_manifest),
        },
        {
            "requirement": "heldout_hard_splits_available",
            "status": "present" if split_ready else "missing",
            "needed_text": f"{heldout_path}; {hard_path}",
        },
    ]
    blocked_actions = [
        {"action": "run_validation_now", "blocked": not validation_allowed_now, "reason": "explicit validation go is missing" if not explicit_go else "allowed only in next execution stage"},
        {"action": "select_or_tune_on_heldout_hard", "blocked": True, "reason": "heldout/hard are validation-only"},
        {"action": "change_or_expand_hints_during_validation", "blocked": True, "reason": "frozen manifest must remain unchanged"},
        {"action": "online_goal_searcher_integration", "blocked": True, "reason": "validation is offline only"},
        {"action": "claim_general_top1_gain", "blocked": True, "reason": "requires validation package pass and later review"},
    ]
    metrics = {
        "explicit_validation_go_present": explicit_go,
        "validation_allowed_now": validation_allowed_now,
        "frozen_candidate_ready": frozen_ready,
        "frozen_hint_rows": len(frozen_manifest),
        "heldout_rows": _line_count(heldout_path),
        "hard_rows": _line_count(hard_path),
        "split_leakage_shared_keys": split_summary.get("leakage_check", {}).get("shared_keys_across_splits", "unknown"),
        "validation_command_count": len(command_contract),
        "required_artifact_count": len(required_artifacts),
        "stop_condition_count": len(stop_conditions),
        "training_allowed": False,
        "threshold_change_allowed": False,
        "goal_searcher_change_allowed": False,
        "online_integration_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "validation_boundary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_validation_boundary.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "required_artifacts_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_required_artifacts.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "go_requirements_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_go_requirements.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Do not run validation now. The frozen 11.1 candidate is ready for a future validation stage, but explicit validation go is missing. "
        "11.3 defines the A/B heldout/hard boundary and required artifacts only."
        if not validation_allowed_now
        else "Explicit validation go is present and prerequisites are ready; validation may proceed in the next execution stage under the command contract."
    )
    report = {
        "stage": "Goal LTR v1 / 11.3 validation boundary definition and explicit go/no-go",
        "read_only": True,
        "source_artifacts": {
            "freeze_summary": str(args.freeze_summary),
            "frozen_manifest": str(args.frozen_manifest),
            "heldout_split": str(heldout_path),
            "hard_split": str(hard_path),
            "split_summary": str(split_summary_path),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.3 is read-only. It does not run heldout/hard validation, train, tune, change thresholds, edit parser/query hints, "
            "edit taxonomy rows, edit feature whitelists, wire GoalSearcher online behavior, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "11.4 heldout/hard A/B validation execution",
            "default": "do_not_execute_without_explicit_go",
            "required_user_text": "go: run 11.4 heldout/hard A/B validation for frozen 11.1 parser/query hints",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_csv(Path(artifacts["validation_boundary_csv"]), boundary_rows, list(boundary_rows[0].keys()))
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, list(command_contract[0].keys()))
    _write_csv(Path(artifacts["required_artifacts_csv"]), required_artifacts, list(required_artifacts[0].keys()))
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, list(stop_conditions[0].keys()))
    _write_csv(Path(artifacts["go_requirements_csv"]), go_requirements, list(go_requirements[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

