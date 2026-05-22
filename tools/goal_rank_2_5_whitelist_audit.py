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

DEFAULT_INPUT_CSV = PROJECT_ROOT / "data" / "goal_search" / "hard_pairs" / "rank_2_5_train_whitelist.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_buckets.csv"
DEFAULT_CLEAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_clean.csv"
DEFAULT_REVIEW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_review.csv"
DEFAULT_PSEUDO_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_whitelist_audit_pseudo.csv"

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

AMBIGUOUS_DIAGNOSES = {
    "conflicting_query_terms_or_label_ambiguous",
    "subtype_diff_not_in_query_or_label_specific",
    "low_discrimination_same_family_book",
}

REVIEW_DIAGNOSES = {
    "parameter_signal_not_strong_enough",
    "existing_features_not_weighted_enough",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _bucket_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _read_csv(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
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
    return value.rstrip("0").rstrip(".").lstrip("0") or "0"


def _numbers_from_text(value: str) -> set[str]:
    return {_normalize_number(match.group(0)) for match in NUMBER_RE.finditer(_clean(value))}


def _numbers_from_pipe(value: str) -> set[str]:
    numbers: set[str] = set()
    for item in _clean(value).split("|"):
        numbers.update(_numbers_from_text(item))
    return numbers


def _query_numbers(row: dict[str, Any]) -> set[str]:
    return _numbers_from_text(_clean(row.get("query")) + " " + _clean(row.get("query_specs")))


def _candidate_numbers(row: dict[str, Any], prefix: str) -> set[str]:
    numbers = _numbers_from_pipe(row.get(f"{prefix}_numbers", ""))
    if numbers:
        return numbers
    return _numbers_from_text(_clean(row.get(f"{prefix}_name")))


def _number_diff_support(row: dict[str, Any]) -> tuple[bool, str, str]:
    query_numbers = _query_numbers(row)
    positive_numbers = _candidate_numbers(row, "positive")
    top_numbers = _candidate_numbers(row, "top")
    diff_numbers = positive_numbers.symmetric_difference(top_numbers)
    if not diff_numbers:
        return False, "", "|".join(sorted(query_numbers))
    supported = bool(diff_numbers & query_numbers)
    return supported, "|".join(sorted(diff_numbers)), "|".join(sorted(query_numbers))


def _audit_row(row: dict[str, Any]) -> dict[str, Any]:
    positive_hits = _int(row.get("query_hits_positive_count"))
    top_hits = _int(row.get("query_hits_top_count"))
    positive_signal_count = _int(row.get("positive_signal_feature_count"))
    top_surface_count = _int(row.get("top_surface_feature_count"))
    rank = _int(row.get("gated_positive_rank"))
    primary = _clean(row.get("primary_category"))
    diagnosis = _clean(row.get("diagnosis"))
    candidate_numbers_differ = _int(row.get("candidate_numbers_differ")) > 0
    query_number_diff_supported, diff_numbers, query_numbers = _number_diff_support(row)

    flags: list[str] = []
    if top_hits > positive_hits:
        flags.append("query_surface_favors_current_top")
    if top_hits and positive_hits:
        flags.append("conflicting_query_hits")
    if candidate_numbers_differ and not query_number_diff_supported:
        flags.append("candidate_number_diff_not_in_query")
    if primary == "param_tier_near_miss" and candidate_numbers_differ and not query_number_diff_supported and positive_hits == 0:
        flags.append("param_tier_label_too_specific")
    if positive_hits == 0 and positive_signal_count == 0 and not query_number_diff_supported:
        flags.append("no_query_supported_positive_signal")
    if rank >= 4:
        flags.append("positive_rank_ge_4")
    if primary == "same_family_cross_book_sorting" and positive_hits == 0 and not query_number_diff_supported:
        flags.append("cross_book_without_direct_query_evidence")
    if diagnosis in AMBIGUOUS_DIAGNOSES:
        flags.append(f"ambiguous_diagnosis:{diagnosis}")
    if diagnosis in REVIEW_DIAGNOSES:
        flags.append(f"review_diagnosis:{diagnosis}")

    hard_pseudo_flags = {
        "query_surface_favors_current_top",
        "param_tier_label_too_specific",
        "no_query_supported_positive_signal",
        "ambiguous_diagnosis:conflicting_query_terms_or_label_ambiguous",
        "ambiguous_diagnosis:subtype_diff_not_in_query_or_label_specific",
        "ambiguous_diagnosis:low_discrimination_same_family_book",
    }
    review_flags = {
        "candidate_number_diff_not_in_query",
        "positive_rank_ge_4",
        "cross_book_without_direct_query_evidence",
        "conflicting_query_hits",
    }

    if any(flag in hard_pseudo_flags for flag in flags):
        decision = "pseudo_whitelist"
        risk = "high"
        reason = "query_or_label_evidence_does_not_cleanly_support_positive"
    elif any(flag in review_flags or flag.startswith("review_diagnosis:") for flag in flags):
        decision = "review_before_training"
        risk = "medium"
        reason = "needs_manual_or_bucket_review_before_training"
    else:
        decision = "clean_candidate"
        risk = "low"
        reason = "positive_has_query_or_feature_evidence_without_major_audit_flags"

    return {
        "whitelist_audit_decision": decision,
        "whitelist_audit_risk": risk,
        "whitelist_audit_reason": reason,
        "whitelist_audit_flags": "|".join(flags),
        "query_number_diff_supported": int(query_number_diff_supported),
        "candidate_diff_numbers": diff_numbers,
        "query_numbers": query_numbers,
        "source_file_single_source_note": "",
        **row,
    }


def _audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audited = [_audit_row(row) for row in rows]
    source_counts = Counter(_bucket_key(row.get("source_file")) for row in audited)
    if len(source_counts) == 1 and audited:
        source = next(iter(source_counts))
        note = f"all_whitelist_rows_from_source:{source}"
        for row in audited:
            row["source_file_single_source_note"] = note
    return audited


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(rows: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in (
            "whitelist_audit_decision",
            "whitelist_audit_risk",
            "primary_category",
            "query_family",
            "province",
            "source_file",
            "gated_positive_rank",
            "audit_reason",
            "diagnosis",
            "query_number_diff_supported",
            "source_file_single_source_note",
        ):
            counters[dimension][_bucket_key(row.get(dimension))] += 1
        for flag in _clean(row.get("whitelist_audit_flags")).split("|"):
            if flag:
                counters["audit_flag"][flag] += 1
        decision = _bucket_key(row.get("whitelist_audit_decision"))
        counters[f"decision_family:{decision}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"decision_province:{decision}"][_bucket_key(row.get("province"))] += 1
        counters[f"decision_primary_category:{decision}"][_bucket_key(row.get("primary_category"))] += 1

    total = len(rows)
    bucket_rows: list[dict[str, Any]] = []
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})

    decisions = counters["whitelist_audit_decision"]
    summary = {
        "rows": total,
        "clean_candidate_count": decisions.get("clean_candidate", 0),
        "clean_candidate_rate": _rate(decisions.get("clean_candidate", 0), total),
        "review_before_training_count": decisions.get("review_before_training", 0),
        "review_before_training_rate": _rate(decisions.get("review_before_training", 0), total),
        "pseudo_whitelist_count": decisions.get("pseudo_whitelist", 0),
        "pseudo_whitelist_rate": _rate(decisions.get("pseudo_whitelist", 0), total),
        "single_source_count": len(counters["source_file"]),
        "by_decision": _counter_items(counters["whitelist_audit_decision"], total, top_limit),
        "by_risk": _counter_items(counters["whitelist_audit_risk"], total, top_limit),
        "by_primary_category": _counter_items(counters["primary_category"], total, top_limit),
        "by_query_family": _counter_items(counters["query_family"], total, top_limit),
        "by_province": _counter_items(counters["province"], total, top_limit),
        "by_source_file": _counter_items(counters["source_file"], total, top_limit),
        "by_rank": _counter_items(counters["gated_positive_rank"], total, top_limit),
        "by_audit_flag": _counter_items(counters["audit_flag"], total, top_limit),
        "by_query_number_diff_supported": _counter_items(counters["query_number_diff_supported"], total, top_limit),
        "by_decision_family": {
            decision: _counter_items(counters[f"decision_family:{decision}"], total, top_limit)
            for decision in ("clean_candidate", "review_before_training", "pseudo_whitelist")
        },
        "by_decision_primary_category": {
            decision: _counter_items(counters[f"decision_primary_category:{decision}"], total, top_limit)
            for decision in ("clean_candidate", "review_before_training", "pseudo_whitelist")
        },
        "by_decision_province": {
            decision: _counter_items(counters[f"decision_province:{decision}"], total, top_limit)
            for decision in ("clean_candidate", "review_before_training", "pseudo_whitelist")
        },
    }
    return summary, bucket_rows


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
    selected = [row for row in rows if row["whitelist_audit_decision"] == decision]
    selected.sort(
        key=lambda row: (
            _clean(row.get("whitelist_audit_risk")),
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
            row.get("province", ""),
            row.get("gated_positive_rank", ""),
            row.get("query", ""),
            row.get("positive_id", ""),
            row.get("top_id", ""),
            row.get("whitelist_audit_flags", ""),
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Rank 2-5 Whitelist Audit",
        "",
        "Stage 4.6 audits only the 41 rank2-5 whitelist rows to find pseudo-whitelist cases before any hard-pair training. It does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["clean_candidate_count", summary["clean_candidate_count"]],
                ["clean_candidate_rate", summary["clean_candidate_rate"]],
                ["review_before_training_count", summary["review_before_training_count"]],
                ["review_before_training_rate", summary["review_before_training_rate"]],
                ["pseudo_whitelist_count", summary["pseudo_whitelist_count"]],
                ["pseudo_whitelist_rate", summary["pseudo_whitelist_rate"]],
                ["single_source_count", summary["single_source_count"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Decision",
        "",
        _md_table(_counter_table(summary["by_decision"])),
        "",
        "## Audit Flags",
        "",
        _md_table(_counter_table(summary["by_audit_flag"], limit=20)),
        "",
        "## Family",
        "",
        _md_table(_counter_table(summary["by_query_family"], limit=20)),
        "",
        "## Province",
        "",
        _md_table(_counter_table(summary["by_province"], limit=20)),
        "",
        "## Source",
        "",
        _md_table(_counter_table(summary["by_source_file"])),
        "",
        "## Clean Candidate Mix",
        "",
        _md_table(_counter_table(summary["by_decision_primary_category"]["clean_candidate"], limit=20)),
        "",
        "## Review Mix",
        "",
        _md_table(_counter_table(summary["by_decision_primary_category"]["review_before_training"], limit=20)),
        "",
        "## Pseudo Mix",
        "",
        _md_table(_counter_table(summary["by_decision_primary_category"]["pseudo_whitelist"], limit=20)),
        "",
        "## Samples",
        "",
        "Clean candidate:",
        "",
        _md_table(
            [["pair_id", "primary", "family", "province", "rank", "query", "positive_id", "top_id", "flags"]]
            + _sample_rows(rows, "clean_candidate", sample_limit)
        ),
        "",
        "Review before training:",
        "",
        _md_table(
            [["pair_id", "primary", "family", "province", "rank", "query", "positive_id", "top_id", "flags"]]
            + _sample_rows(rows, "review_before_training", sample_limit)
        ),
        "",
        "Pseudo whitelist:",
        "",
        _md_table(
            [["pair_id", "primary", "family", "province", "rank", "query", "positive_id", "top_id", "flags"]]
            + _sample_rows(rows, "pseudo_whitelist", sample_limit)
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit rank2-5 train whitelist for pseudo-whitelist cases without training")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--clean-csv", default=str(DEFAULT_CLEAN_CSV))
    parser.add_argument("--review-csv", default=str(DEFAULT_REVIEW_CSV))
    parser.add_argument("--pseudo-csv", default=str(DEFAULT_PSEUDO_CSV))
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows, input_fields = _read_csv(Path(args.input_csv))
    audited = _audit_rows(source_rows)
    summary, bucket_rows = _summarize(audited, args.top_limit)

    leading_fields = [
        "whitelist_audit_decision",
        "whitelist_audit_risk",
        "whitelist_audit_reason",
        "whitelist_audit_flags",
        "query_number_diff_supported",
        "candidate_diff_numbers",
        "query_numbers",
        "source_file_single_source_note",
    ]
    fieldnames = leading_fields + [field for field in input_fields if field not in leading_fields]

    clean_rows = [row for row in audited if row["whitelist_audit_decision"] == "clean_candidate"]
    review_rows = [row for row in audited if row["whitelist_audit_decision"] == "review_before_training"]
    pseudo_rows = [row for row in audited if row["whitelist_audit_decision"] == "pseudo_whitelist"]

    _write_csv(Path(args.details_csv), audited, fieldnames)
    _write_csv(Path(args.clean_csv), clean_rows, fieldnames)
    _write_csv(Path(args.review_csv), review_rows, fieldnames)
    _write_csv(Path(args.pseudo_csv), pseudo_rows, fieldnames)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    artifacts = {
        "details_csv": args.details_csv,
        "buckets_csv": args.buckets_csv,
        "clean_csv": args.clean_csv,
        "review_csv": args.review_csv,
        "pseudo_csv": args.pseudo_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.6 rank2-5 whitelist pseudo audit",
        "read_only_input": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input_csv": args.input_csv,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, audited, args.sample_limit)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "rows": summary["rows"],
                    "clean_candidate_count": summary["clean_candidate_count"],
                    "review_before_training_count": summary["review_before_training_count"],
                    "pseudo_whitelist_count": summary["pseudo_whitelist_count"],
                    "single_source_count": summary["single_source_count"],
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
