from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .datasets import normalize_quota_id


ASSET_MODE = "reconstructed_from_national_index"
UNAVAILABLE_FIELDS = (
    "work_type",
    "kva",
    "kv",
    "ampere",
    "weight_t",
    "shape",
    "perimeter",
    "large_side",
    "elevator_stops",
    "elevator_speed",
)
NUMERIC_TEXT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


@dataclass(frozen=True, slots=True)
class ReconstructedAssetReport:
    asset_mode: str
    province: str
    source_index_path: Path
    database_path: Path
    manifest_path: Path
    source_sha256: str
    source_row_count: int
    target_row_count: int
    oracle_count: int
    missing_oracle_ids: tuple[str, ...]
    duplicate_quota_chapter_count: int
    numeric_specialty_count: int
    numeric_specialty_values: tuple[str, ...]
    non_empty_rates: dict[str, float]
    null_field_counts: dict[str, int]
    unavailable_fields: tuple[str, ...]
    failed_gates: tuple[str, ...]
    gate_passed: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _book_from_quota_id(quota_id: str) -> str:
    match = re.match(r"^([A-Za-z]+\d{0,2})-", quota_id)
    if not match:
        match = re.match(r"^(\d{1,4})-", quota_id)
    return match.group(1).upper() if match else ""


def _oracle_ids(path: Path, province: str) -> set[str]:
    result: set[str] = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"primary line {line_number} is not an object")
        if str(row.get("province") or "").strip() != province:
            continue
        values: Any = (
            row.get("oracle_quota_ids")
            or row.get("expected_quota_ids")
            or row.get("expected_ids")
            or []
        )
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError:
                values = values.split("|")
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        result.update(
            quota_id
            for value in values
            if (quota_id := normalize_quota_id(value))
        )
    return result


