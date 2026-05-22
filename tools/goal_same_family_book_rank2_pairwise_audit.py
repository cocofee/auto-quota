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

DEFAULT_INPUT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_details.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_pairwise_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_pairwise_audit_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_pairwise_audit_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_pairwise_audit_buckets.csv"

PAIRWISE_DELTA_FEATURES = (
    "delta_current_score",
    "delta_bm25_score",
    "delta_field_score",
    "delta_confidence",
    "delta_token_overlap",
    "delta_numeric_score",
    "delta_domain_rule_score",
    "delta_national_cluster_bonus",
    "delta_material_match",
    "delta_action_match",
    "delta_connection_match",
    "delta_install_method_match",
    "delta_param_exact_count",
    "delta_param_tier_up_count",
    "delta_param_conflict_count",
    "delta_reason_count",
)

STRONG_POSITIVE_FEATURES = {
    "token_overlap",
    "numeric_score",
    "domain_rule_score",
    "material_match",
    "action_match",
    "connection_match",
    "install_method_match",
    "param_exact_count",
    "param_tier_up_count",
    "national_cluster_bonus",
}


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


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _split_pipe(value: str) -> list[str]:
    return [_clean(item) for item in _clean(value).split("|") if _clean(item)]


def _has(value: str) -> bool:
    return bool(_clean(value))


def _feature_set(value: str) -> set[str]:
    return set(_split_pipe(value))


def _positive_signal_features(row: dict[str, Any]) -> list[str]:
    features = _feature_set(row.get("feature_positive_better", ""))
    return sorted(features & STRONG_POSITIVE_FEATURES)


def _top_surface_features(row: dict[str, Any]) -> list[str]:
    features = _feature_set(row.get("feature_top_better", ""))
    surface = {"current_score", "bm25_score", "confidence", "token_overlap"}
    return sorted(features & surface)


def _score_gap_bucket(value: float) -> str:
    if value >= 0.001:
        return "positive_higher"
    if value >= -0.01:
        return "top_higher_lt_0_01"
    if value >= -0.05:
        return "top_higher_0_01_0_05"
    if value >= -0.10:
        return "top_higher_0_05_0_10"
    return "top_higher_ge_0_10"


def _evidence_flags(row: dict[str, Any]) -> dict[str, Any]:
    query_hits_positive = _split_pipe(row.get("query_hits_positive_only", ""))
    query_hits_top = _split_pipe(row.get("query_hits_top_only", ""))
    positive_only = _split_pipe(row.get("positive_only_terms", ""))
    top_only = _split_pipe(row.get("top_only_terms", ""))
    positive_signal_features = _positive_signal_features(row)
    top_surface_features = _top_surface_features(row)
    query_has_param = _int(row.get("query_has_param")) > 0
    query_specs = _clean(row.get("query_specs"))
    positive_numbers = _clean(row.get("positive_numbers"))
    top_numbers = _clean(row.get("top_numbers"))
    raw_ltr_was_positive = _int(row.get("raw_ltr_was_positive")) > 0
    positive_score_gap = _float(row.get("delta_current_score"))

    return {
        "query_hits_positive_count": len(query_hits_positive),
        "query_hits_top_count": len(query_hits_top),
        "positive_only_term_count": len(positive_only),
        "top_only_term_count": len(top_only),
        "positive_signal_feature_count": len(positive_signal_features),
        "top_surface_feature_count": len(top_surface_features),
        "query_has_param": int(query_has_param),
        "query_has_explicit_spec": int(bool(query_specs)),
        "candidate_numbers_differ": int(bool(positive_numbers or top_numbers) and positive_numbers != top_numbers),
        "raw_ltr_was_positive": int(raw_ltr_was_positive),
        "score_gap_bucket": _score_gap_bucket(positive_score_gap),
        "positive_signal_features_strong": "|".join(positive_signal_features),
        "top_surface_features": "|".join(top_surface_features),
    }


