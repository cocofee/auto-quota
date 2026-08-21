from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .datasets import normalize_quota_id


@dataclass(frozen=True, slots=True)
class DatasetExportReport:
    output_path: Path
    source_rows: int
    accepted_rows: int
    rejection_counts: dict[str, int]
    province_counts: dict[str, int]
    project_counts: dict[str, int]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class OssProjectProvenance:
    project_id: str
    original_file_name: str
    source_path: Path
    province_code: str
    xml_format: str
    source_family: str


@dataclass(frozen=True, slots=True)
class OssDatasetExportReport:
    output_paths: Mapping[str, Path]
    source_rows: int
    accepted_rows: int
    rejection_counts: dict[str, int]
    split_counts: dict[str, int]
    project_counts: dict[str, int]
    province_counts: dict[str, int]
    source_family_counts: dict[str, int]
    project_overlap_count: int
    content_sha256: dict[str, str]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = value.split("|")
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        text = _clean(item)
        if text and text not in result:
            result.append(text)
    return result


def _quota_id_list(value: Any) -> list[str]:
    result: list[str] = []
    for item in _string_list(value):
        quota_id = normalize_quota_id(item)
        if quota_id and quota_id not in result:
            result.append(quota_id)
    return result


def _sample_id(row: dict[str, Any], quota_ids: list[str]) -> str:
    identity = {
        "bill_code": _clean(row.get("bill_code")),
        "bill_name": _clean(row.get("bill_name")),
        "bill_text": _clean(row.get("bill_text")),
        "oracle_quota_ids": quota_ids,
        "project_name": _clean(row.get("project_name")),
        "province": _clean(row.get("province")),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"human-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _province_code(province: str) -> str:
    for marker, code in (
        ("福建", "FJ"),
        ("浙江", "ZJ"),
        ("江苏", "JS"),
        ("北京", "BJ"),
    ):
        if marker in province:
            return code
    return ""


def _detect_xml_format(path: Path) -> str:
    tags: list[str] = []
    for _, element in ET.iterparse(path, events=("start",)):
        tags.append(str(element.tag))
        if len(tags) >= 2:
            break
    root_tag = tags[0] if tags else ""
    if root_tag == "JingJiBiao":
        return "13jk"
    if "浙江" in root_tag or "计价成果" in root_tag:
        return "zhejiang"
    if root_tag == "GCZJWJ":
        return "gczjwj"
    if root_tag == "root" and len(tags) > 1 and tags[1] == "mergedRoot":
        return "zaojia_home"
    return "unknown"


def _xml_name_index(root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.casefold() == ".xml":
            index.setdefault(path.name.casefold(), []).append(path.resolve())
    return index


def _resolve_oss_project(
    project_name: str,
    province: str,
    root: Path,
    name_index: Mapping[str, list[Path]],
) -> OssProjectProvenance:
    original_file_name = re.sub(
        r"^oss_\d{8}_\d{4}_",
        "",
        _clean(project_name),
        flags=re.IGNORECASE,
    )
    province_code = _province_code(_clean(province))
    if not original_file_name or not province_code:
        raise ValueError("missing OSS project or supported province")
    matching = name_index.get(original_file_name.casefold(), [])
    canonical = [
        path
        for path in matching
        if len(path.relative_to(root).parts) >= 3
        and path.relative_to(root).parts[:2] == ("by_province", province_code)
    ]
    if len(canonical) != 1:
        raise ValueError(
            f"expected one canonical OSS XML for {original_file_name} in {province_code}, "
            f"found {len(canonical)}"
        )
    source_path = canonical[0]
    xml_format = _detect_xml_format(source_path)
    if xml_format == "unknown":
        raise ValueError(f"unknown XML format: {source_path}")
    return OssProjectProvenance(
        project_id=source_path.stem.casefold(),
        original_file_name=source_path.name,
        source_path=source_path,
        province_code=province_code,
        xml_format=xml_format,
        source_family=f"oss_xml/{province_code}/{xml_format}",
    )


def resolve_oss_project(
    project_name: str,
    province: str,
    xml_root: str | Path,
) -> OssProjectProvenance:
    root = Path(xml_root).resolve()
    return _resolve_oss_project(
        project_name,
        province,
        root,
        _xml_name_index(root),
    )


def _oss_split(project_id: str, split_seed: str) -> str:
    digest = hashlib.sha256(f"{split_seed}:{project_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "dev"
    return "eval"


def _oss_sample_id(
    row: dict[str, Any],
    project_id: str,
    quota_ids: list[str],
) -> str:
    identity = {
        "bill_code": _clean(row.get("bill_code")),
        "bill_name": _clean(row.get("bill_name")),
        "bill_text": _clean(row.get("bill_text")),
        "oracle_quota_ids": quota_ids,
        "project_id": project_id,
        "province": _clean(row.get("province")),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"oss-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def export_oss_diagnostic_cases(
    experience_db: str | Path,
    xml_root: str | Path,
    output_dir: str | Path,
    split_seed: str,
) -> OssDatasetExportReport:
    db_path = Path(experience_db).resolve()
    root = Path(xml_root).resolve()
    destination = Path(output_dir)
    name_index = _xml_name_index(root)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    id, bill_text, bill_name, bill_code, bill_unit, quota_ids,
                    quota_names, source, confidence, province, project_name,
                    layer, specialty, disputed
                FROM experiences
                WHERE source = 'oss_import'
                ORDER BY id
                """
            )
        ]
    finally:
        conn.close()

    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "dev": [], "eval": []}
    rejections: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    source_family_counts: Counter[str] = Counter()
    provenance_cache: dict[tuple[str, str], OssProjectProvenance | None] = {}
    seen_ids: set[str] = set()
    for row in rows:
        if bool(row.get("disputed")):
            rejections["disputed"] += 1
            continue
        province = _clean(row.get("province"))
        if not province:
            rejections["missing_province"] += 1
            continue
        quota_ids = _quota_id_list(row.get("quota_ids"))
        if not quota_ids:
            rejections["missing_oracle"] += 1
            continue
        cache_key = (_clean(row.get("project_name")), province)
        if cache_key not in provenance_cache:
            try:
                provenance_cache[cache_key] = _resolve_oss_project(
                    cache_key[0],
                    province,
                    root,
                    name_index,
                )
            except (OSError, ET.ParseError, ValueError):
                provenance_cache[cache_key] = None
        provenance = provenance_cache[cache_key]
        if provenance is None:
            rejections["missing_provenance"] += 1
            continue
        sample_id = _oss_sample_id(row, provenance.project_id, quota_ids)
        if sample_id in seen_ids:
            rejections["duplicate_case"] += 1
            continue
        seen_ids.add(sample_id)
        split = _oss_split(provenance.project_id, split_seed)
        record = {
            "bill_code": _clean(row.get("bill_code")),
            "bill_name": _clean(row.get("bill_name")),
            "bill_text": _clean(row.get("bill_text")),
            "confidence": int(row.get("confidence") or 0),
            "oracle_quota_ids": quota_ids,
            "oracle_semantics": "all" if len(quota_ids) > 1 else "any",
            "oracle_quota_names": _string_list(row.get("quota_names")),
            "project_id": provenance.project_id,
            "province": province,
            "sample_id": sample_id,
            "source": str(provenance.source_path),
            "source_family": provenance.source_family,
            "specialty": _clean(row.get("specialty")),
            "split": split,
            "unit": _clean(row.get("bill_unit")),
        }
        by_split[split].append(record)
        project_counts[provenance.project_id] += 1
        province_counts[province] += 1
        source_family_counts[provenance.source_family] += 1

    destination.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, Path] = {}
    content_sha256: dict[str, str] = {}
    project_sets: dict[str, set[str]] = {}
    for split, records in by_split.items():
        records.sort(key=lambda record: record["sample_id"])
        raw = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ).encode("utf-8")
        path = destination / f"{split}.jsonl"
        path.write_bytes(raw)
        output_paths[split] = path
        content_sha256[split] = hashlib.sha256(raw).hexdigest()
        project_sets[split] = {record["project_id"] for record in records}
    overlap = (
        (project_sets["train"] & project_sets["dev"])
        | (project_sets["train"] & project_sets["eval"])
        | (project_sets["dev"] & project_sets["eval"])
    )
    return OssDatasetExportReport(
        output_paths=output_paths,
        source_rows=len(rows),
        accepted_rows=sum(len(records) for records in by_split.values()),
        rejection_counts=dict(sorted(rejections.items())),
        split_counts={split: len(records) for split, records in by_split.items()},
        project_counts=dict(sorted(project_counts.items())),
        province_counts=dict(sorted(province_counts.items())),
        source_family_counts=dict(sorted(source_family_counts.items())),
        project_overlap_count=len(overlap),
        content_sha256=content_sha256,
    )


def export_primary_cases(
    experience_db: str | Path,
    output_path: str | Path,
) -> DatasetExportReport:
    db_path = Path(experience_db).resolve()
    destination = Path(output_path)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    id, bill_text, bill_name, bill_code, bill_unit, quota_ids,
                    quota_names, source, confidence, province, project_name,
                    layer, specialty, disputed
                FROM experiences
                WHERE source = 'user_correction'
                ORDER BY id
                """
            )
        ]
    finally:
        conn.close()

    accepted: list[dict[str, Any]] = []
    rejections: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for row in rows:
        if _clean(row.get("layer")) != "authority":
            rejections["not_authority"] += 1
            continue
        if bool(row.get("disputed")):
            rejections["disputed"] += 1
            continue
        province = _clean(row.get("province"))
        if not province:
            rejections["missing_province"] += 1
            continue
        quota_ids = _quota_id_list(row.get("quota_ids"))
        if not quota_ids:
            rejections["missing_oracle"] += 1
            continue
        sample_id = _sample_id(row, quota_ids)
        if sample_id in seen_ids:
            rejections["duplicate_case"] += 1
            continue
        seen_ids.add(sample_id)
        project_name = _clean(row.get("project_name"))
        record = {
            "bill_code": _clean(row.get("bill_code")),
            "bill_name": _clean(row.get("bill_name")),
            "bill_text": _clean(row.get("bill_text")),
            "confidence": int(row.get("confidence") or 0),
            "oracle_quota_ids": quota_ids,
            "oracle_semantics": "all" if len(quota_ids) > 1 else "any",
            "oracle_quota_names": _string_list(row.get("quota_names")),
            "project_name": project_name,
            "province": province,
            "sample_id": sample_id,
            "source": "user_correction",
            "source_family": "human_user_correction",
            "specialty": _clean(row.get("specialty")),
            "unit": _clean(row.get("bill_unit")),
        }
        accepted.append(record)
        province_counts[province] += 1
        project_counts[project_name or "<empty>"] += 1

    raw = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in accepted
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return DatasetExportReport(
        output_path=destination,
        source_rows=len(rows),
        accepted_rows=len(accepted),
        rejection_counts=dict(sorted(rejections.items())),
        province_counts=dict(sorted(province_counts.items())),
        project_counts=dict(sorted(project_counts.items())),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
