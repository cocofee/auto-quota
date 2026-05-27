from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_AUTH = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_authorization_gate_summary.json"
DEFAULT_ROW_PLAN = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition_row_plan.csv"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif"

NUMERIC_RE = re.compile(r"(?i)(DN\s*\d+|D\s*\d+|De\s*\d+|直径\s*\d+|风量\s*\d+|周长\s*\d+|截面\s*\d+|\d+(?:\.\d+)?\s*(?:mm|m3/h|m³/h|m2|m²))")


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
        "# 12.6 Numeric/Spec Tier What-if",
        "",
        "Dev/OOF-only guarded what-if for same-family numeric/spec tier rows.",
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
        "当前状态：12.6 dev/OOF-only numeric/spec tier what-if 已完成。"
        f"whatif_decision={report['metrics']['whatif_decision']}；"
        f"evaluated_rows={report['metrics']['evaluated_rows']}；"
        f"guard_allowed_rows={report['metrics']['guard_allowed_rows']}；"
        f"candidate_hit1_gain={report['metrics']['candidate_hit1_gain']}；"
        f"new_loss_count={report['metrics']['new_loss_count']}。"
    )
    next_text = (
        "下一步：12.7 numeric/spec tier what-if closure gate。只读收口：由于 query-side 数值证据不足，"
        "默认不进入实现；除非补充带规格的 dev/OOF evidence。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：实现 numeric/spec comparator、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "或用 expected label 反推 query 中不存在的数值规格。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.5 numeric/spec tier what-if authorization gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.6 numeric/spec tier dev/OOF what-if</td>\n"
        "            <td>dev/OOF-only 执行受保护 numeric/spec tier what-if，记录 guard coverage 和 loss audit。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_numeric_spec_tier_whatif_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_numeric_spec_tier_whatif_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _query_has_numeric_evidence(text: str) -> bool:
    return bool(NUMERIC_RE.search(text or ""))


def _positive_rank_min(row: dict[str, str]) -> int | None:
    text = row.get("positive_rank_min") or row.get("positive_ranks") or ""
    ranks = []
    for part in re.split(r"[|,; ]+", text):
        if part.isdigit():
            ranks.append(int(part))
    return min(ranks) if ranks else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev")
    parser.add_argument("--authorization-summary", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--row-plan", type=Path, default=DEFAULT_ROW_PLAN)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    authorization = _read_json(args.authorization_summary)
    if not authorization["metrics"]["execution_allowed_now"]:
        raise SystemExit("12.6 what-if is not authorized; run 12.5 with explicit go first.")

    row_plan = _read_csv(args.row_plan)
    wrong_rank = {row["group_id"]: row for row in _read_csv(args.wrong_rank)}

    details: list[dict[str, Any]] = []
    for plan in row_plan:
        source = wrong_rank.get(plan["group_id"], {})
        if not source or source.get("split") != args.split:
            continue
        query = source.get("query", "")
        positive_rank = _positive_rank_min(source)
        same_family = bool(source.get("query_family")) and source.get("query_family") == source.get("top1_family")
        no_family_conflict = "family conflict" not in source.get("top1_reasons", "")
        candidate_in_rank_2_5 = positive_rank is not None and 2 <= positive_rank <= 5
        param_type_match = plan.get("param_type") not in {"", "numeric_or_spec_tier_unknown"}
        query_numeric_present = _query_has_numeric_evidence(query)
        guard_allowed = all(
            [
                same_family,
                no_family_conflict,
                candidate_in_rank_2_5,
                param_type_match,
                query_numeric_present,
            ]
        )
        guard_block_reasons = []
        if not same_family:
            guard_block_reasons.append("not_same_family")
        if not no_family_conflict:
            guard_block_reasons.append("family_conflict_reason")
        if not candidate_in_rank_2_5:
            guard_block_reasons.append("positive_not_rank_2_5")
        if not param_type_match:
            guard_block_reasons.append("param_type_unknown")
        if not query_numeric_present:
            guard_block_reasons.append("query_numeric_evidence_missing")

        baseline_hit1 = source.get("top1_id") in set((source.get("expected_ids") or "").split("|"))
        candidate_hit1 = bool(guard_allowed and not baseline_hit1)
        details.append(
            {
                "split": args.split,
                "group_id": plan["group_id"],
                "source_file": source.get("source_file", ""),
                "province": source.get("province", ""),
                "query": query,
                "query_family": source.get("query_family", "") or "<empty>",
                "param_type": plan.get("param_type", ""),
                "baseline_top1_id": source.get("top1_id", ""),
                "baseline_top1_name": source.get("top1_name", ""),
                "positive_ids": source.get("positive_ids_in_top80", ""),
                "positive_names": source.get("positive_names_in_top80", ""),
                "positive_rank_min": positive_rank if positive_rank is not None else "",
                "same_family": same_family,
                "no_family_conflict": no_family_conflict,
                "candidate_in_rank_2_5": candidate_in_rank_2_5,
                "param_type_match": param_type_match,
                "query_numeric_present": query_numeric_present,
                "guard_allowed": guard_allowed,
                "guard_block_reasons": ";".join(guard_block_reasons),
                "baseline_hit1": baseline_hit1,
                "candidate_hit1": candidate_hit1,
                "hit1_delta": int(candidate_hit1) - int(baseline_hit1),
                "whatif_action": "promote_guarded_numeric_spec_candidate" if guard_allowed else "no_op_guard_blocked",
            }
        )

    gains = [row for row in details if row["hit1_delta"] > 0]
    losses = [row for row in details if row["hit1_delta"] < 0]
    guard_allowed_rows = [row for row in details if row["guard_allowed"]]
    guard_blocked_rows = [row for row in details if not row["guard_allowed"]]
    block_reason_counts = Counter(
        reason
        for row in guard_blocked_rows
        for reason in str(row["guard_block_reasons"]).split(";")
        if reason
    )
    guard_coverage = [
        {"guard": "same_family", "pass_rows": sum(1 for row in details if row["same_family"]), "total_rows": len(details)},
        {"guard": "no_family_conflict", "pass_rows": sum(1 for row in details if row["no_family_conflict"]), "total_rows": len(details)},
        {"guard": "candidate_in_rank_2_5", "pass_rows": sum(1 for row in details if row["candidate_in_rank_2_5"]), "total_rows": len(details)},
        {"guard": "param_type_match", "pass_rows": sum(1 for row in details if row["param_type_match"]), "total_rows": len(details)},
        {"guard": "query_numeric_present", "pass_rows": sum(1 for row in details if row["query_numeric_present"]), "total_rows": len(details)},
    ]
    loss_audit = [
        {
            "slice_dimension": "overall",
            "slice_key": args.split,
            "groups": len(details),
            "baseline_hit1": sum(1 for row in details if row["baseline_hit1"]),
            "candidate_hit1": sum(1 for row in details if row["candidate_hit1"]),
            "gain": len(gains),
            "loss": len(losses),
            "net": len(gains) - len(losses),
        }
    ]
    source_slices = []
    for source_file, count in Counter(row["source_file"] for row in details).most_common():
        source_rows = [row for row in details if row["source_file"] == source_file]
        source_slices.append(
            {
                "source_file": source_file,
                "rows": count,
                "guard_allowed_rows": sum(1 for row in source_rows if row["guard_allowed"]),
                "gain": sum(1 for row in source_rows if row["hit1_delta"] > 0),
                "loss": sum(1 for row in source_rows if row["hit1_delta"] < 0),
            }
        )
    rollback_report = [
        {
            "component": "numeric/spec tier comparator",
            "rollback": "no runtime code was changed; what-if can be discarded by ignoring reports",
            "risk": "none for current code",
        }
    ]
    stop_triggered = len(guard_allowed_rows) == 0
    whatif_decision = "stop_no_query_numeric_evidence_keep_diagnostic" if stop_triggered else "review_guarded_gains_before_any_plan"
    metrics = {
        "whatif_decision": whatif_decision,
        "evaluated_rows": len(details),
        "guard_allowed_rows": len(guard_allowed_rows),
        "guard_blocked_rows": len(guard_blocked_rows),
        "query_numeric_present_rows": sum(1 for row in details if row["query_numeric_present"]),
        "candidate_hit1_gain": len(gains),
        "new_loss_count": len(losses),
        "net_hit1_delta": len(gains) - len(losses),
        "source_dominated_gain": False,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "heldout_hard_used": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "row_details_jsonl": str(args.output_prefix.with_name(args.output_prefix.name + "_row_details.jsonl")),
        "scorecard_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")),
        "guard_coverage_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guard_coverage.csv")),
        "guard_block_reasons_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guard_block_reasons.csv")),
        "loss_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_audit.csv")),
        "source_slices_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_slices.csv")),
        "rollback_report_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_rollback_report.csv")),
    }
    decision = (
        "Stop and keep this as a diagnostic artifact: the guarded what-if produced no candidate action because query-side "
        "numeric/spec evidence is missing in all evaluated rows. This prevents leakage from expected labels and does not "
        "support implementation."
        if stop_triggered
        else "Review guarded gains and losses before any implementation plan."
    )
    report = {
        "stage": "Goal LTR v1 / 12.6 dev/OOF-only numeric/spec tier what-if",
        "read_only_execution": True,
        "source_artifacts": {
            "authorization_summary": str(args.authorization_summary),
            "row_plan": str(args.row_plan),
            "wrong_rank": str(args.wrong_rank),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "12.6 is dev/OOF-only and report-only. It does not implement, train, tune, change thresholds, edit taxonomy rows, "
            "edit feature whitelists, reopen 11.x, wire GoalSearcher, use heldout/hard for selection, or use expected labels "
            "to infer missing query-side numeric evidence."
        ),
        "next_stage": {
            "stage": "12.7 numeric/spec tier what-if closure gate",
            "default": "do_not_implement",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    with Path(artifacts["row_details_jsonl"]).open("w", encoding="utf-8") as handle:
        for row in details:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _write_csv(Path(artifacts["scorecard_csv"]), [metrics], list(metrics.keys()))
    _write_csv(Path(artifacts["guard_coverage_csv"]), guard_coverage, list(guard_coverage[0].keys()))
    block_rows = [{"reason": key, "rows": value} for key, value in block_reason_counts.most_common()]
    _write_csv(Path(artifacts["guard_block_reasons_csv"]), block_rows, list(block_rows[0].keys()) if block_rows else ["reason", "rows"])
    _write_csv(Path(artifacts["loss_audit_csv"]), loss_audit, list(loss_audit[0].keys()))
    _write_csv(Path(artifacts["source_slices_csv"]), source_slices, list(source_slices[0].keys()) if source_slices else ["source_file"])
    _write_csv(Path(artifacts["rollback_report_csv"]), rollback_report, list(rollback_report[0].keys()))
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
