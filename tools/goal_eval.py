from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.goal_search import GoalSearcher  # noqa: E402
from src.goal_search.national_index import extract_signal  # noqa: E402
from src.goal_search.searcher import _apply_strong_name_signal, _book_matches, _quota_book  # noqa: E402


def _signal_snapshot(record) -> dict:
    signal = getattr(record, "signal", None)
    if signal is None:
        return {}
    return {
        "family": signal.family,
        "action": signal.action,
        "material": signal.material,
        "connection": signal.connection,
        "install_method": signal.install_method,
        "dn": signal.dn,
        "cable_section": signal.cable_section,
        "cable_cores": signal.cable_cores,
        "circuits": signal.circuits,
        "concrete_grade": signal.concrete_grade,
        "thickness": signal.thickness,
        "book": getattr(record, "book", "") or _quota_book(getattr(record, "quota_id", "")),
    }


def _query_signal_snapshot(row: dict) -> dict:
    text = " ".join(
        str(row.get(key) or "").strip()
        for key in ("bill_name", "name", "bill_text", "description", "specialty", "unit")
        if str(row.get(key) or "").strip()
    )
    signal = _apply_strong_name_signal(extract_signal(text), row.get("bill_name") or row.get("name") or "")
    return {
        "family": signal.family,
        "action": signal.action,
        "material": signal.material,
        "connection": signal.connection,
        "install_method": signal.install_method,
        "dn": signal.dn,
        "cable_section": signal.cable_section,
        "cable_cores": signal.cable_cores,
        "circuits": signal.circuits,
        "concrete_grade": signal.concrete_grade,
        "thickness": signal.thickness,
    }


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _read_jsonl(path)
    if suffix == ".csv":
        return _read_csv(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            rows: list[dict] = []
            for province_result in payload.get("results") or payload.get("json_results") or []:
                if not isinstance(province_result, dict):
                    continue
                province = province_result.get("province")
                for detail in province_result.get("details") or []:
                    if not isinstance(detail, dict):
                        continue
                    row = dict(detail)
                    row.setdefault("province", province)
                    if "stored_ids" in row and "expected_ids" not in row:
                        row["expected_ids"] = row.get("stored_ids")
                    if "stored_names" in row and "expected_names" not in row:
                        row["expected_names"] = row.get("stored_names")
                    rows.append(row)
            return rows
    raise ValueError("input only supports .jsonl, .json, or .csv")


def _split_arg(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _expected_ids(row: dict) -> set[str]:
    values: list[str] = []
    for key in ("expected_id", "quota_id", "correct_quota_id", "positive_id"):
        if row.get(key):
            values.append(str(row[key]))
    for key in ("expected_ids", "oracle_quota_ids", "expected_quota_ids", "stored_ids"):
        raw = row.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            values.extend(str(value) for value in raw)
            continue
        try:
            parsed = json.loads(str(raw))
        except Exception:
            parsed = raw
        if isinstance(parsed, list):
            values.extend(str(value) for value in parsed)
        else:
            values.append(str(parsed))
    split_values: list[str] = []
    for value in values:
        split_values.extend(part for part in value.split("|") if part.strip())
    return {value.strip() for value in split_values if value.strip()}


def _row_province(row: dict) -> str:
    return str(row.get("province") or row.get("quota_province") or "").strip()


def _row_id(row: dict, fallback: int) -> str:
    return str(row.get("sample_id") or row.get("bill_id") or row.get("idx") or fallback)


def _counter_key(*parts: object) -> str:
    return "|".join(str(part or "<empty>") for part in parts)


def _top_items(counter: Counter[str], limit: int) -> list[dict]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _skip_row(row: dict, args: argparse.Namespace) -> bool:
    checks = {
        "sample_id": _split_arg(args.exclude_sample_id),
        "source_file": _split_arg(args.exclude_source_file),
        "project_name": _split_arg(args.exclude_project_name),
    }
    for field, excluded in checks.items():
        if excluded and str(row.get(field) or "").strip() in excluded:
            return True
    return False


def _with_leakage_controls(row: dict, args: argparse.Namespace) -> dict:
    item = dict(row)
    item["goal_no_answer_priors"] = not bool(args.allow_answer_priors)
    item["goal_excluded_sources"] = {
        "sample_id": {str(row.get("sample_id") or "").strip()} | _split_arg(args.exclude_sample_id),
        "source_file": {str(row.get("source_file") or "").strip()} | _split_arg(args.exclude_source_file),
        "project_name": {str(row.get("project_name") or "").strip()} | _split_arg(args.exclude_project_name),
    }
    return item


def _miss_reason(searcher: GoalSearcher, row: dict, expected: set[str], hits) -> str:
    top_ids = [hit.quota_id for hit in hits]
    if expected & set(top_ids):
        return "wrong_rank"
    known_expected = [searcher.index.by_quota_id[qid] for qid in expected if qid in searcher.index.by_quota_id]
    if not known_expected:
        return "expected_not_in_local_db"
    if not hits:
        return "no_candidates"

    top = searcher.index.by_quota_id.get(hits[0].quota_id)
    if top is None:
        return "top_not_in_local_db"
    expected_families = {record.signal.family for record in known_expected if record.signal.family}
    if expected_families and top.signal.family and top.signal.family not in expected_families:
        return "wrong_family"

    requested_book = str(row.get("specialty") or "").strip().upper()
    if requested_book:
        top_book = (top.book or _quota_book(top.quota_id)).upper()
        expected_book_match = any(_book_matches(requested_book, (record.book or _quota_book(record.quota_id)).upper()) for record in known_expected)
        if expected_book_match and top_book and not _book_matches(requested_book, top_book):
            return "wrong_book"

    for key in ("dn", "cable_section", "circuits", "concrete_grade", "thickness"):
        top_value = getattr(top.signal, key)
        expected_values = {getattr(record.signal, key) for record in known_expected if getattr(record.signal, key) is not None}
        if top_value is not None and expected_values and top_value not in expected_values:
            return "wrong_param_tier"
    return "wrong_other"


def _update_buckets(
    buckets: dict[str, Counter[str]],
    *,
    detail: dict,
    expected_records: list,
    top_record,
) -> None:
    if detail.get("hit1"):
        return
    reason = str(detail.get("miss_reason") or "")
    province = str(detail.get("province") or "")
    query_family = str((detail.get("query_signal") or {}).get("family") or "")
    top_family = str((detail.get("top_signal") or {}).get("family") or "")
    top_book = str((detail.get("top_signal") or {}).get("book") or "")
    expected_families = sorted({record.signal.family for record in expected_records if record.signal.family})
    expected_books = sorted({(record.book or _quota_book(record.quota_id)).upper() for record in expected_records})
    expected_family = ",".join(expected_families)
    expected_book = ",".join(expected_books)

    buckets["miss_reason"][reason] += 1
    buckets["province_miss"][ _counter_key(province, reason)] += 1
    buckets["query_family_miss"][_counter_key(query_family, reason)] += 1
    buckets["province_family_miss"][_counter_key(province, query_family, reason)] += 1
    buckets["family_confusion"][_counter_key(expected_family, top_family, reason)] += 1
    buckets["book_confusion"][_counter_key(expected_book, top_book, reason)] += 1
    rank = detail.get("expected_rank")
    buckets["expected_rank"][str(rank if rank is not None else "not_in_topk")] += 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate standalone goal-mode quota search")
    parser.add_argument("--input", required=True, help="Input JSONL/CSV")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--allow-answer-priors", action="store_true", help="Allow experience/shadow answer priors")
    parser.add_argument("--exclude-sample-id", default="", help="Comma-separated sample_id values to skip/exclude")
    parser.add_argument("--exclude-source-file", default="", help="Comma-separated source_file values to skip/exclude")
    parser.add_argument("--exclude-project-name", default="", help="Comma-separated project_name values to skip/exclude")
    parser.add_argument("--jsonl", action="store_true", help="Emit per-row JSONL details")
    parser.add_argument("--details-output", default="", help="Write per-row JSONL details to this path")
    parser.add_argument("--bucket-output", default="", help="Write compact bucket report JSON to this path")
    parser.add_argument("--bucket-top", type=int, default=30, help="Max rows per bucket in bucket-output")
    args = parser.parse_args()

    rows = [row for row in _load_rows(Path(args.input)) if not _skip_row(row, args)]
    if args.limit > 0:
        rows = rows[: args.limit]

    searchers: dict[str, GoalSearcher] = {}
    started = time.perf_counter()
    judged = hit1 = hit5 = 0
    miss_samples: list[dict] = []
    province_counts: Counter[str] = Counter()
    miss_reasons: Counter[str] = Counter()
    buckets: dict[str, Counter[str]] = {
        "miss_reason": Counter(),
        "province_miss": Counter(),
        "query_family_miss": Counter(),
        "province_family_miss": Counter(),
        "family_confusion": Counter(),
        "book_confusion": Counter(),
        "expected_rank": Counter(),
    }
    detail_handle = None
    if args.details_output:
        detail_path = Path(args.details_output)
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_handle = detail_path.open("w", encoding="utf-8")

    try:
        for idx, row in enumerate(rows, 1):
            province = _row_province(row)
            expected = _expected_ids(row)
            if not province or not expected:
                continue
            if province not in searchers:
                searchers[province] = GoalSearcher(province)
            item = _with_leakage_controls(row, args)
            hits = searchers[province].search(item, top_k=args.top_k)
            top_ids = [hit.quota_id for hit in hits]
            judged += 1
            province_counts[province] += 1
            row_hit1 = bool(top_ids and top_ids[0] in expected)
            row_hit5 = bool(expected & set(top_ids))
            hit1 += int(row_hit1)
            hit5 += int(row_hit5)
            expected_records = [
                searchers[province].index.by_quota_id[qid]
                for qid in sorted(expected)
                if qid in searchers[province].index.by_quota_id
            ]
            top_record = searchers[province].index.by_quota_id.get(top_ids[0]) if top_ids else None
            detail = {
                "index": idx,
                "sample_id": _row_id(row, idx),
                "source_file": row.get("source_file") or "",
                "project_name": row.get("project_name") or "",
                "bucket": row.get("bucket") or row.get("target_bucket") or row.get("probe_bucket") or "",
                "province": province,
                "query": row.get("bill_name") or row.get("name") or row.get("bill_text") or "",
                "expected_ids": sorted(expected),
                "top_ids": top_ids,
                "hit1": row_hit1,
                "hit5": row_hit5,
                "query_signal": _query_signal_snapshot(row),
                "expected_rank": next((rank for rank, quota_id in enumerate(top_ids, 1) if quota_id in expected), None),
                "top_signal": _signal_snapshot(top_record) if top_record else {},
                "expected_signals": [
                    {"quota_id": record.quota_id, "name": record.name, **_signal_snapshot(record)}
                    for record in expected_records[:3]
                ],
            }
            if not row_hit1:
                reason = _miss_reason(searchers[province], row, expected, hits)
                detail["miss_reason"] = reason
                miss_reasons[reason] += 1
                _update_buckets(buckets, detail=detail, expected_records=expected_records, top_record=top_record)
            if args.jsonl:
                print(json.dumps(detail, ensure_ascii=False))
            if detail_handle is not None:
                detail_handle.write(json.dumps(detail, ensure_ascii=False) + "\n")
            if not row_hit1 and len(miss_samples) < 20:
                sample = dict(detail)
                sample["top"] = [
                    {
                        "quota_id": hit.quota_id,
                        "name": hit.name,
                        "score": hit.score,
                        "reasons": hit.reasons,
                    }
                    for hit in hits[:3]
                ]
                miss_samples.append(sample)
    finally:
        if detail_handle is not None:
            detail_handle.close()

    elapsed = time.perf_counter() - started
    summary = {
        "rows": len(rows),
        "judged": judged,
        "hit1": hit1,
        "hit1_rate": round(hit1 / judged, 4) if judged else None,
        "hit5": hit5,
        "hit5_rate": round(hit5 / judged, 4) if judged else None,
        "elapsed_sec": round(elapsed, 3),
        "rows_per_sec": round(judged / elapsed, 3) if elapsed > 0 else None,
        "searcher_count": len(searchers),
            "province_counts": dict(province_counts),
        "miss_reasons": dict(miss_reasons),
        "miss_samples": miss_samples,
        "answer_priors_allowed": bool(args.allow_answer_priors),
    }
    if args.bucket_output:
        bucket_path = Path(args.bucket_output)
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        bucket_report = {
            "summary": {key: value for key, value in summary.items() if key != "miss_samples"},
            "buckets": {name: _top_items(counter, args.bucket_top) for name, counter in buckets.items()},
        }
        bucket_path.write_text(json.dumps(bucket_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
