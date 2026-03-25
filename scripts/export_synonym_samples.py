from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from recall_error_bucketing import _load_errors, _resolve_errors_path, _synonym_subtype


def main() -> None:
    parser = argparse.ArgumentParser(description="Export top synonym_gap samples by subtype for lexicon work.")
    parser.add_argument("asset", help="benchmark asset directory or all_errors.jsonl path")
    parser.add_argument("--per-type", type=int, default=50, help="max samples per subtype")
    parser.add_argument(
        "--subtypes",
        nargs="+",
        default=["type1_pure_synonym", "type2_hypernym_standard"],
        help="synonym subtypes to export",
    )
    parser.add_argument(
        "--output-dir",
        help="optional output dir (default: <asset_dir>/synonym_samples)",
    )
    args = parser.parse_args()

    errors_path = _resolve_errors_path(args.asset)
    output_dir = Path(args.output_dir) if args.output_dir else errors_path.parent / "synonym_samples"
    output_dir.mkdir(parents=True, exist_ok=True)

    errors = _load_errors(errors_path)
    synonym_cases = [
        case for case in errors
        if case.get("miss_stage") == "recall_miss" and str(case.get("cause") or "") == "synonym_gap"
    ]

    for subtype in args.subtypes:
        picked = []
        for case in synonym_cases:
            if _synonym_subtype(case) != subtype:
                continue
            picked.append({
                "province": case.get("province"),
                "specialty": case.get("specialty"),
                "bill_name": case.get("bill_name"),
                "bill_text": case.get("bill_text"),
                "expected_quota_ids": case.get("expected_quota_ids"),
                "expected_quota_names": case.get("expected_quota_names"),
                "predicted_quota_id": case.get("predicted_quota_id"),
                "predicted_quota_name": case.get("predicted_quota_name"),
                "trace_path": case.get("trace_path"),
            })
            if len(picked) >= args.per_type:
                break

        output_path = output_dir / f"{subtype}.jsonl"
        with output_path.open("w", encoding="utf-8") as fh:
            for row in picked:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{subtype}: {len(picked)} -> {output_path}")


if __name__ == "__main__":
    main()
