"""
Backfill completed-project learning samples from existing priced bill documents.

This reparses `price_documents.document_type='priced_bill_file'` source files and
routes extracted quota-linked bill items through the unified `ingest()` entry so
they land in `experience.db` as `completed_project` learning records.

Examples:
    python tools/backfill_learning_from_price_documents.py --limit 5 --summary-only
    python tools/backfill_learning_from_price_documents.py --document-ids 12280 12279
    python tools/backfill_learning_from_price_documents.py --limit 200 --order asc
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from loguru import logger

import config


BACKEND_DIR = config.PROJECT_ROOT / "web" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reparse priced bill documents and backfill completed_project learning samples."
    )
    parser.add_argument(
        "--price-db",
        default=str(config.get_price_reference_db_path()),
        help="Target price_reference.db path.",
    )
    parser.add_argument(
        "--document-ids",
        nargs="*",
        type=int,
        default=None,
        help="Explicit price_documents IDs to process.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of documents to process when --document-ids is not provided.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset for document scan.",
    )
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="Document scan order by id.",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Only process documents with this region.",
    )
    parser.add_argument(
        "--project-name-like",
        default="",
        help="Optional SQL LIKE filter on project_name.",
    )
    parser.add_argument(
        "--max-row-count",
        type=int,
        default=0,
        help="Only process documents whose historical_boq_items row count is <= this value.",
    )
    parser.add_argument(
        "--sort-by-row-count",
        action="store_true",
        help="Order candidate documents by historical_boq_items row count ascending before id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count records without writing to experience.db.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print aggregate summary only.",
    )
    parser.add_argument(
        "--selection-summary-only",
        action="store_true",
        help="Only summarize the selected documents without reparsing source files.",
    )
    parser.add_argument(
        "--rebuild-fts-after",
        action="store_true",
        help="Rebuild the experience FTS index once after the batch completes.",
    )
    parser.add_argument(
        "--rebuild-vector-after",
        action="store_true",
        help="Rebuild the experience vector index once after the batch completes.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path to write the full JSON report.",
    )
    parser.add_argument(
        "--checkpoint-file",
        default="",
        help="Optional JSON file used to persist processed document IDs across runs.",
    )
    parser.add_argument(
        "--exclude-document-ids-file",
        default="",
        help="Optional JSON/text file containing document IDs to exclude during selection.",
    )
    parser.add_argument(
        "--retry-skipped",
        action="store_true",
        help="Do not exclude skipped document IDs recorded in checkpoint state.",
    )
    parser.add_argument(
        "--exclude-failed",
        action="store_true",
        help="Also exclude failed document IDs recorded in checkpoint state.",
    )
    return parser.parse_args()


def _connect_price_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_id_set(values) -> set[int]:
    normalized: set[int] = set()
    for item in values or []:
        text = str(item).strip()
        if not text:
            continue
        try:
            normalized.add(int(text))
        except ValueError:
            continue
    return normalized


def _load_checkpoint(path: str) -> dict[str, set[int]]:
    if not path:
        return {
            "succeeded_document_ids": set(),
            "failed_document_ids": set(),
            "skipped_document_ids": set(),
        }
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return {
            "succeeded_document_ids": set(),
            "failed_document_ids": set(),
            "skipped_document_ids": set(),
        }
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "succeeded_document_ids": set(),
            "failed_document_ids": set(),
            "skipped_document_ids": set(),
        }
    if not isinstance(payload, dict):
        return {
            "succeeded_document_ids": set(),
            "failed_document_ids": set(),
            "skipped_document_ids": set(),
        }

    # Backward compatibility: older checkpoints only stored processed_document_ids.
    if "processed_document_ids" in payload:
        return {
            "succeeded_document_ids": _normalize_id_set(payload.get("processed_document_ids")),
            "failed_document_ids": set(),
            "skipped_document_ids": set(),
        }

    return {
        "succeeded_document_ids": _normalize_id_set(payload.get("succeeded_document_ids")),
        "failed_document_ids": _normalize_id_set(payload.get("failed_document_ids")),
        "skipped_document_ids": _normalize_id_set(payload.get("skipped_document_ids")),
    }


def _write_checkpoint(path: str, checkpoint_state: dict[str, set[int]]) -> None:
    if not path:
        return
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "succeeded_document_ids": sorted(checkpoint_state.get("succeeded_document_ids", set())),
        "failed_document_ids": sorted(checkpoint_state.get("failed_document_ids", set())),
        "skipped_document_ids": sorted(checkpoint_state.get("skipped_document_ids", set())),
    }
    checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_excluded_document_ids(path: str) -> set[int]:
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.exists():
        return set()
    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return set()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        for key in (
            "document_ids",
            "exclude_document_ids",
            "processed_document_ids",
            "succeeded_document_ids",
        ):
            if key in payload:
                return _normalize_id_set(payload.get(key))
    if isinstance(payload, list):
        return _normalize_id_set(payload)

    raw_tokens = text.replace(",", "\n").splitlines()
    return _normalize_id_set(raw_tokens)


def _resolve_excluded_document_ids(
    checkpoint_state: dict[str, set[int]],
    *,
    excluded_from_file: set[int],
    retry_skipped: bool,
    exclude_failed: bool,
) -> set[int]:
    excluded = set(excluded_from_file)
    excluded.update(checkpoint_state.get("succeeded_document_ids", set()))
    if not retry_skipped:
        excluded.update(checkpoint_state.get("skipped_document_ids", set()))
    if exclude_failed:
        excluded.update(checkpoint_state.get("failed_document_ids", set()))
    return excluded


def _append_not_in_clauses(
    clauses: list[str],
    params: list[object],
    column: str,
    exclude_ids: set[int],
    *,
    chunk_size: int = 800,
) -> None:
    if not exclude_ids:
        return
    ordered_ids = sorted(exclude_ids)
    for offset in range(0, len(ordered_ids), chunk_size):
        chunk = ordered_ids[offset : offset + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        clauses.append(f"{column} NOT IN ({placeholders})")
        params.extend(chunk)


def _select_documents(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    *,
    exclude_ids: set[int] | None = None,
) -> list[sqlite3.Row]:
    exclude_ids = exclude_ids or set()
    if args.document_ids:
        doc_ids = [int(doc_id) for doc_id in args.document_ids if int(doc_id) not in exclude_ids]
        if not doc_ids:
            return []
        placeholders = ",".join("?" for _ in doc_ids)
        return conn.execute(
            f"""
            SELECT id, file_id, project_name, project_stage, specialty, region,
                   NULL AS row_count,
                   source_file_name, source_file_path
            FROM price_documents
            WHERE document_type='priced_bill_file'
              AND id IN ({placeholders})
            ORDER BY id
            """,
            doc_ids,
        ).fetchall()

    base_clauses = ["document_type='priced_bill_file'"]
    base_params: list[object] = []
    if args.region:
        base_clauses.append("region = ?")
        base_params.append(args.region)
    if args.project_name_like:
        base_clauses.append("project_name LIKE ?")
        base_params.append(args.project_name_like)
    _append_not_in_clauses(base_clauses, base_params, "id", exclude_ids)

    base_where = " AND ".join(base_clauses)
    limit_value = max(args.limit, 0)
    offset_value = max(args.offset, 0)

    if args.max_row_count > 0 or args.sort_by_row_count:
        row_count_clauses: list[str] = []
        row_count_params: list[object] = list(base_params)
        if args.max_row_count > 0:
            row_count_clauses.append("COALESCE(item_stats.row_count, 0) <= ?")
            row_count_params.append(int(args.max_row_count))

        row_count_where = ""
        if row_count_clauses:
            row_count_where = "WHERE " + " AND ".join(row_count_clauses)

        order_by = "COALESCE(item_stats.row_count, 0) ASC, candidate_docs.id"
        final_params = [*row_count_params, limit_value, offset_value]
        return conn.execute(
            f"""
            WITH candidate_docs AS (
                SELECT id, file_id, project_name, project_stage, specialty, region,
                       source_file_name, source_file_path
                FROM price_documents
                WHERE {base_where}
            ),
            item_stats AS (
                SELECT h.document_id, COUNT(*) AS row_count
                FROM historical_boq_items h
                JOIN candidate_docs c ON c.id = h.document_id
                GROUP BY h.document_id
            )
            SELECT candidate_docs.id, candidate_docs.file_id, candidate_docs.project_name,
                   candidate_docs.project_stage, candidate_docs.specialty, candidate_docs.region,
                   COALESCE(item_stats.row_count, 0) AS row_count,
                   candidate_docs.source_file_name, candidate_docs.source_file_path
            FROM candidate_docs
            LEFT JOIN item_stats ON item_stats.document_id = candidate_docs.id
            {row_count_where}
            ORDER BY {order_by} {args.order.upper()}
            LIMIT ? OFFSET ?
            """,
            final_params,
        ).fetchall()

    final_params = [*base_params, limit_value, offset_value]
    return conn.execute(
        f"""
        SELECT id, file_id, project_name, project_stage, specialty, region,
               NULL AS row_count,
               source_file_name, source_file_path
        FROM price_documents
        WHERE {base_where}
        ORDER BY id {args.order.upper()}
        LIMIT ? OFFSET ?
        """,
        final_params,
    ).fetchall()


def _build_selection_aggregate(
    docs: list[sqlite3.Row],
    *,
    excluded_ids: set[int],
    args: argparse.Namespace,
) -> dict:
    row_counts = [
        int(row["row_count"])
        for row in docs
        if row["row_count"] is not None
    ]
    missing_source_paths = 0
    missing_source_files = 0
    for row in docs:
        source_path = str(row["source_file_path"] or "").strip()
        if not source_path:
            missing_source_paths += 1
            continue
        if not os.path.exists(source_path):
            missing_source_files += 1

    aggregate = {
        "mode": "selection_summary_only",
        "documents_selected": len(docs),
        "excluded_document_ids": len(excluded_ids),
        "regions": sorted(
            {
                str(row["region"] or "").strip()
                for row in docs
                if str(row["region"] or "").strip()
            }
        ),
        "document_ids_preview": [int(row["id"]) for row in docs[:10]],
        "missing_source_paths": missing_source_paths,
        "missing_source_files": missing_source_files,
    }
    if row_counts:
        aggregate["row_count_total"] = sum(row_counts)
        aggregate["row_count_avg"] = round(sum(row_counts) / len(row_counts), 2)
        aggregate["row_count_min"] = min(row_counts)
        aggregate["row_count_max"] = max(row_counts)
    if args.region:
        aggregate["region_filter"] = args.region
    if args.max_row_count > 0:
        aggregate["max_row_count_filter"] = int(args.max_row_count)
    if args.project_name_like:
        aggregate["project_name_like"] = args.project_name_like
    return aggregate


def _build_learning_records(parsed, doc: sqlite3.Row) -> tuple[list[dict], int]:
    learning_records: list[dict] = []
    quota_backed_items = 0
    for item in parsed.items:
        quota_ids = [q.code for q in item.quotas if q.code]
        quota_names = [q.name for q in item.quotas if q.name]
        if not quota_ids:
            continue
        quota_backed_items += 1
        row = item.to_record()
        learning_records.append(
            {
                "bill_text": item.bill_text,
                "bill_name": item.boq_name_raw,
                "bill_code": item.boq_code,
                "bill_unit": item.unit,
                "quota_ids": quota_ids,
                "quota_names": quota_names,
                "materials": row.get("materials_json") or [],
                "specialty": item.specialty or doc["specialty"] or "",
                "project_name": doc["project_name"] or parsed.project_name or Path(doc["source_file_path"] or "").stem,
                "province": doc["region"] or "",
                "confidence": 95,
                "feature_text": row.get("feature_text") or "",
                "parse_status": "parsed",
            }
        )
    return learning_records, quota_backed_items


def _process_document(doc: sqlite3.Row, *, dry_run: bool) -> dict:
    from src.priced_bill_parser import parse_priced_bill_document
    from app.api.file_intake import ingest

    source_path = str(doc["source_file_path"] or "").strip()
    if not source_path:
        return {
            "document_id": int(doc["id"]),
            "status": "skipped",
            "reason": "missing_source_file_path",
        }
    if not os.path.exists(source_path):
        return {
            "document_id": int(doc["id"]),
            "status": "skipped",
            "reason": "source_file_missing",
            "source_file_path": source_path,
        }

    parsed = parse_priced_bill_document(
        source_path,
        project_name=doc["project_name"] or Path(source_path).stem,
        specialty=doc["specialty"] or "",
    )
    learning_records, quota_backed_items = _build_learning_records(parsed, doc)
    result = {
        "document_id": int(doc["id"]),
        "project_name": doc["project_name"] or parsed.project_name or "",
        "region": doc["region"] or "",
        "source_file_name": doc["source_file_name"] or "",
        "source_file_path": source_path,
        "bill_items": len(parsed.items),
        "quota_backed_items": quota_backed_items,
        "learning_ready_items": len(learning_records),
        "parser_warnings": parsed.warnings,
        "status": "dry_run" if dry_run else "processed",
    }
    if dry_run:
        result["written_learning_items"] = 0
        result["skipped_learning_items"] = 0
        result["learning_warnings"] = []
        return result

    ingest_result = ingest(
        records=learning_records,
        ingest_intent="learning",
        evidence_level="completed_project",
        business_type="priced_bill_file",
        actor="backfill_learning_from_price_documents",
        batch_mode=True,
        source_context={
            "project_name": doc["project_name"] or parsed.project_name or Path(source_path).stem,
            "province": doc["region"] or "",
            "specialty": doc["specialty"] or "",
            "project_stage": doc["project_stage"] or "historical_import",
            "parse_status": "parsed",
            "source": "completed_project",
        },
    )
    result["written_learning_items"] = ingest_result.written_learning
    result["skipped_learning_items"] = ingest_result.skipped
    result["learning_warnings"] = ingest_result.warnings
    return result


def main() -> int:
    args = parse_args()
    checkpoint_state = _load_checkpoint(args.checkpoint_file)
    excluded_from_file = _load_excluded_document_ids(args.exclude_document_ids_file)
    excluded_ids = _resolve_excluded_document_ids(
        checkpoint_state,
        excluded_from_file=excluded_from_file,
        retry_skipped=args.retry_skipped,
        exclude_failed=args.exclude_failed,
    )
    conn = _connect_price_db(args.price_db)
    try:
        docs = _select_documents(conn, args, exclude_ids=excluded_ids)
    finally:
        conn.close()

    if not docs:
        logger.warning("no priced bill documents matched the selection")
        payload = {
            "aggregate": {
                "documents_selected": 0,
                "excluded_document_ids": len(excluded_ids),
            },
            "results": [],
        }
        print(json.dumps(payload if not args.summary_only else payload["aggregate"], ensure_ascii=False, indent=2))
        return 1

    if args.selection_summary_only:
        aggregate = _build_selection_aggregate(
            docs,
            excluded_ids=excluded_ids,
            args=args,
        )
        payload = {"aggregate": aggregate, "results": []}
        if args.report_json:
            report_path = Path(args.report_json)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                aggregate if args.summary_only else payload,
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    results: list[dict] = []
    for index, doc in enumerate(docs, start=1):
        logger.info(
            f"[{index}/{len(docs)}] document_id={doc['id']} project={doc['project_name'] or doc['source_file_name']}"
        )
        try:
            result = _process_document(doc, dry_run=args.dry_run)
            results.append(result)
            doc_id = int(doc["id"])
            status = str(result.get("status") or "")
            if status == "processed":
                checkpoint_state["succeeded_document_ids"].add(doc_id)
                checkpoint_state["failed_document_ids"].discard(doc_id)
                checkpoint_state["skipped_document_ids"].discard(doc_id)
            elif status == "skipped":
                checkpoint_state["skipped_document_ids"].add(doc_id)
                checkpoint_state["failed_document_ids"].discard(doc_id)
            if status != "dry_run":
                _write_checkpoint(args.checkpoint_file, checkpoint_state)
        except Exception as exc:
            logger.exception(f"learning backfill failed for document_id={doc['id']}: {exc}")
            results.append(
                {
                    "document_id": int(doc["id"]),
                    "status": "failed",
                    "reason": str(exc),
                    "source_file_path": doc["source_file_path"] or "",
                }
            )
            checkpoint_state["failed_document_ids"].add(int(doc["id"]))
            _write_checkpoint(args.checkpoint_file, checkpoint_state)

    aggregate = {
        "documents_selected": len(docs),
        "documents_processed": sum(1 for row in results if row.get("status") in {"processed", "dry_run"}),
        "documents_failed": sum(1 for row in results if row.get("status") == "failed"),
        "documents_skipped": sum(1 for row in results if row.get("status") == "skipped"),
        "excluded_document_ids": len(excluded_ids),
        "bill_items": sum(int(row.get("bill_items") or 0) for row in results),
        "quota_backed_items": sum(int(row.get("quota_backed_items") or 0) for row in results),
        "learning_ready_items": sum(int(row.get("learning_ready_items") or 0) for row in results),
        "written_learning_items": sum(int(row.get("written_learning_items") or 0) for row in results),
        "skipped_learning_items": sum(int(row.get("skipped_learning_items") or 0) for row in results),
    }
    if args.checkpoint_file:
        aggregate["checkpoint_state"] = {
            "succeeded_document_ids": len(checkpoint_state["succeeded_document_ids"]),
            "failed_document_ids": len(checkpoint_state["failed_document_ids"]),
            "skipped_document_ids": len(checkpoint_state["skipped_document_ids"]),
        }
    rebuilds: dict[str, str] = {}
    if not args.dry_run and aggregate["written_learning_items"] > 0:
        from src.experience_db import ExperienceDB

        exp_db = ExperienceDB()
        if args.rebuild_fts_after:
            logger.info("rebuilding experience FTS index after batch")
            exp_db.build_fts_index()
            rebuilds["fts"] = "rebuilt"
        if args.rebuild_vector_after:
            logger.info("rebuilding experience vector index after batch")
            exp_db.rebuild_vector_index()
            rebuilds["vector"] = "rebuilt"
    if rebuilds:
        aggregate["rebuilds"] = rebuilds
    payload = {"aggregate": aggregate, "results": results}

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            aggregate if args.summary_only else payload,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if aggregate["documents_processed"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
