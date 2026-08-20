from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.national_index import clean_text, extract_signal
from src.goal_search.oss_alias_prior import normalize_alias_text
from src.goal_search.oss_recall_prior import GENERIC_TERMS, recall_terms
from tools.goal_16x_build_oss_recall_index import (
    DEFAULT_DB_DIR,
    DEFAULT_XML_ROOT,
    _choose_province,
    _configure_db_root,
    _infer_region,
    _province_candidates_by_region,
    _quota_ids_for_province,
    _quota_names_by_id,
    _safe_hash,
    _select_xml_files,
    _signal_payload,
    _source_family,
)
from tools.import_xml import convert_xml_to_pairs

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_v2.jsonl"
DEFAULT_MANIFEST = AGENT_STATE / "goal_17x_oss_recall_index_v2_build_manifest.json"
DEFAULT_SUMMARY = AGENT_STATE / "goal_17x_oss_recall_index_v2_build_summary.md"
DEFAULT_INVENTORY = AGENT_STATE / "goal_17x_oss_recall_index_v2_build_inventory.csv"
DEFAULT_CONFLICT_PAIRS = AGENT_STATE / "goal_17x_oss_recall_index_v2_conflict_pairs.csv"
DEFAULT_SIGNATURE_FIELDS = AGENT_STATE / "goal_17x_oss_recall_index_v2_signature_fields.csv"
DEFAULT_SOURCE_QUALITY = AGENT_STATE / "goal_17x_oss_recall_index_v2_source_quality.csv"
DEFAULT_LOCAL_NEIGHBORS = AGENT_STATE / "goal_17x_oss_recall_index_v2_local_neighbors.csv"

