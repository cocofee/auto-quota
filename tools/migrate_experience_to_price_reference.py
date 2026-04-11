"""
Migrate experience.db records into the unified price reference database.

This script does not try to fabricate prices that do not exist in the source.
It converts existing experience records into seed BOQ reference items so the
new unified price reference layer can immediately reuse:

- bill name / normalized bill text
- quota codes / quota names
- unit
- specialty / region / project metadata
- attached materials payload

Price fields such as composite_unit_price remain NULL until original source
files are re-parsed or future backfill steps enrich them.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

from loguru import logger

import config
from db.sqlite import connect as db_connect
from db.sqlite import connect_init as db_connect_init


PRICE_REFERENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synthetic_key TEXT NOT NULL UNIQUE,
    document_type TEXT NOT NULL DEFAULT 'experience_seed',
    project_name TEXT DEFAULT '',
    project_stage TEXT DEFAULT 'historical_import',
    specialty TEXT DEFAULT '',
    system_name TEXT DEFAULT '',
    subsystem_name TEXT DEFAULT '',
    region TEXT DEFAULT '',
    currency TEXT DEFAULT 'CNY',
    source_date TEXT DEFAULT '',
    source_company TEXT DEFAULT '',
    source_file_name TEXT DEFAULT '',
    source_file_path TEXT DEFAULT '',
    source_file_hash TEXT DEFAULT '',
    source_file_ext TEXT DEFAULT '',
    parse_status TEXT DEFAULT 'seeded',
    parse_version TEXT DEFAULT 'experience_migration_v1',
    notes TEXT DEFAULT '',
    created_at REAL DEFAULT (strftime('%s','now')),
    updated_at REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS historical_boq_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    source_experience_id INTEGER NOT NULL UNIQUE,
    seed_source TEXT NOT NULL DEFAULT 'experience_db',
    project_name TEXT DEFAULT '',
    project_stage TEXT DEFAULT 'historical_import',
    specialty TEXT DEFAULT '',
    system_name TEXT DEFAULT '',
    subsystem_name TEXT DEFAULT '',
    boq_code TEXT DEFAULT '',
    boq_name_raw TEXT DEFAULT '',
    boq_name_normalized TEXT DEFAULT '',
    feature_text TEXT DEFAULT '',
    work_content TEXT DEFAULT '',
    item_features_structured TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    quantity REAL,
    composite_unit_price REAL,
    quota_code TEXT DEFAULT '',
    quota_name TEXT DEFAULT '',
    quota_group TEXT DEFAULT '',
    labor_cost REAL,
    material_cost REAL,
    machine_cost REAL,
    management_fee REAL,
    profit REAL,
    measure_fee REAL,
    other_fee REAL,
    tax REAL,
    currency TEXT DEFAULT 'CNY',
    region TEXT DEFAULT '',
    source_date TEXT DEFAULT '',
    source_sheet TEXT DEFAULT '',
    source_row_no INTEGER,
    remarks TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    search_text TEXT DEFAULT '',
    materials_json TEXT DEFAULT '[]',
    bill_text TEXT DEFAULT '',
    migration_flags TEXT DEFAULT '',
    created_at REAL DEFAULT (strftime('%s','now')),
    updated_at REAL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (document_id) REFERENCES price_documents(id)
);

CREATE INDEX IF NOT EXISTS idx_price_documents_type_project
ON price_documents(document_type, project_name);

CREATE INDEX IF NOT EXISTS idx_historical_boq_items_name
ON historical_boq_items(boq_name_raw);

CREATE INDEX IF NOT EXISTS idx_historical_boq_items_normalized
ON historical_boq_items(boq_name_normalized);

CREATE INDEX IF NOT EXISTS idx_historical_boq_items_quota_code
ON historical_boq_items(quota_code);

CREATE INDEX IF NOT EXISTS idx_historical_boq_items_project_region
ON historical_boq_items(project_name, region);
"""


