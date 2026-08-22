from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from web.backend.app.text_utils import repair_mojibake_text

from .fingerprints import province_query_fingerprint, query_fingerprint
from .suggestions import (
    SUGGESTION_SOURCE,
    SUGGESTION_VERSION,
    NationalIndexSuggestionProvider,
    suggestion_columns,
)

REVIEW_QUEUE_VERSION = "accuracy_review_queue.v5"
REVIEW_SELECTION_VALUES = ("1", "2", "3", "4", "5", "reject")
_QUALITY_ORDER = {
    "name_description_unit": 0,
    "name_description": 1,
    "name_unit": 2,
    "name_only": 3,
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _repair_clean(
    value: Any,
    *,
    field_name: str,
    repair_counts: Counter[str],
) -> str:
    original = _clean(value)
    repaired = _clean(
        repair_mojibake_text(
            str(value or ""),
            preserve_newlines=True,
        )
        or ""
    )
    if repaired != original:
        repair_counts[field_name] += 1
    return repaired


def _stable_hash(*values: Any) -> str:
    payload = "\x1f".join(_clean(value).casefold() for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _open_readonly(path: str | Path) -> tuple[sqlite3.Connection, Path]:
    resolved = Path(path).resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection, resolved


def _load_quota_books(
    path: str | Path,
) -> tuple[dict[str, int], Path, Counter[str]]:
    connection, resolved = _open_readonly(path)
    repair_counts: Counter[str] = Counter()
    try:
        rows = connection.execute(
            """
            SELECT province, COUNT(*) AS row_count
            FROM national_quotas
            WHERE LENGTH(TRIM(COALESCE(province, ''))) > 0
            GROUP BY province
            ORDER BY province
            """
        ).fetchall()
        quota_books: Counter[str] = Counter()
        for row in rows:
            name = _repair_clean(
                row["province"],
                field_name="candidate_quota_book",
                repair_counts=repair_counts,
            )
            if name:
                quota_books[name] += int(row["row_count"])
        return dict(quota_books), resolved, repair_counts
    finally:
        connection.close()


def _candidate_quota_books(
    province: str,
    quota_books: Mapping[str, int],
) -> list[dict[str, Any]]:
    normalized_province = _clean(province).casefold()
    if not normalized_province:
        return []
    return [
        {"name": name, "quota_rows": quota_books[name]}
        for name in sorted(quota_books)
        if normalized_province in name.casefold()
    ]


def _quality_tier(*, description: str, unit: str) -> str:
    if description and unit:
        return "name_description_unit"
    if description:
        return "name_description"
    if unit:
        return "name_unit"
    return "name_only"


def _load_bill_candidates(
    path: str | Path,
    seed: str,
) -> tuple[list[dict[str, Any]], Path, Counter[str]]:
    connection, resolved = _open_readonly(path)
    repair_counts: Counter[str] = Counter()
    try:
        rows = connection.execute(
            """
            SELECT
                b.id AS source_record_id,
                b.file_path,
                f.file_name,
                f.province,
                f.specialty,
                b.sheet_name,
                b.section,
                b.bill_code,
                b.bill_name,
                b.description,
                b.unit
            FROM bill_items AS b
            JOIN files AS f ON f.file_path = b.file_path
            WHERE LENGTH(TRIM(COALESCE(f.province, ''))) > 0
              AND LENGTH(TRIM(COALESCE(b.bill_name, ''))) > 0
            ORDER BY b.id
            """
        )
        candidates: list[dict[str, Any]] = []
        for row in rows:
            province = _repair_clean(
                row["province"],
                field_name="province",
                repair_counts=repair_counts,
            )
            specialty = _repair_clean(
                row["specialty"],
                field_name="specialty",
                repair_counts=repair_counts,
            )
            bill_name = _repair_clean(
                row["bill_name"],
                field_name="bill_name",
                repair_counts=repair_counts,
            )
            description = _repair_clean(
                row["description"],
                field_name="description",
                repair_counts=repair_counts,
            )
            unit = _repair_clean(
                row["unit"],
                field_name="unit",
                repair_counts=repair_counts,
            )
            query_text = " ".join(value for value in (bill_name, description) if value)
            project_id = f"bill-project:{_stable_hash(row['file_path'])[:20]}"
            source_record_id = int(row["source_record_id"])
            candidates.append(
                {
                    "source_record_id": source_record_id,
                    "source_file_name": _repair_clean(
                        row["file_name"],
                        field_name="source_file_name",
                        repair_counts=repair_counts,
                    ),
                    "project_id": project_id,
                    "province": province,
                    "specialty": specialty,
                    "sheet_name": _repair_clean(
                        row["sheet_name"],
                        field_name="sheet_name",
                        repair_counts=repair_counts,
                    ),
                    "section": _repair_clean(
                        row["section"],
                        field_name="section",
                        repair_counts=repair_counts,
                    ),
                    "bill_code": _repair_clean(
                        row["bill_code"],
                        field_name="bill_code",
                        repair_counts=repair_counts,
                    ),
                    "bill_name": bill_name,
                    "bill_text": query_text,
                    "description": description,
                    "unit": unit,
                    "query_fingerprint": query_fingerprint(query_text),
                    "province_query_fingerprint": province_query_fingerprint(
                        province,
                        query_text,
                    ),
                    "quality_tier": _quality_tier(
                        description=description,
                        unit=unit,
                    ),
                    "selection_key": _stable_hash(
                        seed,
                        province,
                        specialty,
                        project_id,
                        source_record_id,
                    ),
                }
            )
        return candidates, resolved, repair_counts
    finally:
        connection.close()


def _deduplicate_candidates(
    candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    ordered = sorted(
        candidates,
        key=lambda row: (
            _QUALITY_ORDER[row["quality_tier"]],
            row["selection_key"],
        ),
    )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in ordered:
        fingerprint = row["province_query_fingerprint"]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(row)
    return unique, len(candidates) - len(unique)


def _sample_province(
    candidates: Sequence[dict[str, Any]],
    *,
    target_count: int,
    max_per_project: int,
    seed: str,
) -> list[dict[str, Any]]:
    by_specialty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_specialty[row["specialty"] or "<unknown>"].append(row)
    for rows in by_specialty.values():
        rows.sort(
            key=lambda row: (
                _QUALITY_ORDER[row["quality_tier"]],
                row["selection_key"],
            )
        )

    province = candidates[0]["province"] if candidates else ""
    specialty_order = sorted(
        by_specialty,
        key=lambda specialty: _stable_hash(seed, province, specialty),
    )
    offsets = {specialty: 0 for specialty in specialty_order}
    project_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []

    while len(selected) < target_count:
        made_progress = False
        for specialty in specialty_order:
            rows = by_specialty[specialty]
            while offsets[specialty] < len(rows):
                row = rows[offsets[specialty]]
                offsets[specialty] += 1
                if project_counts[row["project_id"]] >= max_per_project:
                    continue
                selected.append(row)
                project_counts[row["project_id"]] += 1
                made_progress = True
                break
            if len(selected) >= target_count:
                break
        if not made_progress:
            break
    return selected


def build_review_queue(
    *,
    bill_library_path: str | Path,
    national_index_path: str | Path,
    target_per_province: int = 20,
    max_per_project: int = 2,
    suggested_top_k: int = 5,
    suggested_min_score: float = 20.0,
    seed: str = "independent-gold-v1",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if target_per_province <= 0:
        raise ValueError("target_per_province must be positive")
    if max_per_project <= 0:
        raise ValueError("max_per_project must be positive")
    if not 1 <= suggested_top_k <= 5:
        raise ValueError("suggested_top_k must be between 1 and 5")
    if not 0.0 <= suggested_min_score <= 100.0:
        raise ValueError("suggested_min_score must be between 0 and 100")
    if not _clean(seed):
        raise ValueError("seed must be non-empty")

    quota_books, national_index, quota_book_repairs = _load_quota_books(
        national_index_path
    )
    candidates, bill_library, bill_repairs = _load_bill_candidates(
        bill_library_path,
        seed,
    )
    unique_candidates, duplicate_count = _deduplicate_candidates(candidates)
    by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unique_candidates:
        by_province[row["province"]].append(row)

    selected: list[dict[str, Any]] = []
    province_summaries: list[dict[str, Any]] = []
    suggested_rows = 0
    suggested_candidates = 0
    with NationalIndexSuggestionProvider(
        national_index,
        top_k=suggested_top_k,
        minimum_score=suggested_min_score,
    ) as suggestion_provider:
        for province in sorted(by_province):
            province_rows = _sample_province(
                by_province[province],
                target_count=target_per_province,
                max_per_project=max_per_project,
                seed=seed,
            )
            candidate_books = _candidate_quota_books(province, quota_books)
            for rank, row in enumerate(province_rows, start=1):
                sample_id = (
                    f"review:{_stable_hash(row['project_id'], row['source_record_id'])[:24]}"
                )
                queue_row = {
                    "sample_id": sample_id,
                    "review_status": "pending",
                    "review_selection": "",
                    "dataset_role": "independent_gold_candidate",
                    "source": "bill_library.db",
                    "source_family": "bill_library",
                    "province": province,
                    "specialty": row["specialty"],
                    "project_id": row["project_id"],
                    "source_file_name": row["source_file_name"],
                    "source_record_id": row["source_record_id"],
                    "sheet_name": row["sheet_name"],
                    "section": row["section"],
                    "bill_code": row["bill_code"],
                    "bill_name": row["bill_name"],
                    "bill_text": row["bill_text"],
                    "description": row["description"],
                    "unit": row["unit"],
                    "quality_tier": row["quality_tier"],
                    "query_fingerprint": row["query_fingerprint"],
                    "province_query_fingerprint": row[
                        "province_query_fingerprint"
                    ],
                    "sample_rank_in_province": rank,
                    "candidate_quota_books": candidate_books,
                    "oracle_quota_ids": [],
                    "oracle_quota_names": [],
                    "oracle_semantics": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "review_notes": "",
                }
                suggestions = suggestion_provider.suggest(queue_row)
                queue_row.update(suggestion_columns(suggestions))
                if suggestions:
                    suggested_rows += 1
                    suggested_candidates += len(suggestions)
                selected.append(queue_row)
            province_summaries.append(
                {
                    "province": province,
                    "eligible_unique_queries": len(by_province[province]),
                    "selected": len(province_rows),
                    "distinct_projects": len(
                        {row["project_id"] for row in province_rows}
                    ),
                    "distinct_specialties": len(
                        {row["specialty"] or "<unknown>" for row in province_rows}
                    ),
                    "candidate_quota_book_count": len(candidate_books),
                }
            )

    selected.sort(key=lambda row: (row["province"], row["sample_rank_in_province"]))
    quality_counts = Counter(row["quality_tier"] for row in selected)
    manifest = {
        "version": REVIEW_QUEUE_VERSION,
        "role": "independent_human_gold_candidate_queue",
        "system_baseline_eligible": False,
        "review_required": True,
        "selection": {
            "seed": seed,
            "target_per_province": target_per_province,
            "max_per_project": max_per_project,
            "deduplication": "province_query_fingerprint",
            "stratification": ["province", "specialty", "project_id"],
        },
        "sources": {
            "bill_library": str(bill_library),
            "national_index": str(national_index),
        },
        "eligible_rows_before_deduplication": len(candidates),
        "duplicate_rows_removed": duplicate_count,
        "eligible_unique_queries": len(unique_candidates),
        "selected_rows": len(selected),
        "selected_provinces": len(province_summaries),
        "selected_projects": len({row["project_id"] for row in selected}),
        "selected_specialties": len(
            {row["specialty"] or "<unknown>" for row in selected}
        ),
        "quality_tiers": dict(sorted(quality_counts.items())),
        "text_repair": {
            "strategy": "repair_mojibake_text_before_fingerprinting",
            "repaired_field_values": sum(bill_repairs.values())
            + sum(quota_book_repairs.values()),
            "fields": dict(sorted((bill_repairs + quota_book_repairs).items())),
        },
        "provinces_without_candidate_quota_books": [
            row["province"]
            for row in province_summaries
            if row["candidate_quota_book_count"] == 0
        ],
        "provinces": province_summaries,
        "review_contract": {
            "required_fields": [
                "review_selection",
                "reviewer",
                "reviewed_at",
            ],
            "allowed_review_selections": list(REVIEW_SELECTION_VALUES),
            "oracle_generated_by_promotion": True,
            "reviewer_oracle_fields_must_be_blank": True,
            "promotion_rule": (
                "Only rows where two approved reviewers independently select the same "
                "suggested rank may be promoted. Matching reject selections are preserved "
                "as agreed rejections."
            ),
        },
        "suggestion_contract": {
            "version": SUGGESTION_VERSION,
            "source": SUGGESTION_SOURCE,
            "top_k": suggested_top_k,
            "minimum_score": suggested_min_score,
            "advisory_only": True,
            "oracle_fields_prefilled": False,
            "immutable_during_review": True,
            "suggested_rows": suggested_rows,
            "empty_suggestion_rows": len(selected) - suggested_rows,
            "suggested_candidates": suggested_candidates,
            "fields": [
                "suggested_quota_ids",
                "suggested_quota_names",
                "suggested_quota_books",
                "suggested_scores",
                "suggested_reasons",
                "suggested_source",
                "suggested_version",
            ],
            "promotion_rule": (
                "Suggestions are immutable advisory context. A suggestion becomes an oracle "
                "only when two approved reviewers independently select the same rank."
            ),
        },
    }
    return selected, manifest


def write_review_queue(
    *,
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    resolved = Path(output_dir).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    jsonl_path = resolved / "review_queue.jsonl"
    csv_path = resolved / "review_queue.csv"
    manifest_path = resolved / "review_queue_manifest.json"

    jsonl_payload = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    content_sha256 = hashlib.sha256(jsonl_payload.encode("utf-8")).hexdigest()
    jsonl_path.write_text(jsonl_payload, encoding="utf-8", newline="\n")

    fieldnames = [
        "sample_id",
        "queue_content_sha256",
        "review_status",
        "review_selection",
        "dataset_role",
        "source",
        "source_family",
        "province",
        "specialty",
        "project_id",
        "source_file_name",
        "source_record_id",
        "sheet_name",
        "section",
        "bill_code",
        "bill_name",
        "bill_text",
        "description",
        "unit",
        "quality_tier",
        "query_fingerprint",
        "province_query_fingerprint",
        "sample_rank_in_province",
        "candidate_quota_books",
        "suggested_quota_ids",
        "suggested_quota_names",
        "suggested_quota_books",
        "suggested_scores",
        "suggested_reasons",
        "suggested_source",
        "suggested_version",
        "oracle_quota_ids",
        "oracle_quota_names",
        "oracle_semantics",
        "reviewer",
        "reviewed_at",
        "review_notes",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            payload = dict(row)
            payload["queue_content_sha256"] = content_sha256
            for field_name in (
                "candidate_quota_books",
                "suggested_quota_ids",
                "suggested_quota_names",
                "suggested_quota_books",
                "suggested_scores",
                "suggested_reasons",
                "oracle_quota_ids",
                "oracle_quota_names",
            ):
                payload[field_name] = json.dumps(
                    payload.get(field_name) or [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            writer.writerow(payload)

    manifest_payload = {
        **dict(manifest),
        "outputs": {
            "jsonl": str(jsonl_path),
            "csv": str(csv_path),
        },
        "content_sha256": content_sha256,
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "jsonl": jsonl_path,
        "csv": csv_path,
        "manifest": manifest_path,
    }


__all__ = [
    "REVIEW_SELECTION_VALUES",
    "build_review_queue",
    "write_review_queue",
]
