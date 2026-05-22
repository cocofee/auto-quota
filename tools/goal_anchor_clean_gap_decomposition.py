from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from goal_top80_recall_audit import (  # noqa: E402
    ProvinceQuotaLookup,
    _expected_ids,
    _local_missing_reason,
    _query_family,
    _record_books,
    _record_families,
    _record_names,
    _same_any_book,
)

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_decomposition_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_decomposition_summary.md"
DEFAULT_MISSING_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_top80_missing.csv"
DEFAULT_WRONG_RANK_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_wrong_rank.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_buckets.csv"


@dataclass
class FeatureGroup:
    group_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_by_quota_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    positive_rows: list[dict[str, Any]] = field(default_factory=list)
    query_family: str = ""
    baseline_top_row: dict[str, Any] | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _read_details(path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl(path))


def _rank(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    return _int(row.get("candidate_rank") or row.get("base_rank") or row.get("row_index"))


def _load_feature_groups(path: Path, group_ids: set[str]) -> dict[str, FeatureGroup]:
    groups: dict[str, FeatureGroup] = {}
    for row in _iter_jsonl(path):
        group_id = _clean(row.get("group_id"))
        if group_id not in group_ids:
            continue
        group = groups.setdefault(group_id, FeatureGroup(group_id=group_id))
        group.rows.append(row)
        quota_id = _clean(row.get("quota_id"))
        if quota_id and quota_id not in group.row_by_quota_id:
            group.row_by_quota_id[quota_id] = row
        if _int(row.get("label")) > 0:
            group.positive_rows.append(row)
        if _rank(row) == 1:
            group.baseline_top_row = row
            group.query_family = _clean(row.get("query_family"))

    for group in groups.values():
        group.rows.sort(key=lambda item: (_rank(item) or 999999, _clean(item.get("quota_id"))))
        group.positive_rows.sort(key=lambda item: (_rank(item) or 999999, _clean(item.get("quota_id"))))
        if not group.baseline_top_row and group.rows:
            group.baseline_top_row = group.rows[0]
            group.query_family = _clean(group.rows[0].get("query_family"))
    return groups


def _candidate_row(group: FeatureGroup | None, quota_id: str) -> dict[str, Any] | None:
    if not group:
        return None
    return group.row_by_quota_id.get(_clean(quota_id))


def _join_values(values: list[str] | set[str], sep: str = ",") -> str:
    return sep.join(sorted(_clean(value) for value in values if _clean(value)))


def _positive_ids(group: FeatureGroup | None) -> list[str]:
    if not group:
        return []
    return [_clean(row.get("quota_id")) for row in group.positive_rows if _clean(row.get("quota_id"))]


def _positive_names(group: FeatureGroup | None, limit: int = 3) -> str:
    if not group:
        return ""
    names = [f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip() for row in group.positive_rows[:limit]]
    return " || ".join(name for name in names if name)


def _positive_rank_min(group: FeatureGroup | None) -> int:
    ranks = [_rank(row) for row in (group.positive_rows if group else []) if _rank(row)]
    return min(ranks) if ranks else 0


def _positive_ranks(group: FeatureGroup | None) -> str:
    ranks = [_rank(row) for row in (group.positive_rows if group else []) if _rank(row)]
    return "|".join(str(rank) for rank in sorted(ranks))


def _rank_bucket(rank: int | None) -> str:
    if not rank:
        return "missing"
    if rank == 1:
        return "rank_1"
    if rank <= 5:
        return "rank_2_5"
    if rank <= 10:
        return "rank_6_10"
    if rank <= 20:
        return "rank_11_20"
    if rank <= 40:
        return "rank_21_40"
    if rank <= 80:
        return "rank_41_80"
    return "rank_gt_80"


def _top_row_identity(row: dict[str, Any] | None) -> dict[str, str]:
    if not row:
        return {
            "top_id": "",
            "top_name": "",
            "top_family": "",
            "top_book": "",
            "top_chapter": "",
            "top_unit": "",
            "top_score": "",
        }
    score = _float(row.get("current_score"))
    return {
        "top_id": _clean(row.get("quota_id")),
        "top_name": _clean(row.get("quota_name")),
        "top_family": _clean(row.get("candidate_family")),
        "top_book": _clean(row.get("quota_book")),
        "top_chapter": _clean(row.get("quota_chapter")),
        "top_unit": _clean(row.get("quota_unit")),
        "top_score": "" if score is None else str(round(score, 6)),
    }


def _wrong_rank_reason(
    *,
    query_family: str,
    expected_families: list[str],
    expected_books: list[str],
    gated_top_family: str,
    gated_top_book: str,
    gated_positive_rank: int,
) -> str:
    if not query_family:
        return "query_family_empty"
    if query_family and expected_families and query_family not in expected_families:
        return "query_family_mismatch"
    if expected_families and gated_top_family and gated_top_family not in expected_families:
        return "top1_wrong_family"
    if expected_books and gated_top_book and not _same_any_book(expected_books, gated_top_book):
        return "top1_wrong_book"
    if not gated_top_family:
        return "top1_family_empty"
    if gated_positive_rank and gated_positive_rank <= 5:
        return "near_miss_rank_2_5"
    return "same_family_or_unknown_wrong_rank"


def _build_gap_row(
    *,
    split: str,
    status: str,
    reason: str,
    detail: dict[str, Any],
    group: FeatureGroup | None,
    expected: set[str],
    expected_local_ids: set[str],
    expected_not_local_ids: list[str],
    expected_families: list[str],
    expected_books: list[str],
    expected_names: str,
    query_family: str,
    gated_top_row: dict[str, Any] | None,
) -> dict[str, Any]:
    top = _top_row_identity(gated_top_row)
    rank = _int(detail.get("gated_positive_rank")) or _positive_rank_min(group)
    return {
        "split": split,
        "status": status,
        "reason": reason,
        "rank_bucket": _rank_bucket(rank),
        "group_id": _clean(detail.get("group_id")),
        "sample_id": _clean(detail.get("sample_id")),
        "source_file": _clean(detail.get("source_file")),
        "project_name": _clean(detail.get("project_name")),
        "province": _clean(detail.get("province")),
        "query": _clean(detail.get("query")),
        "query_family": query_family,
        "expected_ids": "|".join(sorted(expected)),
        "expected_local_ids": "|".join(sorted(expected_local_ids)),
        "expected_not_local_ids": "|".join(expected_not_local_ids),
        "expected_families": ",".join(expected_families),
        "expected_books": ",".join(expected_books),
        "expected_names": expected_names,
        "positive_ids_in_top80": "|".join(_positive_ids(group)),
        "positive_names_in_top80": _positive_names(group),
        "positive_ranks": _positive_ranks(group),
        "positive_rank_min": rank or "",
        "baseline_positive_rank": detail.get("baseline_positive_rank"),
        "raw_ltr_positive_rank": detail.get("raw_ltr_positive_rank"),
        "gated_positive_rank": detail.get("gated_positive_rank"),
        "baseline_top_id": _clean(detail.get("baseline_top_id")),
        "baseline_top": _clean(detail.get("baseline_top")),
        "raw_ltr_top_id": _clean(detail.get("raw_ltr_top_id")),
        "raw_ltr_top": _clean(detail.get("raw_ltr_top")),
        "gated_top_id": _clean(detail.get("gated_top_id")),
        "gated_top": _clean(detail.get("gated_top")),
        "gated_top_family": top["top_family"],
        "gated_top_book": top["top_book"],
        "gated_top_chapter": top["top_chapter"],
        "gated_top_unit": top["top_unit"],
        "gated_top_score": top["top_score"],
        "gate_reason": _clean(detail.get("gate_reason")),
        "score_margin": detail.get("score_margin"),
        "top80_rows": len(group.rows) if group else 0,
    }


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _counter_key(value: str) -> str:
    return _clean(value) or "<empty>"


def _add_bucket_counts(counters: dict[str, Counter[str]], row: dict[str, Any]) -> None:
    status = _clean(row.get("status"))
    for dimension, field in (
        ("reason", "reason"),
        ("query_family", "query_family"),
        ("expected_family", "expected_families"),
        ("expected_book", "expected_books"),
        ("province", "province"),
        ("source_file", "source_file"),
        ("gated_top_family", "gated_top_family"),
        ("gated_top_book", "gated_top_book"),
        ("rank_bucket", "rank_bucket"),
    ):
        counters[f"{status}:{dimension}"][_counter_key(row.get(field))] += 1


def _summarize_status(counters: dict[str, Counter[str]], status: str, total: int, top_limit: int) -> dict[str, Any]:
    return {
        "count": total,
        "by_reason": _top_items(counters[f"{status}:reason"], top_limit),
        "by_query_family": _top_items(counters[f"{status}:query_family"], top_limit),
        "by_expected_family": _top_items(counters[f"{status}:expected_family"], top_limit),
        "by_expected_book": _top_items(counters[f"{status}:expected_book"], top_limit),
        "by_province": _top_items(counters[f"{status}:province"], top_limit),
        "by_source_file": _top_items(counters[f"{status}:source_file"], top_limit),
        "by_gated_top_family": _top_items(counters[f"{status}:gated_top_family"], top_limit),
        "by_gated_top_book": _top_items(counters[f"{status}:gated_top_book"], top_limit),
        "by_rank_bucket": _top_items(counters[f"{status}:rank_bucket"], top_limit),
    }


def _decompose_split(
    *,
    split: str,
    data_dir: Path,
    details_dir: Path,
    lookups: dict[str, ProvinceQuotaLookup],
    top_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    details_path = details_dir / f"goal_anchor_clean_eval_details_{split}.jsonl"
    feature_path = data_dir / f"ltr_features_{split}.jsonl"
    details = _read_details(details_path)
    group_ids = {_clean(row.get("group_id")) for row in details if _clean(row.get("group_id"))}
    feature_groups = _load_feature_groups(feature_path, group_ids)

    status_counts: Counter[str] = Counter()
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    missing_rows: list[dict[str, Any]] = []
    wrong_rank_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []

    for detail in details:
        group_id = _clean(detail.get("group_id"))
        group = feature_groups.get(group_id)
        expected = _expected_ids(detail)
        province = _clean(detail.get("province"))
        if province not in lookups:
            lookups[province] = ProvinceQuotaLookup(province)
        local_records, not_local_ids = lookups[province].get_many(expected)
        expected_local_ids = {record.quota_id for record in local_records}
        expected_families = _record_families(local_records)
        expected_books = _record_books(local_records, expected if not local_records else None)
        expected_names = _record_names(local_records)

        query_family = _query_family(detail, group)
        gated_top_id = _clean(detail.get("gated_top_id"))
        gated_top_row = _candidate_row(group, gated_top_id) or (group.baseline_top_row if group else None)
        gated_top_family = _clean(gated_top_row.get("candidate_family")) if gated_top_row else ""
        gated_top_book = _clean(gated_top_row.get("quota_book")) if gated_top_row else ""
        has_positive = bool(detail.get("has_positive"))
        gated_hit1 = bool(detail.get("gated_hit1"))

        if not has_positive:
            status = "top80_missing"
            reason = _local_missing_reason(
                query_family=query_family,
                expected_families=expected_families,
                expected_books=expected_books,
                top1_family=gated_top_family,
                top1_book=gated_top_book,
            )
        elif not gated_hit1:
            status = "top80_present_but_wrong_rank"
            reason = _wrong_rank_reason(
                query_family=query_family,
                expected_families=expected_families,
                expected_books=expected_books,
                gated_top_family=gated_top_family,
                gated_top_book=gated_top_book,
                gated_positive_rank=_int(detail.get("gated_positive_rank")),
            )
        else:
            status_counts["gated_top1_hit"] += 1
            continue

        status_counts[status] += 1
        row = _build_gap_row(
            split=split,
            status=status,
            reason=reason,
            detail=detail,
            group=group,
            expected=expected,
            expected_local_ids=expected_local_ids,
            expected_not_local_ids=not_local_ids,
            expected_families=expected_families,
            expected_books=expected_books,
            expected_names=expected_names,
            query_family=query_family,
            gated_top_row=gated_top_row,
        )
        _add_bucket_counts(counters, row)
        if status == "top80_missing":
            missing_rows.append(row)
        else:
            wrong_rank_rows.append(row)

    for status in ("top80_missing", "top80_present_but_wrong_rank"):
        status_total = status_counts[status]
        for key, counter in counters.items():
            key_status, dimension = key.split(":", 1)
            if key_status != status:
                continue
            for value, count in counter.most_common():
                bucket_rows.append(
                    {
                        "split": split,
                        "status": status,
                        "dimension": dimension,
                        "key": value,
                        "count": count,
                        "rate_within_status": _rate(count, status_total),
                    }
                )

    total = len(details)
    missing = status_counts["top80_missing"]
    wrong_rank = status_counts["top80_present_but_wrong_rank"]
    hit1 = status_counts["gated_top1_hit"]
    summary = {
        "split": split,
        "groups": total,
        "gated_top1_hit": hit1,
        "gated_top1_rate": _rate(hit1, total),
        "top80_missing": missing,
        "top80_missing_rate": _rate(missing, total),
        "top80_present_but_wrong_rank": wrong_rank,
        "top80_present_but_wrong_rank_rate": _rate(wrong_rank, total),
        "wrong_rank_share_of_non_hit": _rate(wrong_rank, missing + wrong_rank),
        "top80_present_groups": hit1 + wrong_rank,
        "top80_present_rate": _rate(hit1 + wrong_rank, total),
        "feature_group_missing": len(group_ids - set(feature_groups)),
        "top80_missing_breakdown": _summarize_status(counters, "top80_missing", missing, top_limit),
        "wrong_rank_breakdown": _summarize_status(counters, "top80_present_but_wrong_rank", wrong_rank, top_limit),
    }
    return summary, missing_rows, wrong_rank_rows, bucket_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _write_breakdown(lines: list[str], title: str, breakdown: dict[str, Any]) -> None:
    lines.extend([f"### {title}", ""])
    for label, key in (
        ("Reason", "by_reason"),
        ("Query family", "by_query_family"),
        ("Expected family", "by_expected_family"),
        ("Expected book", "by_expected_book"),
        ("Province", "by_province"),
        ("Source file", "by_source_file"),
        ("Gated top family", "by_gated_top_family"),
        ("Gated top book", "by_gated_top_book"),
        ("Rank bucket", "by_rank_bucket"),
    ):
        lines.extend([label + ":", "", _md_table(_counter_table(breakdown[key])), ""])


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["splits"]
    lines = [
        "# Goal Anchor-Clean Gap Decomposition",
        "",
        "Stage 3.9 read-only decomposition. It uses Stage 3.8 anchor-clean eval details and existing Top80 LTR feature rows. No tuning, no ranking change, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                [
                    "split",
                    "groups",
                    "top1_hit",
                    "top1_rate",
                    "top80_missing",
                    "missing_rate",
                    "wrong_rank",
                    "wrong_rank_rate",
                    "wrong_rank_share_of_non_hit",
                ],
                *[
                    [
                        item["split"],
                        item["groups"],
                        item["gated_top1_hit"],
                        item["gated_top1_rate"],
                        item["top80_missing"],
                        item["top80_missing_rate"],
                        item["top80_present_but_wrong_rank"],
                        item["top80_present_but_wrong_rank_rate"],
                        item["wrong_rank_share_of_non_hit"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
    ]
    for item in summaries:
        lines.extend([f"## {item['split']}", ""])
        _write_breakdown(lines, "Top80 missing", item["top80_missing_breakdown"])
        _write_breakdown(lines, "Top80 present but wrong rank", item["wrong_rank_breakdown"])

    lines.extend(
        [
            "## Artifacts",
            "",
            _md_table(
                [
                    ["artifact", "path"],
                    ["top80_missing_csv", report["top80_missing_csv"]],
                    ["wrong_rank_csv", report["wrong_rank_csv"]],
                    ["buckets_csv", report["buckets_csv"]],
                ]
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Decompose Stage 3.8 anchor-clean gaps into Top80 missing and wrong-rank lists")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--top-limit", type=int, default=15)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--top80-missing-csv", default=str(DEFAULT_MISSING_CSV))
    parser.add_argument("--wrong-rank-csv", default=str(DEFAULT_WRONG_RANK_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    details_dir = Path(args.details_dir)
    lookups: dict[str, ProvinceQuotaLookup] = {}
    summaries: list[dict[str, Any]] = []
    all_missing: list[dict[str, Any]] = []
    all_wrong_rank: list[dict[str, Any]] = []
    all_buckets: list[dict[str, Any]] = []

    for split in args.splits:
        summary, missing_rows, wrong_rank_rows, bucket_rows = _decompose_split(
            split=split,
            data_dir=data_dir,
            details_dir=details_dir,
            lookups=lookups,
            top_limit=args.top_limit,
        )
        summaries.append(summary)
        all_missing.extend(missing_rows)
        all_wrong_rank.extend(wrong_rank_rows)
        all_buckets.extend(bucket_rows)

    gap_fields = [
        "split",
        "status",
        "reason",
        "rank_bucket",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_local_ids",
        "expected_not_local_ids",
        "expected_families",
        "expected_books",
        "expected_names",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_ranks",
        "positive_rank_min",
        "baseline_positive_rank",
        "raw_ltr_positive_rank",
        "gated_positive_rank",
        "baseline_top_id",
        "baseline_top",
        "raw_ltr_top_id",
        "raw_ltr_top",
        "gated_top_id",
        "gated_top",
        "gated_top_family",
        "gated_top_book",
        "gated_top_chapter",
        "gated_top_unit",
        "gated_top_score",
        "gate_reason",
        "score_margin",
        "top80_rows",
    ]
    _write_csv(Path(args.top80_missing_csv), all_missing, gap_fields)
    _write_csv(Path(args.wrong_rank_csv), all_wrong_rank, gap_fields)
    _write_csv(
        Path(args.buckets_csv),
        all_buckets,
        ["split", "status", "dimension", "key", "count", "rate_within_status"],
    )

    report = {
        "stage": "Goal LTR v1 / stage 3.9 anchor-clean gap decomposition",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": str(data_dir),
        "details_dir": str(details_dir),
        "splits_requested": args.splits,
        "top_limit": args.top_limit,
        "province_lookup_count": len(lookups),
        "top80_missing_csv": args.top80_missing_csv,
        "wrong_rank_csv": args.wrong_rank_csv,
        "buckets_csv": args.buckets_csv,
        "splits": summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "splits": [
                        {
                            "split": item["split"],
                            "groups": item["groups"],
                            "gated_top1_rate": item["gated_top1_rate"],
                            "top80_missing": item["top80_missing"],
                            "top80_missing_rate": item["top80_missing_rate"],
                            "top80_present_but_wrong_rank": item["top80_present_but_wrong_rank"],
                            "top80_present_but_wrong_rank_rate": item["top80_present_but_wrong_rank_rate"],
                            "wrong_rank_share_of_non_hit": item["wrong_rank_share_of_non_hit"],
                        }
                        for item in summaries
                    ],
                },
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                    "top80_missing_csv": args.top80_missing_csv,
                    "wrong_rank_csv": args.wrong_rank_csv,
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
