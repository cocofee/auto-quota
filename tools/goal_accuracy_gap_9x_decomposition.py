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
    _record_books,
    _record_families,
    _record_names,
    _same_any_book,
)

DEFAULT_INPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_accuracy_gap_9x_decomposition"


@dataclass
class FeatureGroup:
    group_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    positives: list[dict[str, Any]] = field(default_factory=list)
    top1: dict[str, Any] | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(_iter_jsonl(path) or [])


def _load_feature_groups(path: Path) -> dict[str, FeatureGroup]:
    groups: dict[str, FeatureGroup] = {}
    for row in _iter_jsonl(path) or []:
        group_id = _clean(row.get("group_id"))
        if not group_id:
            continue
        group = groups.setdefault(group_id, FeatureGroup(group_id=group_id))
        group.rows.append(row)
        if _to_int(row.get("label")) > 0:
            group.positives.append(row)
        if _to_int(row.get("candidate_rank")) == 1:
            group.top1 = row
    for group in groups.values():
        group.rows.sort(key=lambda item: (_to_int(item.get("candidate_rank")) or 999999, _clean(item.get("quota_id"))))
        group.positives.sort(key=lambda item: (_to_int(item.get("candidate_rank")) or 999999, _clean(item.get("quota_id"))))
        if not group.top1 and group.rows:
            group.top1 = group.rows[0]
    return groups


def _rank_bucket(rank: int) -> str:
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


def _top_identity(row: dict[str, Any] | None) -> dict[str, str]:
    if not row:
        return {
            "top1_id": "",
            "top1_name": "",
            "top1_family": "",
            "top1_book": "",
            "top1_chapter": "",
            "top1_unit": "",
            "top1_score": "",
            "top1_reasons": "",
        }
    score = _to_float(row.get("current_score") or row.get("score"))
    return {
        "top1_id": _clean(row.get("quota_id")),
        "top1_name": _clean(row.get("quota_name") or row.get("name")),
        "top1_family": _clean(row.get("candidate_family")),
        "top1_book": _clean(row.get("quota_book")),
        "top1_chapter": _clean(row.get("quota_chapter")),
        "top1_unit": _clean(row.get("quota_unit")),
        "top1_score": "" if score is None else str(round(score, 6)),
        "top1_reasons": _clean(row.get("reasons")),
    }


def _top_from_recall_gap(row: dict[str, Any], lookup: ProvinceQuotaLookup) -> dict[str, str]:
    top = (row.get("top") or [{}])[0] if isinstance(row.get("top"), list) else {}
    quota_id = _clean(top.get("quota_id"))
    records, _ = lookup.get_many({quota_id} if quota_id else set())
    record = records[0] if records else None
    score = _to_float(top.get("score"))
    books = _record_books(records, {quota_id}) if records or quota_id else []
    return {
        "top1_id": quota_id,
        "top1_name": _clean(top.get("name")),
        "top1_family": _clean(record.signal.family if record and getattr(record, "signal", None) else ""),
        "top1_book": _clean(books[0] if books else ""),
        "top1_chapter": _clean(record.chapter if record else ""),
        "top1_unit": _clean(record.unit if record else ""),
        "top1_score": "" if score is None else str(round(score, 6)),
        "top1_reasons": "|".join(str(item) for item in (top.get("reasons") or [])),
    }


def _positive_ids(group: FeatureGroup | None) -> list[str]:
    return [_clean(row.get("quota_id")) for row in (group.positives if group else []) if _clean(row.get("quota_id"))]


def _positive_names(group: FeatureGroup | None, limit: int = 3) -> str:
    if not group:
        return ""
    values = []
    for row in group.positives[:limit]:
        values.append(f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip())
    return " || ".join(value for value in values if value)


def _positive_ranks(group: FeatureGroup | None) -> list[int]:
    return [_to_int(row.get("candidate_rank")) for row in (group.positives if group else []) if _to_int(row.get("candidate_rank"))]


def _wrong_rank_reason(
    *,
    query_family: str,
    expected_families: list[str],
    expected_books: list[str],
    top1_family: str,
    top1_book: str,
    positive_rank: int,
) -> str:
    if not query_family:
        return "query_family_empty"
    if expected_families and query_family not in expected_families:
        return "query_family_mismatch"
    if expected_families and top1_family and top1_family not in expected_families:
        return "top1_wrong_family"
    if expected_books and top1_book and not _same_any_book(expected_books, top1_book):
        return "top1_wrong_book"
    if positive_rank <= 5:
        return "near_miss_rank_2_5"
    if expected_families and top1_family in expected_families and expected_books and _same_any_book(expected_books, top1_book):
        return "same_family_book_sorting"
    return "same_family_or_unknown_wrong_rank"


