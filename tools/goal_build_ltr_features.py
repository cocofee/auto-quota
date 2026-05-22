from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
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

from src.goal_search import GoalSearcher  # noqa: E402
from src.goal_search.national_index import (  # noqa: E402
    QuotaSignal,
    clean_text,
    extract_signal,
    is_pipe_device_false_trigger,
    tokenize,
)
from src.goal_search.searcher import (  # noqa: E402
    _apply_strong_name_signal,
    _book_matches,
    _book_of_record,
    _domain_labels,
    _domain_term_score,
    _field_match_score,
    _numeric_match_score,
    _overlap_score,
    _quota_book,
)
from tools.goal_eval import _expected_ids, _load_rows, _row_id, _row_province, _with_leakage_controls  # noqa: E402


DEFAULT_SPLIT_DIR = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_feature_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_feature_summary.md"

NUMERIC_KEYS = ("dn", "cable_section", "cable_cores", "circuits", "concrete_grade", "thickness")
FIELD_KEYS = ("family", "action", "material", "connection", "install_method")
DIAG_COLUMNS = [
    "split",
    "group_id",
    "row_index",
    "sample_id",
    "source_file",
    "project_name",
    "province",
    "query",
    "expected_ids",
    "candidate_rank",
    "quota_id",
    "quota_name",
    "quota_unit",
    "quota_book",
    "quota_chapter",
    "query_family",
    "candidate_family",
    "reasons",
]
FEATURE_COLUMNS = [
    "base_rank",
    "current_score",
    "confidence",
    "bm25_score",
    "national_cluster_bonus",
    "token_overlap",
    "unit_exact",
    "unit_conflict",
    "book_requested",
    "book_match",
    "book_conflict",
    "chapter_book_match",
    "query_family_present",
    "candidate_family_present",
    "family_match",
    "family_conflict",
    "action_match",
    "material_match",
    "connection_match",
    "install_method_match",
    "field_score",
    "numeric_score",
    "domain_rule_score",
    "domain_label_overlap_count",
    "domain_conflict_count",
    "param_exact_count",
    "param_tier_up_count",
    "param_conflict_count",
    "dn_query_present",
    "dn_candidate_present",
    "dn_exact",
    "dn_tier_up",
    "dn_gap_ratio",
    "cable_section_query_present",
    "cable_section_candidate_present",
    "cable_section_exact",
    "cable_section_tier_up",
    "cable_section_gap_ratio",
    "cable_cores_query_present",
    "cable_cores_candidate_present",
    "cable_cores_exact",
    "cable_cores_gap",
    "circuits_query_present",
    "circuits_candidate_present",
    "circuits_exact",
    "circuits_tier_up",
    "circuits_gap_ratio",
    "concrete_grade_query_present",
    "concrete_grade_candidate_present",
    "concrete_grade_exact",
    "concrete_grade_gap",
    "thickness_query_present",
    "thickness_candidate_present",
    "thickness_exact",
    "thickness_tier_up",
    "thickness_gap_ratio",
    "width_height_query_present",
    "width_height_candidate_present",
    "width_height_exact",
    "width_height_tier_match",
    "width_height_gap_ratio",
    "pipe_device_false_trigger",
    "has_domain_conflict",
    "has_family_conflict_reason",
    "has_book_conflict_reason",
    "has_unit_conflict_reason",
    "has_param_conflict_reason",
    "has_national_reason",
    "reason_count",
]
OUTPUT_FIELDS = ["label", *DIAG_COLUMNS, *FEATURE_COLUMNS]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _clean_float(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result


def _bool(value: bool) -> int:
    return 1 if value else 0


def _feature_text(row: dict[str, Any]) -> str:
    return " ".join(
        clean_text(row.get(key))
        for key in ("bill_name", "name", "bill_text", "description", "specialty", "unit")
        if clean_text(row.get(key))
    )


def _query_text(row: dict[str, Any]) -> str:
    return clean_text(row.get("bill_name") or row.get("name") or row.get("bill_text") or row.get("description"))


def _query_signal(row: dict[str, Any]) -> QuotaSignal:
    return _apply_strong_name_signal(extract_signal(_feature_text(row)), row.get("bill_name") or row.get("name") or "")


def _signal_value(signal: QuotaSignal, key: str) -> Any:
    return getattr(signal, key)


def _numeric_gap_features(query_signal: QuotaSignal, candidate_signal: QuotaSignal, key: str) -> dict[str, float]:
    qv = _signal_value(query_signal, key)
    cv = _signal_value(candidate_signal, key)
    result = {
        f"{key}_query_present": _bool(qv is not None),
        f"{key}_candidate_present": _bool(cv is not None),
        f"{key}_exact": 0,
    }
    if key in {"dn", "cable_section", "circuits", "thickness"}:
        result[f"{key}_tier_up"] = 0
        result[f"{key}_gap_ratio"] = 0.0
    else:
        result[f"{key}_gap"] = 0.0
    if qv is None or cv is None:
        return result

    qf = float(qv)
    cf = float(cv)
    exact = math.isclose(qf, cf, rel_tol=0.0, abs_tol=0.01)
    result[f"{key}_exact"] = _bool(exact)
    if key in {"dn", "cable_section", "circuits", "thickness"}:
        result[f"{key}_tier_up"] = _bool((not exact) and cf >= qf)
        result[f"{key}_gap_ratio"] = round((cf - qf) / max(abs(qf), 1.0), 6)
    else:
        result[f"{key}_gap"] = round(cf - qf, 6)
    return result


DIM_PAIR_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)(?!\d)")
LIMIT_RE = re.compile(r"(?:宽\+高|宽高|周长|半周长|mm以内|≤|<=)\D{0,8}(\d+(?:\.\d+)?)")


