from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_REVIEW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_review.csv"
DEFAULT_ORIGINAL_CLEAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_clean.csv"
DEFAULT_ORIGINAL_PSEUDO_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_pseudo.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_buckets.csv"
DEFAULT_UPGRADE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_upgrade_clean.csv"
DEFAULT_DOWNGRADE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_downgrade_pseudo.csv"
DEFAULT_FINAL_CLEAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_final_clean.csv"
DEFAULT_FINAL_PSEUDO_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_review_resolution_final_pseudo.csv"

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _bucket_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _read_csv(path: Path, *, required: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"required input CSV not found: {path}")
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _normalize_number(value: str) -> str:
    if "." not in value:
        return value.lstrip("0") or "0"
    value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def _numbers_from_text(value: str) -> set[str]:
    return {_normalize_number(match.group(0)) for match in NUMBER_RE.finditer(_clean(value))}


def _numbers_from_pipe(value: str) -> set[str]:
    numbers: set[str] = set()
    for item in _clean(value).split("|"):
        numbers.update(_numbers_from_text(item))
    return numbers


def _candidate_numbers(row: dict[str, Any], prefix: str) -> set[str]:
    numbers = _numbers_from_pipe(row.get(f"{prefix}_numbers", ""))
    if numbers:
        return numbers
    return _numbers_from_text(_clean(row.get(f"{prefix}_name")))


def _query_numbers(row: dict[str, Any]) -> set[str]:
    return _numbers_from_text(_clean(row.get("query")) + " " + _clean(row.get("query_specs")))


def _flag_set(row: dict[str, Any]) -> set[str]:
    return {item for item in _clean(row.get("whitelist_audit_flags")).split("|") if item}


def _join(items: set[str] | list[str]) -> str:
    return "|".join(sorted(items))


def _numeric_support(row: dict[str, Any]) -> dict[str, Any]:
    query_numbers = _query_numbers(row)
    positive_numbers = _candidate_numbers(row, "positive")
    top_numbers = _candidate_numbers(row, "top")
    positive_unique = positive_numbers - top_numbers
    top_unique = top_numbers - positive_numbers
    return {
        "query_numbers_4_7": _join(query_numbers),
        "positive_unique_numbers": _join(positive_unique),
        "top_unique_numbers": _join(top_unique),
        "positive_unique_number_supported": int(bool(positive_unique & query_numbers)),
        "top_unique_number_supported": int(bool(top_unique & query_numbers)),
        "numbers_same_or_no_unique_diff": int(not positive_unique and not top_unique),
    }


