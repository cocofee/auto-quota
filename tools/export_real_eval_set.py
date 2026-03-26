from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections import defaultdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "common" / "experience.db"
DEFAULT_OUT_PATH = PROJECT_ROOT / "output" / "real_eval" / "real_eval.jsonl"
DEFAULT_SOURCES = ("project_import", "user_confirmed", "user_correction")
PROFILE_DEFAULTS = {
    "smoke": 20,
    "dev": 100,
    "full": None,
}


def _safe_json_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _build_where_clause(
    *,
    layer: str,
    min_confidence: int,
    min_confirm_count: int,
    sources: list[str] | None,
    provinces: list[str] | None,
    projects: list[str] | None,
) -> tuple[str, list[object]]:
    clauses = [
        "layer = ?",
        "confidence >= ?",
        "confirm_count >= ?",
        "TRIM(COALESCE(bill_text, '')) != ''",
        "TRIM(COALESCE(quota_ids, '')) != ''",
    ]
    params: list[object] = [layer, int(min_confidence), int(min_confirm_count)]

    if sources:
        placeholders = ",".join("?" for _ in sources)
        clauses.append(f"source IN ({placeholders})")
        params.extend(sources)
    if provinces:
        placeholders = ",".join("?" for _ in provinces)
        clauses.append(f"province IN ({placeholders})")
        params.extend(provinces)
    if projects:
        placeholders = ",".join("?" for _ in projects)
        clauses.append(f"project_name IN ({placeholders})")
        params.extend(projects)

    return " AND ".join(clauses), params