V2_VERSION = "17x_v2"
V2_CANDIDATE_SOURCE = "17X_V2_OSS_RECALL_MULTI_FIELD_EVIDENCE"
SPEC_KEYS = ("dn", "cable_section", "cable_cores", "circuits", "concrete_grade", "thickness")
SIGNATURE_KEYS = ("action", "material", "connection", "install_method")
PUMP_EQUIPMENT_TERMS = (
    "\u6c34\u6cf5",
    "\u6f5c\u6c34\u6cf5",
    "\u79bb\u5fc3\u6cf5",
    "\u6d88\u9632\u6cf5",
    "\u55b7\u6dcb\u6cf5",
    "\u7ed9\u6c34\u6cf5",
    "\u6392\u6c61\u6cf5",
    "\u6c61\u6c34\u6cf5",
    "\u6cf5\u7ad9",
    "\u6cf5\u623f",
    "\u52a0\u538b\u6cf5",
    "\u589e\u538b\u6cf5",
    "\u771f\u7a7a\u6cf5",
)
PUMP_PROCESS_BLOCK_TERMS = (
    "\u975e\u6cf5\u9001",
    "\u6cf5\u9001",
    "\u5546\u54c1\u6df7\u51dd\u571f",
    "\u6df7\u51dd\u571f",
    "\u56ed\u8def",
    "\u53f0\u9636",
    "\u57ab\u5c42",
    "\u82b1\u5c97\u5ca9",
    "\u57fa\u5c42",
    "\u8def\u5e8a",
    "\u6784\u4ef6",
    "\u788e\u77f3",
    "\u9762\u5c42",
    "\u673a\u5236\u677f",
)
LOCATION_HINTS = {
    "foundation",
    "ground",
    "wall",
    "roof",
    "floor",
    "beam",
    "column",
    "slab",
    "bottom",
    "bathroom",
    "kitchen",
    "outdoor",
    "indoor",
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_list(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _source_entropy(source_families: list[str]) -> float:
    counts = Counter(item for item in source_families if item)
    total = sum(counts.values())
    if not total:
        return 0.0
    return round(-sum((count / total) * math.log(count / total, 2) for count in counts.values()), 6)


def _dedupe(values: list[str], *, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _specific_terms(*values: object, limit: int = 16) -> list[str]:
    terms: list[str] = []
    for value in values:
        for term in recall_terms(value, limit=32):
            if term in GENERIC_TERMS:
                continue
            terms.append(term)
    return _dedupe(terms, limit=limit)


def _signature_lists(text: str, quota_names: list[str]) -> dict[str, list[str]]:
    signal = extract_signal(text)
    payload = _signal_payload(text)
    specs = [f"{key}:{payload[key]}" for key in SPEC_KEYS if payload.get(key) not in (None, "")]
    location_terms = [
        term
        for term in recall_terms(text, limit=40)
        if term in LOCATION_HINTS or any(hint in term for hint in LOCATION_HINTS)
    ]
    quota_text = " ".join(quota_names)
    return {
        "bill_action_signature": _dedupe([signal.action, payload.get("action", "")], limit=8),
        "bill_material_signature": _dedupe([signal.material, payload.get("material", "")], limit=8),
        "bill_spec_signature": _dedupe(specs, limit=8),
        "bill_location_signature": _dedupe(location_terms, limit=8),
        "quota_signature_terms": _specific_terms(quota_text, limit=16),
    }


def _signature_conflicts(bill_payload: dict[str, Any], quota_payload: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    for key in SIGNATURE_KEYS:
        bill_value = clean_text(bill_payload.get(key))
        quota_value = clean_text(quota_payload.get(key))
        if bill_value and quota_value and bill_value != quota_value:
            conflicts.append(f"{key}:{bill_value}!={quota_value}")
    for key in SPEC_KEYS:
        bill_value = bill_payload.get(key)
        quota_value = quota_payload.get(key)
        if bill_value in (None, "") or quota_value in (None, ""):
            continue
        try:
            if abs(float(bill_value) - float(quota_value)) > 1e-6:
                conflicts.append(f"{key}:{bill_value}!={quota_value}")
        except (TypeError, ValueError):
            if clean_text(bill_value) != clean_text(quota_value):
                conflicts.append(f"{key}:{bill_value}!={quota_value}")
    return conflicts


def _book_from_quota_id(quota_id: str) -> str:
    text = clean_text(quota_id)
    if "-" in text:
        return text.split("-", 1)[0]
    return ""


def _quota_concept_label(family: str, quota_names: list[str], positive_terms: list[str]) -> str:
    primary_name = quota_names[0] if quota_names else ""
    anchors = "_".join(positive_terms[:3])
    base = normalize_alias_text(primary_name)[:32] or anchors or family
    return f"{family}:{base}"


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _passes_semantic_filter(
    *,
    args: argparse.Namespace,
    family: str,
    query_text: str,
    quota_name: str,
    counters: Counter[str],
) -> bool:
    if args.semantic_filter != "v2_1" or family != "pump":
        return True
    text = " ".join(part for part in (query_text, quota_name) if part)
    has_equipment = _has_any(text, PUMP_EQUIPMENT_TERMS)
    has_process_or_civil = _has_any(text, PUMP_PROCESS_BLOCK_TERMS)
    if not has_equipment:
        counters["pump_semantic_filter_no_equipment_term"] += 1
        return False
    if has_process_or_civil:
        counters["pump_semantic_filter_process_or_civil_term"] += 1
        return False
    counters["pump_semantic_filter_kept"] += 1
    return True


def _load_local_quota_rows(provinces: set[str]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for province in sorted(provinces):
        db_path = config.get_quota_db_path(province)
        rows: dict[str, dict[str, Any]] = {}
        if not db_path.exists():
            result[province] = rows
            continue
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            has_quotas = conn.execute(
                "select 1 from sqlite_master where type='table' and name='quotas'"
            ).fetchone()
            if not has_quotas:
                result[province] = rows
                continue
            cols = {row[1] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            select_cols = ["quota_id", "name"]
            for optional in ("book", "chapter", "search_text", "unit"):
                if optional in cols:
                    select_cols.append(optional)
            for row in conn.execute(f"select {','.join(select_cols)} from quotas"):
                quota_id = clean_text(row["quota_id"])
                name = clean_text(row["name"])
                if not quota_id or not name:
                    continue
                text = " ".join(clean_text(row[col]) for col in select_cols if col not in {"quota_id"} and col in row.keys())
                signal = extract_signal(text)
                rows[quota_id] = {
                    "quota_id": quota_id,
                    "name": name,
                    "book": clean_text(row["book"]) if "book" in row.keys() else _book_from_quota_id(quota_id),
                    "chapter": clean_text(row["chapter"]) if "chapter" in row.keys() else "",
                    "family": clean_text(signal.family),
                    "terms": set(recall_terms(text, limit=32)),
                    "signal": _signal_payload(text),
                }
        finally:
            conn.close()
        result[province] = rows
    return result


def _local_neighbors(row: dict[str, Any], local_rows: dict[str, dict[str, Any]], max_neighbors: int) -> dict[str, Any]:
    quota_id = clean_text(row["quota_id"])
    target = local_rows.get(quota_id, {})
    target_terms = set(target.get("terms") or row.get("quota_terms") or [])
    target_book = clean_text(target.get("book")) or _book_from_quota_id(quota_id)
    target_family = clean_text(target.get("family")) or clean_text(row.get("query_family"))
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for other_id, other in local_rows.items():
        if other_id == quota_id:
            continue
        other_family = clean_text(other.get("family"))
        other_book = clean_text(other.get("book")) or _book_from_quota_id(other_id)
        if target_family and other_family and other_family != target_family:
            continue
        if target_book and other_book and other_book != target_book:
            continue
        overlap = len(target_terms & set(other.get("terms") or []))
        if overlap <= 0:
            continue
        candidates.append((overlap, other_id, other))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    kept = candidates[:max_neighbors]
    neighbor_ids = [item[1] for item in kept]
    neighbor_terms: set[str] = set()
    for _, _, other in kept:
        neighbor_terms.update(other.get("terms") or [])
    contrast = sorted((target_terms ^ neighbor_terms) - set(GENERIC_TERMS))[:16]
    return {
        "local_neighbor_ids": neighbor_ids,
        "local_neighbor_concept_gap": len(contrast),
        "same_book_neighbor_rank": 1 if neighbor_ids else 0,
        "local_title_contrast_terms": contrast,
    }


def _add_v2_fields(
    rows: list[dict[str, Any]],
    *,
    max_neighbors: int,
    evidence_vector_version: str,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    local_by_province = _load_local_quota_rows({clean_text(row.get("province")) for row in rows})
    grouped_for_conflicts: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_for_conflicts[(row["province"], row["query_family"], _book_from_quota_id(row["quota_id"]))].append(row)

    conflict_pair_rows: list[dict[str, Any]] = []
    signature_rows: list[dict[str, Any]] = []
    source_quality_rows: list[dict[str, Any]] = []
    local_neighbor_rows: list[dict[str, Any]] = []

    for key_rows in grouped_for_conflicts.values():
        for row in key_rows:
            quota_terms = set(row.get("quota_terms") or [])
            conflicts: list[str] = []
            negative_terms: list[str] = []
            for other in key_rows:
                if other is row:
                    continue
                overlap = len(quota_terms & set(other.get("quota_terms") or []))
                if overlap <= 0:
                    continue
                conflicts.append(other["quota_id"])
                negative_terms.extend(sorted((set(other.get("quota_terms") or []) - quota_terms) - set(GENERIC_TERMS))[:6])
            row["conflict_pair_ids"] = _dedupe(conflicts, limit=12)
            row["negative_anchor_terms"] = _dedupe(negative_terms, limit=16)

    for row in rows:
        quota_names = [clean_text(name) for name in row.get("quota_names", []) if clean_text(name)]
        bill_text = " ".join(row.get("bill_name_keys") or [row.get("bill_name_key", "")])
        signature = _signature_lists(bill_text, quota_names)
        quota_payload = _signal_payload(" ".join(quota_names))
        bill_payload = row.get("signal") if isinstance(row.get("signal"), dict) else {}
        positive_terms = _specific_terms(bill_text, " ".join(quota_names), limit=16)
        source_families = [clean_text(item) for item in row.get("source_families", []) if clean_text(item)]
        source_files = [clean_text(item) for item in row.get("source_files", []) if clean_text(item)]
        local = _local_neighbors(row, local_by_province.get(row["province"], {}), max_neighbors)
        duplicate_cluster_id = _safe_hash("|".join(sorted(row.get("source_file_hashes", []))) or row["quota_id"])
        row.update(
            {
                "evidence_vector_version": evidence_vector_version,
                "quota_concept_label": _quota_concept_label(row["query_family"], quota_names, positive_terms),
                "positive_anchor_terms": positive_terms,
                "bill_action_signature": signature["bill_action_signature"],
                "bill_material_signature": signature["bill_material_signature"],
                "bill_spec_signature": signature["bill_spec_signature"],
                "bill_location_signature": signature["bill_location_signature"],
                "signature_conflict_flags": _signature_conflicts(bill_payload, quota_payload),
                "independent_source_family_count": len(set(source_families)),
                "source_entropy": _source_entropy(source_families),
                "duplicate_cluster_id": duplicate_cluster_id,
                "accepted_oss_support_count": int(row.get("support_count") or 0),
                "generated_or_trace_support_count": sum(1 for item in source_files if "generated" in item.lower() or "trace" in item.lower()),
                **local,
                "candidate_source": V2_CANDIDATE_SOURCE,
            }
        )
        conflict_pair_rows.append(
            {
                "province": row["province"],
                "query_family": row["query_family"],
                "quota_id": row["quota_id"],
                "quota_concept_label": row["quota_concept_label"],
                "conflict_pair_ids": _json_list(row["conflict_pair_ids"]),
                "positive_anchor_terms": _json_list(row["positive_anchor_terms"]),
                "negative_anchor_terms": _json_list(row["negative_anchor_terms"]),
            }
        )
        signature_rows.append(
            {
                "province": row["province"],
                "query_family": row["query_family"],
                "quota_id": row["quota_id"],
                "bill_action_signature": _json_list(row["bill_action_signature"]),
                "bill_material_signature": _json_list(row["bill_material_signature"]),
                "bill_spec_signature": _json_list(row["bill_spec_signature"]),
                "bill_location_signature": _json_list(row["bill_location_signature"]),
                "signature_conflict_flags": _json_list(row["signature_conflict_flags"]),
            }
        )
        source_quality_rows.append(
            {
                "province": row["province"],
                "query_family": row["query_family"],
                "quota_id": row["quota_id"],
                "support_count": row["support_count"],
                "independent_source_family_count": row["independent_source_family_count"],
                "source_entropy": row["source_entropy"],
                "duplicate_cluster_id": row["duplicate_cluster_id"],
                "accepted_oss_support_count": row["accepted_oss_support_count"],
                "generated_or_trace_support_count": row["generated_or_trace_support_count"],
            }
        )
        local_neighbor_rows.append(
            {
                "province": row["province"],
                "query_family": row["query_family"],
                "quota_id": row["quota_id"],
                "local_neighbor_ids": _json_list(row["local_neighbor_ids"]),
                "local_neighbor_concept_gap": row["local_neighbor_concept_gap"],
                "same_book_neighbor_rank": row["same_book_neighbor_rank"],
                "local_title_contrast_terms": _json_list(row["local_title_contrast_terms"]),
            }
        )
    artifacts = {
        "conflict_pairs": conflict_pair_rows,
        "signature_fields": signature_rows,
        "source_quality": source_quality_rows,
        "local_neighbors": local_neighbor_rows,
    }
    return rows, artifacts


def _build_rows(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _configure_db_root(args.db_dir)
    province_candidates = _province_candidates_by_region()
    quota_cache: dict[str, set[str]] = {}
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    inventory: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    allowed_regions = {part.strip().upper() for part in args.regions.split(",") if part.strip()}
    selected_files = _select_xml_files(args, allowed_regions)
    selected_source_families: Counter[str] = Counter()
    for xml_path in selected_files:
        region = _infer_region(xml_path, args.xml_root)
        if not region or (allowed_regions and region not in allowed_regions):
            counters["skipped_region"] += 1
            continue
        source_family = _source_family(xml_path, args.xml_root, region)
        selected_source_families[source_family] += 1
        rel = str(xml_path.relative_to(args.xml_root))
        file_hash = _safe_hash(rel)
        pairs = convert_xml_to_pairs(str(xml_path))
        counters["files_parsed"] += 1
        counters["pairs_seen"] += len(pairs)
        used_pairs = 0
        accepted_quota_links = 0
        if args.max_pairs_per_file > 0:
            pairs = pairs[: args.max_pairs_per_file]
        for pair_index, pair in enumerate(pairs, start=1):
            expected = {clean_text(quota.get("code")) for quota in pair.get("quotas") or [] if clean_text(quota.get("code"))}
            quota_names = _quota_names_by_id(pair)
            if not expected:
                counters["pairs_without_expected_quota"] += 1
                continue
            province, local_ids = _choose_province(expected, region, province_candidates, quota_cache)
            if not province or not local_ids:
                counters["pairs_without_local_quota_id"] += 1
                continue
            bill_name = clean_text(pair.get("bill_name"))
            bill_desc = clean_text(pair.get("bill_desc"))
            bill_pattern = clean_text(pair.get("bill_pattern"))
            query_text = " ".join(part for part in (bill_name, bill_desc, bill_pattern) if part)
            signal = extract_signal(query_text)
            family = clean_text(signal.family)
            if family not in args.core_families:
                counters["pairs_without_requested_family"] += 1
                continue
            terms = set(recall_terms(" ".join([bill_name, bill_desc, bill_pattern, " ".join(clean_text(q.get("name")) for q in pair.get("quotas") or [])])))
            bill_terms = set(recall_terms(" ".join([bill_name, bill_desc, bill_pattern])))
            if len(terms) < args.min_terms:
                counters["pairs_with_too_few_terms"] += 1
                continue
            bill_name_key = normalize_alias_text(bill_name)
            signal_payload = _signal_payload(query_text)
            used_pairs += 1
            for quota_id in sorted(local_ids):
                quota_name = clean_text(quota_names.get(quota_id))
                quota_family = clean_text(extract_signal(quota_name).family) if quota_name else ""
                if quota_family and quota_family != family:
                    counters["quota_family_conflict"] += 1
                    continue
                if not _passes_semantic_filter(
                    args=args,
                    family=family,
                    query_text=query_text,
                    quota_name=quota_name,
                    counters=counters,
                ):
                    continue
                key = (province, family, quota_id, "|".join(f"{k}={signal_payload[k]}" for k in sorted(signal_payload)))
                item = grouped.setdefault(
                    key,
                    {
                        "province": province,
                        "query_family": family,
                        "quota_id": quota_id,
                        "bill_name_key": bill_name_key,
                        "terms": set(),
                        "bill_terms": set(),
                        "quota_terms": set(),
                        "quota_names": set(),
                        "bill_name_keys": set(),
                        "support_count": 0,
                        "source_families": [],
                        "source_files": set(),
                        "source_file_hashes": set(),
                        "signal": signal_payload,
                        "evidence": [],
                    },
                )
                item["terms"].update(terms)
                item["bill_terms"].update(bill_terms)
                if bill_name_key:
                    item["bill_name_keys"].add(bill_name_key)
                if quota_name:
                    item["quota_terms"].update(recall_terms(quota_name))
                    item["quota_names"].add(quota_name)
                item["support_count"] += 1
                item["source_families"].append(source_family)
                item["source_files"].add(rel)
                item["source_file_hashes"].add(file_hash)
                if len(item["evidence"]) < args.max_evidence:
                    item["evidence"].append(
                        {
                            "source_family": source_family,
                            "source_file": rel,
                            "source_file_hash": file_hash,
                            "pair_index": pair_index,
                            "bill_name": bill_name,
                        }
                    )
                accepted_quota_links += 1
        inventory.append(
            {
                "relative_path": rel,
                "region": region,
                "source_family": source_family,
                "pairs_parsed": len(pairs),
                "pairs_used": used_pairs,
                "accepted_quota_links": accepted_quota_links,
            }
        )
    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        if int(item["support_count"]) < args.min_support:
            continue
        rows.append(
            {
                "province": item["province"],
                "query_family": item["query_family"],
                "quota_id": item["quota_id"],
                "bill_name_key": item["bill_name_key"],
                "bill_name_keys": sorted(item["bill_name_keys"])[:20],
                "terms": sorted(item["terms"])[: args.max_terms],
                "bill_terms": sorted(item["bill_terms"])[: args.max_terms],
                "quota_terms": sorted(item["quota_terms"])[: args.max_terms],
                "quota_names": sorted(item["quota_names"])[:10],
                "support_count": item["support_count"],
                "source_family_count": len(set(item["source_families"])),
                "source_families": sorted(set(item["source_families"])),
                "source_files": sorted(item["source_files"])[:20],
                "source_file_hashes": sorted(item["source_file_hashes"]),
                "signal": item["signal"],
                "evidence": item["evidence"],
                "candidate_source": V2_CANDIDATE_SOURCE,
            }
        )
    rows.sort(key=lambda row: (row["province"], row["query_family"], row["bill_name_key"], -row["support_count"], row["quota_id"]))
    rows, artifacts = _add_v2_fields(
        rows,
        max_neighbors=args.max_local_neighbors,
        evidence_vector_version=args.evidence_vector_version,
    )
    summary = {
        "selection_mode": args.selection_mode,
        "aggregate_scope": "quota_signature",
        "files_considered": len(selected_files),
        "selected_source_family_count": len(selected_source_families),
        "selected_source_families": dict(sorted(selected_source_families.items())),
        "counters": dict(counters),
        "output_rows_before_min_support": len(grouped),
        "output_rows": len(rows),
        "family_distribution": dict(sorted(Counter(row["query_family"] for row in rows).items())),
        "source_quality": {
            "rows_with_independent_source_family_ge_2": sum(1 for row in rows if row["independent_source_family_count"] >= 2),
            "max_support_count": max((int(row["support_count"]) for row in rows), default=0),
            "max_source_entropy": max((float(row["source_entropy"]) for row in rows), default=0.0),
        },
        "artifacts": artifacts,
    }
    return rows, inventory, summary


def _manifest_hash(rows: list[dict[str, Any]], args: argparse.Namespace) -> str:
    digest = hashlib.sha1()
    digest.update(str(args.output).encode("utf-8"))
    for row in rows:
        digest.update(clean_text(row.get("province")).encode("utf-8"))
        digest.update(clean_text(row.get("query_family")).encode("utf-8"))
        digest.update(clean_text(row.get("quota_id")).encode("utf-8"))
        digest.update(str(row.get("support_count", "")).encode("utf-8"))
    return digest.hexdigest()[:16]


def _write_summary_markdown(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        f"# {manifest['stage']}",
        "",
        f"Updated: {manifest['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{manifest['decision']}**",
        "",
        "## Build Result",
        "",
        f"- Output: `{manifest['output']}`",
        f"- Rows: {manifest['rows']}",
        f"- Families: `{manifest['core_families']}`",
        f"- Manifest hash: `{manifest['manifest_hash']}`",
        "",
        "## Anti-Drift",
        "",
        "The build created a separate default-off v2 artifact only. It did not run dev/OOF shadow, did not run heldout/hard, did not train, did not change runtime readers, did not default-enable OSS recall, and did not change GoalSearcher defaults.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 17.28 default-off v2 OSS recall index artifact")
    parser.add_argument("--xml-root", type=Path, default=DEFAULT_XML_ROOT)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--family", "--core-families", dest="family", default="pump,rebar")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--conflict-pairs", type=Path, default=DEFAULT_CONFLICT_PAIRS)
    parser.add_argument("--signature-fields", type=Path, default=DEFAULT_SIGNATURE_FIELDS)
    parser.add_argument("--source-quality", type=Path, default=DEFAULT_SOURCE_QUALITY)
    parser.add_argument("--local-neighbors", type=Path, default=DEFAULT_LOCAL_NEIGHBORS)
    parser.add_argument("--regions", default="FJ,ZJ,BJ,GD,HN")
    parser.add_argument("--max-files", type=int, default=430)
    parser.add_argument("--max-files-per-source-family", type=int, default=80)
    parser.add_argument("--max-pairs-per-file", type=int, default=300)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--min-terms", type=int, default=2)
    parser.add_argument("--max-terms", type=int, default=28)
    parser.add_argument("--max-evidence", type=int, default=8)
    parser.add_argument("--max-local-neighbors", type=int, default=8)
    parser.add_argument("--selection-mode", choices=("sorted", "balanced-source-family"), default="balanced-source-family")
    parser.add_argument("--semantic-filter", choices=("none", "v2_1"), default="none")
    parser.add_argument("--evidence-vector-version", default=V2_VERSION)
    parser.add_argument("--stage-label", default="17.28 default-off v2 OSS recall index build")
    parser.add_argument("--decision", default="v2_default_off_artifact_built_no_runtime_integration")
    args = parser.parse_args()
    args.core_families = {part.strip() for part in args.family.split(",") if part.strip()}
    forbidden_v1 = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"
    if args.output.resolve() == forbidden_v1.resolve():
        raise ValueError("17.28 v2 build must not overwrite the locked 17x multifield artifact")

    rows, inventory, summary = _build_rows(args)
    manifest_hash = _manifest_hash(rows, args)
    for row in rows:
        row["build_manifest_hash"] = manifest_hash

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    artifacts = summary.pop("artifacts")
    _write_csv(args.inventory, inventory, ["relative_path", "region", "source_family", "pairs_parsed", "pairs_used", "accepted_quota_links"])
    _write_csv(
        args.conflict_pairs,
        artifacts["conflict_pairs"],
        ["province", "query_family", "quota_id", "quota_concept_label", "conflict_pair_ids", "positive_anchor_terms", "negative_anchor_terms"],
    )
    _write_csv(
        args.signature_fields,
        artifacts["signature_fields"],
        ["province", "query_family", "quota_id", "bill_action_signature", "bill_material_signature", "bill_spec_signature", "bill_location_signature", "signature_conflict_flags"],
    )
    _write_csv(
        args.source_quality,
        artifacts["source_quality"],
        ["province", "query_family", "quota_id", "support_count", "independent_source_family_count", "source_entropy", "duplicate_cluster_id", "accepted_oss_support_count", "generated_or_trace_support_count"],
    )
    _write_csv(
        args.local_neighbors,
        artifacts["local_neighbors"],
        ["province", "query_family", "quota_id", "local_neighbor_ids", "local_neighbor_concept_gap", "same_book_neighbor_rank", "local_title_contrast_terms"],
    )

    manifest = {
        "stage": args.stage_label,
        "decision": args.decision,
        "xml_root": str(args.xml_root),
        "db_dir": str(args.db_dir),
        "output": str(args.output),
        "rows": len(rows),
        "core_families": sorted(args.core_families),
        "semantic_filter": args.semantic_filter,
        "evidence_vector_version": args.evidence_vector_version,
        "manifest_hash": manifest_hash,
        "summary": summary,
        "artifact_paths": {
            "manifest": str(args.manifest),
            "summary": str(args.summary),
            "inventory": str(args.inventory),
            "conflict_pairs": str(args.conflict_pairs),
            "signature_fields": str(args.signature_fields),
            "source_quality": str(args.source_quality),
            "local_neighbors": str(args.local_neighbors),
        },
        "trained": False,
        "dev_oof_shadow_run": False,
        "heldout_hard_used": False,
        "online_default_changed": False,
        "goal_searcher_changed": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(args.manifest, manifest)
    _write_summary_markdown(args.summary, manifest)
    print(json.dumps({"output": str(args.output), "rows": len(rows), "manifest": str(args.manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