def _classify_pair(row: dict[str, Any], flags: dict[str, Any]) -> tuple[str, str, str]:
    diagnosis = _clean(row.get("diagnosis"))
    query_hits_positive = flags["query_hits_positive_count"] > 0
    query_hits_top = flags["query_hits_top_count"] > 0
    positive_signal = flags["positive_signal_feature_count"] > 0
    explicit_param = flags["query_has_explicit_spec"] > 0
    query_has_param = flags["query_has_param"] > 0
    numbers_differ = flags["candidate_numbers_differ"] > 0
    raw_ltr_positive = flags["raw_ltr_was_positive"] > 0
    top_surface_only = query_hits_top and not query_hits_positive
    no_query_discriminator = (
        not query_hits_positive
        and not query_hits_top
        and not explicit_param
        and not positive_signal
    )

    if raw_ltr_positive:
        return (
            "learnable_but_blocked_by_safety_gate",
            "do_not_train_first_fix_or_audit_gate",
            "raw_ltr_ranked_positive_top1_but_gate_kept_baseline",
        )
    if query_hits_positive and not query_hits_top:
        return (
            "learnable_hard_negative_subtype",
            "include_as_hard_pair_candidate",
            "query_contains_positive_only_subtype_terms",
        )
    if explicit_param and numbers_differ and not top_surface_only:
        return (
            "learnable_hard_negative_param",
            "include_as_hard_pair_candidate",
            "query_contains_explicit_param_and_candidates_differ",
        )
    if positive_signal and not top_surface_only:
        return (
            "learnable_hard_negative_existing_signal",
            "include_as_hard_pair_candidate",
            "positive_has_existing_feature_advantage_but_current_score_lost",
        )
    if query_hits_positive and query_hits_top:
        return (
            "ambiguous_conflicting_query_terms",
            "manual_label_review_before_training",
            "query_matches_terms_unique_to_both_candidates",
        )
    if top_surface_only:
        return (
            "label_or_query_insufficient_top_surface_stronger",
            "exclude_or_downweight_from_hard_pair",
            "query_surface_evidence_favors_current_top",
        )
    if diagnosis in {"subtype_diff_not_in_query_or_label_specific", "query_lacks_discriminating_terms"}:
        return (
            "label_or_query_insufficient_missing_discriminator",
            "exclude_or_downweight_from_hard_pair",
            "candidate_diff_not_supported_by_query_text",
        )
    if no_query_discriminator:
        return (
            "label_or_query_insufficient_low_signal",
            "exclude_or_downweight_from_hard_pair",
            "no_positive_query_or_param_signal_available",
        )
    if query_has_param and numbers_differ:
        return (
            "weakly_learnable_param_from_candidate_names",
            "keep_for_feature_research_not_training",
            "query_param_signal_is_implicit_or_unreliable",
        )
    return (
        "ambiguous_needs_review",
        "manual_label_review_before_training",
        "signals_do_not_cleanly_favor_either_side",
    )


