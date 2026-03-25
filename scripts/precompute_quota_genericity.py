from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def _iter_records(path: Path):
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("json_results"), list):
        for province_result in payload.get("json_results") or []:
            province = province_result.get("province", "")
            for detail in province_result.get("details") or []:
                detail = dict(detail)
                detail["province"] = province
                yield detail
        return
    if isinstance(payload, list):
        for record in payload:
            yield record


def build_genericity(paths: list[Path]) -> dict[str, dict]:
    retrieval_hits = defaultdict(int)
    positive_hits = defaultdict(int)
    for path in paths:
        for record in _iter_records(path):
            candidate_snapshots = record.get("candidate_snapshots") or record.get("retrieved_candidates") or []
            positive_ids = set(
                str(quota_id or "")
                for quota_id in (record.get("stored_ids") or record.get("expected_quota_ids") or [])
                if str(quota_id or "")
            )
            for candidate in candidate_snapshots:
                quota_id = str(candidate.get("quota_id", "") or "")
                if not quota_id:
                    continue
                retrieval_hits[quota_id] += 1
            for quota_id in positive_ids:
                positive_hits[quota_id] += 1

    output = {}
    all_ids = set(retrieval_hits) | set(positive_hits)
    for quota_id in sorted(all_ids):
        retrieved = retrieval_hits[quota_id]
        positive = positive_hits[quota_id]
        success_ratio = (positive / retrieved) if retrieved else 0.0
        genericity_index = math.log1p(retrieved) - math.log1p(positive)
        output[quota_id] = {
            "retrieval_hits": retrieved,
            "positive_hits": positive,
            "success_ratio": round(success_ratio, 6),
            "genericity_index": round(genericity_index, 6),
            "retrieval_popularity": round(math.log1p(retrieved), 6),
            "specificity_score": round(1.0 / (1.0 + max(genericity_index, 0.0)), 6),
        }
    return output


def main():
    parser = argparse.ArgumentParser(description="Precompute per-quota genericity statistics.")
    parser.add_argument("--input", nargs="+", required=True, help="benchmark summary json / asset jsonl paths")
    parser.add_argument("--output", required=True, help="output json path")
    args = parser.parse_args()

    paths = [Path(path) for path in args.input]
    stats = build_genericity(paths)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"quotas": stats}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(stats)} quota genericity records to {output}")


if __name__ == "__main__":
    main()
