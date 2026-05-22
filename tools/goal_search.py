from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.goal_search import GoalSearcher  # noqa: E402


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


def _load_items(path: str | None) -> list[dict]:
    if not path:
        return []
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        return _read_jsonl(source)
    if source.suffix.lower() == ".csv":
        return _read_csv(source)
    raise ValueError("input only supports .jsonl or .csv")


def _expected_ids(row: dict) -> set[str]:
    values = []
    for key in ("expected_id", "quota_id", "correct_quota_id", "positive_id"):
        if row.get(key):
            values.append(str(row[key]))
    for key in ("expected_ids", "oracle_quota_ids"):
        raw = row.get(key)
        if not raw:
            continue
        if isinstance(raw, list):
            values.extend(str(v) for v in raw)
            continue
        try:
            parsed = json.loads(str(raw))
        except Exception:
            parsed = [raw]
        if isinstance(parsed, list):
            values.extend(str(v) for v in parsed)
        elif isinstance(parsed, str) and "|" in parsed:
            values.extend(part for part in parsed.split("|"))
    split_values = []
    for value in values:
        if "|" in value:
            split_values.extend(value.split("|"))
        else:
            split_values.append(value)
    return {v.strip() for v in split_values if v and v.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone goal-mode quota search")
    parser.add_argument("--province", required=True, help="Province/quota library name or short alias")
    parser.add_argument("--query", default="", help="Single query text")
    parser.add_argument("--unit", default="", help="Single query unit")
    parser.add_argument("--input", default="", help="Batch input: jsonl/csv")
    parser.add_argument("--limit", type=int, default=100, help="Max batch rows")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--jsonl", action="store_true", help="Print JSONL instead of compact table")
    parser.add_argument("--allow-answer-priors", action="store_true", help="Allow experience/shadow answer priors")
    args = parser.parse_args()

    searcher = GoalSearcher(args.province)
    rows = _load_items(args.input)
    if args.query:
        rows = [{"bill_name": args.query, "bill_text": args.query, "unit": args.unit}]
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("no query or input rows supplied")
    for row in rows:
        if isinstance(row, dict) and args.allow_answer_priors:
            row["goal_allow_answer_priors"] = True

    started = time.perf_counter()
    hit1 = 0
    judged = 0
    for idx, row in enumerate(rows, 1):
        hits = searcher.search(row, top_k=args.top_k)
        expected = _expected_ids(row)
        if expected:
            judged += 1
            if hits and hits[0].quota_id in expected:
                hit1 += 1
        payload = {
            "index": idx,
            "query": row.get("bill_name") or row.get("name") or row.get("bill_text") or row.get("description") or "",
            "expected_ids": sorted(expected),
            "top": [asdict(hit) for hit in hits],
        }
        if args.jsonl:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            top = hits[0] if hits else None
            marker = ""
            if expected:
                marker = "OK" if top and top.quota_id in expected else "MISS"
            print(
                f"{idx:>4} {marker:<4} "
                f"{top.quota_id if top else '-':<16} "
                f"{top.confidence if top else 0:>5} "
                f"{top.name if top else 'no result'}"
            )

    elapsed = time.perf_counter() - started
    summary = {
        "rows": len(rows),
        "judged": judged,
        "hit1": hit1,
        "hit1_rate": round(hit1 / judged, 4) if judged else None,
        "elapsed_sec": round(elapsed, 3),
        "rows_per_sec": round(len(rows) / elapsed, 3) if elapsed > 0 else None,
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