def _missing_reason(
    *,
    query_family: str,
    expected_families: list[str],
    expected_books: list[str],
    top1_family: str,
    top1_book: str,
) -> str:
    if not query_family:
        return "query_family_empty"
    if expected_families and query_family not in expected_families:
        return "query_family_mismatch"
    if expected_families and top1_family and top1_family not in expected_families:
        return "top1_wrong_family"
    if expected_books and top1_book and not _same_any_book(expected_books, top1_book):
        return "top1_wrong_book"
    if not top1_family:
        return "top1_family_empty"
    return "same_family_or_unknown_top80_gap"


def _expected_context(row: dict[str, Any], lookups: dict[str, ProvinceQuotaLookup]) -> tuple[set[str], set[str], list[str], list[str], list[str], str]:
    expected = _expected_ids(row)
    province = _clean(row.get("province"))
    lookup = lookups.setdefault(province, ProvinceQuotaLookup(province))
    records, not_local_ids = lookup.get_many(expected)
    local_ids = {record.quota_id for record in records}
    return expected, local_ids, not_local_ids, _record_families(records), _record_books(records, expected if not records else None), _record_names(records)


def _gap_row(
    *,
    split: str,
    status: str,
    reason: str,
    rank_bucket: str,
    meta: dict[str, Any],
    expected: set[str],
    expected_local_ids: set[str],
    expected_not_local_ids: list[str],
    expected_families: list[str],
    expected_books: list[str],
    expected_names: str,
    top: dict[str, str],
    group: FeatureGroup | None = None,
) -> dict[str, Any]:
    ranks = _positive_ranks(group)
    row = {
        "split": split,
        "status": status,
        "reason": reason,
        "rank_bucket": rank_bucket,
        "group_id": _clean(meta.get("group_id")),
        "sample_id": _clean(meta.get("sample_id")),
        "source_file": _clean(meta.get("source_file")),
        "project_name": _clean(meta.get("project_name")),
        "province": _clean(meta.get("province")),
        "query": _clean(meta.get("query")),
        "query_family": _clean(meta.get("query_family")),
        "expected_ids": "|".join(sorted(expected)),
        "expected_local_ids": "|".join(sorted(expected_local_ids)),
        "expected_not_local_ids": "|".join(expected_not_local_ids),
        "expected_families": ",".join(expected_families),
        "expected_books": ",".join(expected_books),
        "expected_names": expected_names,
        "positive_ids_in_top80": "|".join(_positive_ids(group)),
        "positive_names_in_top80": _positive_names(group),
        "positive_ranks": "|".join(str(rank) for rank in ranks),
        "positive_rank_min": min(ranks) if ranks else "",
        "top80_rows": len(group.rows) if group else _to_int(meta.get("candidate_count")),
    }
    row.update(top)
    return row


def _counter_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _add_bucket(counters: dict[str, Counter[str]], row: dict[str, Any]) -> None:
    status = _clean(row.get("status"))
    for dimension, field in (
        ("reason", "reason"),
        ("query_family", "query_family"),
        ("expected_family", "expected_families"),
        ("expected_book", "expected_books"),
        ("province", "province"),
        ("source_file", "source_file"),
        ("top1_family", "top1_family"),
        ("top1_book", "top1_book"),
        ("rank_bucket", "rank_bucket"),
    ):
        counters[f"{status}:{dimension}"][_counter_key(row.get(field))] += 1


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _summarize_status(counters: dict[str, Counter[str]], status: str, total: int, top_limit: int) -> dict[str, Any]:
    return {
        "count": total,
        "by_reason": _top_items(counters[f"{status}:reason"], top_limit),
        "by_query_family": _top_items(counters[f"{status}:query_family"], top_limit),
        "by_expected_family": _top_items(counters[f"{status}:expected_family"], top_limit),
        "by_expected_book": _top_items(counters[f"{status}:expected_book"], top_limit),
        "by_province": _top_items(counters[f"{status}:province"], top_limit),
        "by_source_file": _top_items(counters[f"{status}:source_file"], top_limit),
        "by_top1_family": _top_items(counters[f"{status}:top1_family"], top_limit),
        "by_top1_book": _top_items(counters[f"{status}:top1_book"], top_limit),
        "by_rank_bucket": _top_items(counters[f"{status}:rank_bucket"], top_limit),
    }


