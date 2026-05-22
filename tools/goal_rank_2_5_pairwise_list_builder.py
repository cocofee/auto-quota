from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import goal_same_family_book_rank2_pairwise_audit as pairwise_audit
import goal_same_family_book_rank2_review as pairwise_review

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REVIEW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_details.csv"
DEFAULT_GAP_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_wrong_rank.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "goal_search" / "hard_pairs"
DEFAULT_AUDIT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_pairwise_audit_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_pairwise_audit_buckets.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_pairwise_list_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_rank_2_5_pairwise_list_summary.md"

LIST_OUTPUTS = {
    "train_whitelist": "rank_2_5_train_whitelist.csv",
    "review_graylist": "rank_2_5_review_graylist.csv",
    "exclude_or_downweight": "rank_2_5_exclude_or_downweight.csv",
}

CLEAN_TRAIN_CATEGORIES = {
    "same_family_book_sorting",
    "same_family_cross_book_sorting",
    "param_tier_near_miss",
}

NOISY_EXCLUDE_CATEGORIES = {
    "oss_label_too_specific_or_ambiguous",
    "query_family_empty_unclear_or_non_install",
}

GRAY_REVIEW_CATEGORIES = {
    "query_family_empty_but_clear",
    "same_book_family_or_subtype_mismatch",
    "other_near_miss",
}

RECOMMENDATION_TO_LIST = {
    "include_as_hard_pair_candidate": (
        "train_whitelist",
        "eligible_for_future_hard_pair_training",
    ),
    "manual_label_review_before_training": (
        "review_graylist",
        "manual_label_review_required_before_training",
    ),
    "do_not_train_first_fix_or_audit_gate": (
        "review_graylist",
        "fix_or_audit_safety_gate_before_training",
    ),
    "keep_for_feature_research_not_training": (
        "review_graylist",
        "feature_research_before_training",
    ),
    "exclude_or_downweight_from_hard_pair": (
        "exclude_or_downweight",
        "exclude_from_hard_pair_training_or_downweight_for_diagnostics",
    ),
}

LEADING_FIELDS = [
    "pair_id",
    "list_name",
    "use_policy",
    "audit_verdict",
    "training_recommendation",
    "audit_reason",
    "pairwise_audit_verdict",
    "pairwise_training_recommendation",
    "pairwise_audit_reason",
]

PAIRWISE_FEATURE_FIELDS = [
    "query_hits_positive_count",
    "query_hits_top_count",
    "positive_only_term_count",
    "top_only_term_count",
    "positive_signal_feature_count",
    "top_surface_feature_count",
    "query_has_explicit_spec",
    "candidate_numbers_differ",
    "raw_ltr_was_positive",
    "score_gap_bucket",
    "positive_signal_features_strong",
    "top_surface_features",
]


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


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, sum(counter.values()))} for key, count in counter.most_common(limit)]


def _make_pair_id(row: dict[str, Any]) -> str:
    split = _clean(row.get("split")) or "unknown_split"
    group_id = _clean(row.get("group_id")) or f"sample:{_clean(row.get('sample_id')) or 'unknown'}"
    rank = _clean(row.get("gated_positive_rank")) or "unknown_rank"
    top_id = _clean(row.get("top_id")) or "unknown_top"
    positive_id = _clean(row.get("positive_id")) or "unknown_positive"
    return f"{split}:{group_id}:rank{rank}:{top_id}>{positive_id}"


def _load_near_miss_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_csv(path)
    return [row for row in rows if _clean(row.get("rank_bucket")) in {"", "rank_2_5"}]


