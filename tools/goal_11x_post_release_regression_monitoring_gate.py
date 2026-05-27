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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_RELEASE_SUMMARY = AGENT_STATE / "goal_11x_frozen_parser_query_hint_release_summary.json"
DEFAULT_MANIFEST_CHECK = AGENT_STATE / "goal_11x_frozen_parser_query_hint_release_manifest_behavior_check.csv"
DEFAULT_BLOCKED_ACTIONS = AGENT_STATE / "goal_11x_frozen_parser_query_hint_release_blocked_actions.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_post_release_regression_monitoring_gate"


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 11.7 Post-Release Regression/Monitoring Gate",
        "",
        "Read-only gate after the frozen 11.6 parser/query hint release.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：11.7 post-release regression/monitoring gate 已完成。"
        f"gate_decision={report['metrics']['gate_decision']}；"
        f"broader_regression_required_now={str(report['metrics']['broader_regression_required_now']).lower()}；"
        f"monitoring_contract_required={str(report['metrics']['monitoring_contract_required']).lower()}；"
        f"validated_hint_rows={report['metrics']['validated_hint_rows']}；"
        f"manifest_behavior_match={str(report['metrics']['manifest_behavior_match']).lower()}。"
    )
    next_text = (
        "下一步：默认停在 post-release monitoring/observation；只有出现新 evidence、线上接入需求、"
        "或你明确要求新算法方向，才进入新的 gate。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：继续扩展 11.x hints、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做新选择、"
            "编辑 taxonomy/feature whitelist、或把这 9 条 scoped release 宣称为通用 Top1 gain。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>11.6 frozen parser/query hint release</td>"
    row = (
        "          <tr>\n"
        "            <td>11.7 post-release regression/monitoring gate</td>\n"
        "            <td>只读确认 frozen hint release 后是否需要更广回归或监控，并定义 stop conditions。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_post_release_regression_monitoring_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_11x_post_release_regression_monitoring_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-summary", type=Path, default=DEFAULT_RELEASE_SUMMARY)
    parser.add_argument("--manifest-check", type=Path, default=DEFAULT_MANIFEST_CHECK)
    parser.add_argument("--blocked-actions", type=Path, default=DEFAULT_BLOCKED_ACTIONS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    release = _read_json(args.release_summary)
    manifest_checks = _read_csv(args.manifest_check)
    blocked_actions = _read_csv(args.blocked_actions)
    rm = release["metrics"]

    manifest_behavior_match = bool(rm["manifest_behavior_match"]) and all(
        row.get("query_match") == "True"
        and row.get("family_match") == "True"
        and row.get("release_status") == "released"
        for row in manifest_checks
    )
    release_scope_ok = bool(rm["release_scope_ok"])
    no_new_losses = int(rm["total_new_loss_count"]) == 0
    validation_pass = bool(rm["validation_pass"])
    broader_regression_required_now = not (release_scope_ok and manifest_behavior_match and no_new_losses and validation_pass)
    monitoring_contract_required = True
    gate_decision = (
        "accept_release_with_lightweight_monitoring_no_broader_regression_now"
        if not broader_regression_required_now
        else "hold_release_for_broader_regression"
    )

    regression_plan = [
        {
            "scope": "focused_hint_unit_tests",
            "command": "python -m pytest tests/test_goal_11x_parser_recall_hints.py -q",
            "required_now": True,
            "purpose": "prove frozen hint trigger and negative regression remain stable",
        },
        {
            "scope": "query_builder_route_smoke",
            "command": "python -m pytest tests/test_query_builder_fixed_aliases.py tests/test_query_builder_distribution_boxes.py tests/test_query_router.py tests/test_query_builder_stage3_recall_cleanup.py -q",
            "required_now": True,
            "purpose": "catch nearby query-builder route regressions",
        },
        {
            "scope": "broader_full_suite",
            "command": "not required in 11.7 unless smoke tests fail, future hint expansion occurs, or GoalSearcher wiring is requested",
            "required_now": broader_regression_required_now,
            "purpose": "reserved for scope expansion or failing smoke evidence",
        },
    ]
    monitoring_contract = [
        {"field": "query_text", "why": "identify whether a released hint fired"},
        {"field": "triggered_hint_key", "why": "must be one of the frozen 9 release manifest keys"},
        {"field": "before_query", "why": "debug fallback behavior if a release rollback is needed"},
        {"field": "after_query", "why": "confirm emitted query matches the release manifest"},
        {"field": "inferred_family", "why": "detect taxonomy/family drift"},
        {"field": "candidate_count_delta", "why": "monitor recall surface movement without ranking claims"},
        {"field": "top80_hit_delta", "why": "watch for loss, not for new heldout/hard selection"},
        {"field": "source_file_or_batch", "why": "spot source-specific artifacts"},
    ]
    stop_conditions = [
        {"condition": "manifest_mismatch", "action": "stop and rollback/review the affected hint branch"},
        {"condition": "new_loss_count_gt_0_in_smoke_or_monitoring", "action": "hold expansion and run targeted loss audit"},
        {"condition": "non_frozen_hint_triggered", "action": "block release expansion and require new freeze gate"},
        {"condition": "GoalSearcher_wiring_requested", "action": "open separate explicit integration gate"},
        {"condition": "threshold_or_training_change_requested", "action": "reject from 11.x release lane and require new strategy plan"},
    ]
    gate_checks = [
        {"gate": "release_scope_ok", "status": "pass" if release_scope_ok else "fail", "evidence": str(release_scope_ok)},
        {"gate": "validation_pass", "status": "pass" if validation_pass else "fail", "evidence": str(validation_pass)},
        {"gate": "manifest_behavior_match", "status": "pass" if manifest_behavior_match else "fail", "evidence": str(manifest_behavior_match)},
        {"gate": "new_loss_budget", "status": "pass" if no_new_losses else "fail", "evidence": str(rm["total_new_loss_count"])},
        {"gate": "scope_expansion", "status": "pass", "evidence": "no new hints, no training, no threshold change"},
    ]
    blocked_action_review = [
        {
            "action": row.get("action", ""),
            "still_blocked": row.get("blocked", ""),
            "reason": row.get("reason", ""),
            "11_7_disposition": "keep_blocked",
        }
        for row in blocked_actions
    ]
    metrics = {
        "gate_decision": gate_decision,
        "broader_regression_required_now": broader_regression_required_now,
        "monitoring_contract_required": monitoring_contract_required,
        "validated_hint_rows": int(rm["validated_hint_rows"]),
        "manifest_behavior_match": manifest_behavior_match,
        "validation_pass": validation_pass,
        "total_top80_delta": int(rm["total_top80_delta"]),
        "total_hit1_delta": int(rm["total_hit1_delta"]),
        "total_new_loss_count": int(rm["total_new_loss_count"]),
        "training_allowed": False,
        "threshold_change_allowed": False,
        "hint_expansion_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "regression_plan_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_regression_plan.csv")),
        "monitoring_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_monitoring_contract.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "blocked_action_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_action_review.csv")),
    }
    decision = (
        "Accept the 11.6 frozen 9-hint release with lightweight monitoring and the focused regression commands. "
        "A broader regression is not required now because validation passed, the manifest still matches current behavior, "
        "and the loss budget remains zero. Any future hint expansion or GoalSearcher wiring needs a separate explicit gate."
        if not broader_regression_required_now
        else "Hold the release for broader regression because one or more post-release checks failed."
    )
    report = {
        "stage": "Goal LTR v1 / 11.7 post-release regression/monitoring gate",
        "read_only": True,
        "source_artifacts": {
            "release_summary": str(args.release_summary),
            "manifest_check": str(args.manifest_check),
            "blocked_actions": str(args.blocked_actions),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.7 is read-only. It does not expand hints, train, tune, change thresholds, edit taxonomy rows, "
            "edit feature whitelists, use heldout/hard for new selection, wire GoalSearcher, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "post-release monitoring/observation or a new explicit strategy gate",
            "default": "stop_auto_advance",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["regression_plan_csv"]), regression_plan, list(regression_plan[0].keys()))
    _write_csv(Path(artifacts["monitoring_contract_csv"]), monitoring_contract, list(monitoring_contract[0].keys()))
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, list(stop_conditions[0].keys()))
    _write_csv(Path(artifacts["blocked_action_review_csv"]), blocked_action_review, list(blocked_action_review[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0 if not broader_regression_required_now else 1


if __name__ == "__main__":
    raise SystemExit(main())
