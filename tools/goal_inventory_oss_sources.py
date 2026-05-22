from __future__ import annotations

import argparse
import csv
import json
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
from tools.goal_collect_oss_samples import _canonical_sample, _clean, _norm_key, _quota_ids_for_province  # noqa: E402


DEFAULT_EXISTING = config.DATA_DIR / "goal_search" / "oss_samples.jsonl"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_oss_source_inventory.json"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_oss_source_inventory.csv"
DEFAULT_ROOTS = (
    PROJECT_ROOT / "reports" / "agent_state",
    PROJECT_ROOT / "reports" / "attribution",
    PROJECT_ROOT / "data",
)
SUPPORTED_SUFFIXES = {".jsonl", ".json", ".csv"}
QUERY_KEYS = ("bill_name", "bill_text", "query", "description", "feature_text")
SKIP_NAME_PREFIXES = ("goal_",)
SKIP_DIR_NAMES = {"__pycache__", ".git"}
SKIP_DATA_PREFIXES = ("qwen3_",)


def _sample_key(sample: dict[str, Any]) -> str:
    return "|".join(
        [
            _clean(sample.get("province")),
            _norm_key(sample.get("bill_name")),
            _norm_key(sample.get("bill_text")),
            ",".join(sorted(str(value) for value in sample.get("expected_ids") or [])),
        ]
    )