def _split_to_group_ids(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    split_to_group_ids: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split = _clean(row.get("split"))
        group_id = _clean(row.get("group_id"))
        if split and group_id:
            split_to_group_ids[split].add(group_id)
    return split_to_group_ids


def _base_pairwise_row(
    *,
    review_row: dict[str, Any],
    gap_row: dict[str, Any],
    top_row: dict[str, Any] | None,
    positive_row: dict[str, Any] | None,
) -> dict[str, Any]:
    diagnosis = pairwise_review._classify(
        review_row=review_row,
        gap_row=gap_row,
        top_row=top_row,
        positive_row=positive_row,
    )
    feature_deltas = {
        f"delta_{feature}": round(pairwise_review._feature_delta(top_row, positive_row, feature), 6)
        for feature in pairwise_review.SCORE_FEATURES
    }
    row = {
        "primary_category": _clean(review_row.get("primary_category")),
        "secondary_tags": _clean(review_row.get("secondary_tags")),
        "rank_bucket": _clean(review_row.get("rank_bucket")),
        "gated_positive_rank": _clean(review_row.get("gated_positive_rank")),
        "split": _clean(review_row.get("split")),
        "group_id": _clean(review_row.get("group_id")),
        "sample_id": _clean(review_row.get("sample_id")),
        "source_file": _clean(review_row.get("source_file")),
        "project_name": _clean(review_row.get("project_name")),
        "province": _clean(review_row.get("province")),
        "query_family": _clean(review_row.get("query_family")),
        "audit_family_hint": _clean(review_row.get("audit_family_hint")),
        "expected_books": _clean(review_row.get("expected_books")),
        "expected_ids": _clean(review_row.get("expected_ids")),
        "expected_names": _clean(review_row.get("expected_names")),
        "query": _clean(review_row.get("query")),
        "top_id": _clean(review_row.get("gated_top_id")),
        "top_name": pairwise_review._name(top_row) or _clean(review_row.get("gated_top")),
        "top_chapter": pairwise_review._chapter(top_row),
        "top_reasons": _clean(top_row.get("reasons")) if top_row else "",
        "positive_id": pairwise_review._qid(positive_row) or _clean(review_row.get("positive_id")),
        "positive_name": pairwise_review._name(positive_row) or _clean(review_row.get("positive_name")),
        "positive_chapter": pairwise_review._chapter(positive_row),
        "positive_reasons": _clean(positive_row.get("reasons")) if positive_row else "",
        "gate_reason": _clean(review_row.get("gate_reason")),
        "score_margin": _clean(review_row.get("score_margin")),
        "diagnosis": diagnosis["diagnosis"],
        "diagnosis_4_5": diagnosis["diagnosis"],
        **diagnosis,
        **feature_deltas,
    }
    return row


def _finalize_recommendation(
    row: dict[str, Any],
    pairwise_verdict: str,
    pairwise_recommendation: str,
    pairwise_reason: str,
) -> tuple[str, str, str]:
    primary = _clean(row.get("primary_category"))
    if primary in NOISY_EXCLUDE_CATEGORIES:
        return (
            "not_learnable_noisy_primary_category",
            "exclude_or_downweight_from_hard_pair",
            f"primary_category={primary}_is_not_safe_for_hard_pair_training",
        )

    if (
        pairwise_recommendation == "include_as_hard_pair_candidate"
        and not _clean(row.get("query_family"))
        and not _clean(row.get("audit_family_hint"))
    ):
        return (
            "candidate_signal_but_missing_object_family",
            "manual_label_review_before_training",
            "pairwise_signal_present_but_object_family_is_empty",
        )

    if pairwise_recommendation == "include_as_hard_pair_candidate" and primary not in CLEAN_TRAIN_CATEGORIES:
        return (
            "candidate_signal_but_primary_category_needs_review",
            "manual_label_review_before_training",
            f"pairwise_signal_present_but_primary_category={primary}_is_not_clean_train_bucket",
        )

    if primary in GRAY_REVIEW_CATEGORIES and pairwise_recommendation != "exclude_or_downweight_from_hard_pair":
        return (
            "potentially_learnable_after_bucket_review",
            "manual_label_review_before_training",
            f"primary_category={primary}_requires_review_before_training",
        )

    return pairwise_verdict, pairwise_recommendation, pairwise_reason


def _list_for_recommendation(recommendation: str) -> tuple[str, str]:
    return RECOMMENDATION_TO_LIST.get(
        recommendation,
        ("review_graylist", "unknown_recommendation_requires_review"),
    )


def _audit_rows(
    review_rows: list[dict[str, Any]],
    gaps: dict[tuple[str, str], dict[str, Any]],
    groups: dict[tuple[str, str], Any],
) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for review_row in review_rows:
        split = _clean(review_row.get("split"))
        group_id = _clean(review_row.get("group_id"))
        group = groups.get((split, group_id))
        top_row = group.by_quota_id.get(_clean(review_row.get("gated_top_id"))) if group else None
        positive_row = pairwise_review._best_positive(group, _clean(review_row.get("positive_id")))
        gap_row = gaps.get((split, group_id), {})
        row = _base_pairwise_row(
            review_row=review_row,
            gap_row=gap_row,
            top_row=top_row,
            positive_row=positive_row,
        )
        flags = pairwise_audit._evidence_flags(row)
        pairwise_verdict, pairwise_recommendation, pairwise_reason = pairwise_audit._classify_pair(row, flags)
        verdict, recommendation, reason = _finalize_recommendation(
            row,
            pairwise_verdict,
            pairwise_recommendation,
            pairwise_reason,
        )
        list_name, use_policy = _list_for_recommendation(recommendation)
        audited.append(
            {
                "pair_id": _make_pair_id(row),
                "list_name": list_name,
                "use_policy": use_policy,
                "audit_verdict": verdict,
                "training_recommendation": recommendation,
                "audit_reason": reason,
                "pairwise_audit_verdict": pairwise_verdict,
                "pairwise_training_recommendation": pairwise_recommendation,
                "pairwise_audit_reason": pairwise_reason,
                **row,
                **flags,
            }
        )
    return audited


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(rows: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    pair_ids = Counter(_clean(row.get("pair_id")) for row in rows)
    for row in rows:
        for dimension in (
            "list_name",
            "use_policy",
            "training_recommendation",
            "audit_verdict",
            "pairwise_training_recommendation",
            "pairwise_audit_verdict",
            "primary_category",
            "diagnosis_4_5",
            "query_family",
            "audit_family_hint",
            "province",
            "expected_books",
            "gated_positive_rank",
            "score_gap_bucket",
            "raw_ltr_was_positive",
        ):
            counters[dimension][_bucket_key(row.get(dimension))] += 1
        counters[f"list_primary_category:{_bucket_key(row.get('list_name'))}"][_bucket_key(row.get("primary_category"))] += 1
        counters[f"list_family:{_bucket_key(row.get('list_name'))}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"list_rank:{_bucket_key(row.get('list_name'))}"][_bucket_key(row.get("gated_positive_rank"))] += 1

    total = len(rows)
    bucket_rows: list[dict[str, Any]] = []
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})

    list_counts = counters["list_name"]
    summary = {
        "rows": total,
        "unique_pair_ids": sum(1 for count in pair_ids.values() if count == 1),
        "duplicate_pair_id_count": sum(count - 1 for count in pair_ids.values() if count > 1),
        "train_whitelist_count": list_counts.get("train_whitelist", 0),
        "train_whitelist_rate": _rate(list_counts.get("train_whitelist", 0), total),
        "review_graylist_count": list_counts.get("review_graylist", 0),
        "review_graylist_rate": _rate(list_counts.get("review_graylist", 0), total),
        "exclude_or_downweight_count": list_counts.get("exclude_or_downweight", 0),
        "exclude_or_downweight_rate": _rate(list_counts.get("exclude_or_downweight", 0), total),
        "unknown_policy_count": counters["use_policy"].get("unknown_recommendation_requires_review", 0),
        "by_list": _counter_items(counters["list_name"], total, top_limit),
        "by_training_recommendation": _counter_items(counters["training_recommendation"], total, top_limit),
        "by_pairwise_training_recommendation": _counter_items(counters["pairwise_training_recommendation"], total, top_limit),
        "by_audit_verdict": _counter_items(counters["audit_verdict"], total, top_limit),
        "by_primary_category": _counter_items(counters["primary_category"], total, top_limit),
        "by_diagnosis_4_5": _counter_items(counters["diagnosis_4_5"], total, top_limit),
        "by_query_family": _counter_items(counters["query_family"], total, top_limit),
        "by_gated_positive_rank": _counter_items(counters["gated_positive_rank"], total, top_limit),
        "by_score_gap_bucket": _counter_items(counters["score_gap_bucket"], total, top_limit),
        "by_list_primary_category": {
            list_name: _counter_items(counters[f"list_primary_category:{list_name}"], total, top_limit)
            for list_name in LIST_OUTPUTS
        },
        "by_list_family": {
            list_name: _counter_items(counters[f"list_family:{list_name}"], total, top_limit)
            for list_name in LIST_OUTPUTS
        },
        "by_list_rank": {
            list_name: _counter_items(counters[f"list_rank:{list_name}"], total, top_limit)
            for list_name in LIST_OUTPUTS
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


def _sample_rows(rows: list[dict[str, Any]], list_name: str, limit: int) -> list[list[object]]:
    selected = [row for row in rows if row["list_name"] == list_name]
    selected.sort(
        key=lambda row: (
            _clean(row.get("primary_category")),
            _int(row.get("gated_positive_rank")),
            _clean(row.get("query_family")),
            _clean(row.get("query")),
        )
    )
    return [
        [
            row["pair_id"],
            row.get("primary_category", ""),
            row.get("gated_positive_rank", ""),
            row.get("query_family", ""),
            row.get("query", ""),
            row.get("positive_id", ""),
            row.get("top_id", ""),
            row.get("audit_reason", ""),
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Rank 2-5 Pairwise List Builder",
        "",
        "Stage 4.5 extends the pairwise whitelist/graylist/exclude mechanism to all rank2-5 near misses. It only freezes training eligibility lists; it does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["unique_pair_ids", summary["unique_pair_ids"]],
                ["duplicate_pair_id_count", summary["duplicate_pair_id_count"]],
                ["train_whitelist_count", summary["train_whitelist_count"]],
                ["train_whitelist_rate", summary["train_whitelist_rate"]],
                ["review_graylist_count", summary["review_graylist_count"]],
                ["review_graylist_rate", summary["review_graylist_rate"]],
                ["exclude_or_downweight_count", summary["exclude_or_downweight_count"]],
                ["exclude_or_downweight_rate", summary["exclude_or_downweight_rate"]],
                ["unknown_policy_count", summary["unknown_policy_count"]],
                ["feature_group_count", report["feature_group_count"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Lists",
        "",
        _md_table(_counter_table(summary["by_list"])),
        "",
        "## Final Recommendation",
        "",
        _md_table(_counter_table(summary["by_training_recommendation"], limit=20)),
        "",
        "## Pairwise Recommendation Before Guardrails",
        "",
        _md_table(_counter_table(summary["by_pairwise_training_recommendation"], limit=20)),
        "",
        "## Primary Category",
        "",
        _md_table(_counter_table(summary["by_primary_category"], limit=20)),
        "",
        "## Train Whitelist Category Mix",
        "",
        _md_table(_counter_table(summary["by_list_primary_category"]["train_whitelist"], limit=20)),
        "",
        "## Graylist Category Mix",
        "",
        _md_table(_counter_table(summary["by_list_primary_category"]["review_graylist"], limit=20)),
        "",
        "## Exclude/Downweight Category Mix",
        "",
        _md_table(_counter_table(summary["by_list_primary_category"]["exclude_or_downweight"], limit=20)),
        "",
        "## Samples",
        "",
        "Train whitelist:",
        "",
        _md_table(
            [["pair_id", "primary_category", "rank", "family", "query", "positive_id", "top_id", "reason"]]
            + _sample_rows(rows, "train_whitelist", sample_limit)
        ),
        "",
        "Review graylist:",
        "",
        _md_table(
            [["pair_id", "primary_category", "rank", "family", "query", "positive_id", "top_id", "reason"]]
            + _sample_rows(rows, "review_graylist", sample_limit)
        ),
        "",
        "Exclude/downweight:",
        "",
        _md_table(
            [["pair_id", "primary_category", "rank", "family", "query", "positive_id", "top_id", "reason"]]
            + _sample_rows(rows, "exclude_or_downweight", sample_limit)
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _audit_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    preferred = [
        *LEADING_FIELDS,
        "primary_category",
        "secondary_tags",
        "rank_bucket",
        "gated_positive_rank",
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query_family",
        "audit_family_hint",
        "expected_books",
        "expected_ids",
        "expected_names",
        "query",
        "positive_id",
        "positive_name",
        "positive_chapter",
        "top_id",
        "top_name",
        "top_chapter",
        "diagnosis",
        "score_direction",
        "chapter_same",
        "raw_ltr_top_id",
        "raw_ltr_top",
        "gate_reason",
        "score_margin",
        "query_terms",
        "positive_terms",
        "top_terms",
        "positive_only_terms",
        "top_only_terms",
        "query_hits_positive_only",
        "query_hits_top_only",
        "query_has_param",
        "query_specs",
        "positive_numbers",
        "top_numbers",
        "positive_param_score",
        "top_param_score",
        "feature_positive_better",
        "feature_top_better",
        "positive_reasons",
        "top_reasons",
        "current_score_delta_positive_minus_top",
        *PAIRWISE_FEATURE_FIELDS,
        *[f"delta_{feature}" for feature in pairwise_review.SCORE_FEATURES],
    ]
    remaining = sorted({key for row in rows for key in row if key not in preferred})
    return preferred + remaining


def main() -> int:
    parser = argparse.ArgumentParser(description="Build rank2-5 pairwise hard-pair white/gray/exclude lists without training")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--review-csv", default=str(DEFAULT_REVIEW_CSV))
    parser.add_argument("--gap-csv", default=str(DEFAULT_GAP_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--audit-details-csv", default=str(DEFAULT_AUDIT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--sample-limit", type=int, default=10)
    args = parser.parse_args()

    started = time.perf_counter()
    review_rows = _load_near_miss_rows(Path(args.review_csv))
    split_to_group_ids = _split_to_group_ids(review_rows)
    gaps = pairwise_review._load_gap_index(Path(args.gap_csv))
    groups = pairwise_review._load_feature_groups(Path(args.data_dir), split_to_group_ids)
    audited = _audit_rows(review_rows, gaps, groups)
    summary, bucket_rows = _summarize(audited, args.top_limit)

    audit_fields = _audit_fieldnames(audited)
    _write_csv(Path(args.audit_details_csv), audited, audit_fields)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    output_dir = Path(args.output_dir)
    output_paths: dict[str, str] = {}
    for list_name, filename in LIST_OUTPUTS.items():
        path = output_dir / filename
        rows = [row for row in audited if row["list_name"] == list_name]
        _write_csv(path, rows, audit_fields)
        output_paths[list_name] = str(path)

    report_artifacts = {
        "audit_details_csv": args.audit_details_csv,
        "buckets_csv": args.buckets_csv,
        **output_paths,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.5 rank2-5 pairwise list freeze",
        "read_only_input": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": args.data_dir,
        "review_csv": args.review_csv,
        "gap_csv": args.gap_csv,
        "output_dir": args.output_dir,
        "feature_group_count": len(groups),
        "summary": summary,
        "artifacts": report_artifacts,
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
                    "train_whitelist_count": summary["train_whitelist_count"],
                    "review_graylist_count": summary["review_graylist_count"],
                    "exclude_or_downweight_count": summary["exclude_or_downweight_count"],
                    "duplicate_pair_id_count": summary["duplicate_pair_id_count"],
                    "unknown_policy_count": summary["unknown_policy_count"],
                },
                "artifacts": report_artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