def _audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for row in rows:
        flags = _evidence_flags(row)
        verdict, recommendation, reason = _classify_pair(row, flags)
        audited.append(
            {
                "audit_verdict": verdict,
                "training_recommendation": recommendation,
                "audit_reason": reason,
                "split": _clean(row.get("split")),
                "group_id": _clean(row.get("group_id")),
                "sample_id": _clean(row.get("sample_id")),
                "province": _clean(row.get("province")),
                "query_family": _clean(row.get("query_family")),
                "audit_family_hint": _clean(row.get("audit_family_hint")),
                "expected_books": _clean(row.get("expected_books")),
                "diagnosis_4_1": _clean(row.get("diagnosis")),
                "query": _clean(row.get("query")),
                "positive_id": _clean(row.get("positive_id")),
                "positive_name": _clean(row.get("positive_name")),
                "top_id": _clean(row.get("top_id")),
                "top_name": _clean(row.get("top_name")),
                "positive_chapter": _clean(row.get("positive_chapter")),
                "top_chapter": _clean(row.get("top_chapter")),
                "chapter_same": _clean(row.get("chapter_same")),
                "score_direction": _clean(row.get("score_direction")),
                "current_score_delta_positive_minus_top": _clean(row.get("current_score_delta_positive_minus_top")),
                "query_terms": _clean(row.get("query_terms")),
                "query_specs": _clean(row.get("query_specs")),
                "query_hits_positive_only": _clean(row.get("query_hits_positive_only")),
                "query_hits_top_only": _clean(row.get("query_hits_top_only")),
                "positive_only_terms": _clean(row.get("positive_only_terms")),
                "top_only_terms": _clean(row.get("top_only_terms")),
                "positive_terms": _clean(row.get("positive_terms")),
                "top_terms": _clean(row.get("top_terms")),
                "positive_numbers": _clean(row.get("positive_numbers")),
                "top_numbers": _clean(row.get("top_numbers")),
                "positive_param_score": _clean(row.get("positive_param_score")),
                "top_param_score": _clean(row.get("top_param_score")),
                "feature_positive_better": _clean(row.get("feature_positive_better")),
                "feature_top_better": _clean(row.get("feature_top_better")),
                "positive_reasons": _clean(row.get("positive_reasons")),
                "top_reasons": _clean(row.get("top_reasons")),
                **flags,
                **{feature: _clean(row.get(feature)) for feature in PAIRWISE_DELTA_FEATURES},
            }
        )
    return audited


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _bucket_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _summarize(rows: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    bucket_rows: list[dict[str, Any]] = []
    for row in rows:
        for dimension in (
            "audit_verdict",
            "training_recommendation",
            "audit_reason",
            "diagnosis_4_1",
            "query_family",
            "expected_books",
            "score_direction",
            "score_gap_bucket",
            "chapter_same",
        ):
            counters[dimension][_bucket_key(row.get(dimension))] += 1
        counters[f"verdict_family:{_bucket_key(row.get('audit_verdict'))}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"recommendation_family:{_bucket_key(row.get('training_recommendation'))}"][_bucket_key(row.get("query_family"))] += 1

    total = len(rows)
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append(
                {
                    "dimension": dimension,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )

    hard_pair_count = counters["training_recommendation"]["include_as_hard_pair_candidate"]
    exclude_count = counters["training_recommendation"]["exclude_or_downweight_from_hard_pair"]
    review_count = counters["training_recommendation"]["manual_label_review_before_training"]
    gate_count = counters["training_recommendation"]["do_not_train_first_fix_or_audit_gate"]
    research_count = counters["training_recommendation"]["keep_for_feature_research_not_training"]
    summary = {
        "rows": total,
        "hard_pair_candidate_count": hard_pair_count,
        "hard_pair_candidate_rate": _rate(hard_pair_count, total),
        "exclude_or_downweight_count": exclude_count,
        "exclude_or_downweight_rate": _rate(exclude_count, total),
        "manual_review_count": review_count,
        "manual_review_rate": _rate(review_count, total),
        "safety_gate_first_count": gate_count,
        "feature_research_count": research_count,
        "by_verdict": _top_items(counters["audit_verdict"], top_limit),
        "by_training_recommendation": _top_items(counters["training_recommendation"], top_limit),
        "by_diagnosis_4_1": _top_items(counters["diagnosis_4_1"], top_limit),
        "by_query_family": _top_items(counters["query_family"], top_limit),
        "by_score_gap_bucket": _top_items(counters["score_gap_bucket"], top_limit),
    }
    return summary, bucket_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def _counter_table(items: list[dict[str, Any]]) -> list[list[object]]:
    return [["key", "count"], *[[item["key"], item["count"]] for item in items]]


def _sample_rows(rows: list[dict[str, Any]], recommendation: str, limit: int) -> list[list[object]]:
    selected = [row for row in rows if row["training_recommendation"] == recommendation]
    selected.sort(key=lambda row: (_clean(row.get("audit_verdict")), _clean(row.get("query_family")), _clean(row.get("query"))))
    return [
        [
            row["audit_verdict"],
            row["query_family"],
            row["query"],
            f"{row['positive_id']} {row['positive_name']}",
            f"{row['top_id']} {row['top_name']}",
            row["audit_reason"],
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Same-Family Same-Book Rank2 Pairwise Audit",
        "",
        "Stage 4.3 read-only pairwise audit. It compares current Top1 against the correct rank2 candidate and decides whether each pair is useful as a learnable hard negative or should be excluded/downweighted because the bill text or label is insufficient. No training and no search changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["hard_pair_candidate_count", summary["hard_pair_candidate_count"]],
                ["hard_pair_candidate_rate", summary["hard_pair_candidate_rate"]],
                ["exclude_or_downweight_count", summary["exclude_or_downweight_count"]],
                ["exclude_or_downweight_rate", summary["exclude_or_downweight_rate"]],
                ["manual_review_count", summary["manual_review_count"]],
                ["safety_gate_first_count", summary["safety_gate_first_count"]],
                ["feature_research_count", summary["feature_research_count"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Verdict",
        "",
        _md_table(_counter_table(summary["by_verdict"])),
        "",
        "## Training Recommendation",
        "",
        _md_table(_counter_table(summary["by_training_recommendation"])),
        "",
        "## Buckets",
        "",
        "4.1 diagnosis:",
        "",
        _md_table(_counter_table(summary["by_diagnosis_4_1"])),
        "",
        "Query family:",
        "",
        _md_table(_counter_table(summary["by_query_family"])),
        "",
        "Score gap bucket:",
        "",
        _md_table(_counter_table(summary["by_score_gap_bucket"])),
        "",
        "## Samples",
        "",
        "Hard-pair candidates:",
        "",
        _md_table(
            [["verdict", "family", "query", "positive_rank2", "current_top1", "reason"]]
            + _sample_rows(rows, "include_as_hard_pair_candidate", sample_limit)
        ),
        "",
        "Exclude/downweight:",
        "",
        _md_table(
            [["verdict", "family", "query", "positive_rank2", "current_top1", "reason"]]
            + _sample_rows(rows, "exclude_or_downweight_from_hard_pair", sample_limit)
        ),
        "",
        "Manual review:",
        "",
        _md_table(
            [["verdict", "family", "query", "positive_rank2", "current_top1", "reason"]]
            + _sample_rows(rows, "manual_label_review_before_training", sample_limit)
        ),
        "",
        "Safety gate first:",
        "",
        _md_table(
            [["verdict", "family", "query", "positive_rank2", "current_top1", "reason"]]
            + _sample_rows(rows, "do_not_train_first_fix_or_audit_gate", sample_limit)
        ),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["details_csv", report["details_csv"]],
                ["buckets_csv", report["buckets_csv"]],
            ]
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only pairwise hard-pair audit for same-family/book rank2 misses")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--top-limit", type=int, default=20)
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.input_csv))
    audited = _audit_rows(source_rows)
    summary, bucket_rows = _summarize(audited, args.top_limit)

    detail_fields = [
        "audit_verdict",
        "training_recommendation",
        "audit_reason",
        "split",
        "group_id",
        "sample_id",
        "province",
        "query_family",
        "audit_family_hint",
        "expected_books",
        "diagnosis_4_1",
        "query",
        "positive_id",
        "positive_name",
        "top_id",
        "top_name",
        "positive_chapter",
        "top_chapter",
        "chapter_same",
        "score_direction",
        "score_gap_bucket",
        "current_score_delta_positive_minus_top",
        "query_terms",
        "query_specs",
        "query_hits_positive_only",
        "query_hits_top_only",
        "positive_only_terms",
        "top_only_terms",
        "positive_numbers",
        "top_numbers",
        "positive_param_score",
        "top_param_score",
        "feature_positive_better",
        "feature_top_better",
        "positive_signal_features_strong",
        "top_surface_features",
        "query_hits_positive_count",
        "query_hits_top_count",
        "positive_only_term_count",
        "top_only_term_count",
        "positive_signal_feature_count",
        "top_surface_feature_count",
        "query_has_param",
        "query_has_explicit_spec",
        "candidate_numbers_differ",
        "raw_ltr_was_positive",
        "positive_reasons",
        "top_reasons",
    ] + list(PAIRWISE_DELTA_FEATURES)

    _write_csv(Path(args.details_csv), audited, detail_fields)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    report = {
        "stage": "Goal LTR v1 / stage 4.3 same-family/book rank2 pairwise hard-pair audit",
        "read_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input_csv": args.input_csv,
        "details_csv": args.details_csv,
        "buckets_csv": args.buckets_csv,
        "summary": summary,
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
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    **summary,
                },
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                    "details_csv": args.details_csv,
                    "buckets_csv": args.buckets_csv,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