def _is_generated_goal_file(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
    parts = {part.lower() for part in rel.parts}
    return path.name.startswith(SKIP_NAME_PREFIXES) or ("goal_search" in parts)


def _should_skip_file(path: Path, *, include_generated: bool) -> bool:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return True
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True
    if not include_generated and _is_generated_goal_file(path):
        return True
    if path.parent == config.DATA_DIR and path.name.startswith(SKIP_DATA_PREFIXES):
        return True
    return False


def _discover_inputs(roots: list[Path], *, max_bytes: int, include_generated: bool) -> tuple[list[Path], list[dict[str, Any]]]:
    inputs: list[Path] = []
    skipped: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*")) if root.exists() else []
        for path in paths:
            if not path.is_file() or _should_skip_file(path, include_generated=include_generated):
                continue
            if path in seen:
                continue
            seen.add(path)
            size = path.stat().st_size
            if size > max_bytes:
                skipped.append({"path": _rel(path), "size_bytes": size, "reason": "over_max_bytes"})
                continue
            inputs.append(path)
    return sorted(inputs), skipped


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _read_jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _read_csv_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _walk_json(value: Any, inherited: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
    inherited = inherited or {}
    if isinstance(value, dict):
        row = dict(value)
        for key in ("province", "project_name", "source_file"):
            if inherited.get(key) and not row.get(key):
                row[key] = inherited[key]
        yield row
        next_inherited = dict(inherited)
        for key in ("province", "project_name", "source_file"):
            if row.get(key):
                next_inherited[key] = row.get(key)
        for child in value.values():
            yield from _walk_json(child, next_inherited)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child, inherited)


def _read_json_rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return _walk_json(payload)


def _iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        yield from _read_jsonl_rows(path)
    elif suffix == ".json":
        yield from _read_json_rows(path)
    elif suffix == ".csv":
        yield from _read_csv_rows(path)


def _looks_like_bill_answer_row(row: dict[str, Any]) -> bool:
    return any(_clean(row.get(key)) for key in QUERY_KEYS)


def _existing_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for row_number, row in enumerate(_read_jsonl_rows(path), 1):
        sample = _canonical_sample(row, path=path, row_number=row_number)
        if sample is not None:
            keys.add(_sample_key(sample))
    return keys


def _source_kind(path: Path) -> str:
    rel = _rel(path).replace("\\", "/")
    if rel.startswith("reports/agent_state/"):
        return "agent_state"
    if rel.startswith("reports/attribution/"):
        return "attribution"
    if rel.startswith("data/source_packs/"):
        return "source_packs"
    if rel.startswith("data/"):
        return "data"
    return "other"


def inspect_source(path: Path, *, existing_keys: set[str], quota_cache: dict[str, set[str]]) -> dict[str, Any]:
    counters: Counter[str] = Counter()
    source_keys: set[str] = set()
    new_keys: set[str] = set()
    province_counts: Counter[str] = Counter()
    source_file_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    for row_number, row in enumerate(_iter_rows(path), 1):
        counters["rows_seen"] += 1
        if not _looks_like_bill_answer_row(row):
            continue
        counters["bill_like_rows"] += 1
        sample = _canonical_sample(row, path=path, row_number=row_number)
        if sample is None:
            counters["missing_required"] += 1
            continue
        counters["canonical_rows"] += 1
        local_ids = _quota_ids_for_province(sample["province"], quota_cache)
        expected_ids = [quota_id for quota_id in sample["expected_ids"] if quota_id in local_ids]
        if not expected_ids:
            counters["expected_not_in_local_db"] += 1
            continue
        sample["expected_ids"] = expected_ids
        key = _sample_key(sample)
        if key in source_keys:
            counters["duplicate_within_source"] += 1
            continue
        source_keys.add(key)
        counters["unique_valid_rows"] += 1
        province_counts[sample["province"]] += 1
        source_file_counts[sample["source_file"]] += 1
        if key in existing_keys:
            counters["duplicate_existing"] += 1
            continue
        new_keys.add(key)
        counters["new_unique_rows"] += 1
        if len(examples) < 3:
            examples.append(
                {
                    "province": sample["province"],
                    "bill_name": sample["bill_name"],
                    "expected_ids": sample["expected_ids"],
                    "sample_id": sample["sample_id"],
                }
            )

    recommended = counters["new_unique_rows"] >= 10 and not _is_generated_goal_file(path)
    return {
        "path": _rel(path),
        "source_kind": _source_kind(path),
        "size_bytes": path.stat().st_size,
        "recommended": bool(recommended),
        "counts": dict(counters),
        "province_counts": dict(province_counts),
        "source_file_counts": dict(source_file_counts),
        "examples": examples,
        "_new_keys": sorted(new_keys),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "path",
        "source_kind",
        "size_bytes",
        "recommended",
        "rows_seen",
        "bill_like_rows",
        "canonical_rows",
        "unique_valid_rows",
        "new_unique_rows",
        "duplicate_existing",
        "duplicate_within_source",
        "expected_not_in_local_db",
        "top_provinces",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            counts = row.get("counts") or {}
            writer.writerow(
                {
                    "path": row["path"],
                    "source_kind": row["source_kind"],
                    "size_bytes": row["size_bytes"],
                    "recommended": row["recommended"],
                    "rows_seen": counts.get("rows_seen", 0),
                    "bill_like_rows": counts.get("bill_like_rows", 0),
                    "canonical_rows": counts.get("canonical_rows", 0),
                    "unique_valid_rows": counts.get("unique_valid_rows", 0),
                    "new_unique_rows": counts.get("new_unique_rows", 0),
                    "duplicate_existing": counts.get("duplicate_existing", 0),
                    "duplicate_within_source": counts.get("duplicate_within_source", 0),
                    "expected_not_in_local_db": counts.get("expected_not_in_local_db", 0),
                    "top_provinces": "; ".join(
                        f"{province}:{count}" for province, count in Counter(row.get("province_counts") or {}).most_common(5)
                    ),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory answer-bearing OSS/report sources for Goal OSS Learning")
    parser.add_argument("--root", nargs="*", default=[str(path) for path in DEFAULT_ROOTS], help="Files or directories to scan")
    parser.add_argument("--existing", default=str(DEFAULT_EXISTING), help="Existing OSS sample JSONL for duplicate checks")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    parser.add_argument("--max-mb", type=float, default=50.0, help="Skip individual files larger than this")
    parser.add_argument("--include-generated", action="store_true", help="Include goal_* and data/goal_search generated files")
    args = parser.parse_args()

    roots = [Path(value) for value in args.root]
    inputs, skipped = _discover_inputs(roots, max_bytes=int(args.max_mb * 1024 * 1024), include_generated=args.include_generated)
    existing = _existing_keys(Path(args.existing))
    quota_cache: dict[str, set[str]] = {}

    sources = [inspect_source(path, existing_keys=existing, quota_cache=quota_cache) for path in inputs]
    sources = sorted(
        sources,
        key=lambda row: (
            -int((row.get("counts") or {}).get("new_unique_rows", 0)),
            -int((row.get("counts") or {}).get("unique_valid_rows", 0)),
            row["path"],
        ),
    )
    recommended = [row for row in sources if row["recommended"]]
    global_new_keys: set[str] = set()
    recommended_global_new_keys: set[str] = set()
    for row in sources:
        keys = set(row.get("_new_keys") or [])
        global_new_keys.update(keys)
        if row["recommended"]:
            recommended_global_new_keys.update(keys)
    for row in sources:
        row.pop("_new_keys", None)
    summary = {
        "roots": [str(root) for root in roots],
        "existing": args.existing,
        "max_mb": args.max_mb,
        "scanned_files": len(inputs),
        "skipped_files": len(skipped),
        "existing_unique_rows": len(existing),
        "candidate_files": sum(1 for row in sources if int((row.get("counts") or {}).get("unique_valid_rows", 0)) > 0),
        "recommended_files": len(recommended),
        "potential_new_unique_rows_sum": sum(int((row.get("counts") or {}).get("new_unique_rows", 0)) for row in recommended),
        "global_new_unique_rows": len(global_new_keys),
        "recommended_global_new_unique_rows": len(recommended_global_new_keys),
        "top_recommended": [
            {
                "path": row["path"],
                "new_unique_rows": (row.get("counts") or {}).get("new_unique_rows", 0),
                "unique_valid_rows": (row.get("counts") or {}).get("unique_valid_rows", 0),
                "source_kind": row["source_kind"],
            }
            for row in recommended[:20]
        ],
    }
    report = {"summary": summary, "sources": sources, "skipped": skipped[:200]}

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(Path(args.csv_output), sources)

    summary["json_output"] = str(json_output)
    summary["csv_output"] = args.csv_output
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
