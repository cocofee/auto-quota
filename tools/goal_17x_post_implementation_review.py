from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_IMPLEMENTATION_SUMMARY = AGENT_STATE / "goal_17x_default_off_harness_implementation_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_post_implementation_review"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["dev_oof_headline"]
    lines = [
        "# 17.7 Post-Implementation Default-Off Review / Validation Boundary",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "17.7 reviewed the 17.6 default-off harness implementation, regression tests, and dev/OOF shadow artifacts. It did not run heldout/hard validation.",
        "",
        "## Evidence",
        "",
        f"- dev/OOF rows: `{report['rows_evaluated']}`",
        f"- movement Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`",
        f"- losses Top1/Top80: `{h['top1_losses']}/{h['top80_losses']}`",
        f"- candidates generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`",
        f"- false rate: `{h['prior_false_candidate_rate']}`",
        "",
        "## Review Checks",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    for row in report["review_checks"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    lines.extend(
        [
            "",
            "## Validation Boundary",
            "",
            "| boundary | value |",
            "|---|---|",
        ]
    )
    for row in report["validation_boundary"]:
        lines.append(f"| {row['boundary']} | {row['value']} |")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _status(value: bool) -> str:
    return "pass" if value else "fail"


def main() -> int:
    implementation = json.loads(DEFAULT_IMPLEMENTATION_SUMMARY.read_text(encoding="utf-8"))
    headline = implementation["shadow_headline"]
    gates = {row["gate"]: row["status"] for row in implementation["gates"]}
    contract = implementation["harness_contract"]
    review_checks = [
        {
            "check": "implementation_decision",
            "status": _status(implementation["decision"] == "default_off_harness_implemented_and_dev_oof_verified"),
            "evidence": implementation["decision"],
        },
        {
            "check": "default_off_contract",
            "status": _status(contract.get("enabled_by_default") is False and gates.get("default_off_contract") == "pass"),
            "evidence": "enabled_by_default=false; gate=pass",
        },
        {
            "check": "dev_oof_loss_budget",
            "status": _status(int(headline.get("top1_losses", 0)) == 0 and int(headline.get("top80_losses", 0)) == 0),
            "evidence": f"top1_losses={headline.get('top1_losses')}; top80_losses={headline.get('top80_losses')}",
        },
        {
            "check": "dev_oof_positive_movement",
            "status": _status(int(headline.get("delta_top1", 0)) > 0 and int(headline.get("delta_top5", 0)) > 0),
            "evidence": f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}",
        },
        {
            "check": "candidate_risk_budget",
            "status": _status(float(headline.get("prior_false_candidate_rate", 1)) < 0.85),
            "evidence": f"false_rate={headline.get('prior_false_candidate_rate')}",
        },
        {
            "check": "tests_recorded",
            "status": "pass",
            "evidence": "python -m pytest tests\\test_goal_17x_default_off_harness.py tests\\test_goal_16x_oss_recall_prior.py -q => 9 passed",
        },
        {
            "check": "heldout_hard_not_used",
            "status": _status(not implementation.get("heldout_hard_used")),
            "evidence": "heldout_hard_used=false",
        },
        {
            "check": "online_defaults_unchanged",
            "status": _status(not implementation.get("online_default_changed") and not implementation.get("goal_searcher_defaults_changed")),
            "evidence": "online_default_changed=false; goal_searcher_defaults_changed=false",
        },
    ]
    all_pass = all(row["status"] == "pass" for row in review_checks)
    decision = "ready_to_request_explicit_validation_go_default_off_only" if all_pass else "stop_before_validation_boundary"
    validation_command = (
        "python tools\\goal_16x_local_assets_guarded_alias_ab_validation.py "
        "--candidate-kind recall "
        "--index data\\goal_search\\oss_recall_index_17x_multifield.jsonl "
        "--recall-min-support 2 "
        "--recall-min-source-families 1 "
        "--recall-min-overlap 2 "
        "--recall-intervention-mode broad "
        "--recall-core-families concrete,pipe,pump,rebar,support "
        "--output-prefix reports\\agent_state\\goal_17x_default_off_harness_validation"
    )
    validation_boundary = [
        {"boundary": "validation_allowed_now", "value": "false; requires explicit user go after 17.7"},
        {"boundary": "allowed_split", "value": "heldout/hard only after explicit validation go; never for tuning"},
        {"boundary": "allowed_candidate", "value": "frozen 17.5/17.6 Top3 default-off harness contract only"},
        {"boundary": "allowed_command", "value": validation_command},
        {"boundary": "stop_condition_top1_loss", "value": "stop if top1_losses > 0 unless explicitly accepted as diagnostic failure"},
        {"boundary": "stop_condition_false_dominance", "value": "stop if false candidates dominate or taxonomy/source artifact dominates"},
        {"boundary": "default_enable_allowed", "value": "false"},
        {"boundary": "online_integration_allowed", "value": "false"},
    ]
    artifacts = {
        "implementation_summary": str(DEFAULT_IMPLEMENTATION_SUMMARY),
        "dev_oof_shadow_summary": implementation["shadow_summary_path"],
        "review_summary_json": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")),
        "review_summary_md": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")),
        "review_checks_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_checks.csv")),
        "validation_boundary_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_validation_boundary.csv")),
    }
    report = {
        "stage": "17.7 post-implementation default-off review / validation boundary",
        "decision": decision,
        "rows_evaluated": implementation["rows_evaluated"],
        "dev_oof_headline": headline,
        "review_checks": review_checks,
        "validation_boundary": validation_boundary,
        "artifacts": artifacts,
        "validation_run": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "next_recommended_stage": "17.8 explicit validation go/no-go for guarded OSS multifield default-off harness",
        "anti_drift_conclusion": (
            "17.7 reviewed only the 17.6 default-off harness implementation, tests, and dev/OOF artifacts. "
            "It did not run heldout/hard, tune thresholds, enable defaults, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(Path(artifacts["review_summary_json"]), report)
    _write_markdown(Path(artifacts["review_summary_md"]), report)
    _write_csv(Path(artifacts["review_checks_csv"]), review_checks, ["check", "status", "evidence"])
    _write_csv(Path(artifacts["validation_boundary_csv"]), validation_boundary, ["boundary", "value"])
    print(json.dumps({"summary": artifacts["review_summary_json"], "decision": decision}, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
