from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.review_sampling import (
    build_review_queue,
    write_review_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic multi-province human review queue"
    )
    parser.add_argument(
        "--bill-library",
        default=str(PROJECT_ROOT / "data" / "bill_library.db"),
    )
    parser.add_argument(
        "--national-index",
        default=str(PROJECT_ROOT / "data" / "goal_search" / "national_index.sqlite"),
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--per-province", type=int, default=20)
    parser.add_argument("--max-per-project", type=int, default=2)
    parser.add_argument("--seed", default="independent-gold-v1")
    args = parser.parse_args()

    rows, manifest = build_review_queue(
        bill_library_path=args.bill_library,
        national_index_path=args.national_index,
        target_per_province=args.per_province,
        max_per_project=args.max_per_project,
        seed=args.seed,
    )
    outputs = write_review_queue(
        rows=rows,
        manifest=manifest,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "selected_rows": manifest["selected_rows"],
                "selected_provinces": manifest["selected_provinces"],
                "selected_projects": manifest["selected_projects"],
                "repaired_field_values": manifest["text_repair"][
                    "repaired_field_values"
                ],
                "provinces_without_candidate_quota_books": manifest[
                    "provinces_without_candidate_quota_books"
                ],
                "outputs": {name: str(path) for name, path in outputs.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
