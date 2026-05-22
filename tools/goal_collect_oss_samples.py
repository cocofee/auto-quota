from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config  # noqa: E402


DEFAULT_OUTPUT = config.DATA_DIR / "goal_search" / "oss_samples.jsonl"
DEFAULT_SOURCE_PATTERNS = (
    "v36_*oss*.jsonl",
    "v36_*shadow*.jsonl",
    "v36_*alignment*.jsonl",
    "v36_*guarded*.jsonl",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm_key(value: object) -> str:
    return re.sub(r"\s+", "", _clean(value).lower())


def _safe_json(value: object) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _split_ids(value: object) -> list[str]:
    value = _safe_json(value)
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_split_ids(item))
        return result
    if isinstance(value, dict):
        return []
    text = _clean(value)
    if not text:
        return []
    parts = re.split(r"[|,，;；\s]+", text)
    return [part.strip() for part in parts if part.strip()]


def _expected_ids(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "expected_ids",
        "expected_id",
        "oracle_quota_ids",
        "expected_quota_ids",
        "stored_ids",
        "quota_id",
        "correct_quota_id",
        "positive_id",
    ):
        values.extend(_split_ids(row.get(key)))
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _default_inputs() -> list[Path]:
    root = PROJECT_ROOT / "reports" / "agent_state"
    paths: set[Path] = set()
    for pattern in DEFAULT_SOURCE_PATTERNS:
        paths.update(root.glob(pattern))
    return sorted(path for path in paths if path.is_file() and not path.name.startswith("goal_"))


def _read_json_rows(path: Path) -> Iterable[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                yield row
        return
    if not isinstance(payload, dict):
        return
    for province_result in payload.get("results") or payload.get("json_results") or []:
        if not isinstance(province_result, dict):
            continue
        province = province_result.get("province")
        for detail in province_result.get("details") or []:
            if not isinstance(detail, dict):
                continue
            row = dict(detail)
            row.setdefault("province", province)
            yield row


def _read_rows(path: Path) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
        return
    if suffix == ".json":
        yield from _read_json_rows(path)
        return
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
        return
    raise ValueError(f"unsupported input: {path}")


def _quota_ids_for_province(province: str, cache: dict[str, set[str]]) -> set[str]:
    if province in cache:
        return cache[province]
    try:
        db_path = config.get_quota_db_path(province)
    except Exception:
        cache[province] = set()
        return cache[province]
    path = Path(db_path)
    if not path.exists():
        cache[province] = set()
        return cache[province]
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("select quota_id from quotas").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    cache[province] = {_clean(row[0]) for row in rows if _clean(row[0])}
    return cache[province]


def _canonical_sample(row: dict[str, Any], *, path: Path, row_number: int) -> dict[str, Any] | None:
    province = _clean(row.get("province") or row.get("quota_province"))
    bill_name = _clean(row.get("bill_name") or row.get("name") or row.get("query"))
    bill_text = _clean(row.get("bill_text") or row.get("description") or row.get("feature_text"))
    expected_ids = _expected_ids(row)
    if not province or not expected_ids or not (bill_name or bill_text):
        return None
    sample_id = _clean(row.get("sample_id") or row.get("bill_id") or row.get("idx") or row_number)
    return {
        "province": province,
        "bill_name": bill_name or bill_text,
        "bill_text": bill_text,
        "unit": _clean(row.get("unit") or row.get("bill_unit")),
        "specialty": _clean(row.get("specialty")),
        "expected_ids": expected_ids,
        "sample_id": sample_id,
        "source_file": _clean(row.get("source_file") or path.name),
        "project_name": _clean(row.get("project_name")),
        "bucket": _clean(
            row.get("bucket")
            or row.get("target_bucket")
            or row.get("probe_bucket")
            or row.get("attribution_category")
            or row.get("error_stage")
        ),
    }


def collect_samples(inputs: list[Path], *, validate_quota_ids: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quota_cache: dict[str, set[str]] = {}
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    counters: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()

    for path in inputs:
        if not path.exists():
            counters["missing_input"] += 1
            continue
        for row_number, row in enumerate(_read_rows(path), 1):
            counters["raw_rows"] += 1
            sample = _canonical_sample(row, path=path, row_number=row_number)
            if sample is None:
                counters["dropped_missing_required"] += 1
                continue
            if validate_quota_ids:
                local_ids = _quota_ids_for_province(sample["province"], quota_cache)
                expected_ids = [qid for qid in sample["expected_ids"] if qid in local_ids]
                if not expected_ids:
                    counters["dropped_expected_not_in_local_db"] += 1
                    continue
                sample["expected_ids"] = expected_ids
            key = "|".join(
                [
                    sample["province"],
                    _norm_key(sample["bill_name"]),
                    _norm_key(sample["bill_text"]),
                    ",".join(sorted(sample["expected_ids"])),
                ]
            )
            if key in seen:
                counters["dropped_duplicate"] += 1
                continue
            seen.add(key)
            samples.append(sample)
            counters["kept"] += 1
            province_counts[sample["province"]] += 1
            source_counts[sample["source_file"]] += 1

    summary = {
        "inputs": [str(path) for path in inputs],
        "validate_quota_ids": validate_quota_ids,
        "counts": dict(counters),
        "province_counts": dict(province_counts),
        "source_counts": dict(source_counts),
    }
    return samples, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect existing OSS/report answer samples for goal search")
    parser.add_argument("--input", nargs="*", default=None, help="Input JSONL/JSON/CSV files. Defaults to known OSS/report JSONL files.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSONL path")
    parser.add_argument("--no-validate-quota-ids", action="store_true", help="Keep rows even when expected_ids are not found in local quota.db")
    args = parser.parse_args()

    inputs = [Path(value) for value in args.input] if args.input else _default_inputs()
    samples, summary = collect_samples(inputs, validate_quota_ids=not args.no_validate_quota_ids)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

    summary["output"] = str(output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