def _first_dim_sum(text: str) -> float | None:
    text = clean_text(text).replace("脳", "x")
    match = DIM_PAIR_RE.search(text)
    if match:
        return float(match.group(1)) + float(match.group(2))
    return None


def _first_limit(text: str) -> float | None:
    text = clean_text(text)
    match = LIMIT_RE.search(text)
    if match:
        return float(match.group(1))
    return None


def _width_height_features(query_text: str, candidate_text: str) -> dict[str, float]:
    query_sum = _first_dim_sum(query_text)
    candidate_limit = _first_limit(candidate_text) or _first_dim_sum(candidate_text)
    result = {
        "width_height_query_present": _bool(query_sum is not None),
        "width_height_candidate_present": _bool(candidate_limit is not None),
        "width_height_exact": 0,
        "width_height_tier_match": 0,
        "width_height_gap_ratio": 0.0,
    }
    if query_sum is None or candidate_limit is None:
        return result
    exact = math.isclose(query_sum, candidate_limit, rel_tol=0.0, abs_tol=0.01)
    result["width_height_exact"] = _bool(exact)
    result["width_height_tier_match"] = _bool(candidate_limit >= query_sum)
    result["width_height_gap_ratio"] = round((candidate_limit - query_sum) / max(query_sum, 1.0), 6)
    return result


def _reason_flags(reasons: list[str]) -> dict[str, int]:
    joined = " | ".join(reasons)
    lower = joined.lower()
    numeric_prefixes = tuple(f"{key} " for key in NUMERIC_KEYS)
    return {
        "domain_conflict_count": sum(1 for reason in reasons if reason.startswith("domain:") and "conflict" in reason),
        "param_exact_count": sum(1 for reason in reasons if reason.startswith(numeric_prefixes) and " exact" in reason),
        "param_tier_up_count": sum(1 for reason in reasons if reason.startswith(numeric_prefixes) and "tier_up" in reason),
        "param_conflict_count": sum(1 for reason in reasons if reason.startswith(numeric_prefixes) and "conflict" in reason),
        "has_domain_conflict": _bool("domain:" in joined and "conflict" in lower),
        "has_family_conflict_reason": _bool("family conflict" in lower),
        "has_book_conflict_reason": _bool("book conflict" in lower),
        "has_unit_conflict_reason": _bool("unit conflict" in lower),
        "has_param_conflict_reason": _bool("conflict" in lower and "domain:" not in lower),
        "has_national_reason": _bool("national_" in lower),
        "reason_count": len(reasons),
    }


