# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_PATH = PROJECT_ROOT / "output" / "real_eval" / "nondet_fetch64_run1.details.jsonl"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "output" / "real_eval" / "retriever_miss_classification"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def normalize_book_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    match = re.match(r"^C0*(\d+)$", raw)
    if match:
        return f"C{int(match.group(1))}"
    match = re.match(r"^0*(\d+)$", raw)
    if match:
        return f"C{int(match.group(1))}"
    return raw


def quota_id_to_book(quota_id: object) -> str:
    raw = str(quota_id or "").strip()
    if not raw:
        return ""
    match = re.match(r"^(C\d+)-", raw, re.IGNORECASE)
    if match:
        return normalize_book_code(match.group(1))
    match = re.match(r"^(\d+)-", raw)
    if match:
        return normalize_book_code(match.group(1))
    match = re.match(r"^([A-Z]{1,3}\d*)-", raw)
    if match:
        return normalize_book_code(match.group(1))
    return ""


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def extract_search_books(record: dict) -> list[str]:
    retriever = dict(record.get("retriever") or {})
    resolution = dict(retriever.get("search_resolution") or {})
    calls = list(resolution.get("calls") or [])

    candidates = []
    has_main_resolution = False
    for call in calls:
        if str((call or {}).get("target") or "").strip() != "main":
            continue
        has_main_resolution = True
        candidates.extend((call or {}).get("resolved_books") or [])

    if not has_main_resolution:
        router = dict(record.get("router") or {})
        classification = dict(router.get("classification") or {})
        unified_plan = dict(router.get("unified_plan") or {})

        candidates.extend(router.get("search_books") or [])
        candidates.extend(classification.get("search_books") or [])
        candidates.extend(unified_plan.get("hard_books") or [])
        candidates.extend(unified_plan.get("preferred_books") or [])
        primary_book = unified_plan.get("primary_book")
        if primary_book:
            candidates.append(primary_book)
    books: list[str] = []
    seen: set[str] = set()
    for normalized in (normalize_book_code(value) for value in candidates):
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        books.append(normalized)
    return books


class QuotaIndexProbe:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._connections: dict[str, sqlite3.Connection | None] = {}

    def _get_connection(self, province: str) -> sqlite3.Connection | None:
        if province not in self._connections:
            db_path = self.root / "db" / "provinces" / province / "quota.db"
            self._connections[province] = sqlite3.connect(str(db_path)) if db_path.exists() else None
        return self._connections[province]

    def oracle_present(self, province: str, oracle_ids: list[str]) -> bool:
        if not oracle_ids:
            return False
        conn = self._get_connection(province)
        if conn is None:
            return False
        cursor = conn.cursor()
        for quota_id in oracle_ids:
            hit = cursor.execute(
                "SELECT 1 FROM quotas WHERE quota_id = ? LIMIT 1",
                (quota_id,),
            ).fetchone()
            if hit:
                return True
        return False

    def oracle_books(self, province: str, oracle_ids: list[str]) -> list[str]:
        if not oracle_ids:
            return []
        conn = self._get_connection(province)
        if conn is None:
            return []
        cursor = conn.cursor()
        books: list[str] = []
        for quota_id in oracle_ids:
            row = cursor.execute(
                "SELECT book FROM quotas WHERE quota_id = ? LIMIT 1",
                (quota_id,),
            ).fetchone()
            normalized = normalize_book_code(row[0] if row else "")
            if normalized:
                books.append(normalized)
        return sorted(set(books))

    def close(self) -> None:
        for conn in self._connections.values():
            if conn is not None:
                conn.close()
        self._connections.clear()


