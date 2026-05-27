from __future__ import annotations

import argparse
import csv
import hashlib
import json
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
from src.goal_search.oss_recall_prior import recall_terms
from tools.import_xml import convert_xml_to_pairs


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_XML_ROOT = Path("D:/\u5e7f\u8054\u8fbe\u4e34\u65f6\u6587\u4ef6/oss_samples")
DEFAULT_DB_DIR = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522" / "db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index.jsonl"
DEFAULT_SUMMARY = AGENT_STATE / "goal_16x_oss_recall_index_build_summary.json"
DEFAULT_INVENTORY = AGENT_STATE / "goal_16x_oss_recall_index_build_inventory.csv"

REGION_HINTS = {
    "FJ": ("福建", "绂忓缓"),
    "ZJ": ("浙江", "娴欐睙"),
    "JS": ("江苏", "姹熻嫃"),
    "BJ": ("北京", "鍖椾含"),
    "GD": ("广东", "骞夸笢"),
    "HN": ("湖南", "婀栧崡"),
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


def _configure_db_root(db_dir: Path) -> None:
    if not (db_dir / "provinces").exists():
        raise FileNotFoundError(f"db root has no provinces directory: {db_dir}")
    config.DB_DIR = db_dir
    config.COMMON_DB_DIR = db_dir / "common"
    config.PROVINCES_DB_DIR = db_dir / "provinces"


def _safe_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _infer_region(path: Path, root: Path) -> str:
    rel = str(path.relative_to(root)).lower()
    parts = path.relative_to(root).parts
    for part in parts:
        upper = part.upper()
        if upper in REGION_HINTS:
            return upper
    if "fj" in rel:
        return "FJ"
    if "zj" in rel:
        return "ZJ"
    if "js" in rel:
        return "JS"
    if "bj" in rel:
        return "BJ"
    if "gd" in rel:
        return "GD"
    for region, hints in REGION_HINTS.items():
        if any(hint.lower() in rel for hint in hints):
            return region
    return ""


def _source_family(path: Path, root: Path, region: str) -> str:
    rel = path.relative_to(root)
    parts = rel.parts
    top_dir = parts[0] if len(parts) > 1 else "<root>"
    province_dir = parts[1] if len(parts) >= 2 and top_dir == "by_province" else "-"
    return f"{region}:{top_dir}:{province_dir}"


def _province_candidates_by_region() -> dict[str, list[str]]:
    provinces = config.list_db_provinces()
    result: dict[str, list[str]] = {}
    for region, hints in REGION_HINTS.items():
        result[region] = sorted(province for province in provinces if any(hint in province for hint in hints))
    return result


def _quota_ids_for_province(province: str, cache: dict[str, set[str]]) -> set[str]:
    if province in cache:
        return cache[province]
    db_path = config.get_quota_db_path(province)
    ids: set[str] = set()
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            ids = {clean_text(row[0]) for row in conn.execute("select quota_id from quotas") if clean_text(row[0])}
        finally:
            conn.close()
    cache[province] = ids
    return ids


def _choose_province(expected: set[str], region: str, candidates: dict[str, list[str]], cache: dict[str, set[str]]) -> tuple[str, set[str]]:
    best_province = ""
    best_ids: set[str] = set()
    for province in candidates.get(region, []):
        ids = _quota_ids_for_province(province, cache)
        matched = expected & ids
        if len(matched) > len(best_ids):
            best_province = province
            best_ids = matched
    return best_province, best_ids


def _iter_xml_files(root: Path, max_files: int) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".xml" and path.stat().st_size > 0]
    files.sort(key=lambda path: (str(path.parent).lower(), path.stat().st_size, path.name.lower()))
    if max_files > 0:
        return files[:max_files]
    return files


