from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ltr_feature_extractor import extract_group_features


def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_from_snapshot(snapshot: dict) -> dict:
    return {
        "quota_id": snapshot.get("quota_id", ""),
        "name": snapshot.get("name", ""),
        "unit": snapshot.get("unit", ""),
        "param_match": snapshot.get("param_match", True),
        "param_tier": snapshot.get("param_tier", 1),
        "bm25_score": snapshot.get("bm25_score"),
        "vector_score": snapshot.get("vector_score"),
        "hybrid_score": snapshot.get("hybrid_score"),
        "rerank_score": snapshot.get("rerank_score"),
        "semantic_rerank_score": snapshot.get("semantic_rerank_score"),
        "spec_rerank_score": snapshot.get("spec_rerank_score"),
        "param_score": snapshot.get("param_score"),
        "logic_score": snapshot.get("logic_score"),
        "feature_alignment_score": snapshot.get("feature_alignment_score"),
        "manual_structured_score": snapshot.get("manual_structured_score"),
        "ltr_score": snapshot.get("ltr_score"),
        "candidate_canonical_features": snapshot.get("candidate_canonical_features") or {},
        "ltr_feature_snapshot": snapshot.get("ltr_feature_snapshot") or {},
    }


def export_dataset(summary_path: Path, output_path: Path):
    payload = _load_summary(summary_path)
    rows: list[dict] = []
    json_results = payload.get("json_results") or payload.get("results") or []
    group_index = 0
    for province_result in json_results:
        province = province_result.get("province", "")
        for detail in province_result.get("details") or []:
            group_id = f"{province}::{group_index}"
            group_index += 1
            snapshots = list(detail.get("candidate_snapshots") or [])
            if not snapshots:
                continue
            item = {
                "name": detail.get("bill_name", ""),
                "description": detail.get("bill_text", ""),
                "specialty": detail.get("specialty", ""),
            }
            candidates = [_candidate_from_snapshot(snapshot) for snapshot in snapshots]
            features = extract_group_features(item, candidates, {"province": province})
            positive_ids = {
                str(quota_id or "") for quota_id in (detail.get("stored_ids") or []) if str(quota_id or "")
            }
            for candidate, feature_row in zip(candidates, features):
                row = {
                    "group_id": group_id,
                    "province": province,
                    "quota_id": candidate.get("quota_id", ""),
                    "label": int(candidate.get("quota_id", "") in positive_ids),
                    "oracle_in_candidates": int(bool(detail.get("oracle_in_candidates", False))),
                    "cause": detail.get("cause", ""),
                    "miss_stage": detail.get("miss_stage", ""),
                    "confidence": detail.get("confidence", 0),
                    "final_changed_by": detail.get("final_changed_by", ""),
                    "pre_ltr_top1_id": detail.get("pre_ltr_top1_id", ""),
                    "post_ltr_top1_id": detail.get("post_ltr_top1_id", ""),
                    "post_arbiter_top1_id": detail.get("post_arbiter_top1_id", ""),
                    "post_final_top1_id": detail.get("post_final_top1_id", ""),
                }
                row.update(feature_row)
                rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} candidate rows to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Export pair-level LTR dataset from benchmark summary.")
    parser.add_argument("--input", required=True, help="benchmark summary json path")
    parser.add_argument("--output", required=True, help="output csv path")
    args = parser.parse_args()
    export_dataset(Path(args.input), Path(args.output))


if __name__ == "__main__":
    main()
