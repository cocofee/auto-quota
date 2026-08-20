from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.reconstructed_assets import materialize_province_db  # noqa: E402


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize an evaluation-only province database"
    )
    parser.add_argument("--national-index", required=True)
    parser.add_argument("--province", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--primary", required=True)
    parser.add_argument("--production-provinces-dir")
    args = parser.parse_args()

    report = materialize_province_db(
        national_index=args.national_index,
        province=args.province,
        output_root=args.output_root,
        primary_dataset=args.primary,
        production_provinces_dir=args.production_provinces_dir,
    )
    print(json.dumps(_jsonable(asdict(report)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
