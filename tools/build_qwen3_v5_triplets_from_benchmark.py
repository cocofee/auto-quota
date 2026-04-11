from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "output" / "benchmark_assets" / "20260324_2212_full_qwen3_v4"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "qwen3_training_triplets_v5_benchmark.jsonl"
DEFAULT_RECALL_ONLY_PATH = PROJECT_ROOT / "data" / "qwen3_training_triplets_v5_recall_only.jsonl"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "qwen3_training_triplets_v5_benchmark_manifest.json"


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _normalize_key(value: object) -> str:
    return _clean_text(value).lower()


def _stable_split(group_key: str, val_ratio: float, test_ratio: float) -> str:
    digest = hashlib.md5(group_key.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < test_ratio:
        return "test"
    if bucket < test_ratio + val_ratio:
        return "val"
    return "train"


def _collect_positive_pairs(row: dict, max_positive: int) -> list[tuple[str, str]]:
    ids = list(row.get("expected_quota_ids") or [])
    names = list(row.get("expected_quota_names") or [])
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    pair_count = max(len(ids), len(names))
    for idx in range(pair_count):
        quota_id = _clean_text(ids[idx] if idx < len(ids) else "")
        quota_name = _clean_text(names[idx] if idx < len(names) else "")
        if not quota_name:
            continue
        key = (quota_id, quota_name)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
        if len(pairs) >= max_positive:
            break
    return pairs


def _collect_negative_pairs(row: dict, max_negatives: int) -> list[tuple[str, str]]:
    positive_ids = {_clean_text(value) for value in row.get("expected_quota_ids") or [] if _clean_text(value)}
    positive_names = {_normalize_key(value) for value in row.get("expected_quota_names") or [] if _clean_text(value)}
    negatives: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    candidates: list[tuple[str, str]] = []
    predicted_id = _clean_text(row.get("predicted_quota_id"))
    predicted_name = _clean_text(row.get("predicted_quota_name"))
    if predicted_id or predicted_name:
        candidates.append((predicted_id, predicted_name))

    for item in row.get("retrieved_candidates") or []:
        candidates.append((_clean_text(item.get("quota_id")), _clean_text(item.get("name"))))

    for quota_id, quota_name in candidates:
        if not quota_name:
            continue
        if quota_id and quota_id in positive_ids:
            continue
        if _normalize_key(quota_name) in positive_names:
            continue
        key = (quota_id, quota_name)
        if key in seen:
            continue
        seen.add(key)
        negatives.append(key)
        if len(negatives) >= max_negatives:
            break
    return negatives


def build_triplets_from_asset_root(
    asset_root: str | Path,
    max_neg_recall: int = 10,
    max_neg_rerank: int = 4,
    max_positive: int = 3,
    val_ratio: float = 0.05,
    test_ratio: float = 0.05,
) -> tuple[list[dict], list[dict], dict]:
    asset_root = Path(asset_root)
    error_files = sorted(asset_root.rglob("all_errors.jsonl"))
    triplets: list[dict] = []
    recall_triplets: list[dict] = []
    seen_triplets: set[tuple[str, str, str]] = set()

    file_stats = Counter()
    cause_stats = Counter()
    province_stats = Counter()
    split_stats = Counter()
    source_stats = Counter()
    unique_queries: set[str] = set()

    for error_file in error_files:
        run_id = error_file.parent.name
        for row_index, row in enumerate(_read_jsonl(error_file), start=1):
            query = _clean_text(row.get("bill_text") or row.get("bill_name"))
            if not query:
                continue

            positive_pairs = _collect_positive_pairs(row, max_positive=max_positive)
            if not positive_pairs:
                continue

            is_recall_miss = not bool(row.get("oracle_in_candidates"))
            negative_pairs = _collect_negative_pairs(
                row,
                max_negatives=max_neg_recall if is_recall_miss else max_neg_rerank,
            )
            if not negative_pairs:
                continue

            province = _clean_text(row.get("province"))
            specialty = _clean_text(row.get("specialty"))
            cause = _clean_text(row.get("cause"))
            group_key = f"{province}|{specialty}|{query}"
            split = _stable_split(group_key, val_ratio=val_ratio, test_ratio=test_ratio)
            source_type = "recall_miss" if is_recall_miss else "ranking_error"

            file_stats["rows"] += 1
            cause_stats[cause or "unknown"] += 1
            province_stats[province or "unknown"] += 1
            unique_queries.add(group_key)

            for positive_id, positive_name in positive_pairs:
                for negative_id, negative_name in negative_pairs:
                    dedupe_key = (
                        _normalize_key(query),
                        _normalize_key(positive_name),
                        _normalize_key(negative_name),
                    )
                    if dedupe_key in seen_triplets:
                        continue
                    seen_triplets.add(dedupe_key)

                    triplet = {
                        "sample_id": f"{run_id}:{row_index}:{len(triplets) + 1}",
                        "run_id": run_id,
                        "source_file": str(error_file),
                        "source_type": source_type,
                        "split": split,
                        "query": query,
                        "positive": positive_name,
                        "negative": negative_name,
                        "positive_id": positive_id,
                        "negative_id": negative_id,
                        "province": province,
                        "specialty": specialty,
                        "cause": cause,
                        "oracle_in_candidates": bool(row.get("oracle_in_candidates")),
                        "bill_name": _clean_text(row.get("bill_name")),
                        "trace_path": list(row.get("trace_path") or []),
                    }
                    triplets.append(triplet)
                    split_stats[split] += 1
                    source_stats[source_type] += 1
                    if is_recall_miss:
                        recall_triplets.append(triplet)

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asset_root": str(asset_root),
        "error_files": [str(path) for path in error_files],
        "parameters": {
            "max_neg_recall": max_neg_recall,
            "max_neg_rerank": max_neg_rerank,
            "max_positive": max_positive,
            "val_ratio": val_ratio,
            "test_ratio": test_ratio,
        },
        "counts": {
            "triplets": len(triplets),
            "recall_only_triplets": len(recall_triplets),
            "unique_queries": len(unique_queries),
            "rows_used": file_stats["rows"],
        },
        "source_type_counts": dict(source_stats),
        "split_counts": dict(split_stats),
        "cause_counts": dict(cause_stats),
        "province_top20": dict(province_stats.most_common(20)),
    }
    return triplets, recall_triplets, manifest


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def export_triplets(
    asset_root: str | Path,
    output_path: str | Path,
    recall_only_path: str | Path,
    manifest_path: str | Path,
    max_neg_recall: int = 10,
    max_neg_rerank: int = 4,
    max_positive: int = 3,
    val_ratio: float = 0.05,
    test_ratio: float = 0.05,
) -> dict:
    triplets, recall_triplets, manifest = build_triplets_from_asset_root(
        asset_root=asset_root,
        max_neg_recall=max_neg_recall,
        max_neg_rerank=max_neg_rerank,
        max_positive=max_positive,
        val_ratio=val_ratio,
        test_ratio=test_ratio,
    )
    output_path = Path(output_path)
    recall_only_path = Path(recall_only_path)
    manifest_path = Path(manifest_path)

    _write_jsonl(output_path, triplets)
    _write_jsonl(recall_only_path, recall_triplets)
    manifest["files"] = {
        "triplets": str(output_path),
        "recall_only_triplets": str(recall_only_path),
        "manifest": str(manifest_path),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build qwen3 v5 benchmark-driven triplets")
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT), help="benchmark asset root or run dir")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="all triplets JSONL path")
    parser.add_argument("--recall-only-output", default=str(DEFAULT_RECALL_ONLY_PATH), help="recall-only JSONL path")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="manifest JSON path")
    parser.add_argument("--max-neg-recall", type=int, default=10, help="max negatives per recall miss row")
    parser.add_argument("--max-neg-rerank", type=int, default=4, help="max negatives per ranking-error row")
    parser.add_argument("--max-positive", type=int, default=3, help="max positives per row")
    parser.add_argument("--val-ratio", type=float, default=0.05, help="validation split ratio")
    parser.add_argument("--test-ratio", type=float, default=0.05, help="test split ratio")
    args = parser.parse_args()

    manifest = export_triplets(
        asset_root=args.asset_root,
        output_path=args.output,
        recall_only_path=args.recall_only_output,
        manifest_path=args.manifest,
        max_neg_recall=args.max_neg_recall,
        max_neg_rerank=args.max_neg_rerank,
        max_positive=args.max_positive,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
    )
    print(f"[OK] wrote triplets: {manifest['files']['triplets']}")
    print(f"[OK] wrote recall-only triplets: {manifest['files']['recall_only_triplets']}")
    print(f"[OK] wrote manifest: {manifest['files']['manifest']}")
    print(
        "[SUMMARY] "
        f"triplets={manifest['counts']['triplets']} "
        f"recall_only={manifest['counts']['recall_only_triplets']} "
        f"unique_queries={manifest['counts']['unique_queries']}"
    )
    print(f"[SOURCE] {manifest['source_type_counts']}")
    print(f"[SPLIT] {manifest['split_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
