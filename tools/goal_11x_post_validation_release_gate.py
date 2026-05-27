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
DEFAULT_VALIDATION = AGENT_STATE / "goal_11x_parser_recall_validation_package_review_summary.json"
DEFAULT_FROZEN = AGENT_STATE / "goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_post_validation_release_gate"


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
        "# 11.5 Post-Validation Release Gate",
        "",
        "Read-only release gate after 11.4 heldout/hard A/B validation.",
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
        "当前状态：11.5 post-validation implementation/release gate 已完成。"
        f"release_gate_decision={report['metrics']['release_gate_decision']}；"
        f"validation_pass={str(report['metrics']['validation_pass']).lower()}；"
        f"release_go_present={str(report['metrics']['explicit_release_go_present']).lower()}；"
        f"release_allowed_now={str(report['metrics']['release_allowed_now']).lower()}；"
        "默认仍不接线上、不改 GoalSearcher。"
    )
    next_text = (
        "下一步：只有用户明确说 go: implement/release 11.6 frozen parser/query hints，"
        "才允许进入 release implementation boundary；否则保持 validated candidate parked。"
    )
    markers = [
        "当前状态：11.5 post-validation implementation/release gate 已完成。",
        "当前状态：11.4 heldout/hard A/B validation 已完成。",
    ]
    marker = next((item for item in markers if item in text), "")
    if marker:
        start = text.index(marker)
        end = text.index("禁止：继续 S2、训练、调参", start)
        text = text[:start] + current + "\n" + next_text + "\n" + text[end:]
    index_marker = "          <tr>\n            <td>11.4 heldout/hard A/B validation package review</td>"
    row = (
        "          <tr>\n"
        "            <td>11.5 post-validation release gate</td>\n"
        "            <td>只读复核 validation pass，并生成 release go request package；默认不接线上。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_post_validation_release_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if index_marker in text and "goal_11x_post_validation_release_gate_summary.json" not in text:
        text = text.replace(index_marker, row + index_marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--explicit-release-go", action="store_true")
    parser.add_argument("--validation-summary", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    validation = _read_json(args.validation_summary)
    frozen = _read_csv(args.frozen_manifest)
    vm = validation["metrics"]
    validation_pass = bool(vm["validation_pass"])
    explicit_go = bool(args.explicit_release_go)
    release_allowed_now = validation_pass and explicit_go
    release_gate_decision = "release_go_requested_not_granted" if validation_pass and not explicit_go else (
        "release_allowed_next_stage" if release_allowed_now else "do_not_release"
    )

    gate_checks = [
        {"gate": "validation_pass", "status": "pass" if validation_pass else "fail", "evidence": str(validation_pass)},
        {"gate": "new_loss_budget", "status": "pass" if int(vm["total_new_loss_count"]) == 0 else "fail", "evidence": str(vm["total_new_loss_count"])},
        {"gate": "heldout_hard_non_negative", "status": "pass" if int(vm["total_top80_delta"]) >= 0 else "fail", "evidence": str(vm["total_top80_delta"])},
        {"gate": "source_dominance", "status": "pass" if not vm["source_dominated"] else "fail", "evidence": str(vm["source_dominated"])},
        {"gate": "explicit_release_go", "status": "pass" if explicit_go else "missing", "evidence": str(explicit_go)},
    ]
    release_scope = [
        {
            "scope_item": "allowed_code_targets_after_go",
            "value": "src/goal_search/national_index.py; src/query_builder.py; tests/test_goal_11x_parser_recall_hints.py",
            "boundary": "only the already validated frozen 9-hint behavior; no new hints",
        },
        {
            "scope_item": "validated_hint_rows",
            "value": len(frozen),
            "boundary": "must match frozen manifest exactly",
        },
        {
            "scope_item": "not_allowed",
            "value": "training; threshold changes; feature whitelist edits; taxonomy row edits; new ranking policy; broad GoalSearcher integration",
            "boundary": "any of these needs a new explicit plan",
        },
        {
            "scope_item": "release_claim",
            "value": "validated small parser/query recall improvement",
            "boundary": "do not claim general Top1 gain or 75% target completion",
        },
    ]
    release_request = [
        {
            "required_text": "go: implement/release 11.6 frozen parser/query hints",
            "meaning": "Allow final release-boundary implementation review for the already validated 9-hint set only.",
            "default_without_text": "do_not_release",
        }
    ]
    rollback_plan = [
        {
            "component": "national_index family hints",
            "rollback": "remove QUERY_FAMILY_HINTS rows/helper additions for 11.1 hints",
            "risk": "low; no model or DB artifact rollback",
        },
        {
            "component": "query_builder recall hints",
            "rollback": "remove _build_goal_11x_parser_recall_query branches and call sites",
            "risk": "low; isolated helper",
        },
        {
            "component": "tests/tools/reports",
            "rollback": "keep audit artifacts; remove only if branch cleanup is requested",
            "risk": "none for runtime",
        },
    ]
    blocked_actions = [
        {"action": "release_now", "blocked": not release_allowed_now, "reason": "explicit release go is missing" if not explicit_go else "allowed only in next implementation stage"},
        {"action": "expand_hint_set", "blocked": True, "reason": "11.4 validated frozen manifest only"},
        {"action": "online_goal_searcher_wiring", "blocked": True, "reason": "not needed for parser/query helper release and requires separate go if broader integration"},
        {"action": "claim_general_top1_gain", "blocked": True, "reason": "validation split is small and scoped"},
        {"action": "train_or_tune", "blocked": True, "reason": "outside 11.x parser recall lane"},
    ]
    metrics = {
        "release_gate_decision": release_gate_decision,
        "validation_pass": validation_pass,
        "explicit_release_go_present": explicit_go,
        "release_allowed_now": release_allowed_now,
        "validated_hint_rows": len(frozen),
        "heldout_top80_delta": int(vm["heldout_top80_delta"]),
        "hard_top80_delta": int(vm["hard_top80_delta"]),
        "total_top80_delta": int(vm["total_top80_delta"]),
        "total_hit1_delta": int(vm["total_hit1_delta"]),
        "total_new_loss_count": int(vm["total_new_loss_count"]),
        "training_allowed": False,
        "threshold_change_allowed": False,
        "goal_searcher_change_allowed": False,
        "online_integration_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "release_scope_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_release_scope.csv")),
        "release_request_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_release_request.csv")),
        "rollback_plan_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_rollback_plan.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Request explicit release go for the validated 9-hint parser/query candidate. Do not release now because explicit release go is missing."
        if validation_pass and not explicit_go
        else (
            "Release may proceed in the next implementation stage under the frozen scope."
            if release_allowed_now
            else "Do not request release; validation did not pass."
        )
    )
    report = {
        "stage": "Goal LTR v1 / 11.5 post-validation implementation/release gate",
        "read_only": True,
        "source_artifacts": {
            "validation_summary": str(args.validation_summary),
            "frozen_manifest": str(args.frozen_manifest),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.5 is read-only. It does not release, train, tune, change thresholds, expand hints, edit taxonomy rows, "
            "edit feature whitelists, wire online GoalSearcher behavior, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "11.6 frozen parser/query hint release implementation boundary",
            "default": "do_not_release_without_explicit_go",
            "required_user_text": "go: implement/release 11.6 frozen parser/query hints",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["release_scope_csv"]), release_scope, list(release_scope[0].keys()))
    _write_csv(Path(artifacts["release_request_csv"]), release_request, list(release_request[0].keys()))
    _write_csv(Path(artifacts["rollback_plan_csv"]), rollback_plan, list(rollback_plan[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