def _split_decomposition(split: str, input_dir: Path, lookups: dict[str, ProvinceQuotaLookup], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    metas = _read_jsonl(input_dir / f"ltr_group_{split}.jsonl")
    recall_gaps = _read_jsonl(input_dir / f"recall_gap_{split}.jsonl")
    groups = _load_feature_groups(input_dir / f"ltr_features_{split}.jsonl")
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    top80_missing_rows: list[dict[str, Any]] = []
    wrong_rank_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []

    hit1 = 0
    for meta in metas:
        positive_rank = _to_int(meta.get("positive_rank"))
        if positive_rank == 1:
            hit1 += 1
            continue
        group_id = _clean(meta.get("group_id"))
        group = groups.get(group_id)
        expected, expected_local_ids, expected_not_local_ids, expected_families, expected_books, expected_names = _expected_context(meta, lookups)
        top = _top_identity(group.top1 if group else None)
        reason = _wrong_rank_reason(
            query_family=_clean(meta.get("query_family")),
            expected_families=expected_families,
            expected_books=expected_books,
            top1_family=top["top1_family"],
            top1_book=top["top1_book"],
            positive_rank=positive_rank,
        )
        row = _gap_row(
            split=split,
            status="top80_present_but_wrong_rank",
            reason=reason,
            rank_bucket=_rank_bucket(positive_rank),
            meta=meta,
            expected=expected,
            expected_local_ids=expected_local_ids,
            expected_not_local_ids=expected_not_local_ids,
            expected_families=expected_families,
            expected_books=expected_books,
            expected_names=expected_names,
            top=top,
            group=group,
        )
        wrong_rank_rows.append(row)
        _add_bucket(counters, row)

    for gap in recall_gaps:
        expected, expected_local_ids, expected_not_local_ids, expected_families, expected_books, expected_names = _expected_context(gap, lookups)
        province = _clean(gap.get("province"))
        lookup = lookups.setdefault(province, ProvinceQuotaLookup(province))
        top = _top_from_recall_gap(gap, lookup)
        reason = _missing_reason(
            query_family=_clean(gap.get("query_family")),
            expected_families=expected_families,
            expected_books=expected_books,
            top1_family=top["top1_family"],
            top1_book=top["top1_book"],
        )
        row = _gap_row(
            split=split,
            status="top80_missing",
            reason=reason,
            rank_bucket="missing",
            meta=gap,
            expected=expected,
            expected_local_ids=expected_local_ids,
            expected_not_local_ids=expected_not_local_ids,
            expected_families=expected_families,
            expected_books=expected_books,
            expected_names=expected_names,
            top=top,
        )
        top80_missing_rows.append(row)
        _add_bucket(counters, row)

    accepted_groups = len(metas)
    missing = len(top80_missing_rows)
    wrong_rank = len(wrong_rank_rows)
    total = accepted_groups + missing
    for status in ("top80_missing", "top80_present_but_wrong_rank"):
        total_status = missing if status == "top80_missing" else wrong_rank
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
                        "rate_within_status": _rate(count, total_status),
                    }
                )

    summary = {
        "split": split,
        "groups": total,
        "accepted_top80_groups": accepted_groups,
        "baseline_top1_hit": hit1,
        "baseline_top1_rate": _rate(hit1, total),
        "top80_missing": missing,
        "top80_missing_rate": _rate(missing, total),
        "top80_present_but_wrong_rank": wrong_rank,
        "top80_present_but_wrong_rank_rate": _rate(wrong_rank, total),
        "wrong_rank_share_of_non_hit": _rate(wrong_rank, wrong_rank + missing),
        "top80_present_groups": accepted_groups,
        "top80_recall_rate": _rate(accepted_groups, total),
        "top80_missing_breakdown": _summarize_status(counters, "top80_missing", missing, top_limit),
        "wrong_rank_breakdown": _summarize_status(counters, "top80_present_but_wrong_rank", wrong_rank, top_limit),
    }
    return summary, top80_missing_rows, wrong_rank_rows, bucket_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _counter_table(items: list[dict[str, Any]]) -> list[list[Any]]:
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
        ("Top1 family", "by_top1_family"),
        ("Top1 book", "by_top1_book"),
        ("Rank bucket", "by_rank_bucket"),
    ):
        lines.extend([label + ":", "", _md_table(_counter_table(breakdown[key])), ""])


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Stage 9.0 Accuracy Gap Decomposition",
        "",
        "Read-only decomposition from the query-anchored Top80 dry-run artifacts. It does not train, tune, change ranking, or modify GoalSearcher.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["split", "groups", "baseline_top1", "top80_recall", "top80_missing", "wrong_rank", "wrong_rank_share_non_hit"],
                *[
                    [
                        row["split"],
                        row["groups"],
                        row["baseline_top1_rate"],
                        row["top80_recall_rate"],
                        row["top80_missing"],
                        row["top80_present_but_wrong_rank"],
                        row["wrong_rank_share_of_non_hit"],
                    ]
                    for row in report["splits"]
                ],
            ]
        ),
        "",
        "## Next Candidate",
        "",
        _md_table([["field", "value"]] + [[key, value] for key, value in report["next_candidate"].items()]),
        "",
    ]
    for split in report["splits"]:
        lines.extend([f"## {split['split']}", ""])
        _write_breakdown(lines, "Top80 missing", split["top80_missing_breakdown"])
        _write_breakdown(lines, "Top80 present but wrong rank", split["wrong_rank_breakdown"])
    lines.extend(
        [
            "## Guardrails",
            "",
            "- Dev buckets may be used to propose the next audit target.",
            "- Heldout and hard buckets are diagnostic only in this stage.",
            "- No model training, threshold tuning, online switch, or GoalSearcher change is allowed.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _ranked_gap_rows(bucket_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"dev": 0, "hard": 1, "heldout": 2}
    rows = [row for row in bucket_rows if row["dimension"] in {"reason", "query_family", "expected_book", "province", "source_file"}]
    rows.sort(key=lambda row: (priority.get(row["split"], 9), row["status"], row["dimension"], -_to_int(row["count"]), row["key"]))
    return rows


def _select_next_candidate(bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    dev_rows = [
        row
        for row in bucket_rows
        if row["split"] == "dev"
        and row["dimension"] == "query_family"
        and row["status"] in {"top80_missing", "top80_present_but_wrong_rank"}
        and row["key"] != "<empty>"
    ]
    if not dev_rows:
        return {
            "selected_from": "none",
            "status": "",
            "query_family": "",
            "support": 0,
            "selection_policy": "no dev query_family bucket available",
            "next_stage": "9.1 inspect empty-family/non-install gap before choosing a fix path",
        }
    dev_rows.sort(key=lambda row: (-_to_int(row["count"]), row["status"], row["key"]))
    best = dev_rows[0]
    return {
        "selected_from": "dev_only",
        "status": best["status"],
        "query_family": best["key"],
        "support": best["count"],
        "selection_policy": "largest non-empty dev query_family gap bucket; heldout not used for selection",
        "next_stage": "9.1 dev-only high-yield bucket audit before any design or tuning",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.0 accuracy gap restart / decomposition")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--splits", nargs="+", default=["dev", "heldout", "hard"])
    parser.add_argument("--top-limit", type=int, default=15)
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    input_dir = Path(args.input_dir)
    output_prefix = Path(args.output_prefix)
    lookups: dict[str, ProvinceQuotaLookup] = {}
    split_summaries: list[dict[str, Any]] = []
    all_missing: list[dict[str, Any]] = []
    all_wrong_rank: list[dict[str, Any]] = []
    all_buckets: list[dict[str, Any]] = []

    for split in args.splits:
        summary, missing_rows, wrong_rank_rows, bucket_rows = _split_decomposition(split, input_dir, lookups, args.top_limit)
        split_summaries.append(summary)
        all_missing.extend(missing_rows)
        all_wrong_rank.extend(wrong_rank_rows)
        all_buckets.extend(bucket_rows)

    ranked_rows = _ranked_gap_rows(all_buckets)
    next_candidate = _select_next_candidate(all_buckets)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "top80_missing_csv": str(output_prefix.with_name(output_prefix.name + "_top80_missing.csv")),
        "wrong_rank_csv": str(output_prefix.with_name(output_prefix.name + "_wrong_rank.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
        "ranked_gap_table_csv": str(output_prefix.with_name(output_prefix.name + "_ranked_gap_table.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.0 accuracy gap restart / decomposition",
        "read_only": True,
        "eval_only": True,
        "baseline_top80_decomposition": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "heldout_not_used_for_selection": True,
        "input_dir": str(input_dir),
        "splits_requested": args.splits,
        "splits": split_summaries,
        "next_candidate": next_candidate,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.0 only decomposes existing Top80 gaps. It selects at most a dev-only audit target for the next stage and does not train, tune, patch a family, or modify GoalSearcher.",
    }

    fields = [
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
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
        "top1_score",
        "top1_reasons",
        "top80_rows",
    ]
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["top80_missing_csv"]), all_missing, fields)
    _write_csv(Path(artifacts["wrong_rank_csv"]), all_wrong_rank, fields)
    _write_csv(Path(artifacts["buckets_csv"]), all_buckets, ["split", "status", "dimension", "key", "count", "rate_within_status"])
    _write_csv(Path(artifacts["ranked_gap_table_csv"]), ranked_rows, ["split", "status", "dimension", "key", "count", "rate_within_status"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "splits": [
                        {
                            "split": row["split"],
                            "groups": row["groups"],
                            "baseline_top1_rate": row["baseline_top1_rate"],
                            "top80_missing": row["top80_missing"],
                            "top80_missing_rate": row["top80_missing_rate"],
                            "wrong_rank": row["top80_present_but_wrong_rank"],
                            "wrong_rank_rate": row["top80_present_but_wrong_rank_rate"],
                            "wrong_rank_share_of_non_hit": row["wrong_rank_share_of_non_hit"],
                        }
                        for row in split_summaries
                    ],
                    "next_candidate": next_candidate,
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
