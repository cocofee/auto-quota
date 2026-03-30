# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.classify_retriever_miss import (
    PROJECT_ROOT,
    QuotaIndexProbe,
    extract_search_books,
    load_jsonl,
    normalize_book_code,
    quota_id_to_book,
)


DEFAULT_INPUT_PATH = PROJECT_ROOT / "output" / "real_eval" / "cross5_smoke_20260330_no_post_anchor.details.jsonl"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "output" / "real_eval" / "wrong_book_classification"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _candidate_books(record: dict) -> list[str]:
    books: list[str] = []
    seen: set[str] = set()
    for quota_id in list(record.get("all_candidate_ids") or []):
        book = normalize_book_code(quota_id_to_book(quota_id))
        if not book or book in seen:
            continue
        seen.add(book)
        books.append(book)
    return books


def _router_search_books(record: dict) -> list[str]:
    router = dict(record.get("router") or {})
    classification = dict(router.get("classification") or {})

    candidates = []
    candidates.extend(router.get("search_books") or [])
    candidates.extend(classification.get("search_books") or [])
    candidates.extend(classification.get("hard_search_books") or [])
    candidates.extend(classification.get("advisory_search_books") or [])

    books: list[str] = []
    seen: set[str] = set()
    for normalized in (normalize_book_code(value) for value in candidates):
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        books.append(normalized)
    return books


def _resolved_main_books(record: dict) -> list[str]:
    retriever = dict(record.get("retriever") or {})
    resolution = dict(retriever.get("search_resolution") or {})
    calls = list(resolution.get("calls") or [])

    books: list[str] = []
    seen: set[str] = set()
    for call in calls:
        if _clean_text((call or {}).get("target")) != "main":
            continue
        for normalized in (normalize_book_code(value) for value in ((call or {}).get("resolved_books") or [])):
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            books.append(normalized)
    return books


def _oracle_books_from_record(record: dict, probe: QuotaIndexProbe) -> list[str]:
    province = _clean_text(record.get("province"))
    oracle_ids = [_clean_text(value) for value in (record.get("oracle_quota_ids") or []) if _clean_text(value)]
    books = [normalize_book_code(value) for value in (probe.oracle_books(province, oracle_ids) or []) if normalize_book_code(value)]
    if books:
        return sorted(dict.fromkeys(books))
    fallback = [normalize_book_code(quota_id_to_book(value)) for value in oracle_ids if normalize_book_code(quota_id_to_book(value))]
    return sorted(dict.fromkeys(fallback))


def _selected_book(record: dict) -> str:
    return normalize_book_code(quota_id_to_book(record.get("algo_id")))


def _used_open_search(record: dict) -> bool:
    retriever = dict(record.get("retriever") or {})
    if "used_open_search" in retriever:
        return bool(retriever.get("used_open_search"))
    resolution = dict(retriever.get("search_resolution") or {})
    for call in list(resolution.get("calls") or []):
        if bool((call or {}).get("open_search")):
            return True
    return False


@dataclass
class WrongBookEvidence:
    oracle_present_in_quota_db: bool
    oracle_books: list[str]
    selected_book: str
    search_books: list[str]
    router_search_books: list[str]
    resolved_main_books: list[str]
    candidate_books: list[str]
    oracle_in_candidates: bool
    used_open_search: bool
    error_stage: str
    miss_stage: str


