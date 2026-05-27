from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from src.goal_search.national_index import infer_family

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DESIGN_GATE = AGENT_STATE / "goal_12x_candidate_pool_absence_coverage_design_gate_summary.json"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_non_global_candidate_pool_absence_coverage_row_audit"


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
        "# 12.10 Non-Global Candidate-Pool Absence Coverage Row Audit",
        "",
        "Read-only row audit of the 19 non-global-repair top1_family_empty / query_family_empty rows.",
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
        "当前状态：12.10 non-global candidate-pool absence coverage row audit 已完成。"
        f"audited_rows={metrics['audited_rows']}；"
        f"parser_hint_candidate_rows={metrics['parser_hint_candidate_rows']}；"
        f"index_or_taxonomy_coverage_rows={metrics['index_or_taxonomy_coverage_rows']}；"
        f"plan_ready_rows={metrics['plan_ready_rows']}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.11 parser/query-family micro-hint feasibility no-go gate。"
        "只读判断 3 条弱 parser/query-family 候选是否足够进入未来 dev/OOF what-if；"
        "默认不实现，若仍太薄则回到 broader 12.x strategy。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现 parser/query-family 规则、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "把 3 条弱候选宣称为通用 Top1 提升、忽略 11.x overlap/SPD guard、或把 taxonomy/index coverage 行当成算法证据。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.9 candidate-pool absence coverage design gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.10 non-global candidate-pool absence coverage row audit</td>\n"
        "            <td>只读审计 19 条非 global-repair top1_family_empty/query_family_empty，拆分 parser hint、index family coverage、taxonomy/DQ、11.x overlap 和 blocked。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_non_global_candidate_pool_absence_coverage_row_audit_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_non_global_candidate_pool_absence_coverage_row_audit_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _classify(row: dict[str, str], current_inferred_family: str) -> dict[str, Any]:
    group_id = row.get("group_id", "")
    query = row.get("query", "")
    reason = row.get("reason", "")

    if group_id in {"dev:168:303", "dev:169:519", "dev:174:302"}:
        return {
            "audit_bucket": "already_covered_or_overlaps_11x_parser_hints",
            "disposition": "exclude_from_new_12x_attribution",
            "implementation_candidate": False,
            "future_feasibility_candidate": False,
            "audit_note": "扩声系统设备 is already covered by the released 11.x weak_current_device parser hint; monitor under 11.x, do not reuse as new 12.x evidence.",
        }
    if group_id == "dev:58:19":
        return {
            "audit_bucket": "blocked_existing_spd_guard_regression_risk",
            "disposition": "blocked_no_new_parser_hint",
            "implementation_candidate": False,
            "future_feasibility_candidate": False,
            "audit_note": "浪涌保护器 touches the existing 11.x SPD/camera guard area; do not add a broad family hint without separate loss evidence.",
        }
    if reason == "query_family_empty" and group_id in {"dev:31:32", "dev:76:16", "dev:173:528"}:
        return {
            "audit_bucket": "weak_parser_query_family_hint_candidate",
            "disposition": "future_feasibility_gate_only",
            "implementation_candidate": False,
            "future_feasibility_candidate": True,
            "audit_note": "Query family is empty and current infer_family remains empty; possible micro-hint, but support is sparse and mixed across lamp/instrument/video domains.",
        }
    if reason == "query_family_empty":
        return {
            "audit_bucket": "query_family_empty_blocked_or_unclear",
            "disposition": "blocked_pending_cleaner_evidence",
            "implementation_candidate": False,
            "future_feasibility_candidate": False,
            "audit_note": f"Query family is empty, but row is not a clean standalone parser candidate; current_inferred_family={current_inferred_family or '<empty>'}.",
        }
    if reason == "top1_family_empty":
        if query.startswith("螺纹阀门"):
            note = "Valve query family is present; the miss is top1/index-family empty plus valve subtype ambiguity, not a parser absence signal."
        elif "风管" in query or "通风管道" in query:
            note = "Duct query family is present; top1 is a duct-book artifact with empty family, so this is index/taxonomy coverage rather than query parser work."
        elif "装饰灯" in query:
            note = "Lamp query family is present in current parser; top1_family_empty points to index/taxonomy coverage or lamp subtype labeling."
        else:
            note = "Query family is present; top1_family_empty points to index family coverage, taxonomy label, or quota-label mixture."
        return {
            "audit_bucket": "index_or_taxonomy_family_coverage_gap",
            "disposition": "dq_or_index_coverage_support_only",
            "implementation_candidate": False,
            "future_feasibility_candidate": False,
            "audit_note": note,
        }
    return {
        "audit_bucket": "blocked_unexpected_reason",
        "disposition": "blocked_pending_manual_review",
        "implementation_candidate": False,
        "future_feasibility_candidate": False,
        "audit_note": "Unexpected row shape for 12.10 target slice.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-gate-summary", type=Path, default=DEFAULT_DESIGN_GATE)
    parser.add_argument("--top80-missing", type=Path, default=DEFAULT_TOP80_MISSING)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    design_gate = _read_json(args.design_gate_summary)
    top80_missing = _read_csv(args.top80_missing)
    rows = [
        row
        for row in top80_missing
        if row.get("source_file") != "global_repair_decision_table.csv"
        and row.get("reason") in {"top1_family_empty", "query_family_empty"}
    ]

    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        current_inferred_family = infer_family(row.get("query", ""))
        classified = _classify(row, current_inferred_family)
        audit_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "reason": row.get("reason", ""),
                "query": row.get("query", ""),
                "query_family_from_artifact": row.get("query_family", "") or "<empty>",
                "current_inferred_family": current_inferred_family or "<empty>",
                "expected_ids": row.get("expected_ids", ""),
                "expected_books": row.get("expected_books", ""),
                "expected_names": row.get("expected_names", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_family": row.get("top1_family", "") or "<empty>",
                "top1_book": row.get("top1_book", ""),
                "top1_reasons": row.get("top1_reasons", ""),
                **classified,
            }
        )

    bucket_counts = Counter(row["audit_bucket"] for row in audit_rows)
    bucket_summary = [
        {
            "audit_bucket": bucket,
            "rows": count,
            "share": round(count / len(audit_rows), 6) if audit_rows else 0,
            "implementation_candidate_rows": sum(
                1 for row in audit_rows if row["audit_bucket"] == bucket and row["implementation_candidate"]
            ),
            "future_feasibility_candidate_rows": sum(
                1 for row in audit_rows if row["audit_bucket"] == bucket and row["future_feasibility_candidate"]
            ),
        }
        for bucket, count in bucket_counts.most_common()
    ]
    candidate_lanes = [
        {
            "lane": "weak_parser_query_family_micro_hints",
            "support_rows": bucket_counts.get("weak_parser_query_family_hint_candidate", 0),
            "status": "feasibility_gate_only_not_implementation",
            "why": "Only 3 rows and they are mixed across 路灯, 空气流量指示器, 视频系统设备.",
            "required_before_whatif": "prove exact query text evidence, define negative guards, and keep dev/OOF-only loss audit.",
        },
        {
            "lane": "index_or_taxonomy_family_coverage_gap",
            "support_rows": bucket_counts.get("index_or_taxonomy_family_coverage_gap", 0),
            "status": "dq_or_index_support_only",
            "why": "Query family is already present; top1_family_empty is not parser absence.",
            "required_before_whatif": "owner/DQ acceptance or separate index coverage plan; no algorithm rule from this audit.",
        },
        {
            "lane": "already_covered_or_overlaps_11x_parser_hints",
            "support_rows": bucket_counts.get("already_covered_or_overlaps_11x_parser_hints", 0),
            "status": "exclude_from_new_12x_attribution",
            "why": "Covered by 11.x released hint path.",
            "required_before_whatif": "do not double count; monitor under 11.x evidence only.",
        },
        {
            "lane": "blocked_existing_spd_guard_regression_risk",
            "support_rows": bucket_counts.get("blocked_existing_spd_guard_regression_risk", 0),
            "status": "blocked",
            "why": "SPD rows have known guard/regression risk from 11.x.",
            "required_before_whatif": "separate SPD-specific evidence and loss guard; no broad hint.",
        },
    ]
    source_summary = [
        {"source_file": key, "rows": count, "share": round(count / len(audit_rows), 6) if audit_rows else 0}
        for key, count in Counter(row["source_file"] for row in audit_rows).most_common()
    ]

    parser_hint_candidate_rows = sum(1 for row in audit_rows if row["future_feasibility_candidate"])
    index_or_taxonomy_rows = bucket_counts.get("index_or_taxonomy_family_coverage_gap", 0)
    overlap_11x_rows = bucket_counts.get("already_covered_or_overlaps_11x_parser_hints", 0)
    blocked_rows = sum(1 for row in audit_rows if row["disposition"].startswith("blocked"))
    metrics = {
        "audited_rows": len(audit_rows),
        "parser_hint_candidate_rows": parser_hint_candidate_rows,
        "index_or_taxonomy_coverage_rows": index_or_taxonomy_rows,
        "overlap_11x_rows": overlap_11x_rows,
        "blocked_rows": blocked_rows,
        "plan_ready_rows": 0,
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "row_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")),
        "bucket_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bucket_summary.csv")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
        "source_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_summary.csv")),
    }
    decision = (
        "No implementation-ready lane. The 19-row non-global slice is mostly top1/index-family empty taxonomy or index coverage. "
        "Only 3 rows are weak parser/query-family micro-hint candidates, and they are too sparse and mixed for direct implementation."
    )
    report = {
        "stage": "Goal LTR v1 / 12.10 non-global candidate-pool absence coverage row audit",
        "read_only": True,
        "source_artifacts": {
            "design_gate_summary": str(args.design_gate_summary),
            "top80_missing": str(args.top80_missing),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_gate_context": {
            "gate_decision": design_gate["metrics"]["gate_decision"],
            "non_global_diagnostic_rows": design_gate["metrics"]["non_global_diagnostic_rows"],
        },
        "anti_drift_conclusion": (
            "12.10 is read-only. It writes only diagnostic artifacts and the dashboard. It does not train, tune, change thresholds, "
            "edit parser/query-family rules, edit taxonomy rows, edit feature whitelists, wire GoalSearcher, use heldout/hard for selection, "
            "or count 11.x overlap rows as new 12.x evidence."
        ),
        "next_stage": {
            "stage": "12.11 parser/query-family micro-hint feasibility no-go gate",
            "default": "read_only_no_implementation",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["row_audit_csv"]),
        audit_rows,
        [
            "group_id",
            "sample_id",
            "source_file",
            "province",
            "reason",
            "query",
            "query_family_from_artifact",
            "current_inferred_family",
            "expected_ids",
            "expected_books",
            "expected_names",
            "top1_id",
            "top1_name",
            "top1_family",
            "top1_book",
            "top1_reasons",
            "audit_bucket",
            "disposition",
            "implementation_candidate",
            "future_feasibility_candidate",
            "audit_note",
        ],
    )
    _write_csv(
        Path(artifacts["bucket_summary_csv"]),
        bucket_summary,
        ["audit_bucket", "rows", "share", "implementation_candidate_rows", "future_feasibility_candidate_rows"],
    )
    _write_csv(
        Path(artifacts["candidate_lanes_csv"]),
        candidate_lanes,
        ["lane", "support_rows", "status", "why", "required_before_whatif"],
    )
    _write_csv(Path(artifacts["source_summary_csv"]), source_summary, ["source_file", "rows", "share"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
