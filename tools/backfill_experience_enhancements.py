"""
Backfill experience metadata/layers and optionally run promotion scans.

Examples:
    python tools/backfill_experience_enhancements.py --dry-run --limit 1000
    python tools/backfill_experience_enhancements.py --sources project_import oss_import --limit 5000
    python tools/backfill_experience_enhancements.py --limit 20000 --run-promotion-scan --promotion-limit-groups 1000
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

import config
from src.experience_db import ExperienceDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill experience normalized/material/quota fields, recalc layers, and optionally run promotion scans."
    )
    parser.add_argument(
        "--experience-db",
        default=str(config.get_experience_db_path()),
        help="Target experience.db path.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Backfill batch size.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of records to backfill.",
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        default=None,
        help="Only process selected sources.",
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Include deleted-layer records in the backfill.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute stats without writing changes.",
    )
    parser.add_argument(
        "--run-promotion-scan",
        action="store_true",
        help="Run verified -> authority promotion scan after backfill.",
    )
    parser.add_argument(
        "--promotion-batch-size",
        type=int,
        default=500,
        help="Promotion update batch size.",
    )
    parser.add_argument(
        "--promotion-limit-groups",
        type=int,
        default=None,
        help="Max number of verified groups to scan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = ExperienceDB(db_path=Path(args.experience_db))

    logger.info("starting experience enhancement backfill")
    backfill_result = db.backfill_experience_enhancements(
        batch_size=args.batch_size,
        limit=args.limit,
        sources=args.sources,
        include_deleted=args.include_deleted,
        dry_run=args.dry_run,
    )
    logger.info({"backfill": backfill_result})

    if args.run_promotion_scan:
        logger.info("starting experience promotion scan")
        promotion_result = db.run_promotion_scan(
            batch_size=args.promotion_batch_size,
            limit_groups=args.promotion_limit_groups,
            dry_run=args.dry_run,
        )
        logger.info({"promotion": promotion_result})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
