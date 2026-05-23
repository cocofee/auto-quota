from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_near_miss_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_duct_size_tier_transferability_9x_review"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _pattern_key(row: dict[str, Any]) -> str:
    subtype = _clean(row.get("query_subtype")) or _clean(row.get("positive_subtype")) or "unknown"
    if _clean(row.get("query_perimeter_hints")):
        tier_kind = "perimeter"
    elif _clean(row.get("query_size_hints")):
        tier_kind = "diameter_or_model"
    else:
        tier_kind = "unknown_size"
    province = _clean(row.get("province"))
    if "重庆" in province:
        domain = "chongqing_wind_duct"
    elif "江西" in province:
        domain = "jiangxi_human_defense"
    else:
        domain = "other_domain"
    return f"{domain}:{subtype}:{tier_kind}"


def _top1_anchor(row: dict[str, Any]) -> str:
    return "|".join(
        [
            _clean(row.get("province")),
            _clean(row.get("source_file")),
            _clean(row.get("top1_id")),
            _clean(row.get("top_tiers")),
        ]
    )


def _review(rows: list[dict[str, Any]], source_count: int, top_anchor_counts: Counter[str]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        pattern_key = _pattern_key(row)
        top_anchor = _top1_anchor(row)
        flags = [flag for flag in _clean(row.get("flags")).split("|") if flag]
        evidence_flags: list[str] = []
        if _clean(row.get("size_relation")) == "query_size_supports_positive":
            evidence_flags.append("explicit_size_supports_positive")
        if _clean(row.get("top1_family")) != "valve":
            evidence_flags.append("family_taxonomy_conflict_present")
        if top_anchor_counts[top_anchor] >= 3:
            evidence_flags.append("repeated_same_top1_anchor")
        if source_count == 1:
            evidence_flags.append("single_source_only")

        if source_count == 1:
            transferability_status = "blocked_single_source"
            transferable = 0
            blocked_reason = "All candidate rows come from one source_file, so this cannot justify a national valve/duct size-tier rule or what-if."
        elif "explicit_size_supports_positive" not in evidence_flags:
            transferability_status = "blocked_weak_size_evidence"
            transferable = 0
            blocked_reason = "The query does not provide a strong size/perimeter signal supporting the positive tier."
        else:
            transferability_status = "needs_cross_source_confirmation"
            transferable = 0
            blocked_reason = "The pattern has size evidence, but still needs cross-source confirmation before any what-if."

        out = {
            "split": _clean(row.get("split")),
            "group_id": _clean(row.get("group_id")),
            "sample_id": _clean(row.get("sample_id")),
            "source_file": _clean(row.get("source_file")),
            "province": _clean(row.get("province")),
            "query": _clean(row.get("query")),
            "primary_issue": _clean(row.get("primary_issue")),
            "pattern_key": pattern_key,
            "query_subtype": _clean(row.get("query_subtype")),
            "size_relation": _clean(row.get("size_relation")),
            "query_size_hints": _clean(row.get("query_size_hints")),
            "query_perimeter_hints": _clean(row.get("query_perimeter_hints")),
            "top_tiers": _clean(row.get("top_tiers")),
            "positive_tiers": _clean(row.get("positive_tiers")),
            "top1_id": _clean(row.get("top1_id")),
            "top1_name": _clean(row.get("top1_name")),
            "top1_family": _clean(row.get("top1_family")),
            "top1_chapter": _clean(row.get("top1_chapter")),
            "positive_ids_in_top80": _clean(row.get("positive_ids_in_top80")),
            "positive_names_in_top80": _clean(row.get("positive_names_in_top80")),
            "positive_rank_min": _clean(row.get("positive_rank_min")),
            "top1_anchor": top_anchor,
            "top1_anchor_count": top_anchor_counts[top_anchor],
            "evidence_flags": "|".join(evidence_flags),
            "original_flags": "|".join(flags),
            "transferability_status": transferability_status,
            "transferable": transferable,
            "blocked_reason": blocked_reason,
        }
        reviewed.append(out)
    return reviewed


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in (
            "transferability_status",
            "pattern_key",
            "primary_issue",
            "province",
            "source_file",
            "query_subtype",
            "size_relation",
            "top1_id",
            "positive_rank_min",
            "top1_anchor_count",
        ):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("evidence_flags")).split("|"):
            if flag:
                counters["evidence_flag"][flag] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_valve_duct_size_tier_review", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _preview(buckets: list[dict[str, Any]], dimension: str, limit: int = 12) -> list[dict[str, Any]]:
    return [row for row in buckets if row["dimension"] == dimension][:limit]


