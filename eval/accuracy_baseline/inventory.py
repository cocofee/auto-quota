from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fingerprints import province_query_fingerprint, query_fingerprint

_ORACLE_KEYS = (
    "oracle_quota_ids",
    "expected_quota_ids",
    "expected_ids",
    "quota_ids",
)

_COVERAGE_REQUIREMENT_NAMES = (
    "min_cases",
    "min_provinces",
    "min_source_families",
    "min_projects",
    "min_specialties",
    "min_splits",
    "max_dominant_province_share",
    "max_dominant_source_family_share",
    "max_dominant_project_share",
    "max_dominant_specialty_share",
    "max_cross_split_query_overlap",
    "max_cross_split_source_family_overlap",
    "max_cross_split_project_overlap",
    "max_cross_split_province_overlap",
    "require_nonempty_source_family",
    "require_nonempty_project",
    "require_nonempty_specialty",
    "require_nonempty_split",
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _query_text(row: Mapping[str, Any]) -> str:
    return " ".join(
        value
        for value in (
            _clean(row.get("bill_name") or row.get("name")),
            _clean(row.get("bill_text") or row.get("description")),
        )
        if value
    )


def _has_oracle(row: Mapping[str, Any]) -> bool:
    for key in _ORACLE_KEYS:
        value = row.get(key)
        if isinstance(value, str) and _clean(value):
            return True
        if isinstance(value, (list, tuple, set)) and any(_clean(item) for item in value):
            return True
    return False


def _distribution(counter: Counter[str], total: int, limit: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "value": value,
            "count": count,
            "share": round(count / total, 6) if total else 0.0,
        }
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _cross_split_overlap(
    records: Sequence[Mapping[str, Any]],
    key: str,
    ignored_values: set[str] | None = None,
) -> dict[str, Any]:
    ignored_values = ignored_values or set()
    value_splits: dict[str, set[str]] = defaultdict(set)
    for record in records:
        value = _clean(record.get(key))
        split = _clean(record.get("effective_split"))
        if value and split and value not in ignored_values:
            value_splits[value].add(split)
    overlaps = sorted(value for value, splits in value_splits.items() if len(splits) > 1)
    return {"count": len(overlaps), "examples": overlaps[:20]}


def _read_jsonl(
    path: str | Path,
    *,
    declared_split: str = "",
) -> tuple[list[dict[str, Any]], str]:
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise TypeError(f"line {line_number} is not a JSON object: {resolved}")
        sample_id = _clean(
            payload.get("case_id")
            or payload.get("sample_id")
            or payload.get("bill_id")
        )
        province = _clean(payload.get("province") or payload.get("quota_province"))
        query = query_fingerprint(_query_text(payload))
        actual_split = _clean(payload.get("split"))
        records.append(
            {
                "sample_id": sample_id,
                "province": province,
                "source_family": _clean(payload.get("source_family")),
                "project_id": _clean(payload.get("project_id") or payload.get("project_name")),
                "source": _clean(payload.get("source") or payload.get("source_file")),
                "specialty": _clean(payload.get("specialty")),
                "query": query,
                "province_sample_id": f"{province}|{sample_id}" if province and sample_id else "",
                "province_query": province_query_fingerprint(province, query),
                "actual_split": actual_split,
                "effective_split": actual_split or declared_split,
                "declared_split": declared_split,
                "has_oracle": _has_oracle(payload),
            }
        )
    return records, hashlib.sha256(raw).hexdigest()


def audit_jsonl_group(
    paths: Mapping[str, str | Path],
    *,
    evidence_role: str,
    file_names_are_splits: bool,
    aggregate_sources: Sequence[str] = (),
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    for name, path in paths.items():
        declared_split = _clean(name) if file_names_are_splits else ""
        file_records, content_sha256 = _read_jsonl(path, declared_split=declared_split)
        records.extend(file_records)
        files[name] = {
            "path": str(Path(path).resolve()),
            "rows": len(file_records),
            "content_sha256": content_sha256,
        }

    total = len(records)
    counters = {
        key: Counter(_clean(record.get(key)) for record in records if _clean(record.get(key)))
        for key in (
            "province",
            "source_family",
            "project_id",
            "source",
            "specialty",
            "effective_split",
        )
    }
    sample_counts = Counter(
        _clean(record.get("province_sample_id"))
        for record in records
        if _clean(record.get("province_sample_id"))
    )
    query_counts = Counter(
        _clean(record.get("query"))
        for record in records
        if _clean(record.get("query"))
    )
    province_query_counts = Counter(
        _clean(record.get("province_query"))
        for record in records
        if _clean(record.get("province_query"))
    )
    normalized_aggregate_sources = {
        _clean(value) for value in aggregate_sources if _clean(value)
    }
    return {
        "evidence_role": evidence_role,
        "system_baseline_eligible": False,
        "files": files,
        "rows": total,
        "labeled_rows": sum(bool(record["has_oracle"]) for record in records),
        "distinct": {
            "provinces": len(counters["province"]),
            "source_families": len(counters["source_family"]),
            "projects": len(counters["project_id"]),
            "sources": len(counters["source"]),
            "specialties": len(counters["specialty"]),
            "splits": len(counters["effective_split"]),
        },
        "missing": {
            "sample_id": sum(not _clean(record.get("sample_id")) for record in records),
            "province": sum(not _clean(record.get("province")) for record in records),
            "source_family": sum(
                not _clean(record.get("source_family")) for record in records
            ),
            "project": sum(not _clean(record.get("project_id")) for record in records),
            "specialty": sum(not _clean(record.get("specialty")) for record in records),
            "split": sum(not _clean(record.get("actual_split")) for record in records),
            "oracle": sum(not bool(record["has_oracle"]) for record in records),
        },
        "split_integrity": {
            "ignored_aggregate_sources": sorted(normalized_aggregate_sources),
            "declared_split_used_count": sum(
                not _clean(record.get("actual_split"))
                and bool(_clean(record.get("declared_split")))
                for record in records
            ),
            "declared_split_mismatch_count": sum(
                bool(_clean(record.get("actual_split")))
                and bool(_clean(record.get("declared_split")))
                and _clean(record.get("actual_split"))
                != _clean(record.get("declared_split"))
                for record in records
            ),
            "cross_split_overlap": {
                key: _cross_split_overlap(
                    records,
                    key,
                    normalized_aggregate_sources if key == "source" else None,
                )
                for key in (
                    "province_sample_id",
                    "query",
                    "province_query",
                    "source",
                    "source_family",
                    "project_id",
                    "province",
                )
            },
        },
        "duplicates": {
            "province_sample_id_count": sum(count > 1 for count in sample_counts.values()),
            "query_count": sum(count > 1 for count in query_counts.values()),
            "province_query_count": sum(
                count > 1 for count in province_query_counts.values()
            ),
        },
        "distributions": {
            key: _distribution(counter, total)
            for key, counter in counters.items()
        },
    }


def _open_readonly(path: str | Path) -> tuple[sqlite3.Connection, Path]:
    resolved = Path(path).resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    return connection, resolved


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _sqlite_distribution(
    connection: sqlite3.Connection,
    *,
    table: str,
    column: str,
    total: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT {column}, COUNT(*) AS row_count
        FROM {table}
        WHERE LENGTH(TRIM(COALESCE({column}, ''))) > 0
        GROUP BY {column}
        ORDER BY row_count DESC, {column}
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "value": _clean(value),
            "count": count,
            "share": round(count / total, 6) if total else 0.0,
        }
        for value, count in rows
    ]


def audit_national_index(path: str | Path) -> dict[str, Any]:
    connection, resolved = _open_readonly(path)
    try:
        if not _table_exists(connection, "national_quotas"):
            raise ValueError(f"national_quotas table not found: {resolved}")
        total = int(connection.execute("SELECT COUNT(*) FROM national_quotas").fetchone()[0])

        def nonempty_count(column: str) -> int:
            return int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM national_quotas
                    WHERE LENGTH(TRIM(COALESCE({column}, ''))) > 0
                    """
                ).fetchone()[0]
            )

        quota_book_count = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT province) FROM national_quotas
                WHERE LENGTH(TRIM(COALESCE(province, ''))) > 0
                """
            ).fetchone()[0]
        )
        duplicate_key_groups = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT province, quota_id
                    FROM national_quotas
                    GROUP BY province, quota_id
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        field_counts = {
            column: nonempty_count(column)
            for column in ("province", "quota_id", "name", "specialty", "family")
        }
        return {
            "path": str(resolved),
            "role": "quota_candidate_corpus",
            "eligible_as_gold": False,
            "rows": total,
            "quota_book_count": quota_book_count,
            "duplicate_province_quota_id_groups": duplicate_key_groups,
            "field_coverage": {
                column: {
                    "nonempty_count": count,
                    "nonempty_rate": round(count / total, 6) if total else 0.0,
                }
                for column, count in field_counts.items()
            },
            "top_quota_books": _sqlite_distribution(
                connection,
                table="national_quotas",
                column="province",
                total=total,
            ),
            "top_families": _sqlite_distribution(
                connection,
                table="national_quotas",
                column="family",
                total=total,
            ),
        }
    finally:
        connection.close()