class AssetOverlapProbe:
    def __init__(self, root: Path) -> None:
        self.experience_conn = sqlite3.connect(str(root / "db" / "common" / "experience.db"))
        self.universal_kb_conn = sqlite3.connect(str(root / "db" / "common" / "universal_kb.db"))

    def experience_exact_hit(
        self,
        *,
        province: str,
        oracle_ids: list[str],
        bill_name: str,
        bill_text: str,
    ) -> bool:
        if not oracle_ids:
            return False
        full_text = _clean_text(" ".join(part for part in [bill_name, bill_text] if part))
        cursor = self.experience_conn.cursor()
        for quota_id in oracle_ids:
            count = cursor.execute(
                """
                SELECT COUNT(*)
                FROM experiences
                WHERE province = ?
                  AND instr(quota_ids, ?) > 0
                  AND (
                        COALESCE(trim(bill_name), '') = ?
                     OR COALESCE(trim(bill_text), '') = ?
                     OR COALESCE(trim(normalized_text), '') = ?
                  )
                """,
                (province, quota_id, bill_name, bill_text, full_text),
            ).fetchone()[0]
            if count:
                return True
        return False

    def universal_kb_exact_hit(
        self,
        *,
        bill_name: str,
        bill_text: str,
        oracle_names: list[str],
    ) -> bool:
        if not oracle_names:
            return False
        bill_name = _clean_text(bill_name)
        bill_text = _clean_text(bill_text)
        full_text = _clean_text(" ".join(part for part in [bill_name, bill_text] if part))
        bill_patterns = [value for value in [full_text, bill_name, bill_text] if value]
        if not bill_patterns:
            return False
        cursor = self.universal_kb_conn.cursor()
        for bill_pattern in dict.fromkeys(bill_patterns):
            for quota_name in oracle_names:
                count = cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM knowledge
                    WHERE bill_pattern = ?
                      AND instr(quota_patterns, ?) > 0
                    """,
                    (bill_pattern, quota_name),
                ).fetchone()[0]
                if count:
                    return True
        return False

    def close(self) -> None:
        self.experience_conn.close()
        self.universal_kb_conn.close()


@dataclass
class RetrieverEvidence:
    oracle_present_in_quota_db: bool
    oracle_books: list[str]
    search_books: list[str]
    authority_hit: bool
    kb_hit: bool
    experience_exact_hit: bool
    universal_kb_exact_hit: bool


def classify_retriever_record(record: dict, evidence: RetrieverEvidence) -> dict:
    oracle_books = [
        normalize_book_code(book)
        for book in (evidence.oracle_books or [])
        if normalize_book_code(book)
    ]
    search_books = [
        normalize_book_code(book)
        for book in (evidence.search_books or [])
        if normalize_book_code(book)
    ]
    scope_miss = bool(search_books) and bool(oracle_books) and not any(
        book in search_books for book in oracle_books
    )
    asset_overlap = evidence.experience_exact_hit or evidence.universal_kb_exact_hit
    asset_not_used = asset_overlap and not (evidence.authority_hit or evidence.kb_hit)

    if not evidence.oracle_present_in_quota_db:
        primary_bucket = "index_miss"
        diagnosis = "oracle quota id not found in province quota.db"
    elif scope_miss:
        primary_bucket = "routing_miss"
        diagnosis = "oracle quota book falls outside router search_books"
    elif asset_not_used:
        primary_bucket = "knowledge_not_used"
        diagnosis = "strong exact asset overlap exists but retriever did not hit authority/kb"
    else:
        primary_bucket = "keyword_miss"
        diagnosis = "oracle book was searchable, index exists, but search terms still missed"

    return {
        "primary_bucket": primary_bucket,
        "diagnosis": diagnosis,
        "scope_miss": scope_miss,
        "asset_overlap": asset_overlap,
        "asset_not_used": asset_not_used,
        "evidence": {
            **asdict(evidence),
            "oracle_books": oracle_books,
            "search_books": search_books,
        },
        "sample_id": _clean_text(record.get("sample_id")),
        "province": _clean_text(record.get("province")),
        "specialty": _clean_text(record.get("specialty")),
        "bill_name": _clean_text(record.get("bill_name")),
        "bill_text": _clean_text(record.get("bill_text")),
        "search_query": _clean_text(record.get("search_query")),
        "oracle_quota_ids": [_clean_text(x) for x in (record.get("oracle_quota_ids") or []) if _clean_text(x)],
        "oracle_quota_names": [_clean_text(x) for x in (record.get("oracle_quota_names") or []) if _clean_text(x)],
        "candidate_count": int(((record.get("retriever") or {}).get("candidate_count") or 0)),
        "router_primary_book": _clean_text((record.get("router") or {}).get("primary_book")),
    }


def classify_retriever_misses(
    *,
    input_path: Path,
    project_root: Path = PROJECT_ROOT,
    examples_per_bucket: int = 20,
) -> tuple[list[dict], dict]:
    rows = load_jsonl(input_path)
    quota_probe = QuotaIndexProbe(project_root)
    asset_probe = AssetOverlapProbe(project_root)

    details: list[dict] = []
    counts = Counter()
    overlap_counts = Counter()
    province_breakdown: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict]] = defaultdict(list)

    try:
        for row in rows:
            if _clean_text(row.get("error_stage")) != "retriever":
                continue

            province = _clean_text(row.get("province"))
            oracle_ids = [_clean_text(x) for x in (row.get("oracle_quota_ids") or []) if _clean_text(x)]
            oracle_names = [_clean_text(x) for x in (row.get("oracle_quota_names") or []) if _clean_text(x)]
            search_books = extract_search_books(row)
            oracle_books = quota_probe.oracle_books(province, oracle_ids)
            if not oracle_books:
                oracle_books = sorted({book for book in (quota_id_to_book(qid) for qid in oracle_ids) if book})

            evidence = RetrieverEvidence(
                oracle_present_in_quota_db=quota_probe.oracle_present(province, oracle_ids),
                oracle_books=oracle_books,
                search_books=search_books,
                authority_hit=bool((row.get("retriever") or {}).get("authority_hit")),
                kb_hit=bool((row.get("retriever") or {}).get("kb_hit")),
                experience_exact_hit=asset_probe.experience_exact_hit(
                    province=province,
                    oracle_ids=oracle_ids,
                    bill_name=_clean_text(row.get("bill_name")),
                    bill_text=_clean_text(row.get("bill_text")),
                ),
                universal_kb_exact_hit=asset_probe.universal_kb_exact_hit(
                    bill_name=_clean_text(row.get("bill_name")),
                    bill_text=_clean_text(row.get("bill_text")),
                    oracle_names=oracle_names,
                ),
            )
            classified = classify_retriever_record(row, evidence)
            details.append(classified)

            bucket = classified["primary_bucket"]
            counts[bucket] += 1
            province_breakdown[province][bucket] += 1
            if classified["scope_miss"]:
                overlap_counts["scope_miss"] += 1
            if classified["asset_overlap"]:
                overlap_counts["asset_overlap"] += 1
            if evidence.experience_exact_hit:
                overlap_counts["experience_exact_hit"] += 1
            if evidence.universal_kb_exact_hit:
                overlap_counts["universal_kb_exact_hit"] += 1
            if evidence.oracle_present_in_quota_db:
                overlap_counts["oracle_present_in_quota_db"] += 1
            else:
                overlap_counts["oracle_absent_from_quota_db"] += 1

            if len(examples[bucket]) < examples_per_bucket:
                examples[bucket].append(
                    {
                        "sample_id": classified["sample_id"],
                        "province": classified["province"],
                        "search_query": classified["search_query"],
                        "oracle_quota_ids": classified["oracle_quota_ids"],
                        "oracle_quota_names": classified["oracle_quota_names"],
                        "evidence": classified["evidence"],
                    }
                )
    finally:
        quota_probe.close()
        asset_probe.close()

    summary = {
        "input_path": str(input_path),
        "retriever_miss_total": len(details),
        "primary_bucket_counts": dict(sorted(counts.items())),
        "overlap_counts": dict(sorted(overlap_counts.items())),
        "province_breakdown": {
            province: dict(sorted(counter.items()))
            for province, counter in sorted(
                province_breakdown.items(),
                key=lambda item: (-sum(item[1].values()), item[0]),
            )
        },
        "examples": dict(examples),
        "notes": [
            "primary_bucket is exclusive and uses precedence: index_miss > routing_miss > knowledge_not_used > keyword_miss",
            "asset overlap is reported separately because it can coexist with routing/index problems",
            "experience_exact_hit may be inflated when eval samples are sourced from the same experience asset pool",
        ],
    }
    return details, summary


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify stable retriever misses into exclusive buckets.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--examples-per-bucket", type=int, default=20)
    args = parser.parse_args()

    details, summary = classify_retriever_misses(
        input_path=args.input,
        examples_per_bucket=max(int(args.examples_per_bucket or 20), 1),
    )

    summary_path = args.output_prefix.with_suffix(".summary.json")
    details_path = args.output_prefix.with_suffix(".details.jsonl")
    _write_json(summary_path, summary)
    _write_jsonl(details_path, details)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nwritten: {summary_path}")
    print(f"written: {details_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
