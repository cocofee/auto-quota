"""
Backfill priced bill files into the unified price reference database.

This script parses historical priced Excel/XML files and writes bill-level
composite unit prices into `price_reference.db`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from loguru import logger

import config
from src.priced_bill_parser import parse_priced_bill_document
from src.price_reference_db import PriceReferenceDB


BACKEND_DIR = config.PROJECT_ROOT / "web" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.file_intake import ingest


SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xml", ".13jk"}


def _iter_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return [
        path
        for path in sorted(input_path.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


def _stable_file_id(path: Path, root: Path | None = None) -> str:
    source = str(path.resolve())
    if root:
        try:
            source = str(path.resolve().relative_to(root.resolve()))
        except Exception:
            pass
    digest = hashlib.md5(source.encode("utf-8")).hexdigest()[:16]
    return f"legacy_priced_bill_{digest}"


def _sync_learning(
    *,
    items: list[dict],
    region: str,
    project_name: str,
    specialty: str,
    project_stage: str,
) -> dict:
    learning_records: list[dict] = []
    for item in items:
        quotas = item.get("quotas") or []
        quota_ids = [q.get("code", "").strip() for q in quotas if str(q.get("code", "")).strip()]
        quota_names = [q.get("name", "").strip() for q in quotas if str(q.get("name", "")).strip()]
        if not quota_ids:
            continue
        learning_records.append(
            {
                "bill_text": item.get("bill_text") or "",
                "bill_name": item.get("boq_name_raw") or "",
                "bill_code": item.get("boq_code") or "",
                "bill_unit": item.get("unit") or "",
                "quota_ids": quota_ids,
                "quota_names": quota_names,
                "materials": item.get("materials_json") or [],
                "specialty": specialty or item.get("specialty") or "",
                "project_name": project_name,
                "province": region,
                "confidence": 95,
                "feature_text": item.get("feature_text") or "",
                "parse_status": "parsed",
            }
        )

    if not learning_records:
        return {"written": 0, "rejected": 0, "warnings": ["no learning-ready records"]}

    ingest_result = ingest(
        records=learning_records,
        ingest_intent="learning",
        evidence_level="completed_project",
        business_type="priced_bill_file",
        actor="backfill_priced_bills_to_price_reference",
        source_context={
            "project_name": project_name,
            "province": region,
            "specialty": specialty,
            "project_stage": project_stage,
            "parse_status": "parsed",
            "source": "completed_project",
        },
    )
    return {
        "written": ingest_result.written_learning,
        "rejected": ingest_result.skipped,
        "warnings": ingest_result.warnings,
    }


def backfill_file(
    *,
    path: Path,
    price_db: PriceReferenceDB,
    root: Path | None = None,
    region: str = "",
    specialty: str = "",
    project_stage: str = "historical_import",
    sync_learning: bool = False,
) -> dict:
    parsed = parse_priced_bill_document(path, project_name=path.stem, specialty=specialty)
    payload_items: list[dict] = []
    priced_items = 0

    for parsed_item in parsed.items:
        row = parsed_item.to_record()
        row["project_name"] = parsed.project_name or path.stem
        row["project_stage"] = project_stage
        row["specialty"] = row.get("specialty") or specialty
        row["seed_source"] = "backfill_script"
        payload_items.append(row)
        if row.get("composite_unit_price") is not None:
            priced_items += 1

    file_id = _stable_file_id(path, root=root)
    document_id = price_db.create_document_from_file(
        file_id=file_id,
        document_type="priced_bill_file",
        project_name=parsed.project_name or path.stem,
        project_stage=project_stage,
        specialty=specialty,
        region=region,
        source_file_name=path.name,
        source_file_path=str(path),
        source_file_ext=path.suffix.lower(),
        status="created",
    )
    written = price_db.replace_boq_items(document_id, payload_items)

    learning_stats = {"written": 0, "rejected": 0}
    if sync_learning:
        learning_ready_items = []
        for parsed_item in parsed.items:
            row = parsed_item.to_record()
            row["quotas"] = [{"code": q.code, "name": q.name} for q in parsed_item.quotas]
            learning_ready_items.append(row)
        learning_stats = _sync_learning(
            items=learning_ready_items,
            region=region,
            project_name=parsed.project_name or path.stem,
            specialty=specialty,
            project_stage=project_stage,
        )

    summary = {
        "file_path": str(path),
        "file_type": parsed.file_type,
        "bill_items": len(parsed.items),
        "priced_items": priced_items,
        "written_price_reference_items": written,
        "written_learning_items": learning_stats["written"],
        "rejected_learning_items": learning_stats["rejected"],
        "learning_warnings": learning_stats.get("warnings") or [],
        "warnings": parsed.warnings,
    }
    price_db.update_document_parse(document_id, status="parsed", parse_summary=summary)
    return {
        "file": str(path),
        "document_id": document_id,
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill priced bill files into price_reference.db"
    )
    parser.add_argument(
        "input_path",
        help="A priced bill file or a directory containing historical priced files.",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Region/province to stamp on imported price reference records.",
    )
    parser.add_argument(
        "--specialty",
        default="",
        help="Optional specialty to stamp on imported records.",
    )
    parser.add_argument(
        "--project-stage",
        default="historical_import",
        help="Project stage metadata to stamp on imported records.",
    )
    parser.add_argument(
        "--sync-learning",
        action="store_true",
        help="Also write quota relationships into ExperienceDB.",
    )
    parser.add_argument(
        "--price-db",
        default=str(config.get_price_reference_db_path()),
        help="Target price_reference.db path.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path to write full per-file results as JSON.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only aggregate summary to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path)
    if not input_path.exists():
        logger.error(f"input path does not exist: {input_path}")
        return 1

    files = _iter_input_files(input_path)
    if not files:
        logger.error(f"no supported priced bill files found under: {input_path}")
        return 1

    root = input_path if input_path.is_dir() else input_path.parent
    price_db = PriceReferenceDB(db_path=args.price_db)
    results = []
    for index, path in enumerate(files, start=1):
        logger.info(f"[{index}/{len(files)}] backfilling {path}")
        try:
            results.append(
                backfill_file(
                    path=path,
                    price_db=price_db,
                    root=root,
                    region=args.region,
                    specialty=args.specialty,
                    project_stage=args.project_stage,
                    sync_learning=args.sync_learning,
                )
            )
        except Exception as exc:
            logger.exception(f"backfill failed for {path}: {exc}")

    aggregate = {
        "files_total": len(files),
        "files_ok": len(results),
        "files_failed": len(files) - len(results),
        "bill_items": sum(item["summary"]["bill_items"] for item in results),
        "priced_items": sum(item["summary"]["priced_items"] for item in results),
        "written_price_reference_items": sum(item["summary"]["written_price_reference_items"] for item in results),
        "written_learning_items": sum(item["summary"]["written_learning_items"] for item in results),
    }
    payload = {"aggregate": aggregate, "results": results}
    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.summary_only:
        print(json.dumps({"aggregate": aggregate}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
