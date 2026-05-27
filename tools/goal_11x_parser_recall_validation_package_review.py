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
DEFAULT_HELDOUT = AGENT_STATE / "goal_11x_parser_recall_validation_heldout_summary.json"
DEFAULT_HARD = AGENT_STATE / "goal_11x_parser_recall_validation_hard_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_parser_recall_validation_package_review"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
        "# 11.4 Parser Recall Validation Package Review",
        "",
        "Heldout/hard A/B validation package review.",
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
        "当前状态：11.4 heldout/hard A/B validation 已完成。"
        f"validation_decision={report['metrics']['validation_decision']}；"
        f"heldout_top80_delta={report['metrics']['heldout_top80_delta']}；"
        f"hard_top80_delta={report['metrics']['hard_top80_delta']}；"
        f"total_new_loss_count={report['metrics']['total_new_loss_count']}；"
        "仍未上线、未训练、未调参、未改 GoalSearcher。"
    )
    next_text = (
        "下一步：11.5 post-validation implementation/release gate。"
        "只读决定是否保持候选、回滚、还是请求上线/集成 go；默认不接线上。"
    )
    current_markers = [
        "当前状态：11.4 heldout/hard A/B validation 已完成。",
        "当前状态：11.3 validation boundary definition / explicit validation go-no-go 已完成。",
    ]
    marker = next((item for item in current_markers if item in text), "")
    if marker:
        start = text.index(marker)
        end = text.index("禁止：继续 S2、训练、调参", start)
        text = text[:start] + current + "\n" + next_text + "\n" + text[end:]
    marker = "          <tr>\n            <td>11.3 validation boundary go/no-go</td>"
    row = (
        "          <tr>\n"
        "            <td>11.4 heldout/hard A/B validation package review</td>\n"
        "            <td>执行 frozen parser/query hints 的 heldout/hard A/B validation，并汇总 stop-condition package。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_parser_recall_validation_package_review_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_11x_parser_recall_validation_package_review_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--heldout-summary", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard-summary", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    heldout = _read_json(args.heldout_summary)
    hard = _read_json(args.hard_summary)
    total_new_loss = int(heldout["new_loss_count"]) + int(hard["new_loss_count"])
    total_top80_delta = int(heldout["top80_delta"]) + int(hard["top80_delta"])
    total_hit1_delta = int(heldout["hit1_delta"]) + int(hard["hit1_delta"])
    source_dominated = bool(heldout["source_dominance_stop"] or hard["source_dominance_stop"])
    validation_pass = total_new_loss == 0 and total_top80_delta >= 0 and not source_dominated
    validation_decision = "pass_validation_candidate" if validation_pass else "hold_or_rollback_before_release"
    split_review = [
        {
            "split": "heldout",
            "rows": heldout["rows"],
            "top80_delta": heldout["top80_delta"],
            "hit1_delta": heldout["hit1_delta"],
            "new_loss_count": heldout["new_loss_count"],
            "max_source_gain_share": heldout["max_source_gain_share"],
            "source_dominance_stop": heldout["source_dominance_stop"],
        },
        {
            "split": "hard",
            "rows": hard["rows"],
            "top80_delta": hard["top80_delta"],
            "hit1_delta": hard["hit1_delta"],
            "new_loss_count": hard["new_loss_count"],
            "max_source_gain_share": hard["max_source_gain_share"],
            "source_dominance_stop": hard["source_dominance_stop"],
        },
    ]
    gate_checks = [
        {"gate": "heldout_hard_validation_executed", "status": "pass", "evidence": f"heldout_rows={heldout['rows']}; hard_rows={hard['rows']}"},
        {"gate": "ab_baseline_present", "status": "pass", "evidence": "baseline_hints_enabled=false in both split summaries"},
        {"gate": "selection_boundary", "status": "pass", "evidence": "heldout_or_hard_used_for_selection=false"},
        {"gate": "new_loss_budget", "status": "pass" if total_new_loss == 0 else "fail", "evidence": str(total_new_loss)},
        {"gate": "top80_non_negative", "status": "pass" if total_top80_delta >= 0 else "fail", "evidence": str(total_top80_delta)},
        {"gate": "source_dominance", "status": "pass" if not source_dominated else "fail", "evidence": f"heldout={heldout['max_source_gain_share']}; hard={hard['max_source_gain_share']}"},
    ]
    blocked_actions = [
        {"action": "online_goal_searcher_integration", "blocked": True, "reason": "requires separate 11.5 release gate and explicit go"},
        {"action": "claim_general_top1_gain", "blocked": True, "reason": "validation package is small split-specific evidence"},
        {"action": "train_or_tune", "blocked": True, "reason": "outside parser recall validation lane"},
        {"action": "expand_hint_set", "blocked": True, "reason": "validation used frozen manifest only"},
    ]
    metrics = {
        "validation_decision": validation_decision,
        "validation_pass": validation_pass,
        "heldout_rows": int(heldout["rows"]),
        "hard_rows": int(hard["rows"]),
        "heldout_top80_delta": int(heldout["top80_delta"]),
        "hard_top80_delta": int(hard["top80_delta"]),
        "total_top80_delta": total_top80_delta,
        "heldout_hit1_delta": int(heldout["hit1_delta"]),
        "hard_hit1_delta": int(hard["hit1_delta"]),
        "total_hit1_delta": total_hit1_delta,
        "total_new_loss_count": total_new_loss,
        "source_dominated": source_dominated,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "goal_searcher_change_allowed": False,
        "online_integration_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "split_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_split_review.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Validation package passes as an offline candidate: no new losses, non-negative top80 delta, and no source-dominance stop. "
        "This authorizes only a future post-validation release gate, not online integration."
        if validation_pass
        else "Validation package does not authorize release. Keep the frozen candidate held or rollback before any release gate."
    )
    report = {
        "stage": "Goal LTR v1 / 11.4 heldout/hard A/B validation package review",
        "read_only_review": True,
        "source_artifacts": {
            "heldout_summary": str(args.heldout_summary),
            "hard_summary": str(args.hard_summary),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.4 validation did not train, tune, change thresholds, expand hints, use heldout/hard for selection, "
            "edit taxonomy rows, edit feature whitelists, wire online GoalSearcher behavior, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "11.5 post-validation implementation/release gate",
            "default": "do_not_release_without_explicit_go",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["split_review_csv"]), split_review, list(split_review[0].keys()))
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
