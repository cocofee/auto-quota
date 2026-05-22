from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config  # noqa: E402


DEFAULT_INPUT = config.DATA_DIR / "goal_search" / "oss_samples.jsonl"
DEFAULT_OUTPUT_DIR = config.DATA_DIR / "goal_search" / "splits"
DEFAULT_SEED = "goal-oss-learning-v1"
SPLITS = ("dev", "heldout", "hard")
HARD_HINTS = (
    "hard",
    "miss",
    "wrong",
    "error",
    "rank_minus",
    "overturned",
    "snapshot_window",
    "high_frequency",
    "guardrail",
)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm(value: object) -> str:
    return re.sub(r"\s+", "", _clean(value).lower())


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} is not a JSON object: {path}")
            rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _leakage_keys(row: dict[str, Any], *, aggregate_source_files: set[str] | None = None) -> list[str]:
    aggregate_source_files = aggregate_source_files or set()
    province = _clean(row.get("province"))
    keys: list[str] = []

    project_name = _norm(row.get("project_name"))
    if project_name:
        keys.append(f"project:{project_name}")

    source_file = _norm(row.get("source_file"))
    if source_file and source_file not in aggregate_source_files:
        keys.append(f"source:{source_file}")

    sample_id = _norm(row.get("sample_id"))
    if province and sample_id:
        keys.append(f"sample:{province}|{sample_id}")

    bill_name = _norm(row.get("bill_name"))
    bill_text = _norm(row.get("bill_text"))
    if province and (bill_name or bill_text):
        keys.append(f"bill:{province}|{bill_name}|{bill_text}")

    return keys


def _hard_eligible(row: dict[str, Any]) -> bool:
    text = f"{_clean(row.get('source_file'))} {_clean(row.get('bucket'))}".lower()
    return any(hint in text for hint in HARD_HINTS)


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _build_components(rows: list[dict[str, Any]], *, aggregate_source_files: set[str]) -> list[dict[str, Any]]:
    uf = UnionFind(len(rows))
    key_owner: dict[str, int] = {}
    row_keys: list[list[str]] = []

    for index, row in enumerate(rows):
        keys = _leakage_keys(row, aggregate_source_files=aggregate_source_files)
        row_keys.append(keys)
        for key in keys:
            owner = key_owner.get(key)
            if owner is None:
                key_owner[key] = index
            else:
                uf.union(index, owner)

    grouped_indexes: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        grouped_indexes[uf.find(index)].append(index)

    components: list[dict[str, Any]] = []
    for indexes in grouped_indexes.values():
        keys = sorted({key for index in indexes for key in row_keys[index]})
        component_id = _stable_hash("\n".join(keys or [str(indexes[0])]))[:12]
        component_rows = [rows[index] for index in indexes]
        components.append(
            {
                "id": component_id,
                "indexes": indexes,
                "rows": component_rows,
                "size": len(indexes),
                "hard_rows": sum(1 for row in component_rows if _hard_eligible(row)),
                "hash": _stable_hash(DEFAULT_SEED + component_id),
                "source_files": sorted({_clean(row.get("source_file")) for row in component_rows if _clean(row.get("source_file"))}),
                "projects": sorted({_clean(row.get("project_name")) for row in component_rows if _clean(row.get("project_name"))}),
                "provinces": sorted({_clean(row.get("province")) for row in component_rows if _clean(row.get("province"))}),
                "buckets": dict(Counter(_clean(row.get("bucket")) for row in component_rows)),
            }
        )

    return sorted(components, key=lambda item: (-int(item["size"]), str(item["hash"])))


def _target_counts(total: int, dev_ratio: float, heldout_ratio: float, hard_ratio: float) -> dict[str, int]:
    dev_target = round(total * dev_ratio)
    heldout_target = round(total * heldout_ratio)
    hard_target = round(total * hard_ratio)
    dev_target += total - dev_target - heldout_target - hard_target
    return {
        "dev": dev_target,
        "heldout": heldout_target,
        "hard": hard_target,
    }


def _choose_split(component: dict[str, Any], counts: dict[str, int], targets: dict[str, int]) -> str:
    allowed = ["dev", "heldout"]
    if int(component["hard_rows"]) > 0:
        allowed.append("hard")

    def rank(split: str) -> tuple[int, int, int]:
        deficit = targets[split] - counts[split]
        hard_fit = 1 if split == "hard" and int(component["hard_rows"]) > 0 else 0
        tie_order = {"heldout": 2, "hard": 1, "dev": 0}[split]
        return deficit, hard_fit, tie_order

    return max(allowed, key=rank)


