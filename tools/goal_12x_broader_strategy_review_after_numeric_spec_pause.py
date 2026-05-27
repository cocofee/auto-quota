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
DEFAULT_12X_STRATEGY = AGENT_STATE / "goal_12x_accuracy_strategy_definition_summary.json"
DEFAULT_12X_INVENTORY = AGENT_STATE / "goal_12x_candidate_pool_rank_position_inventory_summary.json"
DEFAULT_12X_NUMERIC_CLOSURE = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_closure_gate_summary.json"
DEFAULT_CANDIDATE_ABSENCE = AGENT_STATE / "goal_12x_candidate_pool_rank_position_inventory_candidate_pool_absence.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_broader_strategy_review_after_numeric_spec_pause"


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
        "# 12.8 Broader Strategy Review After Numeric/Spec Pause",
        "",
        "Read-only broader 12.x strategy review.",
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
        "当前状态：12.8 broader 12.x strategy review after numeric/spec lane pause 已完成。"
        f"selected_next_lane={report['metrics']['selected_next_lane']}；"
        f"numeric_spec_lane_status={report['metrics']['numeric_spec_lane_status']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.9 candidate-pool absence / query-family coverage diagnostics design gate。"
        "只读判断 top1_family_empty / query_family_empty 是否存在不依赖 owner mappings 的 parser/query-family 诊断路线。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：重开 numeric/spec lane、重开 11.x、直接实现、训练、调参、改阈值、改 GoalSearcher、"
            "使用 heldout/hard 做选择、或需要 owner mappings 时继续硬推。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.7 numeric/spec tier what-if closure gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.8 broader strategy review after numeric/spec pause</td>\n"
        "            <td>只读回到 broader 12.x，选择不依赖规格 evidence/owner mappings 的下一条诊断路线。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_broader_strategy_review_after_numeric_spec_pause_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_broader_strategy_review_after_numeric_spec_pause_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_12X_STRATEGY)
    parser.add_argument("--inventory-summary", type=Path, default=DEFAULT_12X_INVENTORY)
    parser.add_argument("--numeric-closure", type=Path, default=DEFAULT_12X_NUMERIC_CLOSURE)
    parser.add_argument("--candidate-absence", type=Path, default=DEFAULT_CANDIDATE_ABSENCE)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy = _read_json(args.strategy_summary)
    inventory = _read_json(args.inventory_summary)
    numeric = _read_json(args.numeric_closure)
    absence = _read_csv(args.candidate_absence)
    absence_total = sum(int(row["rows"]) for row in absence)

    lane_review = [
        {
            "lane": "numeric_spec_tier_rank_position",
            "status": "paused",
            "reason": "12.6 had 0 guarded rows; query/bill_text numeric evidence absent",
            "next_allowed": "only with new explicit numeric query/bill_text dev/OOF evidence",
            "score": 0,
        },
        {
            "lane": "global_repair_rank_position",
            "status": "blocked",
            "reason": "12.2 found global_repair_source_share=0.964332",
            "next_allowed": "only with independent non-global evidence",
            "score": 0,
        },
        {
            "lane": "candidate_pool_absence_query_family_coverage_diagnostics",
            "status": "select",
            "reason": f"candidate-pool absence has {absence_total} rows; top1_family_empty/query_family_empty dominate and can be diagnosed read-only",
            "next_allowed": "12.9 design gate; no implementation",
            "score": 4,
        },
        {
            "lane": "owner_mapping_taxonomy_fixes",
            "status": "defer",
            "reason": "requires owner mappings or accepted DQ artifacts",
            "next_allowed": "only with owner package",
            "score": 1,
        },
        {
            "lane": "ranking_training_objective",
            "status": "defer",
            "reason": "training would mix attribution and needs a new explicit experiment plan",
            "next_allowed": "separate training authorization gate",
            "score": 1,
        },
    ]
    selected = next(row for row in lane_review if row["status"] == "select")
    absence_review = [
        {
            "reason": row["reason"],
            "rows": int(row["rows"]),
            "share": float(row["share_of_top80_missing_rows"]),
            "12_8_disposition": (
                "include_in_12_9_diagnostics"
                if row["reason"] in {"top1_family_empty", "query_family_empty"}
                else "secondary_retrieval_boundary_review"
            ),
        }
        for row in absence
    ]
    guardrails = [
        {"guardrail": "no_owner_mapping_dependency", "requirement": "12.9 must remain diagnostic unless owner mappings are provided"},
        {"guardrail": "no_heldout_hard_selection", "requirement": "Use existing dev/dev-OOF evidence only"},
        {"guardrail": "no_11x_reopen", "requirement": "Do not expand or reattribute 11.x hints"},
        {"guardrail": "no_implementation", "requirement": "12.9 is design gate only"},
        {"guardrail": "source_artifact_check", "requirement": "Separate global_repair/source-dominated artifacts from independent evidence"},
    ]
    metrics = {
        "selected_next_lane": selected["lane"],
        "numeric_spec_lane_status": numeric["metrics"]["closure_decision"],
        "candidate_absence_rows": absence_total,
        "top1_family_empty_rows": next((int(row["rows"]) for row in absence if row["reason"] == "top1_family_empty"), 0),
        "query_family_empty_rows": next((int(row["rows"]) for row in absence if row["reason"] == "query_family_empty"), 0),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "lane_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_review.csv")),
        "candidate_absence_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_absence_review.csv")),
        "guardrails_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guardrails.csv")),
    }
    decision = (
        "Park the numeric/spec lane and select candidate-pool absence / query-family coverage diagnostics for 12.9. "
        "This lane uses existing dev/dev-OOF evidence and avoids the blocked numeric/spec, global_repair, 11.x, owner-mapping, "
        "and training paths. 12.8 does not authorize implementation."
    )
    report = {
        "stage": "Goal LTR v1 / 12.8 broader 12.x strategy review after numeric/spec lane pause",
        "read_only": True,
        "source_artifacts": {
            "strategy_summary": str(args.strategy_summary),
            "inventory_summary": str(args.inventory_summary),
            "numeric_closure": str(args.numeric_closure),
            "candidate_absence": str(args.candidate_absence),
        },
        "metrics": metrics,
        "decision": decision,
        "context": {
            "12x_selected_lane": strategy["metrics"]["selected_lane"],
            "primary_bottleneck": inventory["metrics"]["primary_bottleneck"],
        },
        "anti_drift_conclusion": (
            "12.8 is read-only. It does not reopen numeric/spec or 11.x, implement, train, tune, change thresholds, "
            "edit taxonomy rows, edit feature whitelists, wire GoalSearcher, use heldout/hard for selection, or continue a lane that needs owner mappings."
        ),
        "next_stage": {
            "stage": "12.9 candidate-pool absence / query-family coverage diagnostics design gate",
            "default": "read_only_design_gate_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["lane_review_csv"]), lane_review, list(lane_review[0].keys()))
    _write_csv(Path(artifacts["candidate_absence_review_csv"]), absence_review, list(absence_review[0].keys()))
    _write_csv(Path(artifacts["guardrails_csv"]), guardrails, list(guardrails[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
