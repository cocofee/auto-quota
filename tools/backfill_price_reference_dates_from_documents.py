"""
Backfill price-reference item dates from price_documents metadata.

This targets rows whose item-level `source_date` / `price_date_iso` are empty
but the owning document name or project name still carries a recognizable date
token such as:

- 2024-04-13
- 2024.4.13
- 2024年4月13日
- 2024-04
- 2024年4月
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from loguru import logger

import config
from src.price_reference_db import PriceReferenceDB


DATE_TOKEN_SQL = """
(
     d.source_file_name GLOB '*[0-9][0-9][0-9][0-9]-*'
  OR d.source_file_name LIKE '%年%月%'
  OR d.project_name GLOB '*[0-9][0-9][0-9][0-9]-*'
  OR d.project_name LIKE '%年%月%'
)
"""


def _find_target_document_ids(db_path: Path, *, limit: int | None = None) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        sql = f"""
        SELECT d.id
        FROM price_documents d
        WHERE EXISTS (
            SELECT 1
            FROM historical_boq_items b
            WHERE b.document_id = d.id
              AND COALESCE(TRIM(b.price_date_iso), '') = ''
        )
          AND {DATE_TOKEN_SQL}
        ORDER BY d.id
        """
        if limit:
            sql += " LIMIT ?"
            rows = conn.execute(sql, (limit,)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing item dates from price_documents filename/project metadata."
    )
    parser.add_argument(
        "--price-db",
        default=str(config.get_price_reference_db_path()),
        help="Target price_reference.db path.",
    )
    parser.add_argument(
        "--limit-docs",
        type=int,
        default=None,
        help="Only process the first N matching documents.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Inner row batch size passed to backfill_boq_item_enhancements.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print matching document ids; do not write.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.price_db)
    if not db_path.exists():
        logger.error(f"price db does not exist: {db_path}")
        return 1

    doc_ids = _find_target_document_ids(db_path, limit=args.limit_docs)
    payload = {
        "price_db": str(db_path),
        "matching_documents": len(doc_ids),
        "document_ids_preview": doc_ids[:20],
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    db = PriceReferenceDB(db_path=db_path)
    results = []
    total_processed = 0
    total_updated = 0
    for index, document_id in enumerate(doc_ids, start=1):
        logger.info(f"[{index}/{len(doc_ids)}] backfilling item dates for document_id={document_id}")
        result = db.backfill_boq_item_enhancements(document_id=document_id, batch_size=args.batch_size)
        results.append({"document_id": document_id, **result})
        total_processed += int(result.get("processed") or 0)
        total_updated += int(result.get("updated") or 0)

    payload.update(
        {
            "processed_documents": len(results),
            "processed_rows": total_processed,
            "updated_rows": total_updated,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