def _resolve_row(row: dict[str, Any]) -> dict[str, Any]:
    flags = _flag_set(row)
    numeric = _numeric_support(row)
    rank = _int(row.get("gated_positive_rank"))
    positive_hits = _int(row.get("query_hits_positive_count"))
    top_hits = _int(row.get("query_hits_top_count"))
    positive_signal_count = _int(row.get("positive_signal_feature_count"))
    top_surface_count = _int(row.get("top_surface_feature_count"))
    delta = _float(row.get("current_score_delta_positive_minus_top"))
    primary = _clean(row.get("primary_category"))

    positive_number_supported = numeric["positive_unique_number_supported"] > 0
    top_number_supported = numeric["top_unique_number_supported"] > 0
    direct_positive_terms = positive_hits > 0 and positive_hits > top_hits
    direct_top_terms = top_hits > positive_hits
    score_positive = delta > 1e-6

    resolution_flags: list[str] = []
    if positive_number_supported:
        resolution_flags.append("positive_unique_number_supported_by_query")
    if top_number_supported:
        resolution_flags.append("top_unique_number_supported_by_query")
    if direct_positive_terms:
        resolution_flags.append("query_terms_favor_positive")
    if direct_top_terms:
        resolution_flags.append("query_terms_favor_top")
    if score_positive:
        resolution_flags.append("current_score_already_favors_positive")
    if rank >= 4:
        resolution_flags.append("rank_ge_4")
    if "cross_book_without_direct_query_evidence" in flags:
        resolution_flags.append("cross_book_review_risk")
    if "candidate_number_diff_not_in_query" in flags:
        resolution_flags.append("candidate_number_diff_not_in_query")

    downgrade_reasons: list[str] = []
    upgrade_reasons: list[str] = []

    if top_number_supported and not positive_number_supported:
        downgrade_reasons.append("query_numeric_evidence_supports_current_top")
    if direct_top_terms:
        downgrade_reasons.append("query_surface_terms_support_current_top")
    if "query_surface_favors_current_top" in flags:
        downgrade_reasons.append("prior_audit_surface_favors_current_top")
    if rank >= 4 and not positive_number_supported and not direct_positive_terms:
        downgrade_reasons.append("rank_deep_without_direct_positive_evidence")
    if "cross_book_without_direct_query_evidence" in flags and not positive_number_supported and not direct_positive_terms:
        downgrade_reasons.append("cross_book_without_direct_positive_evidence")
    if "candidate_number_diff_not_in_query" in flags and not positive_number_supported and not direct_positive_terms and not score_positive:
        downgrade_reasons.append("candidate_numeric_difference_unbacked_by_query")

    if direct_positive_terms and not direct_top_terms and (rank <= 3 or score_positive):
        upgrade_reasons.append("query_terms_directly_support_positive")
    if primary == "param_tier_near_miss" and positive_number_supported and not top_number_supported and rank <= 4:
        upgrade_reasons.append("explicit_query_number_supports_positive_param_tier")
    if score_positive and rank <= 3 and positive_signal_count > 0 and not direct_top_terms:
        upgrade_reasons.append("existing_score_and_features_already_support_positive")

    if downgrade_reasons:
        decision = "downgrade_pseudo"
        reason = downgrade_reasons[0]
    elif upgrade_reasons:
        decision = "upgrade_clean"
        reason = upgrade_reasons[0]
    else:
        decision = "downgrade_pseudo"
        reason = "insufficient_evidence_to_upgrade_review_row"

    return {
        "review_resolution_decision": decision,
        "review_resolution_reason": reason,
        "review_resolution_flags": "|".join(resolution_flags),
        **numeric,
        **row,
    }


def _resolve_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_resolve_row(row) for row in rows]


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(
    resolved: list[dict[str, Any]],
    original_clean: list[dict[str, Any]],
    original_pseudo: list[dict[str, Any]],
    top_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in resolved:
        for dimension in (
            "review_resolution_decision",
            "review_resolution_reason",
            "primary_category",
            "query_family",
            "province",
            "source_file",
            "gated_positive_rank",
            "whitelist_audit_flags",
            "positive_unique_number_supported",
            "top_unique_number_supported",
        ):
            counters[dimension][_bucket_key(row.get(dimension))] += 1
        for flag in _clean(row.get("review_resolution_flags")).split("|"):
            if flag:
                counters["resolution_flag"][flag] += 1
        decision = _bucket_key(row.get("review_resolution_decision"))
        counters[f"decision_family:{decision}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"decision_primary_category:{decision}"][_bucket_key(row.get("primary_category"))] += 1

    total = len(resolved)
    bucket_rows: list[dict[str, Any]] = []
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})

    decisions = counters["review_resolution_decision"]
    upgrade_count = decisions.get("upgrade_clean", 0)
    downgrade_count = decisions.get("downgrade_pseudo", 0)
    original_clean_count = len(original_clean)
    original_pseudo_count = len(original_pseudo)
    original_whitelist_count = original_clean_count + original_pseudo_count + total
    final_clean_count = original_clean_count + upgrade_count
    final_pseudo_count = original_pseudo_count + downgrade_count
    return (
        {
            "review_rows": total,
            "upgrade_clean_count": upgrade_count,
            "upgrade_clean_rate": _rate(upgrade_count, total),
            "downgrade_pseudo_count": downgrade_count,
            "downgrade_pseudo_rate": _rate(downgrade_count, total),
            "original_clean_count": original_clean_count,
            "original_pseudo_count": original_pseudo_count,
            "original_whitelist_count": original_whitelist_count,
            "final_clean_count": final_clean_count,
            "final_clean_rate_of_original_whitelist": _rate(final_clean_count, original_whitelist_count),
            "final_pseudo_count": final_pseudo_count,
            "final_pseudo_rate_of_original_whitelist": _rate(final_pseudo_count, original_whitelist_count),
            "training_pause_recommended": final_clean_count < 20,
            "by_decision": _counter_items(counters["review_resolution_decision"], total, top_limit),
            "by_reason": _counter_items(counters["review_resolution_reason"], total, top_limit),
            "by_primary_category": _counter_items(counters["primary_category"], total, top_limit),
            "by_query_family": _counter_items(counters["query_family"], total, top_limit),
            "by_rank": _counter_items(counters["gated_positive_rank"], total, top_limit),
            "by_resolution_flag": _counter_items(counters["resolution_flag"], total, top_limit),
            "by_positive_number_supported": _counter_items(counters["positive_unique_number_supported"], total, top_limit),
            "by_top_number_supported": _counter_items(counters["top_unique_number_supported"], total, top_limit),
            "by_decision_family": {
                decision: _counter_items(counters[f"decision_family:{decision}"], total, top_limit)
                for decision in ("upgrade_clean", "downgrade_pseudo")
            },
            "by_decision_primary_category": {
                decision: _counter_items(counters[f"decision_primary_category:{decision}"], total, top_limit)
                for decision in ("upgrade_clean", "downgrade_pseudo")
            },
        },
        bucket_rows,
    )


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]], limit: int | None = None) -> list[list[object]]:
    selected = items if limit is None else items[:limit]
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in selected]]