def split_samples(
    rows: list[dict[str, Any]],
    *,
    dev_ratio: float,
    heldout_ratio: float,
    hard_ratio: float,
    aggregate_source_files: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    aggregate_source_files = aggregate_source_files or set()
    components = _build_components(rows, aggregate_source_files=aggregate_source_files)
    targets = _target_counts(len(rows), dev_ratio, heldout_ratio, hard_ratio)
    assignments: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    component_summaries: list[dict[str, Any]] = []
    counts = {split: 0 for split in SPLITS}

    for component in components:
        split = _choose_split(component, counts, targets)
        counts[split] += int(component["size"])
        assignments[split].extend(component["rows"])
        component_summaries.append(
            {
                "id": component["id"],
                "split": split,
                "rows": component["size"],
                "hard_rows": component["hard_rows"],
                "source_files": component["source_files"],
                "projects": component["projects"],
                "provinces": component["provinces"],
                "buckets": component["buckets"],
            }
        )

    shared_keys = _shared_leakage_keys(assignments, aggregate_source_files=aggregate_source_files)
    warnings: list[str] = []
    largest_component = max((int(component["size"]) for component in components), default=0)
    if largest_component > max(targets["heldout"], targets["hard"], 1):
        warnings.append(
            "largest leakage component is larger than heldout/hard targets; split ratios are approximate until OSS sample diversity grows"
        )

    summary = {
        "total_rows": len(rows),
        "total_components": len(components),
        "ratios": {"dev": dev_ratio, "heldout": heldout_ratio, "hard": hard_ratio},
        "aggregate_source_files": sorted(aggregate_source_files),
        "target_rows": targets,
        "actual_rows": {split: len(split_rows) for split, split_rows in assignments.items()},
        "actual_components": dict(Counter(item["split"] for item in component_summaries)),
        "split_stats": {split: _split_stats(split_rows) for split, split_rows in assignments.items()},
        "leakage_check": {
            "shared_keys_across_splits": len(shared_keys),
            "examples": shared_keys[:20],
        },
        "largest_components": sorted(component_summaries, key=lambda item: -int(item["rows"]))[:10],
        "warnings": warnings,
    }
    return assignments, summary


def _split_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "provinces": dict(Counter(_clean(row.get("province")) for row in rows)),
        "source_files": dict(Counter(_clean(row.get("source_file")) for row in rows)),
        "buckets": dict(Counter(_clean(row.get("bucket")) for row in rows)),
        "hard_eligible_rows": sum(1 for row in rows if _hard_eligible(row)),
    }


def _shared_leakage_keys(assignments: dict[str, list[dict[str, Any]]], *, aggregate_source_files: set[str]) -> list[dict[str, Any]]:
    owners: dict[str, str] = {}
    shared: dict[str, set[str]] = defaultdict(set)
    for split, rows in assignments.items():
        for row in rows:
            for key in _leakage_keys(row, aggregate_source_files=aggregate_source_files):
                owner = owners.get(key)
                if owner is None:
                    owners[key] = split
                elif owner != split:
                    shared[key].update([owner, split])
    return [{"key": key, "splits": sorted(splits)} for key, splits in sorted(shared.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-safe split for Goal OSS Learning samples")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input OSS samples JSONL")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output split directory")
    parser.add_argument("--dev-ratio", type=float, default=0.6)
    parser.add_argument("--heldout-ratio", type=float, default=0.2)
    parser.add_argument("--hard-ratio", type=float, default=0.2)
    parser.add_argument(
        "--aggregate-source-file",
        action="append",
        default=[],
        help="Treat this physical source_file as an aggregate container and do not use it as a leakage key",
    )
    args = parser.parse_args()

    ratio_sum = args.dev_ratio + args.heldout_ratio + args.hard_ratio
    if abs(ratio_sum - 1.0) > 1e-6:
        raise SystemExit(f"split ratios must sum to 1.0, got {ratio_sum}")

    rows = _read_jsonl(Path(args.input))
    aggregate_source_files = {_norm(value) for value in args.aggregate_source_file if _norm(value)}
    assignments, summary = split_samples(
        rows,
        dev_ratio=args.dev_ratio,
        heldout_ratio=args.heldout_ratio,
        hard_ratio=args.hard_ratio,
        aggregate_source_files=aggregate_source_files,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        _write_jsonl(output_dir / f"{split}.jsonl", assignments[split])

    summary.update(
        {
            "input": args.input,
            "output_dir": str(output_dir),
            "outputs": {split: str(output_dir / f"{split}.jsonl") for split in SPLITS},
            "summary_output": str(output_dir / "split_summary.json"),
        }
    )
    (output_dir / "split_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