def _build_feature_row(
    *,
    split: str,
    row: dict[str, Any],
    row_index: int,
    hit_rank: int,
    hit,
    searcher: GoalSearcher,
    query_signal: QuotaSignal,
    expected: set[str],
) -> dict[str, Any]:
    quota = searcher.index.by_quota_id[hit.quota_id]
    query_text = _feature_text(row)
    group_id = f"{split}:{row_index}:{_row_id(row, row_index)}"
    query_tokens = query_signal.tokens or tokenize(query_text)
    query_token_set = set(query_tokens)
    candidate_book = _book_of_record(quota)
    requested_book = clean_text(row.get("specialty")).upper()
    candidate_book_upper = clean_text(candidate_book).upper()
    chapter_upper = clean_text(quota.chapter).upper()
    chapter_compact = re.sub(r"[^A-Z0-9]", "", chapter_upper)
    requested_compact = re.sub(r"[^A-Z0-9]", "", requested_book)
    field_score, _field_reasons = _field_match_score(query_signal, quota)
    numeric_score, _numeric_reasons = _numeric_match_score(query_signal, quota)
    domain_score, _domain_reasons = _domain_term_score(query_text, query_signal, quota)
    domain_overlap = _domain_labels(query_text) & _domain_labels(quota.search_text)
    reasons = list(hit.reasons or [])

    features: dict[str, Any] = {
        "label": _bool(hit.quota_id in expected),
        "split": split,
        "group_id": group_id,
        "row_index": row_index,
        "sample_id": _row_id(row, row_index),
        "source_file": clean_text(row.get("source_file")),
        "project_name": clean_text(row.get("project_name")),
        "province": _row_province(row),
        "query": _query_text(row),
        "expected_ids": "|".join(sorted(expected)),
        "candidate_rank": hit_rank,
        "quota_id": hit.quota_id,
        "quota_name": hit.name,
        "quota_unit": hit.unit,
        "quota_book": candidate_book,
        "quota_chapter": quota.chapter,
        "query_family": query_signal.family,
        "candidate_family": quota.signal.family,
        "reasons": "|".join(reasons),
        "base_rank": hit_rank,
        "current_score": hit.score,
        "confidence": hit.confidence,
        "bm25_score": _clean_float((hit.source_scores or {}).get("bm25")),
        "national_cluster_bonus": _clean_float((hit.source_scores or {}).get("prior")),
        "token_overlap": round(_overlap_score(query_token_set, quota.tokens), 6),
        "unit_exact": _bool(bool(row.get("unit")) and bool(quota.unit) and clean_text(row.get("unit")) == clean_text(quota.unit)),
        "unit_conflict": _bool(bool(row.get("unit")) and bool(quota.unit) and clean_text(row.get("unit")) != clean_text(quota.unit)),
        "book_requested": _bool(bool(requested_book)),
        "book_match": _bool(bool(requested_book and candidate_book_upper and _book_matches(requested_book, candidate_book_upper))),
        "book_conflict": _bool(bool(requested_book and candidate_book_upper and not _book_matches(requested_book, candidate_book_upper))),
        "chapter_book_match": _bool(bool(requested_compact and requested_compact in chapter_compact)),
        "query_family_present": _bool(bool(query_signal.family)),
        "candidate_family_present": _bool(bool(quota.signal.family)),
        "family_match": _bool(bool(query_signal.family and quota.signal.family and query_signal.family == quota.signal.family)),
        "family_conflict": _bool(bool(query_signal.family and quota.signal.family and query_signal.family != quota.signal.family)),
        "field_score": round(field_score, 6),
        "numeric_score": round(numeric_score, 6),
        "domain_rule_score": round(domain_score, 6),
        "domain_label_overlap_count": len(domain_overlap),
        "pipe_device_false_trigger": _bool(is_pipe_device_false_trigger(query_text)),
    }
    for key in ("action", "material", "connection", "install_method"):
        qv = clean_text(getattr(query_signal, key))
        cv = clean_text(getattr(quota.signal, key))
        features[f"{key}_match"] = _bool(bool(qv and cv and qv == cv))
    for key in NUMERIC_KEYS:
        features.update(_numeric_gap_features(query_signal, quota.signal, key))
    features.update(_width_height_features(query_text, quota.search_text))
    features.update(_reason_flags(reasons))
    return features


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})


