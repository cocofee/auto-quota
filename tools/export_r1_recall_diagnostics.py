from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "output" / "benchmark_compare" / "ltr_v2_mixed_safety_candidate_latest_result.json"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "reports" / "attribution" / "r1_recall_miss_diagnostics.csv"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "reports" / "attribution" / "r1_recall_miss_summary.json"

CSV_FIELDS = [
    "province",
    "bill_id",
    "bill_name",
    "specialty",
    "correct_quota_id",
    "algo_id",
    "candidate_count",
    "recall_topk_count",
    "bucket",
    "no_match_reason",
    "top_candidate_ids",
    "top_candidate_names",
]


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        import orjson

        return orjson.loads(raw)
    except Exception:
        return json.loads(raw.decode("utf-8"))


def _first_value(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] if value else "")
    return str(value or "")


def _quota_prefix(quota_id: str) -> str:
    text = str(quota_id or "").strip()
    if "-" in text:
        return text.split("-", 1)[0]
    match = re.match(r"([A-Za-z]+\d*)", text)
    return match.group(1) if match else ""


def _normalized_prefix(prefix: str) -> str:
    text = str(prefix or "").upper().strip()
    if re.fullmatch(r"C\d+", text):
        return text[1:]
    return text


def _candidate_snapshots(detail: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = detail.get("candidate_snapshots") or []
    return candidates if isinstance(candidates, list) else []


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("quota_id") or candidate.get("id") or "")


def _candidate_name(candidate: dict[str, Any]) -> str:
    return str(candidate.get("name") or candidate.get("quota_name") or "")


def _recall_topk_count(detail: dict[str, Any]) -> int:
    values = detail.get("recall_topk_ids") or detail.get("all_candidate_ids") or []
    return len(values) if isinstance(values, list) else 0


def classify_r1_bucket(detail: dict[str, Any]) -> str:
    reason = str(detail.get("no_match_reason") or "")
    if reason == "all candidates rejected by hard parameter validation":
        return "hard_param_reject"
    if reason == "搜索无匹配结果":
        return "search_no_result"
    if "缺少专业" in reason:
        return "weak_context_manual_review"

    specialty = str(detail.get("specialty") or "")
    if not specialty:
        return "missing_specialty_context"

    correct_quota_id = _first_value(detail.get("stored_ids") or detail.get("expected_quota_ids"))
    correct_prefix = _normalized_prefix(_quota_prefix(correct_quota_id))
    specialty_prefix = _normalized_prefix(specialty)
    if correct_prefix and specialty_prefix and correct_prefix != specialty_prefix:
        return "real_specialty_route_mismatch"

    if len(_candidate_snapshots(detail)) <= 3:
        return "thin_candidate_pool"
    return "semantic_candidate_pool_miss"


def is_r1_recall_miss(detail: dict[str, Any]) -> bool:
    return not bool(detail.get("is_match")) and int(detail.get("recall_rank", 0) or 0) == -1


def build_r1_recall_diagnostics(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bucket_counts: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    bucket_province_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for province_result in payload.get("results", []) or []:
        province = str(province_result.get("province") or "")
        for detail in province_result.get("details", []) or []:
            if not isinstance(detail, dict) or not is_r1_recall_miss(detail):
                continue
            candidates = _candidate_snapshots(detail)
            bucket = classify_r1_bucket(detail)
            correct_quota_id = _first_value(detail.get("stored_ids") or detail.get("expected_quota_ids"))
            top_candidates = candidates[:5]
            row = {
                "province": province,
                "bill_id": str(detail.get("bill_id") or detail.get("sample_id") or ""),
                "bill_name": str(detail.get("bill_name") or ""),
                "specialty": str(detail.get("specialty") or ""),
                "correct_quota_id": correct_quota_id,
                "algo_id": str(detail.get("algo_id") or ""),
                "candidate_count": len(candidates),
                "recall_topk_count": _recall_topk_count(detail),
                "bucket": bucket,
                "no_match_reason": str(detail.get("no_match_reason") or ""),
                "top_candidate_ids": "|".join(_candidate_id(candidate) for candidate in top_candidates),
                "top_candidate_names": "|".join(_candidate_name(candidate) for candidate in top_candidates),
            }
            rows.append(row)
            bucket_counts[bucket] += 1
            province_counts[province] += 1
            bucket_province_counts[bucket][province] += 1

    rows.sort(
        key=lambda row: (
            str(row["bucket"]),
            str(row["province"]),
            str(row["bill_id"]),
            str(row["correct_quota_id"]),
        )
    )
    summary = {
        "r1_total": len(rows),
        "bucket_counts": dict(sorted(bucket_counts.items(), key=lambda item: (-item[1], item[0]))),
        "province_counts": dict(sorted(province_counts.items(), key=lambda item: (-item[1], item[0]))),
        "bucket_top_provinces": {
            bucket: dict(counter.most_common(5))
            for bucket, counter in sorted(bucket_province_counts.items())
        },
    }
    return rows, summary


def write_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_summary(summary: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export compact R1 recall miss diagnostics from benchmark latest_result JSON.")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT), help="Benchmark latest_result JSON path")
    parser.add_argument("--output-csv", type=str, default=str(DEFAULT_OUTPUT_CSV), help="Output diagnostics CSV path")
    parser.add_argument("--summary-output", type=str, default=str(DEFAULT_SUMMARY_JSON), help="Output summary JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = _load_json(Path(args.input))
    rows, summary = build_r1_recall_diagnostics(payload)
    csv_path = write_csv(rows, Path(args.output_csv))
    summary_path = write_summary(summary, Path(args.summary_output))
    print(f"[R1_DIAG] input: {args.input}")
    print(f"[R1_DIAG] r1_total: {summary['r1_total']}")
    print(f"[R1_DIAG] bucket_counts: {summary['bucket_counts']}")
    print(f"[R1_DIAG] output_csv: {csv_path}")
    print(f"[R1_DIAG] summary_json: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