def _eligible_xml_files(root: Path, allowed_regions: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".xml" or path.stat().st_size <= 0:
            continue
        region = _infer_region(path, root)
        if not region or (allowed_regions and region not in allowed_regions):
            continue
        files.append(path)
    files.sort(key=lambda path: (_source_family(path, root, _infer_region(path, root)), path.stat().st_size, path.name.lower()))
    return files


def _select_xml_files(args: argparse.Namespace, allowed_regions: set[str]) -> list[Path]:
    if args.selection_mode == "sorted":
        return _iter_xml_files(args.xml_root, args.max_files)
    grouped: dict[str, list[Path]] = defaultdict(list)
    for path in _eligible_xml_files(args.xml_root, allowed_regions):
        region = _infer_region(path, args.xml_root)
        source_family = _source_family(path, args.xml_root, region)
        if args.max_files_per_source_family > 0 and len(grouped[source_family]) >= args.max_files_per_source_family:
            continue
        grouped[source_family].append(path)
    selected: list[Path] = []
    source_families = sorted(grouped)
    while source_families and (args.max_files <= 0 or len(selected) < args.max_files):
        next_families: list[str] = []
        for source_family in source_families:
            paths = grouped[source_family]
            if not paths:
                continue
            selected.append(paths.pop(0))
            if paths:
                next_families.append(source_family)
            if args.max_files > 0 and len(selected) >= args.max_files:
                break
        source_families = next_families
    return selected


def _signal_payload(text: str) -> dict[str, Any]:
    signal = extract_signal(text)
    payload: dict[str, Any] = {}
    for key in ("action", "material", "connection", "install_method"):
        value = clean_text(getattr(signal, key))
        if value:
            payload[key] = value
    for key in ("dn", "cable_section", "cable_cores", "circuits", "concrete_grade", "thickness"):
        value = getattr(signal, key)
        if value is not None:
            payload[key] = value
    return payload


def _quota_names_by_id(pair: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for quota in pair.get("quotas") or []:
        code = clean_text(quota.get("code"))
        name = clean_text(quota.get("name"))
        if code and name:
            result[code] = name
    return result


def _signal_signature(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("action", "material", "connection", "install_method", "dn", "cable_section", "cable_cores", "circuits", "concrete_grade", "thickness"):
        value = payload.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "|".join(parts) or "generic"


def _group_key(
    *,
    args: argparse.Namespace,
    province: str,
    family: str,
    quota_id: str,
    bill_name_key: str,
    signal_payload: dict[str, Any],
) -> tuple[str, ...]:
    if args.aggregate_scope == "bill_name":
        return (province, family, quota_id, bill_name_key)
    return (province, family, quota_id, _signal_signature(signal_payload))


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
                continue
            province, local_ids = _choose_province(expected, region, province_candidates, quota_cache)
            if not province or not local_ids:
                counters["pairs_without_local_quota_id"] += 1
                continue
            bill_name = clean_text(pair.get("bill_name"))
            bill_desc = clean_text(pair.get("bill_desc"))
            bill_text = clean_text(pair.get("bill_pattern")) or " ".join(part for part in (bill_name, bill_desc) if part)
            query_text = " ".join(part for part in (bill_name, bill_desc) if part)
            signal = extract_signal(query_text)
            family = clean_text(signal.family)
            if family not in args.core_families:
                counters["pairs_without_core_family"] += 1
                continue
            terms = set(recall_terms(" ".join([bill_name, bill_desc, " ".join(clean_text(q.get("name")) for q in pair.get("quotas") or [])])))
            bill_terms = set(recall_terms(" ".join([bill_name, bill_desc])))
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
                key = _group_key(
                    args=args,
                    province=province,
                    family=family,
                    quota_id=quota_id,
                    bill_name_key=bill_name_key,
                    signal_payload=signal_payload,
                )
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
                        "source_families": set(),
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
                item["source_families"].add(source_family)
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
                "source_family_count": len(item["source_families"]),
                "source_families": sorted(item["source_families"]),
                "source_files": sorted(item["source_files"])[:20],
                "source_file_hashes": sorted(item["source_file_hashes"]),
                "signal": item["signal"],
                "evidence": item["evidence"],
                "candidate_source": "16B_OSS_RECALL_MULTI_FIELD_SUPPORT",
            }
        )
    rows.sort(key=lambda row: (row["province"], row["query_family"], row["bill_name_key"], -row["support_count"], row["quota_id"]))
    summary = {
        "selection_mode": args.selection_mode,
        "aggregate_scope": args.aggregate_scope,
        "files_considered": len(selected_files),
        "selected_source_family_count": len(selected_source_families),
        "selected_source_families": dict(sorted(selected_source_families.items())),
        "counters": dict(counters),
        "output_rows_before_min_support": len(grouped),
        "output_rows": len(rows),
    }
    return rows, inventory, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 16.x broader OSS recall/index from XML mother data")
    parser.add_argument("--xml-root", type=Path, default=DEFAULT_XML_ROOT)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--regions", default="FJ,ZJ,BJ,GD,HN")
    parser.add_argument("--max-files", type=int, default=160)
    parser.add_argument("--max-files-per-source-family", type=int, default=0)
    parser.add_argument("--max-pairs-per-file", type=int, default=300)
    parser.add_argument("--min-support", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 2) or 2))
    parser.add_argument("--min-terms", type=int, default=2)
    parser.add_argument("--max-terms", type=int, default=28)
    parser.add_argument("--max-evidence", type=int, default=8)
    parser.add_argument("--selection-mode", choices=("sorted", "balanced-source-family"), default="balanced-source-family")
    parser.add_argument("--aggregate-scope", choices=("bill_name", "quota_signature"), default="quota_signature")
    parser.add_argument(
        "--core-families",
        default=",".join(getattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", ("concrete", "rebar", "pipe", "pump", "support"))),
    )
    args = parser.parse_args()
    args.core_families = {part.strip() for part in args.core_families.split(",") if part.strip()}
    rows, inventory, summary = _build_rows(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    report = {
        "stage": "16.4 source-family-balanced OSS recall/index build",
        "xml_root": str(args.xml_root),
        "db_dir": str(args.db_dir),
        "output": str(args.output),
        "min_support": args.min_support,
        "core_families": sorted(args.core_families),
        "summary": summary,
        "trained": False,
        "tuned": False,
        "online_default_changed": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(args.summary, report)
    _write_csv(args.inventory, inventory, ["relative_path", "region", "source_family", "pairs_parsed", "pairs_used", "accepted_quota_links"])
    print(json.dumps({"output": str(args.output), "rows": len(rows), "summary": str(args.summary)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
