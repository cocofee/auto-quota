from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def _load_errors(path: Path) -> list[dict]:
    errors: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            errors.append(json.loads(line))
    return errors


def _resolve_errors_path(asset: str) -> Path:
    path = Path(asset)
    if path.is_dir():
        return path / "all_errors.jsonl"
    return path


def _candidate_count_bucket(case: dict) -> str:
    count = int(case.get("candidate_count") or len(case.get("all_candidate_ids") or []))
    if count <= 0:
        return "0"
    if count <= 4:
        return "1-4"
    if count <= 9:
        return "5-9"
    return "10+"


def _trace_key(case: dict) -> str:
    trace = case.get("trace_path") or []
    return " -> ".join(str(part) for part in trace) if trace else "unknown"


def _heuristic_bucket(case: dict) -> str:
    count = int(case.get("candidate_count") or len(case.get("all_candidate_ids") or []))
    cause = str(case.get("cause") or "")
    expected_book = str(case.get("expected_book") or "")
    predicted_book = str(case.get("predicted_book") or "")
    trace = _trace_key(case)

    if count <= 0:
        return "no_result"
    if cause == "wrong_book" or (expected_book and predicted_book and expected_book != predicted_book):
        return "book_filter_or_scope"
    if cause == "synonym_gap":
        return "synonym_or_query"
    if cause in {"search_off", "search_bias"}:
        return "search_bias"
    if "rule" in trace:
        return "path_hijack"
    return "other_recall"


def _sample_cases(cases: list[dict], sample_size: int, seed: int) -> list[dict]:
    if len(cases) <= sample_size:
        return list(cases)
    rng = random.Random(seed)
    return rng.sample(cases, sample_size)


def _print_counter(title: str, counter: Counter, total: int, limit: int | None = None) -> None:
    print(title)
    print("-" * len(title))
    items = counter.most_common(limit)
    for key, count in items:
        pct = (count / total * 100.0) if total else 0.0
        print(f"{key}: {count} ({pct:.1f}%)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bucket benchmark recall misses for offline diagnosis.")
    parser.add_argument("asset", help="benchmark asset directory or all_errors.jsonl path")
    parser.add_argument("--sample-size", type=int, default=200, help="number of recall miss samples to export")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed")
    parser.add_argument(
        "--sample-out",
        help="optional output path for sampled recall misses jsonl (default: <asset_dir>/recall_miss_samples.jsonl)",
    )
    args = parser.parse_args()

    errors_path = _resolve_errors_path(args.asset)
    errors = _load_errors(errors_path)
    recall_misses = [case for case in errors if case.get("miss_stage") == "recall_miss"]

    print(f"all_errors: {len(errors)}")
    print(f"recall_miss: {len(recall_misses)}")
    print()

    province_counter = Counter(str(case.get("province") or "")[:16] for case in recall_misses)
    cause_counter = Counter(str(case.get("cause") or "unknown") for case in recall_misses)
    trace_counter = Counter(_trace_key(case) for case in recall_misses)
    count_bucket_counter = Counter(_candidate_count_bucket(case) for case in recall_misses)
    heuristic_counter = Counter(_heuristic_bucket(case) for case in recall_misses)
    specialty_counter = Counter(str(case.get("specialty") or "") for case in recall_misses)

    _print_counter("By Province", province_counter, len(recall_misses), limit=20)
    _print_counter("By Cause", cause_counter, len(recall_misses))
    _print_counter("By Trace", trace_counter, len(recall_misses), limit=12)
    _print_counter("By Candidate Count", count_bucket_counter, len(recall_misses))
    _print_counter("By Heuristic Bucket", heuristic_counter, len(recall_misses))
    _print_counter("By Specialty", specialty_counter, len(recall_misses), limit=20)

    bucketed_cases: dict[str, list[dict]] = defaultdict(list)
    for case in recall_misses:
        bucketed_cases[_heuristic_bucket(case)].append(case)

    per_bucket_quota: dict[str, int] = {}
    if recall_misses:
        for bucket, count in heuristic_counter.items():
            quota = round(args.sample_size * count / len(recall_misses))
            per_bucket_quota[bucket] = max(1, quota)

    sampled: list[dict] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for bucket, cases in sorted(bucketed_cases.items(), key=lambda item: heuristic_counter[item[0]], reverse=True):
        take = min(len(cases), per_bucket_quota.get(bucket, 0))
        for case in _sample_cases(cases, take, args.seed):
            key = (
                str(case.get("province") or ""),
                str(case.get("bill_text") or ""),
                str(case.get("predicted_quota_id") or ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sampled.append(
                {
                    "heuristic_bucket": bucket,
                    "province": case.get("province"),
                    "specialty": case.get("specialty"),
                    "cause": case.get("cause"),
                    "candidate_count": case.get("candidate_count"),
                    "trace_path": case.get("trace_path"),
                    "bill_name": case.get("bill_name"),
                    "bill_text": case.get("bill_text"),
                    "expected_quota_ids": case.get("expected_quota_ids"),
                    "expected_quota_names": case.get("expected_quota_names"),
                    "predicted_quota_id": case.get("predicted_quota_id"),
                    "predicted_quota_name": case.get("predicted_quota_name"),
                    "expected_book": case.get("expected_book"),
                    "predicted_book": case.get("predicted_book"),
                }
            )

    if len(sampled) > args.sample_size:
        sampled = sampled[: args.sample_size]

    sample_out = Path(args.sample_out) if args.sample_out else errors_path.parent / "recall_miss_samples.jsonl"
    with sample_out.open("w", encoding="utf-8") as fh:
        for case in sampled:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"sampled_recall_miss: {len(sampled)}")
    print(f"sample_output: {sample_out}")


if __name__ == "__main__":
    main()
