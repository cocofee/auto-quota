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
DEFAULT_12X_STRATEGY = AGENT_STATE / "goal_12x_accuracy_strategy_definition_summary.json"
DEFAULT_RANK_INVENTORY = AGENT_STATE / "goal_12x_candidate_pool_rank_position_inventory_summary.json"
DEFAULT_RANK25_AUDIT = AGENT_STATE / "goal_12x_non_global_rank25_slice_audit_summary.json"
DEFAULT_NUMERIC_CLOSURE = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_closure_gate_summary.json"
DEFAULT_MICRO_HINT_NOGO = AGENT_STATE / "goal_12x_parser_query_micro_hint_feasibility_no_go_gate_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_broader_strategy_review_after_micro_hint_no_go"


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
        "# 12.12 Broader 12.x Strategy Review After Parser/Query Micro-Hint No-Go",
        "",
        "Read-only strategy review after parking the thin parser/query micro-hint route.",
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
    metrics = report["metrics"]
    current = (
        "当前状态：12.12 broader 12.x strategy review after parser/query micro-hint no-go 已完成。"
        f"selected_next_lane={metrics['selected_next_lane']}；"
        f"electrical_box_non_global_rows={metrics['electrical_box_non_global_rows']}；"
        f"electrical_box_independent_source_files={metrics['electrical_box_independent_source_files']}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.13 electrical-box installation/context rank-depth audit。"
        "只读审计 13 条非 global、非 11.x-overlap 的 electrical_box wrong-rank 行，"
        "拆清落地式/杆上/普通成套箱、AL/ALE/AT/AP 编号、rank_2_5 与 rank_11_20 的可学习边界；"
        "仍不训练、不实现、不改阈值、不改 GoalSearcher。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：重开 numeric/spec、直接实现 electrical_box 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "把 global_repair 单源证据当通用收益、把 11.x overlap 计入 12.x 新收益、或在没有 subtype/risk 审计前进入 what-if。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.11 parser/query-family micro-hint feasibility no-go gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.12 broader 12.x strategy review after parser/query micro-hint no-go</td>\n"
        "            <td>只读回到整体 12.x，选择下一条不依赖薄弱 micro-hint、owner mappings 或立即实现的路线。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_broader_strategy_review_after_micro_hint_no_go_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_broader_strategy_review_after_micro_hint_no_go_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _electrical_context(row: dict[str, str]) -> str:
    top1_name = row.get("top1_name", "")
    query = row.get("query", "")
    expected_ids = row.get("expected_ids", "")
    if "杆上" in top1_name:
        return "pole_mounted_top1_absorption"
    if "落地式" in top1_name:
        return "floor_mounted_top1_absorption"
    if any(token in query for token in ("AL", "ALE", "AT", "AP", "AE", "ALG")):
        return "box_code_subtype_signal"
    if expected_ids:
        return "same_family_box_tier_or_install_context"
    return "unclear_electrical_box_context"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-summary", type=Path, default=DEFAULT_12X_STRATEGY)
    parser.add_argument("--rank-inventory-summary", type=Path, default=DEFAULT_RANK_INVENTORY)
    parser.add_argument("--rank25-audit-summary", type=Path, default=DEFAULT_RANK25_AUDIT)
    parser.add_argument("--numeric-closure-summary", type=Path, default=DEFAULT_NUMERIC_CLOSURE)
    parser.add_argument("--micro-hint-nogo-summary", type=Path, default=DEFAULT_MICRO_HINT_NOGO)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    strategy = _read_json(args.strategy_summary)
    rank_inventory = _read_json(args.rank_inventory_summary)
    rank25_audit = _read_json(args.rank25_audit_summary)
    numeric_closure = _read_json(args.numeric_closure_summary)
    micro_hint_nogo = _read_json(args.micro_hint_nogo_summary)
    wrong_rank = _read_csv(args.wrong_rank)

    non_global_wrong_rank = [row for row in wrong_rank if row.get("source_file") != "global_repair_decision_table.csv"]
    electrical_rows = [
        row
        for row in non_global_wrong_rank
        if row.get("query_family") == "electrical_box"
        and row.get("group_id") != "dev:62:10"
    ]
    electrical_source_files = {row.get("source_file", "") for row in electrical_rows if row.get("source_file")}
    electrical_provinces = {row.get("province", "") for row in electrical_rows if row.get("province")}
    electrical_rank_counts = Counter(row.get("rank_bucket") or "<empty>" for row in electrical_rows)
    electrical_context_counts = Counter(_electrical_context(row) for row in electrical_rows)

    candidate_lanes = [
        {
            "lane": "electrical_box_installation_context_rank_depth_audit",
            "support_rows": len(electrical_rows),
            "status": "select_next_read_only_audit",
            "why": "Largest remaining non-global same-family wrong-rank slice; spans rank_2_5 and rank_11_20 with repeated box install/context absorption.",
            "blocked_or_boundary": "No implementation or what-if until subtype/context audit separates learnable signal from source/province artifact.",
        },
        {
            "lane": "numeric_spec_tier_rank_position",
            "support_rows": numeric_closure["metrics"].get("evaluated_rows", 0),
            "status": "parked",
            "why": "12.6 guard_allowed_rows=0 and query_numeric_present_rows=0.",
            "blocked_or_boundary": "Requires explicit query/bill_text numeric/spec evidence before re-entry.",
        },
        {
            "lane": "parser_query_micro_hints",
            "support_rows": micro_hint_nogo["metrics"].get("micro_hint_candidate_rows", 0),
            "status": "no_go",
            "why": "Only three single-row mixed-domain candidates.",
            "blocked_or_boundary": "Do not run what-if or implement.",
        },
        {
            "lane": "candidate_pool_absence_query_family_coverage",
            "support_rows": 19,
            "status": "closed_to_micro_no_go",
            "why": "Full bucket is global_repair dominated and non-global parser hint support is too thin.",
            "blocked_or_boundary": "Only DQ/index coverage support remains; no parser implementation.",
        },
        {
            "lane": "ranking_training_or_objective",
            "support_rows": rank_inventory["metrics"].get("wrong_rank_rows", 0),
            "status": "defer",
            "why": "Training/tuning is outside current no-immediate-implementation boundary.",
            "blocked_or_boundary": "Needs separate explicit go and leakage/split plan.",
        },
        {
            "lane": "goal_searcher_integration",
            "support_rows": 9,
            "status": "defer",
            "why": "11.x scoped hints are released but online integration is a separate gate.",
            "blocked_or_boundary": "Requires explicit integration request; not a 12.x evidence-mining lane.",
        },
    ]
    electrical_preview = [
        {
            "group_id": row.get("group_id", ""),
            "rank_bucket": row.get("rank_bucket", ""),
            "source_file": row.get("source_file", ""),
            "province": row.get("province", ""),
            "query": row.get("query", ""),
            "expected_ids": row.get("expected_ids", ""),
            "positive_rank_min": row.get("positive_rank_min", ""),
            "top1_id": row.get("top1_id", ""),
            "top1_name": row.get("top1_name", ""),
            "context_bucket": _electrical_context(row),
        }
        for row in electrical_rows
    ]
    electrical_summary = [
        {
            "dimension": "rank_bucket",
            "key": key,
            "rows": count,
            "share": round(count / len(electrical_rows), 6) if electrical_rows else 0,
        }
        for key, count in electrical_rank_counts.most_common()
    ] + [
        {
            "dimension": "context_bucket",
            "key": key,
            "rows": count,
            "share": round(count / len(electrical_rows), 6) if electrical_rows else 0,
        }
        for key, count in electrical_context_counts.most_common()
    ] + [
        {
            "dimension": "source_file",
            "key": key,
            "rows": count,
            "share": round(count / len(electrical_rows), 6) if electrical_rows else 0,
        }
        for key, count in Counter(row.get("source_file", "") for row in electrical_rows).most_common()
    ] + [
        {
            "dimension": "province",
            "key": key,
            "rows": count,
            "share": round(count / len(electrical_rows), 6) if electrical_rows else 0,
        }
        for key, count in Counter(row.get("province", "") for row in electrical_rows).most_common()
    ]
    guardrails = [
        {
            "guardrail": "read_only_next",
            "status": "required",
            "detail": "12.13 may only audit rows and define feasibility; no code/rule change.",
        },
        {
            "guardrail": "exclude_11x_overlap",
            "status": "required",
            "detail": "dev:62:10 电箱安装（利旧） remains under 11.x attribution and is excluded from the new 12.x support count.",
        },
        {
            "guardrail": "no_heldout_hard_selection",
            "status": "required",
            "detail": "Use dev/OOF artifacts only unless a future explicit validation gate is reached.",
        },
        {
            "guardrail": "no_direct_subtype_rule",
            "status": "required",
            "detail": "Do not write electrical_box subtype/rank rules before subtype/context loss evidence and negative guards exist.",
        },
    ]
    metrics = {
        "selected_next_lane": "electrical_box_installation_context_rank_depth_audit",
        "non_global_wrong_rank_rows": len(non_global_wrong_rank),
        "electrical_box_non_global_rows": len(electrical_rows),
        "electrical_box_rank_2_5_rows": electrical_rank_counts.get("rank_2_5", 0),
        "electrical_box_rank_11_20_rows": electrical_rank_counts.get("rank_11_20", 0),
        "electrical_box_independent_source_files": len(electrical_source_files),
        "electrical_box_provinces": len(electrical_provinces),
        "numeric_spec_lane_status": "parked_no_query_numeric_evidence",
        "parser_micro_hint_status": "no_go_too_thin_mixed_domain",
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "electrical_box_preview_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_electrical_box_preview.csv")),
        "electrical_box_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_electrical_box_summary.csv")),
        "guardrails_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_guardrails.csv")),
    }
    decision = (
        "Park the parser/query micro-hint route and select a read-only electrical-box installation/context rank-depth audit for 12.13. "
        "This is the remaining lane with the cleanest non-global same-family support, but it is not implementation-ready."
    )
    report = {
        "stage": "Goal LTR v1 / 12.12 broader 12.x strategy review after parser/query micro-hint no-go",
        "read_only": True,
        "source_artifacts": {
            "strategy_summary": str(args.strategy_summary),
            "rank_inventory_summary": str(args.rank_inventory_summary),
            "rank25_audit_summary": str(args.rank25_audit_summary),
            "numeric_closure_summary": str(args.numeric_closure_summary),
            "micro_hint_nogo_summary": str(args.micro_hint_nogo_summary),
            "wrong_rank": str(args.wrong_rank),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_context": {
            "selected_12x_lane": strategy["metrics"]["selected_lane"],
            "rank25_prior_electrical_box_rows": rank25_audit["metrics"]["electrical_box_rows"],
            "micro_hint_whatif_allowed_now": micro_hint_nogo["metrics"]["whatif_allowed_now"],
        },
        "anti_drift_conclusion": (
            "12.12 is read-only. It does not reopen numeric/spec, run what-if, train, tune, change thresholds, implement electrical_box rules, "
            "edit parser/query-family rules, edit taxonomy rows, wire GoalSearcher, use heldout/hard for selection, or count 11.x overlap as new 12.x evidence."
        ),
        "next_stage": {
            "stage": "12.13 electrical-box installation/context rank-depth audit",
            "default": "read_only_audit_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["candidate_lanes_csv"]),
        candidate_lanes,
        ["lane", "support_rows", "status", "why", "blocked_or_boundary"],
    )
    _write_csv(
        Path(artifacts["electrical_box_preview_csv"]),
        electrical_preview,
        [
            "group_id",
            "rank_bucket",
            "source_file",
            "province",
            "query",
            "expected_ids",
            "positive_rank_min",
            "top1_id",
            "top1_name",
            "context_bucket",
        ],
    )
    _write_csv(Path(artifacts["electrical_box_summary_csv"]), electrical_summary, ["dimension", "key", "rows", "share"])
    _write_csv(Path(artifacts["guardrails_csv"]), guardrails, ["guardrail", "status", "detail"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
