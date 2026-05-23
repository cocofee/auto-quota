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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_RESELECTION_SUMMARY = AGENT_STATE / "goal_ranked_gap_reselection_after_query_family_empty_9x_summary.json"
DEFAULT_RESELECTION_CANDIDATES = AGENT_STATE / "goal_ranked_gap_reselection_after_query_family_empty_9x_candidates.csv"
DEFAULT_DECOMPOSITION_SUMMARY = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_no_eligible_wrong_rank_closure_9x"

MIN_SUPPORT = 20
MIN_PROVINCES = 3
MIN_SOURCES = 2


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_label(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_family": _clean(row.get("query_family")),
        "reason": _clean(row.get("reason")),
        "count": _to_int(row.get("count")),
        "province_count": _to_int(row.get("province_count")),
        "source_count": _to_int(row.get("source_count")),
        "eligibility": _clean(row.get("eligibility")),
        "eligibility_reason": _clean(row.get("eligibility_reason")),
    }


def _gate_policy_options(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    active = [row for row in candidates if _clean(row.get("eligibility")) not in {"excluded", "blocked"}]
    support_10 = [
        row
        for row in active
        if _to_int(row.get("count")) >= 10
        and _to_int(row.get("province_count")) >= MIN_PROVINCES
        and _to_int(row.get("source_count")) >= MIN_SOURCES
    ]
    source_1 = [
        row
        for row in active
        if _to_int(row.get("count")) >= MIN_SUPPORT
        and _to_int(row.get("province_count")) >= MIN_PROVINCES
        and _to_int(row.get("source_count")) >= 1
    ]
    province_1 = [
        row
        for row in active
        if _to_int(row.get("count")) >= MIN_SUPPORT
        and _to_int(row.get("province_count")) >= 1
        and _to_int(row.get("source_count")) >= MIN_SOURCES
    ]
    options = [
        {
            "option": "keep_current_gate",
            "admitted_groups": 0,
            "admitted_rows": 0,
            "risk": "none",
            "recommendation": "keep",
            "rationale": "No candidate passes support>=20, province>=3, source>=2 after audited exclusions.",
        },
        {
            "option": "lower_support_to_10_keep_diversity",
            "admitted_groups": len(support_10),
            "admitted_rows": sum(_to_int(row.get("count")) for row in support_10),
            "risk": "fragmented_low_support",
            "recommendation": "do_not_use_as_automatic_next_step",
            "rationale": "Would admit small buckets rather than high-support transferable directions.",
        },
        {
            "option": "lower_source_to_1_keep_support",
            "admitted_groups": len(source_1),
            "admitted_rows": sum(_to_int(row.get("count")) for row in source_1),
            "risk": "single_source_artifacts",
            "recommendation": "do_not_use_as_automatic_next_step",
            "rationale": "Would weaken the anti-same-source guard that blocked several prior directions.",
        },
        {
            "option": "lower_province_to_1_keep_support_source",
            "admitted_groups": len(province_1),
            "admitted_rows": sum(_to_int(row.get("count")) for row in province_1),
            "risk": "no_gain",
            "recommendation": "not_relevant",
            "rationale": "Province diversity is not the bottleneck.",
        },
    ]
    detail = {
        "current_gate": {
            "min_support": MIN_SUPPORT,
            "min_province_count": MIN_PROVINCES,
            "min_source_count": MIN_SOURCES,
        },
        "lower_support_to_10_keep_diversity": [_candidate_label(row) for row in support_10[:12]],
        "lower_source_to_1_keep_support": [_candidate_label(row) for row in source_1[:12]],
        "lower_province_to_1_keep_support_source": [_candidate_label(row) for row in province_1[:12]],
    }
    return detail, options


def _summarize_missing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(_clean(row.get("query_family")) or "<empty>", _clean(row.get("reason")) or "<empty>")].append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for (family, reason), items in grouped.items():
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        expected_books = Counter(_clean(row.get("expected_books")) or "<empty>" for row in items)
        top1_books = Counter(_clean(row.get("top1_book")) or "<empty>" for row in items)
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        top1_families = Counter(_clean(row.get("top1_family")) or "<empty>" for row in items)
        dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
        count = len(items)
        out.append(
            {
                "query_family": family,
                "reason": reason,
                "count": count,
                "rate_within_dev_top80_missing": _rate(count, total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_source_count,
                "dominant_source_rate": _rate(dominant_source_count, count),
                "top_expected_book": expected_books.most_common(1)[0][0] if expected_books else "",
                "top_expected_book_count": expected_books.most_common(1)[0][1] if expected_books else 0,
                "top1_book_mode": top1_books.most_common(1)[0][0] if top1_books else "",
                "top1_book_mode_count": top1_books.most_common(1)[0][1] if top1_books else 0,
                "top1_family_mode": top1_families.most_common(1)[0][0] if top1_families else "",
                "top1_family_mode_count": top1_families.most_common(1)[0][1] if top1_families else 0,
                "example_queries": " | ".join(query for query, _ in queries.most_common(8)),
                "recall_audit_shape": (
                    "high_support_but_source_dominated"
                    if count >= MIN_SUPPORT and len(sources) == 1
                    else "high_support_diverse"
                    if count >= MIN_SUPPORT and len(sources) >= MIN_SOURCES
                    else "small_or_fragmented"
                ),
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _missing_overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources = Counter(_clean(row.get("source_file")) for row in rows if _clean(row.get("source_file")))
    reasons = Counter(_clean(row.get("reason")) for row in rows)
    families = Counter(_clean(row.get("query_family")) or "<empty>" for row in rows)
    provinces = Counter(_clean(row.get("province")) for row in rows if _clean(row.get("province")))
    top1_families = Counter(_clean(row.get("top1_family")) or "<empty>" for row in rows)
    dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
    return {
        "dev_top80_missing_rows": len(rows),
        "reason_counts": dict(reasons.most_common()),
        "query_family_counts": dict(families.most_common(12)),
        "province_count": len(provinces),
        "source_count": len(sources),
        "dominant_source": dominant_source,
        "dominant_source_count": dominant_source_count,
        "dominant_source_rate": _rate(dominant_source_count, len(rows)),
        "top1_family_counts": dict(top1_families.most_common(8)),
    }


def _dev_decomposition_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    dev = next(item for item in summary["splits"] if item["split"] == "dev")
    return {
        "dev_groups": dev["groups"],
        "dev_baseline_top1_hit": dev["baseline_top1_hit"],
        "dev_baseline_top1_rate": dev["baseline_top1_rate"],
        "dev_top80_missing": dev["top80_missing"],
        "dev_top80_missing_rate": dev["top80_missing_rate"],
        "dev_wrong_rank": dev["top80_present_but_wrong_rank"],
        "dev_wrong_rank_rate": dev["top80_present_but_wrong_rank_rate"],
        "dev_wrong_rank_share_of_non_hit": dev["wrong_rank_share_of_non_hit"],
        "dev_top80_recall_rate": dev["top80_recall_rate"],
    }


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any], gate_options: list[dict[str, Any]], recall_buckets: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.27 No Eligible Wrong-rank Closure Review",
        "",
        "Read-only closure review for the dev wrong-rank bucket-mining lane. This decides between switching to recall-missing decomposition and explicitly reviewing gate policy, without training, tuning, rule patches, ranking changes, heldout selection, or GoalSearcher changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["dev_wrong_rank_rows", metrics["wrong_rank_lane"]["dev_wrong_rank_rows"]],
                ["remaining_dev_wrong_rank_rows", metrics["wrong_rank_lane"]["remaining_dev_wrong_rank_rows"]],
                ["remaining_candidate_groups", metrics["wrong_rank_lane"]["candidate_groups"]],
                ["eligible_candidates", metrics["wrong_rank_lane"]["eligible_candidates"]],
                ["support_below_20_candidates", metrics["wrong_rank_lane"]["support_below_20_candidates"]],
                ["source_below_2_candidates", metrics["wrong_rank_lane"]["source_below_2_candidates"]],
                ["dev_top80_missing_rows", metrics["recall_missing_lane"]["dev_top80_missing_rows"]],
                ["dev_top80_missing_rate", metrics["dev_decomposition"]["dev_top80_missing_rate"]],
                ["recall_missing_dominant_source_rate", metrics["recall_missing_lane"]["dominant_source_rate"]],
            ]
        ),
        "",
        "## Gate Policy Options",
        "",
        _md_table(
            [["option", "admitted_groups", "admitted_rows", "risk", "recommendation"]]
            + [
                [row["option"], row["admitted_groups"], row["admitted_rows"], row["risk"], row["recommendation"]]
                for row in gate_options
            ]
        ),
        "",
        "## Recall-missing Preview",
        "",
        _md_table(
            [["family", "reason", "count", "provinces", "sources", "dominant_source_rate", "shape", "examples"]]
            + [
                [
                    row["query_family"],
                    row["reason"],
                    row["count"],
                    row["province_count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["recall_audit_shape"],
                    row["example_queries"],
                ]
                for row in recall_buckets[:14]
            ]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.27 no eligible remaining dev wrong-rank closure review")
    parser.add_argument("--reselection-summary", default=str(DEFAULT_RESELECTION_SUMMARY))
    parser.add_argument("--reselection-candidates", default=str(DEFAULT_RESELECTION_CANDIDATES))
    parser.add_argument("--decomposition-summary", default=str(DEFAULT_DECOMPOSITION_SUMMARY))
    parser.add_argument("--top80-missing", default=str(DEFAULT_TOP80_MISSING))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    reselection_summary = _read_json(Path(args.reselection_summary))
    candidates = _read_csv(Path(args.reselection_candidates))
    decomposition_summary = _read_json(Path(args.decomposition_summary))
    top80_missing = [
        row
        for row in _read_csv(Path(args.top80_missing))
        if _clean(row.get("split")) == "dev" and _clean(row.get("status")) == "top80_missing"
    ]
    gate_detail, gate_options = _gate_policy_options(candidates)
    recall_buckets = _summarize_missing(top80_missing)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_policy_options_csv": str(output_prefix.with_name(output_prefix.name + "_gate_policy_options.csv")),
        "recall_missing_preview_csv": str(output_prefix.with_name(output_prefix.name + "_recall_missing_preview.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.27 no eligible remaining dev wrong-rank bucket closure review",
        "read_only": True,
        "eval_only": True,
        "dev_only_analysis": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_9_26_summary": str(Path(args.reselection_summary)),
            "stage_9_26_candidates": str(Path(args.reselection_candidates)),
            "stage_9_0_decomposition_summary": str(Path(args.decomposition_summary)),
            "top80_missing_rows": str(Path(args.top80_missing)),
        },
        "metrics": {
            "wrong_rank_lane": reselection_summary["metrics"],
            "dev_decomposition": _dev_decomposition_metrics(decomposition_summary),
            "gate_policy_review": gate_detail,
            "recall_missing_lane": _missing_overview(top80_missing),
        },
        "top_recall_missing_buckets": recall_buckets[:16],
        "decision": (
            "Close the current dev wrong-rank bucket-mining lane and switch next to recall-missing decomposition. "
            "The unchanged wrong-rank gate has no eligible remaining bucket after audited exclusions, while gate relaxation would mainly admit "
            "low-support fragments or single-source artifacts. Recall-missing still covers 330 dev rows, so the next step should be a read-only "
            "recall-missing decomposition with explicit source-dominance and taxonomy-empty checks, not gate relaxation."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.27 only reviews closure of the dev wrong-rank mining lane and selects the next analysis lane. It does not train, tune, "
            "patch rules, change ranking, modify GoalSearcher, use heldout for selection, or relax the gate."
        ),
        "next_stage": {
            "stage": "9.28 recall-missing decomposition kickoff",
            "goal": (
                "Read-only decomposition of dev top80_missing rows, starting from reason/query_family/source/province buckets and separating "
                "true recall failures from empty taxonomy labels and single-source artifacts."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
            ],
        },
    }

    gate_fields = ["option", "admitted_groups", "admitted_rows", "risk", "recommendation", "rationale"]
    recall_fields = [
        "query_family",
        "reason",
        "count",
        "rate_within_dev_top80_missing",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "top_expected_book",
        "top_expected_book_count",
        "top1_book_mode",
        "top1_book_mode_count",
        "top1_family_mode",
        "top1_family_mode_count",
        "example_queries",
        "recall_audit_shape",
    ]
    _write_csv(Path(artifacts["gate_policy_options_csv"]), gate_options, gate_fields)
    _write_csv(Path(artifacts["recall_missing_preview_csv"]), recall_buckets, recall_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_options, recall_buckets)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "decision": report["decision"],
                "wrong_rank_metrics": report["metrics"]["wrong_rank_lane"],
                "recall_missing_overview": report["metrics"]["recall_missing_lane"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
