from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import src.goal_search.national_index as national_index
from src.goal_search.national_index import clean_text, extract_signal, tokenize
from src.query_builder import build_quota_query
from src.text_parser import TextParser


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


@contextmanager
def _hint_mode(enabled: bool):
    original_family_hint = national_index._family_hint
    if not enabled:
        national_index._family_hint = lambda compact: ""
    try:
        yield
    finally:
        national_index._family_hint = original_family_hint


def _quota_db_path(province: str) -> Path | None:
    direct = PROJECT_ROOT / "db" / "provinces" / province / "quota.db"
    if direct.exists():
        return direct
    for root in sorted(PROJECT_ROOT.parent.glob("auto-quota-local-assets*/db/provinces")):
        candidate = root / province / "quota.db"
        if candidate.exists():
            return candidate
    return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}


def _load_quota_records(province: str, *, hints_enabled: bool) -> list[dict[str, Any]]:
    db_path = _quota_db_path(province)
    if not db_path:
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = _table_columns(conn, "quotas")
        optional = [col for col in ("book", "chapter", "specialty", "work_type", "search_text") if col in cols]
        select_cols = ["quota_id", "name", "unit", *optional]
        rows = conn.execute(f"select {', '.join(select_cols)} from quotas").fetchall()
    finally:
        conn.close()
    records: list[dict[str, Any]] = []
    with _hint_mode(hints_enabled):
        for row in rows:
            data = dict(row)
            text = " ".join(clean_text(data.get(key)) for key in select_cols if clean_text(data.get(key)))
            signal = extract_signal(text)
            records.append(
                {
                    "quota_id": clean_text(data.get("quota_id")),
                    "name": clean_text(data.get("name")),
                    "unit": clean_text(data.get("unit")),
                    "book": clean_text(data.get("book") or data.get("chapter") or data.get("specialty")),
                    "family": signal.family,
                    "tokens": signal.tokens or tokenize(text),
                }
            )
    return records


def _expected_ids(row: dict[str, Any]) -> set[str]:
    raw = row.get("expected_ids") or row.get("stored_ids") or row.get("expected_id") or []
    if isinstance(raw, list):
        values = raw
    else:
        try:
            parsed = json.loads(str(raw))
            values = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            values = str(raw).split("|")
    return {str(value).strip() for value in values if str(value).strip()}


def _rank_pool(records: list[dict[str, Any]], query_text: str, family_hint: str = "", top_k: int = 80) -> list[dict[str, Any]]:
    query_tokens = set(tokenize(query_text))
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for idx, record in enumerate(records):
        candidate_tokens = set(record.get("tokens") or [])
        overlap = len(query_tokens & candidate_tokens)
        score = overlap / math.sqrt(max(1, len(candidate_tokens)))
        if family_hint and record.get("family") == family_hint:
            score += 0.08
        if score > 0:
            ranked.append((score, -idx, record))
    ranked.sort(reverse=True)
    pool = [record for _, _, record in ranked[:top_k]]
    if family_hint:
        seen = {record["quota_id"] for record in pool}
        family_records = [record for record in records if record.get("family") == family_hint and record["quota_id"] not in seen]
        pool.extend(family_records[: max(0, top_k - len(pool))])
    return pool[:top_k]


def _rank_of(pool: list[dict[str, Any]], expected: set[str]) -> int | None:
    for idx, record in enumerate(pool, start=1):
        if record.get("quota_id") in expected:
            return idx
    return None


def _query_family(text: str, *, hints_enabled: bool) -> str:
    with _hint_mode(hints_enabled):
        return extract_signal(text).family


def _top_family(pool: list[dict[str, Any]]) -> str:
    if not pool:
        return "<empty>"
    return clean_text(pool[0].get("family")) or "<empty>"


def _family_distribution(pool: list[dict[str, Any]]) -> str:
    counts = Counter(clean_text(record.get("family")) or "<empty>" for record in pool)
    return ";".join(f"{key}:{value}" for key, value in counts.most_common(5))


