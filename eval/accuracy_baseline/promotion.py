from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import DatasetKind, EvalCase, OracleSemantics
from .coverage import summarize_dataset_coverage
from .datasets import normalize_quota_id
from .fingerprints import province_query_fingerprint, query_fingerprint

PROMOTION_VERSION = "accuracy_review_promotion.v3"
_CONTEXT_FIELDS = (
    "province",
    "specialty",
    "project_id",
    "source",
    "source_family",
    "source_file_name",
    "source_record_id",
    "sheet_name",
    "section",
    "bill_code",
    "bill_name",
    "bill_text",
    "description",
    "unit",
    "query_fingerprint",
    "province_query_fingerprint",
)
_SUGGESTION_LIST_FIELDS = (
    "suggested_quota_ids",
    "suggested_quota_names",
    "suggested_quota_books",
    "suggested_scores",
    "suggested_reasons",
)
_SUGGESTION_CONTEXT_FIELDS = (
    *_SUGGESTION_LIST_FIELDS,
    "suggested_source",
    "suggested_version",
)


class PromotionValidationError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _stable_hash(*values: Any) -> str:
    payload = "\x1f".join(_clean(value).casefold() for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            parsed = []
        else:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = value
    return json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_review_file(
    path: str | Path,
) -> tuple[list[dict[str, Any]], Path, str]:
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    suffix = resolved.suffix.lower()
    if suffix == ".csv":
        rows = [
            dict(row)
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        ]
    elif suffix == ".jsonl":
        rows = []
        for line_number, line in enumerate(
            raw.decode("utf-8-sig").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError(
                    f"line {line_number} is not a JSON object: {resolved}"
                )
            rows.append(payload)
    else:
        raise ValueError(f"unsupported review file type: {resolved}")
    return rows, resolved, hashlib.sha256(raw).hexdigest()


def _load_json_object(
    path: str | Path,
    *,
    label: str,
) -> tuple[dict[str, Any], Path, str]:
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object: {resolved}")
    return payload, resolved, hashlib.sha256(raw).hexdigest()


def _index_reviews(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    errors: list[str],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row_number, row in enumerate(rows, start=2):
        sample_id = _clean(row.get("sample_id"))
        if not sample_id:
            errors.append(f"{label}:row_{row_number}:missing_sample_id")
            continue
        if sample_id in indexed:
            errors.append(f"{label}:{sample_id}:duplicate_sample_id")
            continue
        indexed[sample_id] = row
    return indexed


def _string_list(value: Any, *, quota_ids: bool = False) -> list[str]:
    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = text.split("|")
    if not isinstance(parsed, (list, tuple, set)):
        parsed = [parsed]
    result: list[str] = []
    for item in parsed:
        text = normalize_quota_id(item) if quota_ids else _clean(item)
        if text:
            result.append(text)
    return result


def _reviewed_at(
    row: Mapping[str, Any],
    *,
    label: str,
    sample_id: str,
    errors: list[str],
) -> str:
    value = _clean(row.get("reviewed_at"))
    if not value:
        errors.append(f"{label}:{sample_id}:missing_reviewed_at")
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}:{sample_id}:invalid_reviewed_at")
        return ""
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{label}:{sample_id}:reviewed_at_timezone_required")
        return ""
    return parsed.isoformat()


def _oracle_payload(
    row: Mapping[str, Any],
    *,
    label: str,
    sample_id: str,
    errors: list[str],
) -> tuple[tuple[tuple[str, str], ...], str]:
    quota_ids = _string_list(row.get("oracle_quota_ids"), quota_ids=True)
    quota_names = _string_list(row.get("oracle_quota_names"))
    semantics = _clean(row.get("oracle_semantics")).casefold()

    if not quota_ids:
        errors.append(f"{label}:{sample_id}:missing_oracle_quota_ids")
    if len(set(quota_ids)) != len(quota_ids):
        errors.append(f"{label}:{sample_id}:duplicate_oracle_quota_ids")
    if len(quota_names) != len(quota_ids):
        errors.append(f"{label}:{sample_id}:oracle_name_count_mismatch")
    if semantics not in {"any", "all"}:
        errors.append(f"{label}:{sample_id}:invalid_oracle_semantics")

    pairs = tuple(sorted(zip(quota_ids, quota_names), key=lambda item: item[0]))
    return pairs, semantics


def _query_key(row: Mapping[str, Any]) -> str:
    stored = query_fingerprint(row.get("query_fingerprint"))
    if stored:
        return stored
    bill_text = _clean(row.get("bill_text"))
    description = _clean(row.get("description"))
    bill_name = _clean(row.get("bill_name"))
    return query_fingerprint(bill_text or f"{bill_name} {description}")


def _validate_queue_manifest(
    *,
    queue_path: Path,
    queue_sha256: str,
    queue_rows: Sequence[Mapping[str, Any]],
    manifest_path: str | Path | None,
) -> tuple[dict[str, Any], Path, str]:
    resolved_manifest = (
        Path(manifest_path).resolve()
        if manifest_path
        else queue_path.with_name("review_queue_manifest.json")
    )
    manifest, resolved_manifest, manifest_sha256 = _load_json_object(
        resolved_manifest,
        label="review queue manifest",
    )
    errors: list[str] = []
    if _clean(manifest.get("content_sha256")) != queue_sha256:
        errors.append("review_queue_manifest:content_sha256_mismatch")
    if manifest.get("selected_rows") != len(queue_rows):
        errors.append("review_queue_manifest:selected_rows_mismatch")
    if not _clean(manifest.get("version")).startswith("accuracy_review_queue."):
        errors.append("review_queue_manifest:invalid_version")
    if errors:
        raise PromotionValidationError(errors)
    return manifest, resolved_manifest, manifest_sha256


def _load_reviewer_registry(
    path: str | Path,
) -> tuple[set[str], dict[str, Any], Path, str]:
    registry, resolved, registry_sha256 = _load_json_object(
        path,
        label="reviewer registry",
    )
    errors: list[str] = []
    if not _clean(registry.get("version")):
        errors.append("reviewer_registry:missing_version")
    if not _clean(registry.get("approval_reference")):
        errors.append("reviewer_registry:missing_approval_reference")
    records = registry.get("reviewers")
    if not isinstance(records, list):
        errors.append("reviewer_registry:reviewers_must_be_a_list")
        records = []

    reviewer_ids: set[str] = set()
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            errors.append(f"reviewer_registry:row_{index}:invalid_record")
            continue
        reviewer_id = _clean(record.get("reviewer_id"))
        normalized = reviewer_id.casefold()
        if not reviewer_id:
            errors.append(f"reviewer_registry:row_{index}:missing_reviewer_id")
        elif normalized in seen:
            errors.append(f"reviewer_registry:{reviewer_id}:duplicate_reviewer_id")
        else:
            seen.add(normalized)
            if record.get("active") is True:
                reviewer_ids.add(normalized)
    if len(reviewer_ids) < 2:
        errors.append("reviewer_registry:at_least_two_active_reviewers_required")
    if errors:
        raise PromotionValidationError(sorted(set(errors)))
    return reviewer_ids, registry, resolved, registry_sha256


def _validate_context_against_queue(
    authority: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    label: str,
    sample_id: str,
    queue_sha256: str,
    errors: list[str],
) -> None:
    if _clean(review.get("queue_content_sha256")) != queue_sha256:
        errors.append(f"{label}:{sample_id}:queue_content_sha256_mismatch")
    for field_name in _CONTEXT_FIELDS:
        if field_name not in review:
            errors.append(f"{label}:{sample_id}:missing_context:{field_name}")
            continue
        if _clean(review.get(field_name)) != _clean(authority.get(field_name)):
            errors.append(f"{label}:{sample_id}:context_conflict:{field_name}")
    for field_name in _SUGGESTION_CONTEXT_FIELDS:
        if field_name not in authority:
            continue
        if field_name not in review:
            errors.append(f"{label}:{sample_id}:missing_context:{field_name}")
            continue
        if field_name in _SUGGESTION_LIST_FIELDS:
            matches = _canonical_json(review.get(field_name)) == _canonical_json(
                authority.get(field_name)
            )
        else:
            matches = _clean(review.get(field_name)) == _clean(
                authority.get(field_name)
            )
        if not matches:
            errors.append(f"{label}:{sample_id}:context_conflict:{field_name}")


def _validate_queue_label_isolation(
    authority: Mapping[str, Any],
    *,
    sample_id: str,
    errors: list[str],
) -> None:
    if _string_list(authority.get("oracle_quota_ids"), quota_ids=True):
        errors.append(f"review_queue:{sample_id}:oracle_quota_ids_must_be_blank")
    if _string_list(authority.get("oracle_quota_names")):
        errors.append(f"review_queue:{sample_id}:oracle_quota_names_must_be_blank")
    for field_name in ("oracle_semantics", "reviewer", "reviewed_at"):
        if _clean(authority.get(field_name)):
            errors.append(f"review_queue:{sample_id}:{field_name}_must_be_blank")

    if not any(field_name in authority for field_name in _SUGGESTION_CONTEXT_FIELDS):
        return
    values: dict[str, Any] = {}
    for field_name in _SUGGESTION_LIST_FIELDS:
        raw = authority.get(field_name)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else []
            except (TypeError, ValueError, json.JSONDecodeError):
                errors.append(f"review_queue:{sample_id}:invalid_{field_name}")
                raw = []
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            errors.append(f"review_queue:{sample_id}:invalid_{field_name}")
            raw = []
        values[field_name] = list(raw)
    expected_count = len(values["suggested_quota_ids"])
    for field_name in _SUGGESTION_LIST_FIELDS[1:]:
        if len(values[field_name]) != expected_count:
            errors.append(
                f"review_queue:{sample_id}:suggested_field_count_mismatch:{field_name}"
            )
    if expected_count and not _clean(authority.get("suggested_source")):
        errors.append(f"review_queue:{sample_id}:missing_suggested_source")
    if expected_count and not _clean(authority.get("suggested_version")):
        errors.append(f"review_queue:{sample_id}:missing_suggested_version")


def _reviewed_sample(
    authority: Mapping[str, Any],
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    sample_id: str,
    queue_sha256: str,
    approved_reviewers: set[str],
    errors: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    row_errors: list[str] = []
    audits: list[dict[str, str]] = []
    statuses: list[str] = []
    reviewers: list[str] = []

    for label, row in (("review_a", first), ("review_b", second)):
        _validate_context_against_queue(
            authority,
            row,
            label=label,
            sample_id=sample_id,
            queue_sha256=queue_sha256,
            errors=row_errors,
        )
        status = _clean(row.get("review_status")).casefold()
        if status not in {"accepted", "rejected"}:
            row_errors.append(f"{label}:{sample_id}:invalid_review_status")
        reviewer = _clean(row.get("reviewer"))
        if not reviewer:
            row_errors.append(f"{label}:{sample_id}:missing_reviewer")
        elif reviewer.casefold() not in approved_reviewers:
            row_errors.append(f"{label}:{sample_id}:reviewer_not_approved")
        reviewed_at = _reviewed_at(
            row,
            label=label,
            sample_id=sample_id,
            errors=row_errors,
        )
        statuses.append(status)
        reviewers.append(reviewer)
        audits.append(
            {
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "review_notes": _clean(row.get("review_notes")),
            }
        )

    if reviewers[0] and reviewers[0].casefold() == reviewers[1].casefold():
        row_errors.append(f"{sample_id}:reviewers_must_be_distinct")
    if statuses[0] != statuses[1]:
        row_errors.append(f"{sample_id}:review_status_conflict")
    if row_errors:
        errors.extend(row_errors)
        return None, None

    if statuses[0] == "rejected":
        return None, {
            "sample_id": sample_id,
            "review_status": "rejected",
            "review_audit": audits,
        }

    oracle_errors: list[str] = []
    first_oracle = _oracle_payload(
        first,
        label="review_a",
        sample_id=sample_id,
        errors=oracle_errors,
    )
    second_oracle = _oracle_payload(
        second,
        label="review_b",
        sample_id=sample_id,
        errors=oracle_errors,
    )
    if first_oracle != second_oracle:
        oracle_errors.append(f"{sample_id}:oracle_conflict")
    if oracle_errors:
        errors.extend(oracle_errors)
        return None, None

    pairs, semantics = first_oracle
    query_key = _query_key(authority)
    province = _clean(authority.get("province"))
    return {
        "sample_id": sample_id,
        "review_status": "accepted",
        "dataset_role": "independent_human_gold",
        "source": _clean(authority.get("source")),
        "source_family": _clean(authority.get("source_family")),
        "label_source_family": "dual_independent_human_review",
        "province": province,
        "specialty": _clean(authority.get("specialty")),
        "project_id": _clean(authority.get("project_id")),
        "source_file_name": _clean(authority.get("source_file_name")),
        "source_record_id": _clean(authority.get("source_record_id")),
        "sheet_name": _clean(authority.get("sheet_name")),
        "section": _clean(authority.get("section")),
        "bill_code": _clean(authority.get("bill_code")),
        "bill_name": _clean(authority.get("bill_name")),
        "bill_text": _clean(authority.get("bill_text")),
        "description": _clean(authority.get("description")),
        "unit": _clean(authority.get("unit")),
        "query_fingerprint": query_key,
        "province_query_fingerprint": province_query_fingerprint(
            province,
            query_key,
        ),
        "oracle_quota_ids": [quota_id for quota_id, _ in pairs],
        "oracle_quota_names": [name for _, name in pairs],
        "oracle_semantics": semantics,
        "review_audit": audits,
    }, None


def _candidate_book_names(authority: Mapping[str, Any]) -> list[str]:
    value = authority.get("candidate_quota_books")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        name = _clean(item.get("name")) if isinstance(item, Mapping) else _clean(item)
        if name and name not in result:
            result.append(name)
    return result


def _validate_oracles_against_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    queue_by_id: Mapping[str, Mapping[str, Any]],
    national_index_path: str | Path,
) -> tuple[Path, int]:
    resolved = Path(national_index_path).resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    errors: list[str] = []
    checked = 0
    try:
        for row in rows:
            sample_id = _clean(row.get("sample_id"))
            books = _candidate_book_names(queue_by_id[sample_id])
            if not books:
                errors.append(f"{sample_id}:missing_candidate_quota_books")
                continue
            placeholders = ",".join("?" for _ in books)
            for quota_id, submitted_name in zip(
                row.get("oracle_quota_ids") or [],
                row.get("oracle_quota_names") or [],
            ):
                matches = connection.execute(
                    f"""
                    SELECT province, name
                    FROM national_quotas
                    WHERE province IN ({placeholders}) AND quota_id = ?
                    """,
                    (*books, quota_id),
                ).fetchall()
                checked += 1
                if not matches:
                    errors.append(
                        f"{sample_id}:{quota_id}:oracle_not_in_candidate_books"
                    )
                    continue
                canonical_names = {_clean(match[1]).casefold() for match in matches}
                if _clean(submitted_name).casefold() not in canonical_names:
                    errors.append(f"{sample_id}:{quota_id}:oracle_name_mismatch")
    finally:
        connection.close()
    if errors:
        raise PromotionValidationError(sorted(set(errors)))
    return resolved, checked


def _assign_isolated_splits(
    rows: Sequence[dict[str, Any]],
    *,
    split_names: Sequence[str],
    seed: str,
) -> int:
    splits = tuple(_clean(name) for name in split_names if _clean(name))
    if len(splits) < 2 or len(set(splits)) != len(splits):
        raise ValueError("split_names must contain at least two distinct values")

    parent = {row["sample_id"]: row["sample_id"] for row in rows}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: str, second: str) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    first_by_project: dict[str, str] = {}
    first_by_query: dict[str, str] = {}
    for row in rows:
        sample_id = row["sample_id"]
        evaluation_query = query_fingerprint(
            " ".join((row["bill_name"], row["bill_text"]))
        )
        keys = (
            (row["project_id"], first_by_project),
            *((key, first_by_query) for key in {row["query_fingerprint"], evaluation_query}),
        )
        for key, seen in keys:
            if not key:
                continue
            existing = seen.setdefault(key, sample_id)
            union(sample_id, existing)

    components: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        components.setdefault(find(row["sample_id"]), []).append(row)

    ordered_components = sorted(
        components.values(),
        key=lambda component: (
            -len(component),
            _stable_hash(seed, *(row["sample_id"] for row in component)),
        ),
    )
    split_loads: Counter[str] = Counter()
    for component in ordered_components:
        component_key = _stable_hash(*(row["sample_id"] for row in component))
        split = min(
            splits,
            key=lambda name: (
                split_loads[name],
                _stable_hash(seed, component_key, name),
            ),
        )
        for row in component:
            row["split"] = split
        split_loads[split] += len(component)
    return len(components)


def _to_eval_case(row: Mapping[str, Any]) -> EvalCase:
    return EvalCase(
        case_id=_clean(row.get("sample_id")),
        dataset_kind=DatasetKind.PRIMARY,
        province=_clean(row.get("province")),
        bill_name=_clean(row.get("bill_name")),
        bill_text=_clean(row.get("bill_text")),
        unit=_clean(row.get("unit")),
        specialty=_clean(row.get("specialty")),
        oracle_quota_ids=tuple(
            _string_list(row.get("oracle_quota_ids"), quota_ids=True)
        ),
        source_family=_clean(row.get("source_family")),
        project_id=_clean(row.get("project_id")),
        oracle_semantics=OracleSemantics(_clean(row.get("oracle_semantics"))),
        source=_clean(row.get("source")),
        split=_clean(row.get("split")),
    )


def build_promoted_dataset(
    *,
    review_queue_path: str | Path,
    review_a_path: str | Path,
    review_b_path: str | Path,
    national_index_path: str | Path,
    reviewer_registry_path: str | Path,
    review_queue_manifest_path: str | Path | None = None,
    coverage_requirements: Mapping[str, Any] | None = None,
    split_names: Sequence[str] = ("dev", "heldout"),
    seed: str = "independent-gold-split-v1",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _clean(seed):
        raise ValueError("seed must be non-empty")

    queue_rows, queue_path, queue_sha256 = _load_review_file(review_queue_path)
    queue_manifest, queue_manifest_path, queue_manifest_sha256 = (
        _validate_queue_manifest(
            queue_path=queue_path,
            queue_sha256=queue_sha256,
            queue_rows=queue_rows,
            manifest_path=review_queue_manifest_path,
        )
    )
    approved_reviewers, registry, registry_path, registry_sha256 = (
        _load_reviewer_registry(reviewer_registry_path)
    )
    first_rows, first_path, first_sha256 = _load_review_file(review_a_path)
    second_rows, second_path, second_sha256 = _load_review_file(review_b_path)

    errors: list[str] = []
    queue_by_id = _index_reviews(queue_rows, label="review_queue", errors=errors)
    first_by_id = _index_reviews(first_rows, label="review_a", errors=errors)
    second_by_id = _index_reviews(second_rows, label="review_b", errors=errors)
    queue_ids = set(queue_by_id)
    for sample_id, authority in queue_by_id.items():
        _validate_queue_label_isolation(
            authority,
            sample_id=sample_id,
            errors=errors,
        )
    for label, indexed in (("review_a", first_by_id), ("review_b", second_by_id)):
        for sample_id in sorted(queue_ids - set(indexed)):
            errors.append(f"{label}:{sample_id}:missing_sample")
        for sample_id in sorted(set(indexed) - queue_ids):
            errors.append(f"{label}:{sample_id}:unknown_sample")

    promoted: list[dict[str, Any]] = []
    agreed_rejections: list[dict[str, Any]] = []
    for sample_id in sorted(queue_ids & set(first_by_id) & set(second_by_id)):
        row, rejected = _reviewed_sample(
            queue_by_id[sample_id],
            first_by_id[sample_id],
            second_by_id[sample_id],
            sample_id=sample_id,
            queue_sha256=queue_sha256,
            approved_reviewers=approved_reviewers,
            errors=errors,
        )
        if row is not None:
            promoted.append(row)
        if rejected is not None:
            agreed_rejections.append(rejected)

    if not promoted:
        errors.append("no_promotable_rows")
    if errors:
        raise PromotionValidationError(sorted(set(errors)))

    national_index, checked_oracles = _validate_oracles_against_index(
        promoted,
        queue_by_id=queue_by_id,
        national_index_path=national_index_path,
    )
    component_count = _assign_isolated_splits(
        promoted,
        split_names=split_names,
        seed=seed,
    )
    promoted.sort(key=lambda row: row["sample_id"])
    coverage = summarize_dataset_coverage(
        [_to_eval_case(row) for row in promoted],
        DatasetKind.PRIMARY,
        coverage_requirements,
    )
    observed = coverage["observed"]
    if observed["cross_split_query_overlap_count"]:
        raise RuntimeError("split assignment leaked a query across splits")
    if observed["cross_split_project_overlap_count"]:
        raise RuntimeError("split assignment leaked a project across splits")

    split_counts = Counter(row["split"] for row in promoted)
    manifest = {
        "version": PROMOTION_VERSION,
        "role": "independent_human_gold_evaluation_dataset",
        "reviewed_rows": len(queue_rows),
        "promoted_rows": len(promoted),
        "agreed_rejected_rows": len(agreed_rejections),
        "agreed_rejections": agreed_rejections,
        "system_baseline_eligible": coverage["system_baseline_eligible"],
        "scope": coverage["scope"],
        "review_queue": {
            "path": str(queue_path),
            "content_sha256": queue_sha256,
            "manifest_path": str(queue_manifest_path),
            "manifest_sha256": queue_manifest_sha256,
            "version": queue_manifest["version"],
        },
        "review_sources": [
            {
                "path": str(first_path),
                "rows": len(first_rows),
                "content_sha256": first_sha256,
            },
            {
                "path": str(second_path),
                "rows": len(second_rows),
                "content_sha256": second_sha256,
            },
        ],
        "reviewer_registry": {
            "path": str(registry_path),
            "content_sha256": registry_sha256,
            "version": registry["version"],
            "approval_reference": registry["approval_reference"],
        },
        "oracle_authority": {
            "path": str(national_index),
            "table": "national_quotas",
            "checked_oracles": checked_oracles,
        },
        "review_contract": {
            "required_reviews_per_sample": 2,
            "approved_distinct_reviewers_required": True,
            "queue_content_hash_required": True,
            "timezone_aware_reviewed_at_required": True,
            "matching_oracle_payload_required": True,
            "oracle_authority_match_required": True,
        },
        "suggestion_isolation": {
            "queue_oracle_fields_required_blank": True,
            "suggestions_immutable_in_reviews": True,
            "suggestions_excluded_from_promoted_rows": True,
            "suggestions_never_auto_promoted": True,
        },
        "split_assignment": {
            "seed": seed,
            "method": "project_and_global_query_connected_components",
            "component_count": component_count,
            "counts": dict(sorted(split_counts.items())),
        },
        "isolation": {
            "cross_split_query_overlap_count": observed[
                "cross_split_query_overlap_count"
            ],
            "cross_split_project_overlap_count": observed[
                "cross_split_project_overlap_count"
            ],
            "passed": True,
        },
        "coverage": coverage,
    }
    return promoted, manifest


def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_promoted_dataset(
    *,
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    resolved = Path(output_dir).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    dataset_path = resolved / "primary_dataset.jsonl"
    rejected_path = resolved / "agreed_rejections.jsonl"
    manifest_path = resolved / "promotion_manifest.json"

    dataset_payload = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    rejected_payload = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in manifest.get("agreed_rejections") or []
    )
    _write_text_atomic(dataset_path, dataset_payload)
    _write_text_atomic(rejected_path, rejected_payload)
    manifest_payload = {
        **dict(manifest),
        "outputs": {
            "dataset": str(dataset_path),
            "agreed_rejections": str(rejected_path),
        },
        "content_sha256": hashlib.sha256(
            dataset_payload.encode("utf-8")
        ).hexdigest(),
    }
    _write_text_atomic(
        manifest_path,
        json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return {
        "dataset": dataset_path,
        "agreed_rejections": rejected_path,
        "manifest": manifest_path,
    }


__all__ = [
    "PromotionValidationError",
    "build_promoted_dataset",
    "write_promoted_dataset",
]
