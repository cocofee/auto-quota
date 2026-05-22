from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.goal_search.national_index import DEFAULT_INDEX_PATH, build_national_index  # noqa: E402


def _split_csv(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build standalone national quota index for goal search")
    parser.add_argument("--output", default=str(DEFAULT_INDEX_PATH), help="Output SQLite path")
    parser.add_argument("--province", default="", help="Comma-separated province names to include")
    parser.add_argument("--limit-provinces", type=int, default=None, help="Debug limit")
    parser.add_argument("--limit-rows-per-province", type=int, default=None, help="Debug limit")
    args = parser.parse_args()

    summary = build_national_index(
        output_path=Path(args.output),
        province_filter=_split_csv(args.province) if args.province else None,
        limit_provinces=args.limit_provinces,
        limit_rows_per_province=args.limit_rows_per_province,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