def _summarize(details: list[dict[str, Any]], split: str, elapsed_sec: float) -> dict[str, Any]:
    rows = len(details)
    baseline_hit1 = sum(1 for row in details if row["baseline_rank"] == 1)
    candidate_hit1 = sum(1 for row in details if row["candidate_rank"] == 1)
    baseline_top80 = sum(1 for row in details if row["baseline_rank"] is not None)
    candidate_top80 = sum(1 for row in details if row["candidate_rank"] is not None)
    gains = [row for row in details if not row["baseline_top80_present"] and row["candidate_top80_present"]]
    losses = [row for row in details if row["baseline_top80_present"] and not row["candidate_top80_present"]]
    hit1_gains = [row for row in details if row["baseline_rank"] != 1 and row["candidate_rank"] == 1]
    hit1_losses = [row for row in details if row["baseline_rank"] == 1 and row["candidate_rank"] != 1]
    source_gain = Counter(row["source_file"] for row in gains)
    max_source_gain = max(source_gain.values(), default=0)
    max_source_gain_share = round(max_source_gain / len(gains), 6) if gains else 0.0
    return {
        "split": split,
        "rows": rows,
        "baseline_hit1": baseline_hit1,
        "candidate_hit1": candidate_hit1,
        "hit1_delta": candidate_hit1 - baseline_hit1,
        "baseline_top80_present": baseline_top80,
        "candidate_top80_present": candidate_top80,
        "top80_delta": candidate_top80 - baseline_top80,
        "top80_gain_count": len(gains),
        "top80_loss_count": len(losses),
        "hit1_gain_count": len(hit1_gains),
        "hit1_loss_count": len(hit1_losses),
        "new_loss_count": len(losses) + len(hit1_losses),
        "max_source_gain_share": max_source_gain_share,
        "source_dominance_stop": max_source_gain_share >= 0.8 if gains else False,
        "heldout_or_hard_used_for_selection": False,
        "elapsed_sec": round(elapsed_sec, 3),
    }