def _sample_rows(rows: list[dict[str, Any]], decision: str, limit: int) -> list[list[object]]:
    selected = [row for row in rows if row["review_resolution_decision"] == decision]
    selected.sort(
        key=lambda row: (
            _clean(row.get("review_resolution_reason")),
            _clean(row.get("primary_category")),
            _clean(row.get("query_family")),
            _int(row.get("gated_positive_rank")),
            _clean(row.get("pair_id")),
        )
    )
    return [
        [
            row.get("pair_id", ""),
            row.get("primary_category", ""),
            row.get("query_family", ""),
            row.get("gated_positive_rank", ""),
            row.get("query", ""),
            row.get("positive_id", ""),
            row.get("top_id", ""),
            row.get("review_resolution_reason", ""),
            row.get("review_resolution_flags", ""),
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Rank 2-5 Review Resolution",
        "",
        "Stage 4.7 resolves only the 19 review rows from the whitelist audit into upgrade-clean or downgrade-pseudo. It does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["review_rows", summary["review_rows"]],
                ["upgrade_clean_count", summary["upgrade_clean_count"]],
                ["upgrade_clean_rate", summary["upgrade_clean_rate"]],
                ["downgrade_pseudo_count", summary["downgrade_pseudo_count"]],
                ["downgrade_pseudo_rate", summary["downgrade_pseudo_rate"]],
                ["original_clean_count", summary["original_clean_count"]],
                ["original_pseudo_count", summary["original_pseudo_count"]],
                ["original_whitelist_count", summary["original_whitelist_count"]],
                ["final_clean_count", summary["final_clean_count"]],
                ["final_clean_rate_of_original_whitelist", summary["final_clean_rate_of_original_whitelist"]],
                ["final_pseudo_count", summary["final_pseudo_count"]],
                ["final_pseudo_rate_of_original_whitelist", summary["final_pseudo_rate_of_original_whitelist"]],
                ["training_pause_recommended", summary["training_pause_recommended"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Decision",
        "",
        _md_table(_counter_table(summary["by_decision"])),
        "",
        "## Reasons",
        "",
        _md_table(_counter_table(summary["by_reason"], limit=20)),
        "",
        "## Resolution Flags",
        "",
        _md_table(_counter_table(summary["by_resolution_flag"], limit=20)),
        "",
        "## Upgrade Clean Mix",
        "",
        _md_table(_counter_table(summary["by_decision_primary_category"]["upgrade_clean"], limit=20)),
        "",
        "## Downgrade Pseudo Mix",
        "",
        _md_table(_counter_table(summary["by_decision_primary_category"]["downgrade_pseudo"], limit=20)),
        "",
        "## Samples",
        "",
        "Upgrade clean:",
        "",
        _md_table(
            [["pair_id", "primary", "family", "rank", "query", "positive_id", "top_id", "reason", "flags"]]
            + _sample_rows(rows, "upgrade_clean", sample_limit)
        ),
        "",
        "Downgrade pseudo:",
        "",
        _md_table(
            [["pair_id", "primary", "family", "rank", "query", "positive_id", "top_id", "reason", "flags"]]
            + _sample_rows(rows, "downgrade_pseudo", sample_limit)
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve rank2-5 whitelist review rows into upgrade-clean/downgrade-pseudo")
    parser.add_argument("--review-csv", default=str(DEFAULT_REVIEW_CSV))
    parser.add_argument("--original-clean-csv", default=str(DEFAULT_ORIGINAL_CLEAN_CSV))
    parser.add_argument("--original-pseudo-csv", default=str(DEFAULT_ORIGINAL_PSEUDO_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--upgrade-csv", default=str(DEFAULT_UPGRADE_CSV))
    parser.add_argument("--downgrade-csv", default=str(DEFAULT_DOWNGRADE_CSV))
    parser.add_argument("--final-clean-csv", default=str(DEFAULT_FINAL_CLEAN_CSV))
    parser.add_argument("--final-pseudo-csv", default=str(DEFAULT_FINAL_PSEUDO_CSV))
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()

    started = time.perf_counter()
    review_rows, review_fields = _read_csv(Path(args.review_csv), required=True)
    original_clean, clean_fields = _read_csv(Path(args.original_clean_csv), required=True)
    original_pseudo, pseudo_fields = _read_csv(Path(args.original_pseudo_csv), required=True)
    resolved = _resolve_rows(review_rows)
    summary, bucket_rows = _summarize(resolved, original_clean, original_pseudo, args.top_limit)

    leading_fields = [
        "review_resolution_decision",
        "review_resolution_reason",
        "review_resolution_flags",
        "query_numbers_4_7",
        "positive_unique_numbers",
        "top_unique_numbers",
        "positive_unique_number_supported",
        "top_unique_number_supported",
        "numbers_same_or_no_unique_diff",
    ]
    fieldnames = leading_fields + [field for field in review_fields if field not in leading_fields]

    upgrade_rows = [row for row in resolved if row["review_resolution_decision"] == "upgrade_clean"]
    downgrade_rows = [row for row in resolved if row["review_resolution_decision"] == "downgrade_pseudo"]
    final_clean = original_clean + upgrade_rows
    final_pseudo = original_pseudo + downgrade_rows
    final_clean_fields = list(dict.fromkeys(clean_fields + fieldnames))
    final_pseudo_fields = list(dict.fromkeys(pseudo_fields + fieldnames))

    _write_csv(Path(args.details_csv), resolved, fieldnames)
    _write_csv(Path(args.upgrade_csv), upgrade_rows, fieldnames)
    _write_csv(Path(args.downgrade_csv), downgrade_rows, fieldnames)
    _write_csv(Path(args.final_clean_csv), final_clean, final_clean_fields)
    _write_csv(Path(args.final_pseudo_csv), final_pseudo, final_pseudo_fields)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    artifacts = {
        "details_csv": args.details_csv,
        "buckets_csv": args.buckets_csv,
        "upgrade_clean_csv": args.upgrade_csv,
        "downgrade_pseudo_csv": args.downgrade_csv,
        "final_clean_csv": args.final_clean_csv,
        "final_pseudo_csv": args.final_pseudo_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.7 rank2-5 review resolution",
        "read_only_input": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "review_csv": args.review_csv,
        "original_clean_csv": args.original_clean_csv,
        "original_pseudo_csv": args.original_pseudo_csv,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, resolved, args.sample_limit)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "review_rows": summary["review_rows"],
                    "upgrade_clean_count": summary["upgrade_clean_count"],
                    "downgrade_pseudo_count": summary["downgrade_pseudo_count"],
                    "final_clean_count": summary["final_clean_count"],
                    "final_pseudo_count": summary["final_pseudo_count"],
                    "training_pause_recommended": summary["training_pause_recommended"],
                },
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
