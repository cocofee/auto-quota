from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.inventory import build_coverage_inventory


def _named_paths(values: list[str], parser: argparse.ArgumentParser) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name.strip() or not raw_path.strip():
            parser.error(f"expected NAME=PATH, got: {value}")
        if name in result:
            parser.error(f"duplicate split name: {name}")
        path = Path(raw_path).resolve()
        if not path.is_file():
            parser.error(f"dataset not found: {path}")
        result[name.strip()] = path
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit accuracy evidence coverage without running matching algorithms"
    )
    parser.add_argument("--primary")
    parser.add_argument("--oss-split", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--oss-aggregate-source", action="append", default=[])
    parser.add_argument(
        "--historical-split",
        action="append",
        default=[],
        metavar="NAME=PATH",
    )
    parser.add_argument("--historical-aggregate-source", action="append", default=[])
    parser.add_argument("--national-index")
    parser.add_argument("--bill-library")
    parser.add_argument("--oss-root")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    oss_splits = _named_paths(args.oss_split, parser)
    historical_splits = _named_paths(args.historical_split, parser)
    if not any(
        (
            args.primary,
            oss_splits,
            historical_splits,
            args.national_index,
            args.bill_library,
            args.oss_root,
        )
    ):
        parser.error("at least one evidence source is required")

    report = build_coverage_inventory(
        primary_path=args.primary,
        oss_splits=oss_splits,
        historical_splits=historical_splits,
        oss_aggregate_sources=args.oss_aggregate_source,
        historical_aggregate_sources=args.historical_aggregate_source,
        national_index_path=args.national_index,
        bill_library_path=args.bill_library,
        oss_root=args.oss_root,
    )
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "system_baseline_eligible": report["system_baseline_eligible"],
                "evidence_sets": {
                    name: values["rows"]
                    for name, values in report["evidence_sets"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