def _interleave_records(records: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        key = (
            _clean_text(record.get("source")),
            _clean_text(record.get("project_name")),
        )
        buckets[key].append(record)
    ordered_keys = sorted(
        buckets,
        key=lambda item: (
            item[0] != "project_import",
            item[0],
            item[1],
        ),
    )
    merged: list[dict] = []
    idx = 0
    while True:
        emitted = False
        for key in ordered_keys:
            bucket = buckets[key]
            if idx < len(bucket):
                merged.append(bucket[idx])
                emitted = True
        if not emitted:
            break
        idx += 1
    return merged


def _cap_records_per_province(records: list[dict], max_per_province: int | None) -> list[dict]:
    if max_per_province is None or int(max_per_province) <= 0:
        return list(records)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[_clean_text(record.get("province"))].append(record)

    capped: list[dict] = []
    for province in sorted(grouped):
        diversified = _interleave_records(grouped[province])
        capped.extend(diversified[: int(max_per_province)])
    return capped


def fetch_real_eval_records(
    db_path: str | Path,
    *,
    layer: str = "authority",
    min_confidence: int = 95,
    min_confirm_count: int = 1,
    sources: list[str] | None = None,
    provinces: list[str] | None = None,
    projects: list[str] | None = None,
    limit: int | None = None,
    max_per_province: int | None = None,
) -> list[dict]:
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        available_columns = {
            str(row[1]).strip()
            for row in conn.execute("PRAGMA table_info(experiences)").fetchall()
        }
        optional_columns = [
            column for column in ("section", "sheet_name", "context_prior", "source_file_name", "source_file_stem")
            if column in available_columns
        ]
        select_columns = [
            "id",
            "source",
            "layer",
            "province",
            "project_name",
            "confidence",
            "confirm_count",
            "bill_name",
            "bill_text",
            "bill_code",
            "specialty",
            "quota_ids",
            "quota_names",
        ] + optional_columns
        where_clause, params = _build_where_clause(
            layer=layer,
            min_confidence=min_confidence,
            min_confirm_count=min_confirm_count,
            sources=sources,
            provinces=provinces,
            projects=projects,
        )
        sql = f"""
            SELECT
                {", ".join(select_columns)}
            FROM experiences
            WHERE {where_clause}
            ORDER BY province ASC, source ASC, confirm_count DESC, confidence DESC, id ASC
        """
        if limit is not None and int(limit) > 0:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    records: list[dict] = []
    for row in rows:
        quota_ids = [str(value).strip() for value in _safe_json_list(row["quota_ids"]) if str(value).strip()]
        quota_names = [str(value).strip() for value in _safe_json_list(row["quota_names"]) if str(value).strip()]
        if not quota_ids:
            continue
        record = {
            "sample_id": f"exp:{int(row['id'])}",
            "experience_id": int(row["id"]),
            "source": _clean_text(row["source"]),
            "layer": _clean_text(row["layer"]),
            "province": _clean_text(row["province"]),
            "project_name": _clean_text(row["project_name"]),
            "confidence": int(row["confidence"] or 0),
            "confirm_count": int(row["confirm_count"] or 0),
            "bill_name": _clean_text(row["bill_name"]),
            "bill_text": _clean_text(row["bill_text"]),
            "bill_code": _clean_text(row["bill_code"]),
            "specialty": _clean_text(row["specialty"]),
            "oracle_quota_ids": quota_ids,
            "oracle_quota_names": quota_names,
        }
        if "section" in row.keys():
            record["section"] = _clean_text(row["section"])
        if "sheet_name" in row.keys():
            record["sheet_name"] = _clean_text(row["sheet_name"])
        if "context_prior" in row.keys():
            try:
                record["context_prior"] = json.loads(row["context_prior"] or "{}")
            except Exception:
                record["context_prior"] = {}
        if "source_file_name" in row.keys():
            record["source_file_name"] = _clean_text(row["source_file_name"])
        if "source_file_stem" in row.keys():
            record["source_file_stem"] = _clean_text(row["source_file_stem"])
        context_prior = dict(record.get("context_prior") or {})
        if not record.get("source_file_name") and context_prior.get("source_file_name"):
            record["source_file_name"] = _clean_text(context_prior.get("source_file_name"))
        if not record.get("source_file_stem"):
            context_source_stem = context_prior.get("source_file_stem") or context_prior.get("source_file_title")
            if context_source_stem:
                record["source_file_stem"] = _clean_text(context_source_stem)
        records.append(record)
    records = _cap_records_per_province(records, max_per_province)
    if limit is not None and int(limit) > 0:
        records = records[: int(limit)]
    return records


def export_real_eval_set(
    db_path: str | Path,
    out_path: str | Path,
    *,
    layer: str = "authority",
    min_confidence: int = 95,
    min_confirm_count: int = 1,
    sources: list[str] | None = None,
    provinces: list[str] | None = None,
    projects: list[str] | None = None,
    limit: int | None = None,
    max_per_province: int | None = None,
) -> tuple[Path, dict]:
    records = fetch_real_eval_records(
        db_path,
        layer=layer,
        min_confidence=min_confidence,
        min_confirm_count=min_confirm_count,
        sources=sources,
        provinces=provinces,
        projects=projects,
        limit=limit,
        max_per_province=max_per_province,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    by_source = Counter(record["source"] for record in records)
    by_province = Counter(record["province"] for record in records)
    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": str(Path(db_path)),
        "out_path": str(out_path),
        "filters": {
            "layer": layer,
            "min_confidence": int(min_confidence),
            "min_confirm_count": int(min_confirm_count),
            "sources": list(sources or []),
            "provinces": list(provinces or []),
            "projects": list(projects or []),
            "limit": int(limit) if limit is not None else None,
            "max_per_province": int(max_per_province) if max_per_province is not None else None,
        },
        "count": len(records),
        "by_source": dict(sorted(by_source.items())),
        "by_province_top20": dict(by_province.most_common(20)),
        "notes": {
            "default_mode": "trusted_real_samples",
            "recommended_eval": "Run closed-book first to avoid experience self-hit leakage.",
        },
    }
    manifest_path = out_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export trusted real-world evaluation samples from experience DB")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="experience.db path")
    parser.add_argument("--out", default=str(DEFAULT_OUT_PATH), help="output jsonl path")
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="full", help="export size preset")
    parser.add_argument("--layer", default="authority", help="experience layer filter")
    parser.add_argument("--min-confidence", type=int, default=95, help="minimum confidence")
    parser.add_argument("--min-confirm-count", type=int, default=1, help="minimum confirm count")
    parser.add_argument("--source", action="append", dest="sources", help="source filter, repeatable")
    parser.add_argument("--province", action="append", dest="provinces", help="province filter, repeatable")
    parser.add_argument("--project", action="append", dest="projects", help="project name filter, repeatable")
    parser.add_argument("--limit", type=int, default=None, help="max rows to export")
    parser.add_argument("--max-per-province", type=int, default=None, help="cap rows per province after diversification")
    args = parser.parse_args()

    sources = list(args.sources or DEFAULT_SOURCES)
    max_per_province = args.max_per_province
    if max_per_province is None:
        max_per_province = PROFILE_DEFAULTS.get(args.profile)
    out_path, manifest = export_real_eval_set(
        args.db_path,
        args.out,
        layer=args.layer,
        min_confidence=args.min_confidence,
        min_confirm_count=args.min_confirm_count,
        sources=sources,
        provinces=list(args.provinces or []),
        projects=list(args.projects or []),
        limit=args.limit,
        max_per_province=max_per_province,
    )
    print(f"[OK] wrote real eval set: {out_path}")
    print(f"  count: {manifest['count']}")
    print(f"  by_source: {manifest['by_source']}")
    print(f"  profile: {args.profile} max_per_province={max_per_province}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
