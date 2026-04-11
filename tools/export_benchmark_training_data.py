from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.constrained_ranker import build_constrained_ranker_training_sample


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ASSET_ROOT = PROJECT_ROOT / "output" / "benchmark_assets"
DEFAULT_OUT_ROOT = PROJECT_ROOT / "output" / "benchmark_training"


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _stable_split(group_key: str, val_ratio: float = 0.1, test_ratio: float = 0.1) -> str:
    digest = hashlib.md5(group_key.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < test_ratio:
        return "test"
    if bucket < test_ratio + val_ratio:
        return "val"
    return "train"


def _candidate_from_retrieved(candidate: dict) -> dict:
    candidate = candidate or {}
    reasoning = candidate.get("reasoning") or {}
    return {
        "quota_id": str(candidate.get("quota_id", "") or ""),
        "name": str(candidate.get("name", "") or ""),
        "reasoning": dict(reasoning) if isinstance(reasoning, dict) else {},
    }


def _normalize_candidate_group(record: dict) -> list[dict]:
    candidate_snapshots = list(record.get("candidate_snapshots") or [])
    if candidate_snapshots:
        candidates = [dict(candidate) for candidate in candidate_snapshots if isinstance(candidate, dict)]
    else:
        candidates = [
            _candidate_from_retrieved(candidate)
            for candidate in (record.get("retrieved_candidates") or [])
            if isinstance(candidate, dict)
        ]

    predicted_id = str(record.get("predicted_quota_id", "") or "")
    predicted_name = str(record.get("predicted_quota_name", "") or "")
    seen_keys: set[tuple[str, str]] = set()
    normalized: list[dict] = []
    for candidate in candidates:
        key = (
            str(candidate.get("quota_id", "") or ""),
            str(candidate.get("name", "") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        normalized.append(candidate)

    predicted_key = (predicted_id, predicted_name)
    if (predicted_id or predicted_name) and predicted_key not in seen_keys:
        normalized.insert(0, {"quota_id": predicted_id, "name": predicted_name})
    return normalized


def _build_cgr_item(record: dict) -> dict:
    bill_name = _clean_text(record.get("bill_name"))
    bill_text = _clean_text(record.get("bill_text"))
    specialty = _clean_text(record.get("specialty"))
    province = _clean_text(record.get("province"))
    return {
        "name": bill_name,
        "description": bill_text,
        "specialty": specialty,
        "province": province,
        "search_books": [specialty] if specialty else [],
    }


def _build_cgr_context(record: dict) -> dict:
    bill_name = _clean_text(record.get("bill_name"))
    bill_text = _clean_text(record.get("bill_text"))
    specialty = _clean_text(record.get("specialty"))
    province = _clean_text(record.get("province"))
    raw_query = bill_text or bill_name
    return {
        "province": province,
        "search_books": [specialty] if specialty else [],
        "search_query": raw_query,
        "canonical_query": {
            "raw_query": raw_query,
            "search_query": raw_query,
            "validation_query": raw_query,
        },
    }


def _build_cgr_training_rows(asset_root: Path) -> tuple[list[dict], list[dict]]:
    group_rows: list[dict] = []
    accept_rows: list[dict] = []

    for path in sorted(asset_root.rglob("all_errors.jsonl")):
        run_id = path.parent.name
        for idx, record in enumerate(_read_jsonl(path), start=1):
            candidates = _normalize_candidate_group(record)
            if not candidates:
                continue
            query_text = _clean_text(record.get("bill_text") or record.get("bill_name"))
            specialty = _clean_text(record.get("specialty"))
            province = _clean_text(record.get("province"))
            split = _stable_split(f"{province}|{specialty}|{query_text}")
            source_type = "ranking_error" if record.get("oracle_in_candidates") else "recall_miss"
            sample_id = f"{run_id}:cgr:{idx}"
            sample = build_constrained_ranker_training_sample(
                _build_cgr_item(record),
                candidates,
                _build_cgr_context(record),
                oracle_quota_ids=list(record.get("expected_quota_ids") or []),
                sample_id=sample_id,
                split=split,
                metadata={
                    "run_id": run_id,
                    "source_file": str(path),
                    "source_type": source_type,
                    "cause": str(record.get("cause", "") or ""),
                    "oracle_in_candidates": bool(record.get("oracle_in_candidates", False)),
                    "expected_quota_names": list(record.get("expected_quota_names") or []),
                    "predicted_quota_id": str(record.get("predicted_quota_id", "") or ""),
                    "predicted_quota_name": str(record.get("predicted_quota_name", "") or ""),
                    "candidate_count": int(record.get("candidate_count", len(candidates)) or len(candidates)),
                    "trace_path": list(record.get("trace_path") or []),
                    "match_source": str(record.get("match_source", "") or ""),
                    "miss_stage": str(record.get("miss_stage", "") or ""),
                },
            )
            group_rows.append(sample)

            accept_row = dict(sample.get("accept") or {})
            accept_row.update({
                "sample_id": sample_id,
                "split": split,
                "run_id": run_id,
                "source_file": str(path),
                "source_type": source_type,
                "cause": str(record.get("cause", "") or ""),
                "oracle_in_candidates": bool(record.get("oracle_in_candidates", False)),
                "query_text": query_text,
                "bill_name": _clean_text(record.get("bill_name")),
                "specialty": specialty,
                "expected_quota_ids": list(record.get("expected_quota_ids") or []),
                "expected_quota_names": list(record.get("expected_quota_names") or []),
                "gate": float(sample.get("gate", 0.5) or 0.5),
                "top_probability": float(sample.get("accept", {}).get("p1", 0.0) or 0.0),
            })
            accept_rows.append(accept_row)

    return group_rows, accept_rows


def build_training_datasets(asset_root: str | Path) -> dict[str, list[dict]]:
    asset_root = Path(asset_root)
    rerank_rows: list[dict] = []
    route_rows: list[dict] = []
    tier_rows: list[dict] = []

    for path in asset_root.rglob("rerank_pairs.jsonl"):
        run_id = path.parent.name
        for idx, record in enumerate(_read_jsonl(path), start=1):
            rerank_rows.append({
                "sample_id": f"{run_id}:rerank:{idx}",
                "run_id": run_id,
                "province": str(record.get("province") or "").strip(),
                "specialty": str(record.get("specialty") or "").strip(),
                "bill_name": str(record.get("bill_name") or "").strip(),
                "query_text": str(record.get("bill_text") or record.get("bill_name") or "").strip(),
                "positive_quota_ids": list(record.get("positive_quota_ids") or []),
                "positive_quota_names": list(record.get("positive_quota_names") or []),
                "negative_quota_id": str(record.get("negative_quota_id") or "").strip(),
                "negative_quota_name": str(record.get("negative_quota_name") or "").strip(),
                "retrieved_candidates": list(record.get("retrieved_candidates") or []),
                "trace_path": list(record.get("trace_path") or []),
            })

    for path in asset_root.rglob("route_errors.jsonl"):
        run_id = path.parent.name
        for idx, record in enumerate(_read_jsonl(path), start=1):
            route_rows.append({
                "sample_id": f"{run_id}:route:{idx}",
                "run_id": run_id,
                "province": str(record.get("province") or "").strip(),
                "specialty": str(record.get("specialty") or "").strip(),
                "bill_name": str(record.get("bill_name") or "").strip(),
                "query_text": str(record.get("bill_text") or record.get("bill_name") or "").strip(),
                "expected_book": str(record.get("expected_book") or "").strip(),
                "predicted_book": str(record.get("predicted_book") or "").strip(),
            })

    for path in asset_root.rglob("tier_errors.jsonl"):
        run_id = path.parent.name
        for idx, record in enumerate(_read_jsonl(path), start=1):
            tier_rows.append({
                "sample_id": f"{run_id}:tier:{idx}",
                "run_id": run_id,
                "province": str(record.get("province") or "").strip(),
                "specialty": str(record.get("specialty") or "").strip(),
                "bill_name": str(record.get("bill_name") or "").strip(),
                "query_text": str(record.get("bill_text") or record.get("bill_name") or "").strip(),
                "expected_quota_names": list(record.get("expected_quota_names") or []),
                "predicted_quota_name": str(record.get("predicted_quota_name") or "").strip(),
                "trace_path": list(record.get("trace_path") or []),
            })

    cgr_group_rows, cgr_accept_rows = _build_cgr_training_rows(asset_root)

    return {
        "rerank": rerank_rows,
        "route": route_rows,
        "tier": tier_rows,
        "cgr_group": cgr_group_rows,
        "cgr_accept": cgr_accept_rows,
    }


def export_training_datasets(asset_root: str | Path, out_root: str | Path) -> tuple[Path, dict]:
    datasets = build_training_datasets(asset_root)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    counts = Counter()
    written_files: dict[str, str] = {}
    file_map = {
        "rerank": "rerank_train.jsonl",
        "route": "route_train.jsonl",
        "tier": "tier_train.jsonl",
        "cgr_group": "cgr_group_train.jsonl",
        "cgr_accept": "cgr_accept_train.jsonl",
    }
    for name, rows in datasets.items():
        output_path = out_root / file_map[name]
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        counts[name] = len(rows)
        written_files[name] = str(output_path)

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asset_root": str(Path(asset_root)),
        "counts": dict(counts),
        "files": written_files,
        "notes": {
            "cgr_group": "Derived from benchmark error assets only. Suitable for hard-case ranking supervision.",
            "cgr_accept": "Accept rows currently come from benchmark error assets only, not full online traffic.",
        },
    }
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export benchmark assets into training datasets")
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT), help="benchmark asset root")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="training dataset output root")
    args = parser.parse_args()

    manifest_path, manifest = export_training_datasets(args.asset_root, args.out_root)
    print(f"[OK] wrote benchmark training manifest: {manifest_path}")
    print(f"  counts: {manifest.get('counts', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
