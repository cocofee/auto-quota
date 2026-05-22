from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config  # noqa: E402
from src.goal_search.national_index import _apply_structured_values, extract_signal  # noqa: E402
from src.goal_search.searcher import _book_matches, _book_of_record, _quota_book  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_summary.md"
DEFAULT_MISSING_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_local_missing.csv"
DEFAULT_NOT_LOCAL_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_expected_not_local.csv"


@dataclass
class FeatureGroup:
    group_id: str
    rows: int = 0
    positive_count: int = 0
    positive_ranks: list[int] = field(default_factory=list)
    positive_ids: list[str] = field(default_factory=list)
    query_family: str = ""
    top1_id: str = ""
    top1_name: str = ""
    top1_family: str = ""
    top1_book: str = ""
    top1_chapter: str = ""
    top1_score: float | None = None


@dataclass
class LocalQuotaRecord:
    quota_id: str
    name: str
    unit: str = ""
    chapter: str = ""
    book: str = ""
    search_text: str = ""
    signal: Any = None


class ProvinceQuotaLookup:
    def __init__(self, province: str):
        self.province = province
        self.path = Path(config.get_quota_db_path(province))
        self.cache: dict[str, LocalQuotaRecord | None] = {}
        self.columns: set[str] | None = None

    def get_many(self, quota_ids: set[str]) -> tuple[list[LocalQuotaRecord], list[str]]:
        missing = sorted(qid for qid in quota_ids if qid not in self.cache)
        if missing:
            self._fetch_many(missing)
        records: list[LocalQuotaRecord] = []
        not_local: list[str] = []
        for qid in sorted(quota_ids):
            record = self.cache.get(qid)
            if record is None:
                not_local.append(qid)
            else:
                records.append(record)
        return records, not_local

    def _fetch_many(self, quota_ids: list[str]) -> None:
        for qid in quota_ids:
            self.cache.setdefault(qid, None)
        if not self.path.exists():
            return

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            if self.columns is None:
                self.columns = {row["name"] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            optional = [
                "work_type",
                "specialty",
                "chapter",
                "material",
                "connection",
                "dn",
                "cable_section",
                "circuits",
                "book",
                "search_text",
            ]
            select_cols = ["quota_id", "name", "unit"] + [col for col in optional if col in self.columns]
            placeholders = ",".join("?" for _ in quota_ids)
            rows = conn.execute(
                f"select {', '.join(select_cols)} from quotas where quota_id in ({placeholders})",
                quota_ids,
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            data = dict(row)
            search_text = " ".join(
                _clean(data.get(key))
                for key in ("quota_id", "name", "unit", "work_type", "specialty", "chapter", "material", "connection", "search_text")
                if _clean(data.get(key))
            )
            signal = extract_signal(search_text)
            _apply_structured_values(signal, data)
            record = LocalQuotaRecord(
                quota_id=_clean(data.get("quota_id")),
                name=_clean(data.get("name")),
                unit=_clean(data.get("unit")),
                chapter=_clean(data.get("chapter")),
                book=_clean(data.get("book")),
                search_text=search_text,
                signal=signal,
            )
            self.cache[record.quota_id] = record


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _expected_ids(row: dict[str, Any]) -> set[str]:
    values: list[str] = []
    raw = row.get("expected_ids") or row.get("expected_id") or row.get("quota_id") or row.get("stored_ids")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw)
    elif raw:
        text = str(raw)
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = text
        if isinstance(parsed, list):
            values.extend(str(item) for item in parsed)
        else:
            values.append(str(parsed))

    result: set[str] = set()
    for value in values:
        for part in str(value).split("|"):
            part = part.strip()
            if part:
                result.add(part)
    return result


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _bucket_key(*parts: Any) -> str:
    return "|".join(_clean(part) or "<empty>" for part in parts)


def _load_feature_groups(path: Path) -> dict[str, FeatureGroup]:
    groups: dict[str, FeatureGroup] = {}
    for row in _iter_jsonl(path):
        group_id = _clean(row.get("group_id"))
        if not group_id:
            continue
        group = groups.setdefault(group_id, FeatureGroup(group_id=group_id))
        group.rows += 1
        rank = int(float(row.get("candidate_rank") or group.rows))
        label = int(float(row.get("label") or 0))
        if label > 0:
            group.positive_count += 1
            group.positive_ranks.append(rank)
            qid = _clean(row.get("quota_id"))
            if qid:
                group.positive_ids.append(qid)
        if rank == 1:
            group.query_family = _clean(row.get("query_family"))
            group.top1_id = _clean(row.get("quota_id"))
            group.top1_name = _clean(row.get("quota_name"))
            group.top1_family = _clean(row.get("candidate_family"))
            group.top1_book = _clean(row.get("quota_book"))
            group.top1_chapter = _clean(row.get("quota_chapter"))
            try:
                group.top1_score = float(row.get("current_score") or 0.0)
            except (TypeError, ValueError):
                group.top1_score = None
    return groups


def _query_family(meta: dict[str, Any], feature_group: FeatureGroup | None) -> str:
    if feature_group and feature_group.query_family:
        return feature_group.query_family
    text = " ".join(_clean(meta.get(key)) for key in ("query", "bill_name", "bill_text", "specialty", "unit") if _clean(meta.get(key)))
    return _clean(extract_signal(text).family)


def _record_books(records: list[Any], fallback_ids: set[str] | None = None) -> list[str]:
    books = sorted({_book_of_record(record) or _quota_book(record.quota_id) for record in records if record})
    if not books and fallback_ids:
        books = sorted({_quota_book(qid) for qid in fallback_ids if _quota_book(qid)})
    return [book for book in books if book]


def _record_families(records: list[Any]) -> list[str]:
    return sorted({record.signal.family for record in records if getattr(record, "signal", None) and record.signal.family})


def _record_names(records: list[Any], limit: int = 3) -> str:
    names = [f"{record.quota_id} {record.name}" for record in records[:limit]]
    return " || ".join(names)


def _same_any_book(expected_books: list[str], top_book: str) -> bool:
    return any(_book_matches(book, top_book) or book == top_book for book in expected_books if book and top_book)


def _local_missing_reason(
    *,
    query_family: str,
    expected_families: list[str],
    expected_books: list[str],
    top1_family: str,
    top1_book: str,
) -> str:
    if expected_families and top1_family and top1_family not in expected_families:
        return "top1_wrong_family"
    if query_family and expected_families and query_family not in expected_families:
        return "query_family_mismatch"
    if expected_books and top1_book and not _same_any_book(expected_books, top1_book):
        return "top1_wrong_book"
    if not query_family:
        return "query_family_empty"
    if not top1_family:
        return "top1_family_empty"
    return "same_family_or_unknown_top80_gap"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "split",
        "status",
        "reason",
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
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_score",
        "top80_rows",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _summarize_family_table(rows: list[dict[str, Any]], total_by_family: Counter[str], limit: int) -> list[dict[str, Any]]:
    miss_by_family = Counter(row["query_family"] or "<empty>" for row in rows)
    table: list[dict[str, Any]] = []
    for family, miss_count in miss_by_family.most_common(limit):
        total = total_by_family.get(family, 0)
        table.append(
            {
                "query_family": family,
                "local_missing_top80": miss_count,
                "total_groups": total,
                "local_missing_rate": _rate(miss_count, total),
            }
        )
    return table


def _audit_split(
    *,
    split: str,
    data_dir: Path,
    lookups: dict[str, ProvinceQuotaLookup],
    top_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    meta_path = data_dir / f"ltr_group_{split}.jsonl"
    feature_path = data_dir / f"ltr_features_{split}.jsonl"
    metas = _read_jsonl(meta_path)
    feature_groups = _load_feature_groups(feature_path)

    status_counts: Counter[str] = Counter()
    total_by_query_family: Counter[str] = Counter()
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    local_missing_rows: list[dict[str, Any]] = []
    expected_not_local_rows: list[dict[str, Any]] = []
    positive_ranks: list[int] = []

    for meta in metas:
        group_id = _clean(meta.get("group_id"))
        feature_group = feature_groups.get(group_id)
        expected = _expected_ids(meta)
        province = _clean(meta.get("province"))
        query_family = _query_family(meta, feature_group)
        total_by_query_family[query_family or "<empty>"] += 1

        if province not in lookups:
            lookups[province] = ProvinceQuotaLookup(province)
        local_records, not_local_ids = lookups[province].get_many(expected)
        expected_local_ids = {record.quota_id for record in local_records}
        expected_families = _record_families(local_records)
        expected_books = _record_books(local_records, expected if not local_records else None)
        top1_family = feature_group.top1_family if feature_group else ""
        top1_book = feature_group.top1_book if feature_group else ""
        positive_count = feature_group.positive_count if feature_group else 0

        if positive_count > 0:
            status = "has_expected_in_top80"
            positive_ranks.extend(feature_group.positive_ranks)
        elif not local_records:
            status = "expected_not_in_local_db"
        else:
            status = "local_expected_missing_top80"
        status_counts[status] += 1

        counters["status_by_province"][_bucket_key(province, status)] += 1
        counters["status_by_source"][_bucket_key(_clean(meta.get("source_file")), status)] += 1
        counters["status_by_query_family"][_bucket_key(query_family, status)] += 1

        if status == "local_expected_missing_top80":
            reason = _local_missing_reason(
                query_family=query_family,
                expected_families=expected_families,
                expected_books=expected_books,
                top1_family=top1_family,
                top1_book=top1_book,
            )
            counters["local_missing_reason"][reason] += 1
            counters["local_missing_query_family"][query_family or "<empty>"] += 1
            counters["local_missing_expected_family"][",".join(expected_families) or "<empty>"] += 1
            counters["local_missing_expected_book"][",".join(expected_books) or "<empty>"] += 1
            counters["local_missing_top1_family"][top1_family or "<empty>"] += 1
            counters["local_missing_top1_book"][top1_book or "<empty>"] += 1
            counters["local_missing_province"][province or "<empty>"] += 1
            counters["local_missing_source"][_clean(meta.get("source_file")) or "<empty>"] += 1
            counters["local_missing_family_book"][_bucket_key(",".join(expected_families), ",".join(expected_books))] += 1
            local_missing_rows.append(
                {
                    "split": split,
                    "status": status,
                    "reason": reason,
                    "group_id": group_id,
                    "sample_id": _clean(meta.get("sample_id")),
                    "source_file": _clean(meta.get("source_file")),
                    "project_name": _clean(meta.get("project_name")),
                    "province": province,
                    "query": _clean(meta.get("query")),
                    "query_family": query_family,
                    "expected_ids": "|".join(sorted(expected)),
                    "expected_local_ids": "|".join(sorted(expected_local_ids)),
                    "expected_not_local_ids": "|".join(not_local_ids),
                    "expected_families": ",".join(expected_families),
                    "expected_books": ",".join(expected_books),
                    "expected_names": _record_names(local_records),
                    "top1_id": feature_group.top1_id if feature_group else "",
                    "top1_name": feature_group.top1_name if feature_group else "",
                    "top1_family": top1_family,
                    "top1_book": top1_book,
                    "top1_chapter": feature_group.top1_chapter if feature_group else "",
                    "top1_score": feature_group.top1_score if feature_group else "",
                    "top80_rows": feature_group.rows if feature_group else 0,
                }
            )

        if status == "expected_not_in_local_db":
            fallback_books = _record_books([], expected)
            counters["not_local_expected_book"][",".join(fallback_books) or "<empty>"] += 1
            counters["not_local_query_family"][query_family or "<empty>"] += 1
            counters["not_local_province"][province or "<empty>"] += 1
            counters["not_local_source"][_clean(meta.get("source_file")) or "<empty>"] += 1
            expected_not_local_rows.append(
                {
                    "split": split,
                    "status": status,
                    "reason": "expected_id_absent_from_target_quota_db",
                    "group_id": group_id,
                    "sample_id": _clean(meta.get("sample_id")),
                    "source_file": _clean(meta.get("source_file")),
                    "project_name": _clean(meta.get("project_name")),
                    "province": province,
                    "query": _clean(meta.get("query")),
                    "query_family": query_family,
                    "expected_ids": "|".join(sorted(expected)),
                    "expected_local_ids": "",
                    "expected_not_local_ids": "|".join(not_local_ids),
                    "expected_families": "",
                    "expected_books": ",".join(fallback_books),
                    "expected_names": "",
                    "top1_id": feature_group.top1_id if feature_group else "",
                    "top1_name": feature_group.top1_name if feature_group else "",
                    "top1_family": top1_family,
                    "top1_book": top1_book,
                    "top1_chapter": feature_group.top1_chapter if feature_group else "",
                    "top1_score": feature_group.top1_score if feature_group else "",
                    "top80_rows": feature_group.rows if feature_group else 0,
                }
            )

    total = len(metas)
    local_denominator = status_counts["has_expected_in_top80"] + status_counts["local_expected_missing_top80"]
    summary = {
        "split": split,
        "groups": total,
        "has_expected_in_top80": status_counts["has_expected_in_top80"],
        "top80_recall_ceiling_rate": _rate(status_counts["has_expected_in_top80"], total),
        "expected_not_in_local_db": status_counts["expected_not_in_local_db"],
        "expected_not_in_local_db_rate": _rate(status_counts["expected_not_in_local_db"], total),
        "local_expected_missing_top80": status_counts["local_expected_missing_top80"],
        "local_expected_missing_top80_rate": _rate(status_counts["local_expected_missing_top80"], total),
        "local_adjusted_top80_recall_rate": _rate(status_counts["has_expected_in_top80"], local_denominator),
        "positive_rank_avg": round(sum(positive_ranks) / len(positive_ranks), 3) if positive_ranks else None,
        "positive_rank_max": max(positive_ranks) if positive_ranks else None,
        "status_counts": dict(status_counts),
        "local_missing_reason": _top_items(counters["local_missing_reason"], top_limit),
        "local_missing_by_query_family": _top_items(counters["local_missing_query_family"], top_limit),
        "local_missing_by_expected_family": _top_items(counters["local_missing_expected_family"], top_limit),
        "local_missing_by_expected_book": _top_items(counters["local_missing_expected_book"], top_limit),
        "local_missing_by_top1_family": _top_items(counters["local_missing_top1_family"], top_limit),
        "local_missing_by_top1_book": _top_items(counters["local_missing_top1_book"], top_limit),
        "local_missing_by_province": _top_items(counters["local_missing_province"], top_limit),
        "local_missing_by_source": _top_items(counters["local_missing_source"], top_limit),
        "local_missing_by_expected_family_book": _top_items(counters["local_missing_family_book"], top_limit),
        "expected_not_local_by_query_family": _top_items(counters["not_local_query_family"], top_limit),
        "expected_not_local_by_book": _top_items(counters["not_local_expected_book"], top_limit),
        "expected_not_local_by_province": _top_items(counters["not_local_province"], top_limit),
        "expected_not_local_by_source": _top_items(counters["not_local_source"], top_limit),
        "query_family_local_missing_table": _summarize_family_table(local_missing_rows, total_by_query_family, top_limit),
    }
    return summary, local_missing_rows, expected_not_local_rows


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["splits"]
    lines = [
        "# Goal Top80 Recall Audit",
        "",
        "Stage 3.2 read-only audit. No model tuning, no ranking change, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                [
                    "split",
                    "groups",
                    "top80_ceiling",
                    "local_adjusted_ceiling",
                    "not_local",
                    "local_missing_top80",
                    "positive_rank_avg",
                ],
                *[
                    [
                        item["split"],
                        item["groups"],
                        item["top80_recall_ceiling_rate"],
                        item["local_adjusted_top80_recall_rate"],
                        item["expected_not_in_local_db"],
                        item["local_expected_missing_top80"],
                        item["positive_rank_avg"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Local Expected Missing Top80",
        "",
    ]
    for item in summaries:
        lines.extend(
            [
                f"### {item['split']}",
                "",
                "Reason:",
                "",
                _md_table(_counter_table(item["local_missing_reason"])),
                "",
                "Query family:",
                "",
                _md_table(_counter_table(item["local_missing_by_query_family"])),
                "",
                "Expected family:",
                "",
                _md_table(_counter_table(item["local_missing_by_expected_family"])),
                "",
                "Expected book:",
                "",
                _md_table(_counter_table(item["local_missing_by_expected_book"])),
                "",
                "Province:",
                "",
                _md_table(_counter_table(item["local_missing_by_province"])),
                "",
                "Source:",
                "",
                _md_table(_counter_table(item["local_missing_by_source"])),
                "",
            ]
        )
    lines.extend(["## Expected Not In Local DB", ""])
    for item in summaries:
        lines.extend(
            [
                f"### {item['split']}",
                "",
                "Query family:",
                "",
                _md_table(_counter_table(item["expected_not_local_by_query_family"])),
                "",
                "Book from expected id:",
                "",
                _md_table(_counter_table(item["expected_not_local_by_book"])),
                "",
                "Province:",
                "",
                _md_table(_counter_table(item["expected_not_local_by_province"])),
                "",
            ]
        )
    lines.extend(
        [
            "## Artifacts",
            "",
            _md_table(
                [
                    ["artifact", "path"],
                    ["local_missing_csv", report["local_missing_csv"]],
                    ["expected_not_local_csv", report["expected_not_local_csv"]],
                ]
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Goal Search Top80 recall ceiling from existing LTR feature rows")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--top-limit", type=int, default=20)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--local-missing-csv", default=str(DEFAULT_MISSING_CSV))
    parser.add_argument("--expected-not-local-csv", default=str(DEFAULT_NOT_LOCAL_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    lookups: dict[str, ProvinceQuotaLookup] = {}
    summaries: list[dict[str, Any]] = []
    all_local_missing: list[dict[str, Any]] = []
    all_expected_not_local: list[dict[str, Any]] = []

    for split in args.splits:
        summary, local_missing, expected_not_local = _audit_split(
            split=split,
            data_dir=data_dir,
            lookups=lookups,
            top_limit=args.top_limit,
        )
        summaries.append(summary)
        all_local_missing.extend(local_missing)
        all_expected_not_local.extend(expected_not_local)

    local_missing_csv = Path(args.local_missing_csv)
    expected_not_local_csv = Path(args.expected_not_local_csv)
    _write_csv(local_missing_csv, all_local_missing)
    _write_csv(expected_not_local_csv, all_expected_not_local)

    report = {
        "stage": "Goal LTR v1 / stage 3.2 Top80 recall ceiling audit",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": str(data_dir),
        "splits_requested": args.splits,
        "province_lookup_count": len(lookups),
        "local_missing_csv": str(local_missing_csv),
        "expected_not_local_csv": str(expected_not_local_csv),
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
                    "splits": args.splits,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "splits": [
                    {
                        "split": item["split"],
                        "groups": item["groups"],
                        "top80_recall_ceiling_rate": item["top80_recall_ceiling_rate"],
                        "local_adjusted_top80_recall_rate": item["local_adjusted_top80_recall_rate"],
                        "expected_not_in_local_db": item["expected_not_in_local_db"],
                        "local_expected_missing_top80": item["local_expected_missing_top80"],
                    }
                    for item in summaries
                ],
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                    "local_missing_csv": str(local_missing_csv),
                    "expected_not_local_csv": str(expected_not_local_csv),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