def classify_wrong_book_record(record: dict, evidence: WrongBookEvidence) -> dict:
    oracle_books = [normalize_book_code(value) for value in (evidence.oracle_books or []) if normalize_book_code(value)]
    search_books = [normalize_book_code(value) for value in (evidence.search_books or []) if normalize_book_code(value)]
    router_search_books = [
        normalize_book_code(value)
        for value in (evidence.router_search_books or [])
        if normalize_book_code(value)
    ]
    resolved_main_books = [
        normalize_book_code(value)
        for value in (evidence.resolved_main_books or [])
        if normalize_book_code(value)
    ]
    candidate_books = [normalize_book_code(value) for value in (evidence.candidate_books or []) if normalize_book_code(value)]
    selected_book = normalize_book_code(evidence.selected_book)

    scope_miss = bool(search_books) and bool(oracle_books) and not any(book in search_books for book in oracle_books)
    oracle_book_in_candidates = bool(candidate_books) and any(book in candidate_books for book in oracle_books)
    selected_book_matches_oracle_book = bool(selected_book) and selected_book in set(oracle_books)
    selected_book_in_scope = bool(selected_book) and selected_book in set(search_books)
    selected_book_in_router_scope = bool(selected_book) and selected_book in set(router_search_books)
    selected_book_in_resolved_main = bool(selected_book) and selected_book in set(resolved_main_books)

    if not evidence.oracle_present_in_quota_db:
        primary_bucket = "index_miss"
        diagnosis = "oracle quota id is missing from province quota.db"
    elif scope_miss:
        primary_bucket = "routing_scope_miss"
        diagnosis = "router/retriever scope excluded the oracle book"
    elif evidence.oracle_in_candidates and evidence.miss_stage == "post_rank_miss":
        primary_bucket = "post_rank_wrong_book"
        diagnosis = "oracle candidate entered the pool but was changed after ranking"
    elif evidence.oracle_in_candidates:
        primary_bucket = "rank_wrong_book"
        diagnosis = "oracle candidate entered the pool but a wrong-book candidate won ranking"
    elif evidence.used_open_search and not search_books:
        primary_bucket = "open_search_drift"
        diagnosis = "retrieval fell back to open search and drifted into the wrong book"
    else:
        primary_bucket = "in_scope_recall_miss"
        diagnosis = "oracle book stayed in scope but the oracle candidate never entered the pool"

    secondary_bucket = ""
    secondary_diagnosis = ""
    tertiary_bucket = ""
    tertiary_diagnosis = ""
    if primary_bucket == "in_scope_recall_miss":
        if selected_book and router_search_books and not selected_book_in_router_scope:
            secondary_bucket = "true_out_of_scope_leakage"
            secondary_diagnosis = "winning wrong-book candidates came from books outside router-declared search scope"
            if selected_book_in_resolved_main:
                tertiary_bucket = "resolved_scope_drift"
                tertiary_diagnosis = "main resolution expanded into a book outside router-declared scope"
            elif candidate_books and selected_book in set(candidate_books):
                tertiary_bucket = "candidate_merge_leakage"
                tertiary_diagnosis = "scope-out candidate entered the final pool without belonging to router or resolved-main scope"
        elif (
            selected_book
            and router_search_books
            and selected_book_in_router_scope
            and resolved_main_books
            and not selected_book_in_resolved_main
        ):
            secondary_bucket = "borrow_scope_pollution"
            secondary_diagnosis = "winning wrong-book candidates were allowed by router/planner scope but were not materialized in main resolved scope"
        elif selected_book and search_books and not selected_book_in_scope:
            secondary_bucket = "true_out_of_scope_leakage"
            secondary_diagnosis = "winning wrong-book candidates came from books outside effective retrieval scope"
        elif oracle_book_in_candidates and selected_book_in_scope:
            secondary_bucket = "same_scope_wrong_family"
            secondary_diagnosis = "oracle book was already present in retrieved books, but query/entity drift still favored a wrong family/book"
        elif search_books and any(book in search_books for book in oracle_books):
            secondary_bucket = "book_not_materialized"
            secondary_diagnosis = "oracle book stayed in scope, but retrieval never materialized candidates from that book"
        else:
            secondary_bucket = "generic_in_scope_recall_miss"
            secondary_diagnosis = "in-scope recall miss without a sharper retrieval signature"

    return {
        "primary_bucket": primary_bucket,
        "diagnosis": diagnosis,
        "secondary_bucket": secondary_bucket,
        "secondary_diagnosis": secondary_diagnosis,
        "tertiary_bucket": tertiary_bucket,
        "tertiary_diagnosis": tertiary_diagnosis,
        "scope_miss": scope_miss,
        "oracle_book_in_candidates": oracle_book_in_candidates,
        "selected_book_matches_oracle_book": selected_book_matches_oracle_book,
        "selected_book_in_scope": selected_book_in_scope,
        "sample_id": _clean_text(record.get("sample_id")),
        "province": _clean_text(record.get("province")),
        "specialty": _clean_text(record.get("specialty")),
        "bill_name": _clean_text(record.get("bill_name")),
        "bill_text": _clean_text(record.get("bill_text")),
        "search_query": _clean_text(record.get("search_query")),
        "oracle_quota_ids": [_clean_text(value) for value in (record.get("oracle_quota_ids") or []) if _clean_text(value)],
        "oracle_quota_names": [_clean_text(value) for value in (record.get("oracle_quota_names") or []) if _clean_text(value)],
        "algo_id": _clean_text(record.get("algo_id")),
        "algo_name": _clean_text(record.get("algo_name")),
        "candidate_count": int(record.get("candidate_count", 0) or 0),
        "evidence": {
            **asdict(evidence),
            "oracle_books": oracle_books,
            "selected_book": selected_book,
            "search_books": search_books,
            "router_search_books": router_search_books,
            "resolved_main_books": resolved_main_books,
            "candidate_books": candidate_books,
            "selected_book_in_router_scope": selected_book_in_router_scope,
            "selected_book_in_resolved_main": selected_book_in_resolved_main,
        },
    }