def ensure_target_schema(target_db: Path) -> None:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    conn = db_connect_init(target_db)
    try:
        conn.executescript(PRICE_REFERENCE_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def build_document_key(row: dict) -> str:
    source = (row.get("source") or "").strip()
    project_name = (row.get("project_name") or "").strip()
    province = (row.get("province") or "").strip()
    specialty = (row.get("specialty") or "").strip()
    return "||".join(
        [
            "experience_seed",
            source or "unknown_source",
            project_name or "unknown_project",
            province or "unknown_region",
            specialty or "unknown_specialty",
        ]
    )


def build_document_payload(row: dict) -> dict:
    source = (row.get("source") or "").strip()
    project_name = (row.get("project_name") or "").strip()
    region = (row.get("province") or "").strip()
    specialty = (row.get("specialty") or "").strip()
    synthetic_key = build_document_key(row)
    source_file_name = f"{source or 'experience'}::{project_name or 'unknown'}::{region or 'unknown'}"
    return {
        "synthetic_key": synthetic_key,
        "project_name": project_name,
        "specialty": specialty,
        "region": region,
        "source_file_name": source_file_name,
        "notes": "Seed document generated from experience.db migration; prices pending backfill.",
    }


def get_or_create_document_id(conn, cache: dict[str, int], row: dict) -> int:
    payload = build_document_payload(row)
    synthetic_key = payload["synthetic_key"]
    cached = cache.get(synthetic_key)
    if cached:
        return cached

    existing = conn.execute(
        "SELECT id FROM price_documents WHERE synthetic_key=?",
        (synthetic_key,),
    ).fetchone()
    if existing:
        doc_id = int(existing[0])
        cache[synthetic_key] = doc_id
        return doc_id

    cur = conn.execute(
        """
        INSERT INTO price_documents (
            synthetic_key, document_type, project_name, project_stage, specialty,
            region, currency, source_file_name, parse_status, parse_version,
            notes, created_at, updated_at
        ) VALUES (?, 'experience_seed', ?, 'historical_import', ?, ?, 'CNY', ?, 'seeded',
                  'experience_migration_v1', ?, ?, ?)
        """,
        (
            synthetic_key,
            payload["project_name"],
            payload["specialty"],
            payload["region"],
            payload["source_file_name"],
            payload["notes"],
            time.time(),
            time.time(),
        ),
    )
    doc_id = int(cur.lastrowid)
    cache[synthetic_key] = doc_id
    return doc_id


def derive_feature_text(row: dict) -> str:
    bill_name = (row.get("bill_name") or "").strip()
    bill_text = (row.get("bill_text") or "").strip()
    if not bill_text:
        return ""
    if bill_name and bill_text != bill_name:
        return bill_text
    return ""


def derive_search_text(row: dict, quota_codes: list[str], quota_names: list[str]) -> str:
    parts = [
        (row.get("bill_name") or "").strip(),
        (row.get("bill_text") or "").strip(),
        (row.get("normalized_text") or "").strip(),
        " ".join(quota_codes),
        " ".join(quota_names),
        (row.get("specialty") or "").strip(),
        (row.get("province") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def iter_source_rows(
    source_db: Path,
    limit: int | None = None,
    only_sources: set[str] | None = None,
) -> Iterable[dict]:
    conn = db_connect(source_db, row_factory=True)
    try:
        sql = """
            SELECT id, bill_text, bill_name, bill_code, bill_unit,
                   quota_ids, quota_names, source, confidence, confirm_count,
                   province, project_name, created_at, updated_at,
                   notes, layer, specialty, materials, normalized_text
            FROM experiences
        """
        params: list = []
        clauses: list[str] = []
        if only_sources:
            placeholders = ",".join("?" for _ in only_sources)
            clauses.append(f"source IN ({placeholders})")
            params.extend(sorted(only_sources))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = conn.execute(sql, params)
        for row in cursor:
            yield dict(row)
    finally:
        conn.close()


def migrate(
    source_db: Path,
    target_db: Path,
    limit: int | None = None,
    dry_run: bool = False,
    only_sources: set[str] | None = None,
) -> dict:
    ensure_target_schema(target_db)

    doc_cache: dict[str, int] = {}
    stats = Counter()
    source_counter = Counter()

    if dry_run:
        for row in iter_source_rows(source_db, limit=limit, only_sources=only_sources):
            quota_ids = json_list(row.get("quota_ids"))
            quota_names = json_list(row.get("quota_names"))
            if not quota_ids:
                stats["skipped_no_quota"] += 1
                continue
            source_counter[row.get("source") or ""] += 1
            stats["candidate_rows"] += len(quota_ids)
            stats["experience_rows"] += 1
        return {
            "mode": "dry_run",
            "source_db": str(source_db),
            "target_db": str(target_db),
            "stats": dict(stats),
            "sources": dict(source_counter),
        }

    conn = db_connect_init(target_db)
    try:
        for row in iter_source_rows(source_db, limit=limit, only_sources=only_sources):
            quota_ids = json_list(row.get("quota_ids"))
            quota_names = json_list(row.get("quota_names"))
            materials = json_list(row.get("materials"))
            if not quota_ids:
                stats["skipped_no_quota"] += 1
                continue

            doc_id = get_or_create_document_id(conn, doc_cache, row)
            source_counter[row.get("source") or ""] += 1

            for index, quota_code in enumerate(quota_ids):
                quota_name = quota_names[index] if index < len(quota_names) else ""
                conn.execute(
                    """
                    INSERT OR IGNORE INTO historical_boq_items (
                        document_id, source_experience_id, seed_source,
                        project_name, project_stage, specialty,
                        boq_code, boq_name_raw, boq_name_normalized,
                        feature_text, unit, quota_code, quota_name,
                        currency, region, remarks, tags, search_text,
                        materials_json, bill_text, migration_flags,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        int(row["id"]) * 100 + index,
                        "experience_db",
                        row.get("project_name") or "",
                        "historical_import",
                        row.get("specialty") or "",
                        row.get("bill_code") or "",
                        row.get("bill_name") or row.get("bill_text") or "",
                        row.get("normalized_text") or row.get("bill_text") or "",
                        derive_feature_text(row),
                        row.get("bill_unit") or "",
                        quota_code,
                        quota_name,
                        "CNY",
                        row.get("province") or "",
                        (row.get("notes") or "").strip(),
                        json.dumps(
                            {
                                "source": row.get("source") or "",
                                "layer": row.get("layer") or "",
                                "confidence": row.get("confidence"),
                                "confirm_count": row.get("confirm_count"),
                            },
                            ensure_ascii=False,
                        ),
                        derive_search_text(row, quota_ids, quota_names),
                        json.dumps(materials, ensure_ascii=False),
                        row.get("bill_text") or "",
                        "seed_from_experience_db;price_pending_backfill",
                        row.get("created_at") or time.time(),
                        row.get("updated_at") or time.time(),
                    ),
                )
                stats["boq_items_written"] += 1
            stats["experience_rows"] += 1

        conn.commit()
    finally:
        conn.close()

    stats["documents_touched"] = len(doc_cache)
    return {
        "mode": "migrate",
        "source_db": str(source_db),
        "target_db": str(target_db),
        "stats": dict(stats),
        "sources": dict(source_counter),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate experience.db into the unified price reference seed BOQ table."
    )
    parser.add_argument(
        "--source-db",
        default=str(config.get_experience_db_path()),
        help="Path to experience.db",
    )
    parser.add_argument(
        "--target-db",
        default=str(config.get_price_reference_db_path()),
        help="Path to price_reference.db",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N experience rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Profile source rows without writing target data.",
    )
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated source filters, for example: user_confirmed,project_import",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_db = Path(args.source_db)
    target_db = Path(args.target_db)
    only_sources = {item.strip() for item in args.sources.split(",") if item.strip()} or None

    if not source_db.exists():
        logger.error(f"source db does not exist: {source_db}")
        return 1

    result = migrate(
        source_db=source_db,
        target_db=target_db,
        limit=args.limit,
        dry_run=args.dry_run,
        only_sources=only_sources,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
