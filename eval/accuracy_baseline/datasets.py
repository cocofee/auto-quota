from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .contracts import DatasetKind, EvalCase


@dataclass(frozen=True, slots=True)
class DatasetLoadResult:
    path: Path
    dataset_kind: DatasetKind
    cases: tuple[EvalCase, ...]
    total_rows: int
    rejection_counts: dict[str, int]
    content_sha256: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_quota_id(value: Any) -> str:
    text = "".join(_clean(value).split())
    text = re.sub(r"换$", "", text)
    if text.startswith("借"):
        text = text[1:]
    return re.sub(r"\*[\d.]+$", "", text).strip()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = value.split("|")
    values: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        text = normalize_quota_id(item)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _case_from_row(
    row: dict[str, Any],
    kind: DatasetKind,
    index: int,
) -> tuple[EvalCase | None, str]:
    province = _clean(row.get("province") or row.get("quota_province"))
    if not province:
        return None, "missing_province"
    oracles = _string_tuple(
        row.get("oracle_quota_ids")
        or row.get("expected_quota_ids")
        or row.get("expected_ids")
        or row.get("quota_ids")
    )
    if not oracles:
        return None, "missing_oracle"
    source = _clean(row.get("source") or row.get("source_file"))
    source_family = _clean(row.get("source_family") or source)
    project_id = _clean(
        row.get("project_id") or row.get("project_name") or row.get("source_file")
    )
    if kind == DatasetKind.OSS_DIAGNOSTIC and (not source_family or not project_id):
        return None, "missing_provenance"
    case_id = _clean(
        row.get("case_id") or row.get("sample_id") or row.get("bill_id") or index
    )
    known = {
        "case_id",
        "sample_id",
        "bill_id",
        "province",
        "quota_province",
        "bill_name",
        "name",
        "bill_text",
        "description",
        "unit",
        "specialty",
        "oracle_quota_ids",
        "expected_quota_ids",
        "expected_ids",
        "quota_ids",
        "source",
        "source_file",
        "source_family",
        "project_id",
        "project_name",
        "split",
    }
    return EvalCase(
        case_id=case_id,
        dataset_kind=kind,
        province=province,
        bill_name=_clean(row.get("bill_name") or row.get("name")),
        bill_text=_clean(row.get("bill_text") or row.get("description")),
        unit=_clean(row.get("unit")),
        specialty=_clean(row.get("specialty")),
        oracle_quota_ids=oracles,
        source_family=source_family,
        project_id=project_id,
        source=source,
        split=_clean(row.get("split")),
        metadata={key: value for key, value in row.items() if key not in known},
    ), ""


def load_dataset(path: str | Path, dataset_kind: DatasetKind) -> DatasetLoadResult:
    resolved = Path(path)
    raw = resolved.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw.decode("utf-8-sig").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number} is not a JSON object: {resolved}")
        rows.append(payload)

    cases: list[EvalCase] = []
    rejections: Counter[str] = Counter()
    for index, row in enumerate(rows, start=1):
        case, reason = _case_from_row(row, dataset_kind, index)
        if case is None:
            rejections[reason] += 1
        else:
            cases.append(case)
    return DatasetLoadResult(
        path=resolved,
        dataset_kind=dataset_kind,
        cases=tuple(cases),
        total_rows=len(rows),
        rejection_counts=dict(sorted(rejections.items())),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