def classify_wrong_book_details(
    *,
    input_path: Path,
    project_root: Path = PROJECT_ROOT,
    examples_per_bucket: int = 20,
) -> tuple[list[dict], dict]:
    rows = load_jsonl(input_path)
    probe = QuotaIndexProbe(project_root)

    details: list[dict] = []
    counts = Counter()
    secondary_counts = Counter()
    tertiary_counts = Counter()
    stage_counts = Counter()
    province_breakdown: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[dict]] = defaultdict(list)

    try:
        for row in rows:
            if _clean_text(row.get("cause")) != "wrong_book":
                continue

            province = _clean_text(row.get("province"))
            oracle_ids = [_clean_text(value) for value in (row.get("oracle_quota_ids") or []) if _clean_text(value)]
            evidence = WrongBookEvidence(
                oracle_present_in_quota_db=probe.oracle_present(province, oracle_ids),
                oracle_books=_oracle_books_from_record(row, probe),
                selected_book=_selected_book(row),
                search_books=extract_search_books(row),
                router_search_books=_router_search_books(row),
                resolved_main_books=_resolved_main_books(row),
                candidate_books=_candidate_books(row),
                oracle_in_candidates=bool(row.get("oracle_in_candidates")),
                used_open_search=_used_open_search(row),
                error_stage=_clean_text(row.get("error_stage")),
                miss_stage=_clean_text(row.get("miss_stage")),
            )
            classified = classify_wrong_book_record(row, evidence)
            details.append(classified)

            bucket = classified["primary_bucket"]
            counts[bucket] += 1
            if classified.get("secondary_bucket"):
                secondary_counts[classified["secondary_bucket"]] += 1
            if classified.get("tertiary_bucket"):
                tertiary_counts[classified["tertiary_bucket"]] += 1
            stage_counts[evidence.error_stage or ""] += 1
            province_breakdown[province][bucket] += 1

            if len(examples[bucket]) < examples_per_bucket:
                examples[bucket].append(
                    {
                        "sample_id": classified["sample_id"],
                        "province": classified["province"],
                        "search_query": classified["search_query"],
                        "oracle_quota_ids": classified["oracle_quota_ids"],
                        "algo_id": classified["algo_id"],
                        "evidence": classified["evidence"],
                    }
                )
    finally:
        probe.close()

    summary = {
        "input_path": str(input_path),
        "wrong_book_total": len(details),
        "primary_bucket_counts": dict(sorted(counts.items())),
        "secondary_bucket_counts": dict(sorted(secondary_counts.items())),
        "tertiary_bucket_counts": dict(sorted(tertiary_counts.items())),
        "error_stage_counts": dict(sorted((key, value) for key, value in stage_counts.items() if key)),
        "province_breakdown": {
            province: dict(sorted(counter.items()))
            for province, counter in sorted(
                province_breakdown.items(),
                key=lambda item: (-sum(item[1].values()), item[0]),
            )
        },
        "examples": dict(examples),
        "notes": [
            "Buckets are exclusive and ordered by precedence: index_miss > routing_scope_miss > post_rank_wrong_book > rank_wrong_book > open_search_drift > in_scope_recall_miss",
            "routing_scope_miss means the oracle book was excluded before ranking, so ranking changes are not the first target",
            "post_rank_wrong_book means a right-book candidate existed in-pool but was changed after ranking",
            "true_out_of_scope_leakage means the winning book is outside router-declared scope; borrow_scope_pollution means planner/router allowed it but main scope never resolved it",
            "true_out_of_scope_leakage is further split into resolved_scope_drift vs candidate_merge_leakage",
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


def _output_path(prefix: Path, suffix: str) -> Path:
    return Path(f"{prefix}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify wrong_book cases into actionable buckets.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--examples-per-bucket", type=int, default=20)
    args = parser.parse_args()

    details, summary = classify_wrong_book_details(
        input_path=args.input,
        examples_per_bucket=max(int(args.examples_per_bucket or 20), 1),
    )

    summary_path = _output_path(args.output_prefix, ".summary.json")
    details_path = _output_path(args.output_prefix, ".details.jsonl")
    _write_json(summary_path, summary)
    _write_jsonl(details_path, details)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nwritten: {summary_path}")
    print(f"written: {details_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