def audit_bill_library(path: str | Path) -> dict[str, Any]:
    connection, resolved = _open_readonly(path)
    try:
        for table in ("files", "bill_items"):
            if not _table_exists(connection, table):
                raise ValueError(f"{table} table not found: {resolved}")
        file_count = int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0])
        item_count = int(connection.execute("SELECT COUNT(*) FROM bill_items").fetchone()[0])
        missing_province_files = int(
            connection.execute(
                "SELECT COUNT(*) FROM files WHERE LENGTH(TRIM(COALESCE(province, ''))) = 0"
            ).fetchone()[0]
        )
        missing_specialty_files = int(
            connection.execute(
                "SELECT COUNT(*) FROM files WHERE LENGTH(TRIM(COALESCE(specialty, ''))) = 0"
            ).fetchone()[0]
        )
        known_province_items = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(bill_count), 0) FROM files
                WHERE LENGTH(TRIM(COALESCE(province, ''))) > 0
                """
            ).fetchone()[0]
        )
        province_count = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT province) FROM files
                WHERE LENGTH(TRIM(COALESCE(province, ''))) > 0
                """
            ).fetchone()[0]
        )
        specialty_count = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT specialty) FROM files
                WHERE LENGTH(TRIM(COALESCE(specialty, ''))) > 0
                """
            ).fetchone()[0]
        )
        return {
            "path": str(resolved),
            "role": "unlabeled_query_sampling_frame",
            "eligible_as_gold": False,
            "files": file_count,
            "bill_items": item_count,
            "known_province_bill_items": known_province_items,
            "province_count": province_count,
            "specialty_count": specialty_count,
            "missing_province_files": missing_province_files,
            "missing_province_file_share": (
                round(missing_province_files / file_count, 6) if file_count else 0.0
            ),
            "missing_specialty_files": missing_specialty_files,
            "missing_specialty_file_share": (
                round(missing_specialty_files / file_count, 6) if file_count else 0.0
            ),
            "top_provinces": _sqlite_distribution(
                connection,
                table="files",
                column="province",
                total=file_count,
            ),
            "top_specialties": _sqlite_distribution(
                connection,
                table="files",
                column="specialty",
                total=file_count,
            ),
        }
    finally:
        connection.close()


def audit_oss_root(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    canonical_root = resolved / "by_province"
    if not canonical_root.is_dir():
        canonical_root = resolved
    provinces: list[dict[str, Any]] = []
    for directory in sorted(
        (item for item in canonical_root.iterdir() if item.is_dir()),
        key=lambda item: item.name,
    ):
        xml_paths = [
            item
            for item in directory.rglob("*")
            if item.is_file() and item.suffix.casefold() == ".xml"
        ]
        provinces.append(
            {
                "province_code": directory.name,
                "xml_files": len(xml_paths),
                "bytes": sum(item.stat().st_size for item in xml_paths),
            }
        )
    return {
        "path": str(resolved),
        "canonical_root": str(canonical_root),
        "role": "oss_training_and_diagnostic_corpus",
        "eligible_as_independent_gold": False,
        "province_directory_count": len(provinces),
        "xml_files": sum(row["xml_files"] for row in provinces),
        "bytes": sum(row["bytes"] for row in provinces),
        "provinces": provinces,
    }


def build_coverage_inventory(
    *,
    primary_path: str | Path | None = None,
    oss_splits: Mapping[str, str | Path] | None = None,
    historical_splits: Mapping[str, str | Path] | None = None,
    oss_aggregate_sources: Sequence[str] = (),
    historical_aggregate_sources: Sequence[str] = (),
    national_index_path: str | Path | None = None,
    bill_library_path: str | Path | None = None,
    oss_root: str | Path | None = None,
) -> dict[str, Any]:
    evidence_sets: dict[str, Any] = {}
    sampling_frames: dict[str, Any] = {}
    if primary_path:
        evidence_sets["human_primary_slice"] = audit_jsonl_group(
            {"primary": primary_path},
            evidence_role="human_reviewed_slice",
            file_names_are_splits=False,
        )
    if oss_splits:
        evidence_sets["oss_diagnostic"] = audit_jsonl_group(
            oss_splits,
            evidence_role="oss_diagnostic_only",
            file_names_are_splits=True,
            aggregate_sources=oss_aggregate_sources,
        )
    if historical_splits:
        evidence_sets["historical_stress"] = audit_jsonl_group(
            historical_splits,
            evidence_role="historical_failure_stress_only",
            file_names_are_splits=True,
            aggregate_sources=historical_aggregate_sources,
        )
    if national_index_path:
        sampling_frames["national_index"] = audit_national_index(national_index_path)
    if bill_library_path:
        sampling_frames["bill_library"] = audit_bill_library(bill_library_path)
    if oss_root:
        sampling_frames["oss_root"] = audit_oss_root(oss_root)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "system_baseline_eligible": False,
        "headline_policy": {
            "human_primary_slice": "slice_metrics_only",
            "oss_diagnostic": "diagnostic_metrics_only",
            "historical_stress": "repair_and_regression_metrics_only",
            "combined_score_allowed": False,
        },
        "evidence_sets": evidence_sets,
        "sampling_frames": sampling_frames,
        "coverage_contract_draft": {
            "status": "blocked_pending_business_thresholds_and_independent_gold",
            "cli_compatible": False,
            "system_baseline_eligible": False,
            "requirements": {name: None for name in _COVERAGE_REQUIREMENT_NAMES},
            "required_evidence": [
                "independently reviewed human labels",
                "auditable project and source-family provenance",
                "train/evaluation isolation manifest",
                "business-approved province and specialty sampling targets",
            ],
        },
        "next_actions": [
            {
                "priority": "P0",
                "code": "expand_independent_human_gold",
                "reason": "current human evidence is a single-province slice",
            },
            {
                "priority": "P0",
                "code": "approve_coverage_thresholds",
                "reason": "technical checks cannot define business representativeness",
            },
            {
                "priority": "P1",
                "code": "repair_bill_library_province_metadata",
                "reason": "unlabeled query sampling requires reliable province strata",
            },
            {
                "priority": "P1",
                "code": "keep_oss_and_historical_metrics_separate",
                "reason": "diagnostic and failure-oriented samples cannot prove system accuracy",
            },
        ],
    }


__all__ = [
    "audit_bill_library",
    "audit_jsonl_group",
    "audit_national_index",
    "audit_oss_root",
    "build_coverage_inventory",
]
