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
DEFAULT_DESIGN_GATE = AGENT_STATE / "goal_12x_rank_position_design_gate_summary.json"
DEFAULT_WRONG_RANK = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_non_global_rank25_slice_audit"


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
        "# 12.3 Non-Global Rank 2-5 Slice Audit",
        "",
        "Read-only audit of the 24 non-global-repair rank_2_5 rows.",
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
        "当前状态：12.3 non-global rank_2_5 slice audit 已完成。"
        f"audited_rows={report['metrics']['audited_rows']}；"
        f"plan_candidate_rows={report['metrics']['plan_candidate_rows']}；"
        f"recommended_lane={report['metrics']['recommended_lane']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：12.4 numeric/spec tier rank-position minimal plan definition。只读定义是否能把同族数值/规格档位近失误"
        "转成最小实现计划；仍不实现、不训练、不调参。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现、训练、调参、改阈值、改 GoalSearcher、重开 11.x、使用 heldout/hard 做选择、"
            "或把混合 24 条切片当成单一全局规则。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.2 rank-position bottleneck design gate</td>"
    row = (
        "          <tr>\n"
        "            <td>12.3 non-global rank_2_5 slice audit</td>\n"
        "            <td>只读审计 24 条非 global-repair rank_2_5，拆分窄域候选和阻断项。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_non_global_rank25_slice_audit_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_non_global_rank25_slice_audit_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _classify(row: dict[str, str]) -> tuple[str, str, str, bool]:
    query = row.get("query", "")
    query_family = row.get("query_family", "")
    top1_family = row.get("top1_family", "")
    top1_name = row.get("top1_name", "")
    expected_names = row.get("expected_names", "")
    reasons = row.get("top1_reasons", "")
    combined = f"{query} {top1_name} {expected_names} {reasons}"

    if row.get("group_id") in {"dev:62:10", "dev:63:20", "dev:167:98", "dev:170:390"}:
        return (
            "already_covered_or_overlaps_11x_parser_hints",
            "Rows overlap the released 11.x parser/query hint theme; do not use them to justify a new 12.x rank rule.",
            "exclude_from_12x_implementation",
            False,
        )
    if "family conflict" in reasons or (query_family and top1_family and query_family != top1_family):
        return (
            "cross_family_absorption_or_taxonomy_conflict",
            "Top1 crosses family/taxonomy boundary; needs taxonomy/parser audit rather than rank-position rule.",
            "block_from_rank_position_plan",
            False,
        )
    if query_family == "electrical_box" or top1_family == "electrical_box" or _has_any(query, ("配电箱", "电箱")):
        return (
            "electrical_box_install_type_or_size_tier",
            "Repeated near misses around distribution-box installation subtype/size tier; possible narrow plan after subtype evidence.",
            "audit_before_plan",
            False,
        )
    if _has_any(combined, ("公称直径", "DN", "直径", "截面", "风量", "周长", "壁厚", "≤", "以内", "mm", "m3/h")):
        return (
            "same_family_numeric_or_spec_tier",
            "Expected row is close and same/related family; error appears to be numeric/spec tier ordering.",
            "candidate_for_12_4_plan_definition",
            True,
        )
    if query_family == top1_family and query_family:
        return (
            "same_family_non_numeric_subtype",
            "Same-family near miss but no clear numeric/spec lever visible from row fields.",
            "audit_before_plan",
            False,
        )
    return (
        "mixed_or_unclear",
        "Insufficiently clean row-level evidence for a minimal rank-position lever.",
        "block_from_rank_position_plan",
        False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design-gate-summary", type=Path, default=DEFAULT_DESIGN_GATE)
    parser.add_argument("--wrong-rank", type=Path, default=DEFAULT_WRONG_RANK)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    design_gate = _read_json(args.design_gate_summary)
    wrong_rank = _read_csv(args.wrong_rank)
    rows = [
        row
        for row in wrong_rank
        if row.get("source_file") != "global_repair_decision_table.csv"
        and row.get("rank_bucket") == "rank_2_5"
    ]

    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        bucket, note, disposition, plan_candidate = _classify(row)
        audit_rows.append(
            {
                "group_id": row.get("group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "query_family": row.get("query_family", "") or "<empty>",
                "expected_ids": row.get("expected_ids", ""),
                "expected_names": row.get("expected_names", ""),
                "positive_ranks": row.get("positive_ranks", ""),
                "positive_rank_min": row.get("positive_rank_min", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_family": row.get("top1_family", "") or "<empty>",
                "top1_reasons": row.get("top1_reasons", ""),
                "audit_bucket": bucket,
                "audit_note": note,
                "disposition": disposition,
                "plan_candidate": plan_candidate,
            }
        )

    bucket_counts = Counter(row["audit_bucket"] for row in audit_rows)
    bucket_summary = [
        {
            "audit_bucket": bucket,
            "rows": count,
            "share": round(count / len(audit_rows), 6) if audit_rows else 0,
            "plan_candidate_rows": sum(1 for row in audit_rows if row["audit_bucket"] == bucket and row["plan_candidate"]),
        }
        for bucket, count in bucket_counts.most_common()
    ]
    source_purity = [
        {"dimension": "source_file", "key": key, "rows": count, "share": round(count / len(audit_rows), 6)}
        for key, count in Counter(row["source_file"] for row in audit_rows).most_common()
    ]
    family_purity = [
        {"dimension": "query_family", "key": key, "rows": count, "share": round(count / len(audit_rows), 6)}
        for key, count in Counter(row["query_family"] for row in audit_rows).most_common()
    ]
    candidate_lanes = [
        {
            "lane": "same_family_numeric_or_spec_tier",
            "support_rows": bucket_counts.get("same_family_numeric_or_spec_tier", 0),
            "status": "allow_12_4_plan_definition" if bucket_counts.get("same_family_numeric_or_spec_tier", 0) >= 5 else "weak",
            "required_boundary": "exact numeric/spec comparator, same-family guard, rollback and loss-budget checks",
        },
        {
            "lane": "electrical_box_install_type_or_size_tier",
            "support_rows": bucket_counts.get("electrical_box_install_type_or_size_tier", 0),
            "status": "hold_for_subtype_evidence",
            "required_boundary": "box install type/size evidence; avoid broad distribution-box rerank",
        },
        {
            "lane": "cross_family_absorption_or_taxonomy_conflict",
            "support_rows": bucket_counts.get("cross_family_absorption_or_taxonomy_conflict", 0),
            "status": "block_from_rank_position_implementation",
            "required_boundary": "taxonomy/parser audit, not ranking rule",
        },
        {
            "lane": "already_covered_or_overlaps_11x_parser_hints",
            "support_rows": bucket_counts.get("already_covered_or_overlaps_11x_parser_hints", 0),
            "status": "exclude_from_12x_attribution",
            "required_boundary": "monitor under 11.x release only",
        },
    ]
    plan_candidate_rows = sum(1 for row in audit_rows if row["plan_candidate"])
    implementation_allowed_now = False
    recommended_lane = (
        "same_family_numeric_or_spec_tier"
        if plan_candidate_rows >= 5
        else "none_plan_ready"
    )
    metrics = {
        "audited_rows": len(audit_rows),
        "plan_candidate_rows": plan_candidate_rows,
        "recommended_lane": recommended_lane,
        "numeric_spec_tier_rows": bucket_counts.get("same_family_numeric_or_spec_tier", 0),
        "electrical_box_rows": bucket_counts.get("electrical_box_install_type_or_size_tier", 0),
        "cross_family_conflict_rows": bucket_counts.get("cross_family_absorption_or_taxonomy_conflict", 0),
        "overlaps_11x_rows": bucket_counts.get("already_covered_or_overlaps_11x_parser_hints", 0),
        "implementation_allowed_now": implementation_allowed_now,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "heldout_hard_used": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "row_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")),
        "bucket_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_bucket_summary.csv")),
        "source_purity_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_purity.csv")),
        "family_purity_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_family_purity.csv")),
        "candidate_lanes_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_candidate_lanes.csv")),
    }
    decision = (
        "The 24-row non-global rank_2_5 slice is mixed, so it cannot justify a broad rank-position implementation. "
        "The only lane worth carrying forward is the same-family numeric/spec tier subset; it may enter a read-only 12.4 "
        "minimal plan definition. Electrical-box subtype rows, 11.x-overlap rows, and cross-family conflicts stay blocked."
    )
    report = {
        "stage": "Goal LTR v1 / 12.3 non-global rank_2_5 slice audit",
        "read_only": True,
        "source_artifacts": {
            "design_gate_summary": str(args.design_gate_summary),
            "wrong_rank": str(args.wrong_rank),
        },
        "metrics": metrics,
        "decision": decision,
        "design_gate_context": {
            "gate_decision": design_gate["metrics"]["gate_decision"],
            "non_global_rank_2_5_rows": design_gate["metrics"]["non_global_rank_2_5_rows"],
        },
        "anti_drift_conclusion": (
            "12.3 is read-only. It audits 24 non-global rank_2_5 rows only; it does not implement, train, tune, "
            "change thresholds, edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, "
            "or use heldout/hard for selection."
        ),
        "next_stage": {
            "stage": "12.4 numeric/spec tier rank-position minimal plan definition",
            "default": "read_only_plan_definition_only",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["row_audit_csv"]), audit_rows, list(audit_rows[0].keys()))
    _write_csv(Path(artifacts["bucket_summary_csv"]), bucket_summary, list(bucket_summary[0].keys()))
    _write_csv(Path(artifacts["source_purity_csv"]), source_purity, list(source_purity[0].keys()))
    _write_csv(Path(artifacts["family_purity_csv"]), family_purity, list(family_purity[0].keys()))
    _write_csv(Path(artifacts["candidate_lanes_csv"]), candidate_lanes, list(candidate_lanes[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
