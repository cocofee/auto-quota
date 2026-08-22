from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.promotion import (
    PromotionValidationError,
    build_promoted_dataset,
    write_promoted_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate two independent reviews and promote accepted rows"
    )
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--review-queue-manifest")
    parser.add_argument("--review-a", required=True)
    parser.add_argument("--review-b", required=True)
    parser.add_argument("--reviewer-registry", required=True)
    parser.add_argument(
        "--national-index",
        default=str(
            PROJECT_ROOT / "data" / "goal_search" / "national_index.sqlite"
        ),
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--coverage-contract")
    parser.add_argument("--splits", default="dev,heldout")
    parser.add_argument("--seed", default="independent-gold-split-v1")
    args = parser.parse_args()

    coverage_requirements = None
    if args.coverage_contract:
        coverage_path = Path(args.coverage_contract).resolve()
        try:
            coverage_requirements = json.loads(
                coverage_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"invalid coverage contract: {exc}")
        if not isinstance(coverage_requirements, dict):
            parser.error("coverage contract must be a JSON object")

    split_names = tuple(value.strip() for value in args.splits.split(",") if value.strip())
    try:
        rows, manifest = build_promoted_dataset(
            review_queue_path=args.review_queue,
            review_queue_manifest_path=args.review_queue_manifest,
            review_a_path=args.review_a,
            review_b_path=args.review_b,
            national_index_path=args.national_index,
            reviewer_registry_path=args.reviewer_registry,
            coverage_requirements=coverage_requirements,
            split_names=split_names,
            seed=args.seed,
        )
    except PromotionValidationError as exc:
        print(
            json.dumps(
                {"status": "rejected", "errors": list(exc.errors)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(
            json.dumps(
                {"status": "input_error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    outputs = write_promoted_dataset(
        rows=rows,
        manifest=manifest,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "status": "promoted",
                "promoted_rows": manifest["promoted_rows"],
                "agreed_rejected_rows": manifest["agreed_rejected_rows"],
                "scope": manifest["scope"],
                "system_baseline_eligible": manifest[
                    "system_baseline_eligible"
                ],
                "split_counts": manifest["split_assignment"]["counts"],
                "outputs": {name: str(path) for name, path in outputs.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