def _feature_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(rows)
    coverage: list[dict[str, Any]] = []
    for field in FEATURE_COLUMNS:
        present = 0
        nonzero = 0
        for row in rows:
            value = row.get(field)
            if value not in ("", None):
                present += 1
            if isinstance(value, (int, float)) and float(value) != 0.0:
                nonzero += 1
        coverage.append(
            {
                "feature": field,
                "present": present,
                "present_rate": round(present / total, 4) if total else 0.0,
                "nonzero": nonzero,
                "nonzero_rate": round(nonzero / total, 4) if total else 0.0,
            }
        )
    return coverage


def _top_counter(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _summarize_split(split: str, sample_rows: list[dict[str, Any]], feature_rows: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    group_count = len(sample_rows)
    positive_groups = {row["group_id"] for row in feature_rows if row["label"]}
    positives = sum(1 for row in feature_rows if row["label"])
    ranks = [int(row["candidate_rank"]) for row in feature_rows if row["label"]]
    province_counts = Counter(_row_province(row) for row in sample_rows)
    family_counts = Counter(row.get("query_family") or "<empty>" for row in feature_rows if int(row.get("candidate_rank") or 0) == 1)
    return {
        "split": split,
        "input_rows": group_count,
        "feature_rows": len(feature_rows),
        "top_k": max([int(row.get("candidate_rank") or 0) for row in feature_rows] or [0]),
        "positive_rows": positives,
        "positive_query_rows": len(positive_groups),
        "positive_query_rate": round(len(positive_groups) / group_count, 4) if group_count else 0.0,
        "missing_positive_query_rows": group_count - len(positive_groups),
        "positive_rank_avg": round(sum(ranks) / len(ranks), 3) if ranks else None,
        "positive_rank_min": min(ranks) if ranks else None,
        "positive_rank_max": max(ranks) if ranks else None,
        "elapsed_sec": round(elapsed, 3),
        "rows_per_sec": round(group_count / elapsed, 3) if elapsed > 0 else None,
        "province_counts": _top_counter(province_counts),
        "top1_query_family_counts": _top_counter(family_counts),
        "feature_coverage": _feature_coverage(feature_rows),
    }


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Goal LTR Feature Summary",
        "",
        "Stage 1 only: generated TopK LTR feature rows from the current goal_search output. No model training, no ranking changes, no rule changes.",
        "",
        "## Outputs",
        "",
        _md_table([["split", "jsonl", "csv"]] + [[item["split"], item["jsonl"], item["csv"]] for item in report["outputs"]]),
        "",
        "## Split Summary",
        "",
    ]
    summary_rows = [["split", "input_rows", "feature_rows", "positive_rows", "positive_query_rows", "positive_query_rate", "missing_positive_query_rows", "elapsed_sec"]]
    for split in report["splits"]:
        summary_rows.append(
            [
                split["split"],
                split["input_rows"],
                split["feature_rows"],
                split["positive_rows"],
                split["positive_query_rows"],
                split["positive_query_rate"],
                split["missing_positive_query_rows"],
                split["elapsed_sec"],
            ]
        )
    lines.extend([_md_table(summary_rows), "", "## Feature Coverage", ""])
    for split in report["splits"]:
        lines.extend(
            [
                f"### {split['split']}",
                "",
                _md_table(
                    [["feature", "present_rate", "nonzero_rate", "nonzero"]]
                    + [
                        [item["feature"], item["present_rate"], item["nonzero_rate"], item["nonzero"]]
                        for item in split["feature_coverage"]
                    ]
                ),
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_features(args: argparse.Namespace) -> dict[str, Any]:
    split_dir = Path(args.split_dir)
    output_dir = Path(args.output_dir)
    split_names = [part.strip() for part in args.splits.split(",") if part.strip()]
    searchers: dict[str, GoalSearcher] = {}
    outputs: list[dict[str, str]] = []
    split_summaries: list[dict[str, Any]] = []
    global_started = time.perf_counter()

    for split in split_names:
        input_path = split_dir / f"{split}.jsonl"
        rows = _load_rows(input_path)
        rows = [row for row in rows if _row_province(row) and _expected_ids(row)]
        if args.limit > 0:
            rows = rows[: args.limit]

        split_started = time.perf_counter()
        feature_rows: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, 1):
            province = _row_province(row)
            if province not in searchers:
                searchers[province] = GoalSearcher(province)
            searcher = searchers[province]
            expected = _expected_ids(row)
            item = _with_leakage_controls(row, args)
            hits = searcher.search(item, top_k=args.top_k)
            query_signal = _query_signal(row)
            for rank, hit in enumerate(hits, 1):
                if hit.quota_id not in searcher.index.by_quota_id:
                    continue
                feature_rows.append(
                    _build_feature_row(
                        split=split,
                        row=row,
                        row_index=idx,
                        hit_rank=rank,
                        hit=hit,
                        searcher=searcher,
                        query_signal=query_signal,
                        expected=expected,
                    )
                )
            if args.progress_every > 0 and idx % args.progress_every == 0:
                print(f"[{split}] processed {idx}/{len(rows)} rows; features={len(feature_rows)}", file=sys.stderr)

        jsonl_path = output_dir / f"ltr_features_{split}.jsonl"
        csv_path = output_dir / f"ltr_features_{split}.csv"
        _write_jsonl(jsonl_path, feature_rows)
        if args.write_csv:
            _write_csv(csv_path, feature_rows)
            csv_output = str(csv_path)
        else:
            csv_output = ""
        outputs.append({"split": split, "jsonl": str(jsonl_path), "csv": csv_output})
        split_summaries.append(_summarize_split(split, rows, feature_rows, time.perf_counter() - split_started))

    report = {
        "stage": "Goal LTR v1 / stage 1 feature generation",
        "no_model_training": True,
        "no_ranking_change": True,
        "no_rule_change": True,
        "top_k": args.top_k,
        "split_dir": str(split_dir),
        "output_dir": str(output_dir),
        "elapsed_sec": round(time.perf_counter() - global_started, 3),
        "searcher_count": len(searchers),
        "diagnostic_columns": sorted(DIAG_COLUMNS),
        "feature_columns": FEATURE_COLUMNS,
        "outputs": outputs,
        "splits": split_summaries,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Goal LTR v1 TopK feature sets without training or reranking")
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--splits", default="dev,heldout,hard")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--limit", type=int, default=0, help="Optional per-split row limit for smoke tests")
    parser.add_argument("--write-csv", action="store_true", help="Also write CSV copies next to JSONL outputs")
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--allow-answer-priors", action="store_true", help="Forwarded only for compatibility; default is leakage-safe")
    parser.add_argument("--exclude-sample-id", default="")
    parser.add_argument("--exclude-source-file", default="")
    parser.add_argument("--exclude-project-name", default="")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    report = build_features(args)
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    print(json.dumps({"summary": {k: v for k, v in report.items() if k not in {"splits", "feature_columns", "diagnostic_columns"}}, "splits": report["splits"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