def _create_target_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE quotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quota_id TEXT NOT NULL,
            name TEXT NOT NULL,
            unit TEXT,
            work_type TEXT,
            specialty TEXT,
            chapter TEXT,
            dn INTEGER,
            cable_section REAL,
            kva REAL,
            kv REAL,
            ampere REAL,
            weight_t REAL,
            material TEXT,
            connection TEXT,
            circuits INTEGER,
            shape TEXT,
            perimeter REAL,
            large_side REAL,
            elevator_stops INTEGER,
            elevator_speed REAL,
            search_text TEXT,
            book TEXT,
            UNIQUE(quota_id, chapter)
        )
        """
    )
    conn.execute("CREATE INDEX idx_quotas_quota_id ON quotas(quota_id)")
    conn.execute("CREATE INDEX idx_quotas_book ON quotas(book)")
    conn.execute("CREATE INDEX idx_quotas_specialty ON quotas(specialty)")
    conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT)")


def _jsonable_report(report: ReconstructedAssetReport) -> dict[str, Any]:
    payload = asdict(report)
    payload["source_index_path"] = str(report.source_index_path)
    payload["database_path"] = str(report.database_path)
    payload["manifest_path"] = str(report.manifest_path)
    return payload


def materialize_province_db(
    *,
    national_index: str | Path,
    province: str,
    output_root: str | Path,
    primary_dataset: str | Path,
    production_provinces_dir: str | Path | None = None,
) -> ReconstructedAssetReport:
    source_path = Path(national_index).resolve()
    primary_path = Path(primary_dataset).resolve()
    root = Path(output_root).resolve()
    if production_provinces_dir is None:
        import config

        production_provinces_dir = config.PROVINCES_DB_DIR
    production_root = Path(production_provinces_dir).resolve()
    province_dir = (root / "provinces" / province).resolve()
    database_path = province_dir / "quota.db"
    if database_path == production_root or database_path.is_relative_to(production_root):
        raise ValueError("refusing to write inside production provinces directory")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not primary_path.is_file():
        raise FileNotFoundError(primary_path)

    source_conn = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    try:
        source_rows = [
            dict(row)
            for row in source_conn.execute(
                """
                SELECT
                    quota_id, name, unit, chapter, specialty, material,
                    connection, dn, cable_section, circuits, normalized_text
                FROM national_quotas
                WHERE province = ?
                ORDER BY quota_id
                """,
                (province,),
            )
        ]
    finally:
        source_conn.close()

    numeric_specialties = [
        str(row.get("specialty") or "").strip()
        for row in source_rows
        if NUMERIC_TEXT_RE.fullmatch(str(row.get("specialty") or "").strip())
    ]

    province_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = database_path.with_suffix(".db.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    target_conn = sqlite3.connect(temporary_path)
    try:
        _create_target_schema(target_conn)
        target_conn.executemany(
            """
            INSERT INTO quotas (
                quota_id, name, unit, work_type, specialty, chapter, dn,
                cable_section, kva, kv, ampere, weight_t, material, connection,
                circuits, shape, perimeter, large_side, elevator_stops,
                elevator_speed, search_text, book
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?,
                      NULL, NULL, NULL, NULL, NULL, ?, ?)
            """,
            [
                (
                    row["quota_id"],
                    row["name"],
                    row["unit"],
                    row["specialty"],
                    row["chapter"],
                    row["dn"],
                    row["cable_section"],
                    row["material"],
                    row["connection"],
                    row["circuits"],
                    row["normalized_text"],
                    _book_from_quota_id(str(row["quota_id"] or "")),
                )
                for row in source_rows
            ],
        )
        target_conn.executemany(
            "INSERT INTO db_meta(key, value) VALUES (?, ?)",
            (
                ("asset_mode", ASSET_MODE),
                ("source_index", str(source_path)),
                ("province", province),
            ),
        )
        target_conn.commit()
    finally:
        target_conn.close()
    temporary_path.replace(database_path)

    target_conn = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        target_row_count = int(target_conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0])
        duplicate_count = int(
            target_conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT quota_id, chapter
                    FROM quotas
                    GROUP BY quota_id, chapter
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        non_empty_rates = {}
        for field in ("search_text", "book", "specialty"):
            count = int(
                target_conn.execute(
                    f"SELECT COUNT(*) FROM quotas WHERE {field} IS NOT NULL AND TRIM({field}) != ''"
                ).fetchone()[0]
            )
            non_empty_rates[field] = round(count / target_row_count, 6) if target_row_count else 0.0
        target_ids = {
            str(row[0])
            for row in target_conn.execute("SELECT quota_id FROM quotas")
        }
    finally:
        target_conn.close()

    oracle_ids = _oracle_ids(primary_path, province)
    missing_oracles = tuple(sorted(oracle_ids - target_ids))
    failed_gates: list[str] = []
    if not source_rows:
        failed_gates.append("source_nonempty")
    if target_row_count != len(source_rows):
        failed_gates.append("row_count_parity")
    if missing_oracles:
        failed_gates.append("oracle_coverage")
    if duplicate_count:
        failed_gates.append("duplicate_quota_chapter")
    if numeric_specialties:
        failed_gates.append("numeric_specialty")

    manifest_path = province_dir / "asset_manifest.json"
    report = ReconstructedAssetReport(
        asset_mode=ASSET_MODE,
        province=province,
        source_index_path=source_path,
        database_path=database_path,
        manifest_path=manifest_path,
        source_sha256=_sha256(source_path),
        source_row_count=len(source_rows),
        target_row_count=target_row_count,
        oracle_count=len(oracle_ids),
        missing_oracle_ids=missing_oracles,
        duplicate_quota_chapter_count=duplicate_count,
        numeric_specialty_count=len(numeric_specialties),
        numeric_specialty_values=tuple(sorted(set(numeric_specialties))[:20]),
        non_empty_rates=non_empty_rates,
        null_field_counts={field: target_row_count for field in UNAVAILABLE_FIELDS},
        unavailable_fields=UNAVAILABLE_FIELDS,
        failed_gates=tuple(failed_gates),
        gate_passed=not failed_gates,
    )
    manifest_path.write_text(
        json.dumps(_jsonable_report(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