def _distinct(rows: list[dict[str, Any]], field: str) -> set[str]:
    return {_clean(row.get(field)) for row in rows if _clean(row.get(field))}


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    previews = report["artifacts_preview"]["top_buckets"]
    lines = [
        "# Stage 9.14 Valve/Duct Size-tier Transferability Review",
        "",
        "Dev-only review of the 8 valve/duct size-tier candidates from stage 9.13. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["review_rows", report["metrics"]["review_rows"]],
                ["province_count", report["metrics"]["province_count"]],
                ["source_count", report["metrics"]["source_count"]],
                ["pattern_count", report["metrics"]["pattern_count"]],
                ["explicit_size_supported_rows", report["metrics"]["explicit_size_supported_rows"]],
                ["repeated_top1_anchor_rows", report["metrics"]["repeated_top1_anchor_rows"]],
                ["transferable_rows", report["metrics"]["transferable_rows"]],
                ["what_if_ready", report["decision"]["what_if_ready"]],
                ["next_stage", report["next_stage"]["stage"]],
            ]
        ),
        "",
        "## Transferability Status",
        "",
        _md_table([["status", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in previews["transferability_status"]]),
        "",
        "## Pattern Buckets",
        "",
        _md_table([["pattern", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in previews["pattern_key"]]),
        "",
        "## Decision",
        "",
        report["decision"]["reason"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.14 valve/duct size-tier transferability review")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = [
        row
        for row in _read_csv(Path(args.rows))
        if _clean(row.get("learning_status")) == "candidate_for_transferability_review"
    ]
    source_count = len(_distinct(source_rows, "source_file"))
    top_anchor_counts = Counter(_top1_anchor(row) for row in source_rows)
    reviewed = _review(source_rows, source_count, top_anchor_counts)
    buckets = _bucket_rows(reviewed)
    province_count = len(_distinct(reviewed, "province"))
    pattern_count = len(_distinct(reviewed, "pattern_key"))
    explicit_size_supported_rows = sum(1 for row in reviewed if "explicit_size_supports_positive" in row["evidence_flags"])
    repeated_top1_anchor_rows = sum(1 for row in reviewed if "repeated_same_top1_anchor" in row["evidence_flags"])
    transferable_rows = sum(1 for row in reviewed if _clean(row.get("transferable")) == "1")

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.14 valve/duct size-tier transferability review",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifact": str(Path(args.rows)),
        "metrics": {
            "review_rows": len(reviewed),
            "province_count": province_count,
            "source_count": source_count,
            "pattern_count": pattern_count,
            "explicit_size_supported_rows": explicit_size_supported_rows,
            "repeated_top1_anchor_rows": repeated_top1_anchor_rows,
            "single_source_blocked_rows": sum(1 for row in reviewed if row["transferability_status"] == "blocked_single_source"),
            "transferable_rows": transferable_rows,
        },
        "decision": {
            "recommendation": "stop_valve_duct_size_tier_direction",
            "what_if_ready": False,
            "reason": "All 8 candidates have explicit size evidence, but all 8 come from global_repair_decision_table.csv. The rows split into province-specific Jiangxi human-defense valve tiers and Chongqing wind-duct check-valve perimeter tiers, with repeated same Top1 anchors. This is not enough to justify a national valve/duct size-tier what-if or rule.",
        },
        "next_stage": {
            "stage": "9.15 ranked gap reselection after valve/duct single-source block",
            "goal": "exclude the blocked valve/duct size-tier direction and choose the next high-support dev wrong-rank bucket",
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
            ],
        },
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "transferability_status": _preview(buckets, "transferability_status"),
                "pattern_key": _preview(buckets, "pattern_key"),
                "province": _preview(buckets, "province"),
                "source_file": _preview(buckets, "source_file"),
                "top1_id": _preview(buckets, "top1_id"),
                "evidence_flag": _preview(buckets, "evidence_flag"),
            },
            "rows": reviewed,
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.14 stops the valve/duct size-tier direction because the only promising evidence is single-source and province-specific. This prevents turning 8 same-source examples into a valve/duct patch. The next action must return to dev wrong-rank bucket selection.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "primary_issue",
        "pattern_key",
        "query_subtype",
        "size_relation",
        "query_size_hints",
        "query_perimeter_hints",
        "top_tiers",
        "positive_tiers",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_chapter",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_rank_min",
        "top1_anchor",
        "top1_anchor_count",
        "evidence_flags",
        "original_flags",
        "transferability_status",
        "transferable",
        "blocked_reason",
    ]
    _write_csv(Path(artifacts["rows_csv"]), reviewed, row_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "decision": report["decision"], "next_stage": report["next_stage"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
