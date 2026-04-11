"""
Backfill enhanced price-reference fields and rerun outlier scans.

Usage:
    python tools/backfill_price_reference_enhancements.py --boq
    python tools/backfill_price_reference_enhancements.py --quote
    python tools/backfill_price_reference_enhancements.py --boq --document-id 123
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

import config
from src.price_reference_db import PriceReferenceDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill normalized/material/date/price_type fields and rerun price outlier scans."
    )
    parser.add_argument(
        "--price-db",
        default=str(config.get_price_reference_db_path()),
        help="Target price_reference.db path.",
    )
    parser.add_argument(
        "--document-id",
        type=int,
        default=None,
        help="Only process one document_id.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Backfill batch size.",
    )
    parser.add_argument(
        "--boq",
        action="store_true",
        help="Process historical_boq_items.",
    )
    parser.add_argument(
        "--quote",
        action="store_true",
        help="Process historical_quote_items.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.boq and not args.quote:
        logger.error("nothing selected; use --boq and/or --quote")
        return 1

    db = PriceReferenceDB(db_path=Path(args.price_db))
    results = {}

    if args.boq:
        logger.info("backfilling historical_boq_items enhancements")
        results["boq_backfill"] = db.backfill_boq_item_enhancements(
            document_id=args.document_id,
            batch_size=args.batch_size,
        )
        results["boq_outliers"] = db.run_outlier_scan(
            table="historical_boq_items",
            document_id=args.document_id,
        )

    if args.quote:
        logger.info("backfilling historical_quote_items enhancements")
        results["quote_backfill"] = db.backfill_quote_item_enhancements(
            document_id=args.document_id,
            batch_size=args.batch_size,
        )
        results["quote_outliers"] = db.run_outlier_scan(
            table="historical_quote_items",
            document_id=args.document_id,
        )

    logger.info(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