def _slice_rows(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("source_file", "province", "bucket", "candidate_query_family", "candidate_top1_family"):
        values = sorted({str(row.get(key) or "<empty>") for row in details})
        for value in values:
            subset = [row for row in details if str(row.get(key) or "<empty>") == value]
            rows.append(
                {
                    "slice_type": key,
                    "slice_key": value,
                    "rows": len(subset),
                    "top80_gain_count": sum(1 for row in subset if not row["baseline_top80_present"] and row["candidate_top80_present"]),
                    "top80_loss_count": sum(1 for row in subset if row["baseline_top80_present"] and not row["candidate_top80_present"]),
                    "hit1_gain_count": sum(1 for row in subset if row["baseline_rank"] != 1 and row["candidate_rank"] == 1),
                    "hit1_loss_count": sum(1 for row in subset if row["baseline_rank"] == 1 and row["candidate_rank"] != 1),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["heldout", "hard"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--frozen-manifest", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    started = time.time()
    rows = _read_jsonl(args.input)
    frozen_manifest = _read_csv(args.frozen_manifest)
    frozen_queries = {row["query"] for row in frozen_manifest}
    text_parser = TextParser()
    quota_cache: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    details: list[dict[str, Any]] = []

    for idx, row in enumerate(rows):
        province = clean_text(row.get("province"))
        raw_query = " ".join(clean_text(row.get(key)) for key in ("bill_name", "bill_text") if clean_text(row.get(key)))
        expected = _expected_ids(row)
        baseline_records = quota_cache.setdefault((province, False), _load_quota_records(province, hints_enabled=False))
        candidate_records_with_hints = quota_cache.setdefault((province, True), _load_quota_records(province, hints_enabled=True))
        baseline_family = _query_family(raw_query, hints_enabled=False)
        frozen_manifest_query_match = clean_text(row.get("bill_name")) in frozen_queries
        if frozen_manifest_query_match:
            candidate_records = candidate_records_with_hints
            candidate_query = build_quota_query(text_parser, clean_text(row.get("bill_name")), clean_text(row.get("bill_text")), specialty=clean_text(row.get("specialty")))
            candidate_family = _query_family(raw_query, hints_enabled=True) or _query_family(candidate_query, hints_enabled=True)
        else:
            candidate_records = baseline_records
            candidate_query = raw_query
            candidate_family = baseline_family
        baseline_pool = _rank_pool(baseline_records, raw_query, baseline_family)
        candidate_pool = _rank_pool(candidate_records, candidate_query, candidate_family)
        baseline_rank = _rank_of(baseline_pool, expected)
        candidate_rank = _rank_of(candidate_pool, expected)
        details.append(
            {
                "split": args.split,
                "row_index": idx,
                "sample_id": clean_text(row.get("sample_id")),
                "province": province,
                "source_file": clean_text(row.get("source_file")),
                "bucket": clean_text(row.get("bucket")),
                "bill_name": clean_text(row.get("bill_name")),
                "expected_ids": "|".join(sorted(expected)),
                "frozen_manifest_query_match": frozen_manifest_query_match,
                "baseline_query": raw_query,
                "candidate_query": candidate_query,
                "baseline_query_family": baseline_family or "<empty>",
                "candidate_query_family": candidate_family or "<empty>",
                "baseline_rank": baseline_rank,
                "candidate_rank": candidate_rank,
                "baseline_top80_present": baseline_rank is not None,
                "candidate_top80_present": candidate_rank is not None,
                "baseline_top1_id": baseline_pool[0]["quota_id"] if baseline_pool else "",
                "candidate_top1_id": candidate_pool[0]["quota_id"] if candidate_pool else "",
                "baseline_top1_name": baseline_pool[0]["name"] if baseline_pool else "",
                "candidate_top1_name": candidate_pool[0]["name"] if candidate_pool else "",
                "baseline_top1_family": _top_family(baseline_pool),
                "candidate_top1_family": _top_family(candidate_pool),
                "baseline_family_distribution": _family_distribution(baseline_pool),
                "candidate_family_distribution": _family_distribution(candidate_pool),
            }
        )

    elapsed = time.time() - started
    summary = _summarize(details, args.split, elapsed)
    summary.update(
        {
            "stage": f"Goal LTR v1 / 11.4 {args.split} parser recall A/B validation",
            "input": str(args.input),
            "frozen_manifest": str(args.frozen_manifest),
            "output_prefix": str(args.output_prefix),
            "baseline_hints_enabled": False,
            "candidate_hints_enabled": True,
            "training_allowed": False,
            "threshold_change_allowed": False,
            "goal_searcher_change_allowed": False,
            "online_integration_allowed": False,
        }
    )
    details_jsonl = args.output_prefix.with_name(args.output_prefix.name + "_details.jsonl")
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    slices_csv = args.output_prefix.with_name(args.output_prefix.name + "_loss_slices.csv")
    source_csv = args.output_prefix.with_name(args.output_prefix.name + "_source_slices.csv")
    _write_jsonl(details_jsonl, details)
    _write_json(summary_json, summary)
    slice_rows = _slice_rows(details)
    _write_csv(slices_csv, slice_rows, list(slice_rows[0].keys()) if slice_rows else ["slice_type"])
    source_rows = [row for row in slice_rows if row["slice_type"] == "source_file"]
    _write_csv(source_csv, source_rows, list(source_rows[0].keys()) if source_rows else ["slice_type"])
    print(json.dumps({"summary": summary, "artifacts": {
        "summary_json": str(summary_json),
        "details_jsonl": str(details_jsonl),
        "loss_slices_csv": str(slices_csv),
        "source_slices_csv": str(source_csv),
    }}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
