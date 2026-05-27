from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config  # noqa: E402
from src.goal_search import GoalSearcher  # noqa: E402
from src.goal_search.national_index import clean_text  # noqa: E402
from tools.goal_build_ltr_features import (  # noqa: E402
    DIAG_COLUMNS,
    FEATURE_COLUMNS,
    _build_feature_row,
    _feature_text,
    _query_signal,
    _query_text,
)
from tools.goal_eval import _row_id, _with_leakage_controls  # noqa: E402
from tools.import_xml import convert_xml_to_pairs  # noqa: E402


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_OSS_XML_ROOT = Path(r"D:\广联达临时文件\oss_samples")
DEFAULT_INVENTORY = AGENT_STATE / "goal_13x_oss_xml_mother_data_manifest_file_inventory.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_DIR = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix"
DEFAULT_REPORT_JSON = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_summary.json"
DEFAULT_REPORT_MD = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_summary.md"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_LOCAL_ASSETS_DB_DIR = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522" / "db"

REGION_HINTS = {
    "FJ": ("福建",),
    "ZJ": ("浙江",),
    "JS": ("江苏",),
    "BJ": ("北京",),
}

FORBIDDEN_TRAINING_FEATURES = {
    "anchor_group_id",
    "anchor_reason",
    "anchor_status",
    "bill_name",
    "bill_text",
    "candidate_id",
    "candidate_name",
    "candidate_rank",
    "correct_quota_id",
    "expected_id",
    "expected_ids",
    "expected_quota_id",
    "expected_quota_ids",
    "group_id",
    "name",
    "positive_id",
    "project_name",
    "province",
    "query",
    "query_text",
    "quota_book",
    "quota_chapter",
    "quota_id",
    "quota_name",
    "quota_unit",
    "raw_query",
    "reasons",
    "row_index",
    "sample_id",
    "source_family",
    "source_file",
    "source_region",
    "source_top_dir",
    "split",
    "stored_ids",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _hash_int(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12], 16)


def _fold(key: str, fold_count: int) -> int:
    return _hash_int(key) % max(fold_count, 1)


def _read_feature_whitelist(path: Path) -> list[str]:
    payload = _read_json(path)
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} missing training_features")
    result = [clean_text(feature) for feature in features if clean_text(feature)]
    forbidden = sorted(set(result) & FORBIDDEN_TRAINING_FEATURES)
    if forbidden:
        raise ValueError(f"forbidden fields leaked into training_features: {forbidden}")
    return result


def _clean_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result


def _format_number(value: Any) -> str:
    number = _clean_float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.10g}"


def _write_matrix_csv(path: Path, rows: list[dict[str, Any]], training_features: list[str]) -> list[dict[str, Any]]:
    invalid_numeric: Counter[str] = Counter()
    missing_features: Counter[str] = Counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["label", *training_features], extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            matrix_row: dict[str, Any] = {"label": int(row.get("label") or 0)}
            for feature in training_features:
                if feature not in row:
                    missing_features[feature] += 1
                value = row.get(feature, 0)
                try:
                    float(value)
                except (TypeError, ValueError):
                    invalid_numeric[feature] += 1
                matrix_row[feature] = _format_number(value)
            writer.writerow(matrix_row)
    issues: list[dict[str, Any]] = []
    issues.extend({"type": "missing_feature", "feature": key, "count": value} for key, value in missing_features.most_common())
    issues.extend({"type": "invalid_numeric", "feature": key, "count": value} for key, value in invalid_numeric.most_common())
    return issues


def _copy_whitelist(path: Path, training_features: list[str], source_whitelist: Path) -> None:
    payload = {
        "stage": "13.4 OSS XML source-aware training matrix build",
        "training_features": training_features,
        "label_column": "label",
        "group_column": "group_id",
        "fold_column": "oof_fold",
        "excluded_diagnostic_columns": sorted(set(DIAG_COLUMNS) | FORBIDDEN_TRAINING_FEATURES),
        "source_whitelist": str(source_whitelist),
        "notes": [
            "Matrix build only; no training or threshold tuning.",
            "OSS source/province/source_family fields are diagnostics and split metadata only.",
            "Identifiers, answer ids, source paths, and quota ids are excluded from training matrix CSV.",
        ],
    }
    _write_json(path, payload)


def _has_any_quota_db(provinces_dir: Path) -> bool:
    return provinces_dir.exists() and any(provinces_dir.glob("*/quota.db"))


def _configure_db_dirs(db_dir_arg: str) -> dict[str, Any]:
    configured = Path(db_dir_arg) if db_dir_arg else Path(config.DB_DIR)
    candidates = [configured]
    if DEFAULT_LOCAL_ASSETS_DB_DIR not in candidates:
        candidates.append(DEFAULT_LOCAL_ASSETS_DB_DIR)
    selected = configured
    reason = "configured"
    for candidate in candidates:
        if _has_any_quota_db(candidate / "provinces"):
            selected = candidate
            reason = "configured" if candidate == configured else "local_assets_fallback"
            break
    config.DB_DIR = selected
    config.COMMON_DB_DIR = selected / "common"
    config.PROVINCES_DB_DIR = selected / "provinces"
    return {
        "db_dir": str(config.DB_DIR),
        "common_db_dir": str(config.COMMON_DB_DIR),
        "provinces_db_dir": str(config.PROVINCES_DB_DIR),
        "reason": reason,
        "has_quota_db": _has_any_quota_db(config.PROVINCES_DB_DIR),
    }


def _province_candidates_by_region() -> dict[str, list[str]]:
    available = config.list_db_provinces()
    result: dict[str, list[str]] = {}
    for region, hints in REGION_HINTS.items():
        matches = [province for province in available if any(hint in province for hint in hints)]
        result[region] = sorted(matches)
    return result


def _read_inventory(path: Path, root: Path) -> list[dict[str, Any]]:
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["path"] = str(Path(row["path"]))
            row["size_bytes"] = int(row.get("size_bytes") or 0)
        return rows
    rows = []
    for xml_path in root.rglob("*.xml"):
        rel = xml_path.relative_to(root)
        parts = rel.parts
        rows.append(
            {
                "path": str(xml_path),
                "relative_path": str(rel),
                "file_name": xml_path.name,
                "size_bytes": xml_path.stat().st_size,
                "top_dir": parts[0] if parts else "",
                "province_dir": parts[1] if len(parts) >= 3 and parts[0] == "by_province" else "",
                "unique_name_size_key": f"{xml_path.name.lower()}::{xml_path.stat().st_size}",
            }
        )
    return rows


def _infer_region(row: dict[str, Any]) -> str:
    province_dir = clean_text(row.get("province_dir")).upper()
    top_dir = clean_text(row.get("top_dir")).lower()
    rel = clean_text(row.get("relative_path")).lower()
    if province_dir in REGION_HINTS:
        return province_dir
    if top_dir.startswith("fj") or "\\fj" in rel or "/fj" in rel:
        return "FJ"
    if top_dir.startswith("zj") or "\\zj" in rel or "/zj" in rel:
        return "ZJ"
    if top_dir.startswith("js") or "\\js" in rel or "/js" in rel:
        return "JS"
    if top_dir.startswith("bj") or "\\bj" in rel or "/bj" in rel:
        return "BJ"
    if "福建" in rel:
        return "FJ"
    if "浙江" in rel:
        return "ZJ"
    if "江苏" in rel:
        return "JS"
    if "北京" in rel:
        return "BJ"
    return ""


def _source_family(row: dict[str, Any], region: str) -> str:
    top_dir = clean_text(row.get("top_dir")) or "<root>"
    province_dir = clean_text(row.get("province_dir")) or "-"
    return f"{region}:{top_dir}:{province_dir}"


def _pick_evenly(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return list(rows)
    sorted_rows = sorted(rows, key=lambda row: (int(row.get("size_bytes") or 0), clean_text(row.get("relative_path"))))
    picks = []
    for idx in range(limit):
        pos = round(idx * (len(sorted_rows) - 1) / max(limit - 1, 1))
        picks.append(sorted_rows[pos])
    seen = set()
    result = []
    for row in picks:
        key = clean_text(row.get("path"))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _select_files(inventory: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], Counter[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reject_counts: Counter[str] = Counter()
    allowed_regions = {item.strip().upper() for item in args.regions.split(",") if item.strip()}
    seen_unique_name_size: set[str] = set()
    for row in inventory:
        if int(row.get("size_bytes") or 0) <= 0:
            reject_counts["empty_file"] += 1
            continue
        unique_name_size_key = clean_text(row.get("unique_name_size_key")) or f"{clean_text(row.get('file_name')).lower()}::{row.get('size_bytes')}"
        if args.dedupe_unique_name_size and unique_name_size_key in seen_unique_name_size:
            reject_counts["duplicate_unique_name_size_skipped"] += 1
            continue
        seen_unique_name_size.add(unique_name_size_key)
        region = _infer_region(row)
        if not region:
            reject_counts["unknown_region"] += 1
            continue
        if allowed_regions and region not in allowed_regions:
            reject_counts["region_not_selected"] += 1
            continue
        row = dict(row)
        row["source_region"] = region
        row["source_family"] = _source_family(row, region)
        row["unique_name_size_key"] = unique_name_size_key
        grouped[row["source_family"]].append(row)

    family_picks: dict[str, list[dict[str, Any]]] = {}
    for source_family in sorted(grouped):
        family_picks[source_family] = _pick_evenly(grouped[source_family], args.max_files_per_source_family)

    selected = []
    source_families = sorted(family_picks, key=lambda key: (key.split(":", 1)[0], key))
    cursor = 0
    while True:
        added = False
        for source_family in source_families:
            picks = family_picks[source_family]
            if cursor < len(picks):
                selected.append(picks[cursor])
                added = True
                if args.max_total_files > 0 and len(selected) >= args.max_total_files:
                    return selected, reject_counts
        if not added:
            break
        cursor += 1
    if args.max_total_files > 0:
        selected = selected[: args.max_total_files]
    return selected, reject_counts


def _assign_oof_folds(selected_files: list[dict[str, Any]], fold_count: int) -> None:
    if fold_count <= 1:
        for row in selected_files:
            row["oof_fold"] = 0
        return
    fold_loads = [0 for _ in range(fold_count)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected_files:
        grouped[str(row.get("source_family") or "<empty>")].append(row)
    source_families = sorted(grouped, key=lambda key: (-len(grouped[key]), key))
    for source_family in source_families:
        rows = sorted(grouped[source_family], key=lambda row: (int(row.get("size_bytes") or 0), clean_text(row.get("relative_path"))), reverse=True)
        used_folds: set[int] = set()
        for row in rows:
            ranked_folds = sorted(range(fold_count), key=lambda fold: (fold in used_folds, fold_loads[fold], fold))
            fold = ranked_folds[0]
            row["oof_fold"] = fold
            fold_loads[fold] += 1
            used_folds.add(fold)


def _sample_pairs(pairs: list[dict[str, Any]], limit: int) -> list[tuple[int, dict[str, Any]]]:
    indexed = list(enumerate(pairs, 1))
    if limit <= 0 or len(indexed) <= limit:
        return indexed
    sampled: list[tuple[int, dict[str, Any]]] = []
    for idx in range(limit):
        pos = round(idx * (len(indexed) - 1) / max(limit - 1, 1))
        sampled.append(indexed[pos])
    seen = set()
    result = []
    for original_index, pair in sampled:
        key = original_index
        if key not in seen:
            seen.add(key)
            result.append((original_index, pair))
    return result


def _expected_ids_from_pair(pair: dict[str, Any]) -> set[str]:
    expected = set()
    for quota in pair.get("quotas") or []:
        code = clean_text(quota.get("code"))
        if code:
            expected.add(code)
    return expected


def _make_row(
    *,
    pair: dict[str, Any],
    expected: set[str],
    selected_province: str,
    source_row: dict[str, Any],
    pair_index: int,
    group_seq: int,
    fold_count: int,
) -> dict[str, Any]:
    rel = clean_text(source_row.get("relative_path"))
    source_hash = hashlib.sha1(rel.encode("utf-8", errors="ignore")).hexdigest()[:16]
    bill_name = clean_text(pair.get("bill_name"))
    bill_desc = clean_text(pair.get("bill_desc"))
    bill_pattern = clean_text(pair.get("bill_pattern"))
    return {
        "sample_id": f"ossxml:{source_hash}:{pair_index}",
        "bill_id": f"{source_hash}:{pair_index}",
        "bill_name": bill_name,
        "name": bill_name,
        "bill_text": bill_pattern or " ".join(part for part in [bill_name, bill_desc] if part),
        "description": bill_desc,
        "unit": clean_text(pair.get("bill_unit")),
        "specialty": clean_text(pair.get("specialty")),
        "expected_ids": sorted(expected),
        "source_file": rel,
        "project_name": clean_text(pair.get("section")),
        "province": selected_province,
        "source_region": source_row["source_region"],
        "source_family": source_row["source_family"],
        "source_top_dir": clean_text(source_row.get("top_dir")),
        "source_file_hash": source_hash,
        "source_pair_index": pair_index,
        "anchor_group_id": f"ossxml:{source_hash}:{pair_index}:{group_seq}",
        "anchor_status": "accepted_oss_xml_mother_data",
        "anchor_reason": "human_quantity_surveyor_oss_xml_pair",
        "oof_fold": int(source_row.get("oof_fold", _fold(f"{source_row['source_region']}::{rel}", fold_count))),
    }


def _choose_searcher(
    expected: set[str],
    region: str,
    province_candidates: dict[str, list[str]],
    searchers: dict[str, GoalSearcher],
) -> tuple[str, GoalSearcher | None, int]:
    best_province = ""
    best_count = 0
    for province in province_candidates.get(region, []):
        if province not in searchers:
            searchers[province] = GoalSearcher(province)
        count = sum(1 for quota_id in expected if quota_id in searchers[province].index.by_quota_id)
        if count > best_count:
            best_count = count
            best_province = province
    if not best_province:
        return "", None, 0
    return best_province, searchers[best_province], best_count


def _top_snapshot(hits: list[Any], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "quota_id": hit.quota_id,
            "name": hit.name,
            "score": hit.score,
            "confidence": hit.confidence,
            "reasons": list(hit.reasons or []),
        }
        for rank, hit in enumerate(hits[:limit], 1)
    ]


def _top_counter(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _feature_coverage(rows: list[dict[str, Any]], training_features: list[str]) -> list[dict[str, Any]]:
    total = len(rows)
    coverage = []
    for feature in training_features:
        present = 0
        nonzero = 0
        for row in rows:
            value = row.get(feature)
            if value not in ("", None):
                present += 1
            if _clean_float(value) != 0.0:
                nonzero += 1
        coverage.append(
            {
                "feature": feature,
                "present": present,
                "present_rate": round(present / total, 6) if total else 0.0,
                "nonzero": nonzero,
                "nonzero_rate": round(nonzero / total, 6) if total else 0.0,
            }
        )
    return coverage


def _build_matrix(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(args.oss_xml_root)
    inventory = _read_inventory(Path(args.inventory), root)
    selected_files, file_reject_counts = _select_files(inventory, args)
    _assign_oof_folds(selected_files, args.oof_folds)
    db_config = _configure_db_dirs(args.db_dir)
    province_candidates = _province_candidates_by_region()
    missing_region_db = [region for region in {row["source_region"] for row in selected_files} if not province_candidates.get(region)]
    training_features = _read_feature_whitelist(Path(args.feature_whitelist))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    leakage_args = SimpleNamespace(
        allow_answer_priors=False,
        exclude_sample_id="",
        exclude_source_file="",
        exclude_project_name="",
    )
    searchers: dict[str, GoalSearcher] = {}
    feature_rows: list[dict[str, Any]] = []
    group_sizes: list[int] = []
    group_meta: list[dict[str, Any]] = []
    recall_gap_rows: list[dict[str, Any]] = []
    anchor_excluded_rows: list[dict[str, Any]] = []
    file_selection_rows: list[dict[str, Any]] = []

    reject_counts: Counter[str] = Counter(file_reject_counts)
    province_counts: Counter[str] = Counter()
    source_family_counts: Counter[str] = Counter()
    fold_group_counts: Counter[str] = Counter()
    recall_gap_family_counts: Counter[str] = Counter()
    accepted_family_counts: Counter[str] = Counter()
    positive_rank_counts: Counter[str] = Counter()
    candidate_count_counts: Counter[str] = Counter()

    parsed_pairs = 0
    sampled_pairs = 0
    eligible_pairs = 0
    searched_pairs = 0
    accepted_groups = 0
    positive_rows = 0
    baseline_hit1 = 0
    baseline_hit5 = 0
    group_seq = 0

    for file_index, source_row in enumerate(selected_files, 1):
        path = Path(source_row["path"])
        parse_error = ""
        t0 = time.perf_counter()
        try:
            pairs = convert_xml_to_pairs(str(path))
        except Exception as exc:
            pairs = []
            parse_error = repr(exc)
            reject_counts["parse_error"] += 1
        parse_elapsed = round(time.perf_counter() - t0, 3)
        parsed_pairs += len(pairs)
        sampled = _sample_pairs(pairs, args.max_pairs_per_file)
        sampled_pairs += len(sampled)
        file_selection_rows.append(
            {
                "path": str(path),
                "relative_path": source_row.get("relative_path", ""),
                "source_region": source_row["source_region"],
                "source_family": source_row["source_family"],
                "unique_name_size_key": source_row.get("unique_name_size_key", ""),
                "oof_fold": source_row.get("oof_fold", ""),
                "size_bytes": source_row.get("size_bytes", 0),
                "parsed_pairs": len(pairs),
                "sampled_pairs": len(sampled),
                "parse_elapsed_sec": parse_elapsed,
                "parse_error": parse_error,
            }
        )
        if parse_error:
            continue
        for pair_index, pair in sampled:
            expected = _expected_ids_from_pair(pair)
            if not expected:
                reject_counts["missing_expected_ids"] += 1
                continue
            region = source_row["source_region"]
            selected_province, searcher, known_expected_count = _choose_searcher(expected, region, province_candidates, searchers)
            if searcher is None:
                reject_counts["expected_id_not_in_region_db"] += 1
                anchor_excluded_rows.append(
                    {
                        "split": "dev",
                        "sample_id": f"{clean_text(source_row.get('relative_path'))}:{pair_index}",
                        "source_file": source_row.get("relative_path", ""),
                        "source_region": region,
                        "source_family": source_row["source_family"],
                        "expected_ids": sorted(expected),
                        "reject_reason": "expected_id_not_in_region_db",
                    }
                )
                continue
            group_seq += 1
            row = _make_row(
                pair=pair,
                expected=expected,
                selected_province=selected_province,
                source_row=source_row,
                pair_index=pair_index,
                group_seq=group_seq,
                fold_count=args.oof_folds,
            )
            gid = clean_text(row["anchor_group_id"])
            eligible_pairs += 1
            province_counts[selected_province] += 1
            source_family_counts[row["source_family"]] += 1

            hits = searcher.search(_with_leakage_controls(row, leakage_args), top_k=args.top_k)
            searched_pairs += 1
            if len(hits) < 2:
                reject_counts["insufficient_candidates"] += 1
                anchor_excluded_rows.append(
                    {
                        "split": "dev",
                        "group_id": gid,
                        "sample_id": _row_id(row, group_seq),
                        "source_file": row["source_file"],
                        "source_region": row["source_region"],
                        "source_family": row["source_family"],
                        "province": selected_province,
                        "query": _query_text(row),
                        "expected_ids": sorted(expected),
                        "candidate_count": len(hits),
                        "known_expected_count": known_expected_count,
                        "reject_reason": "insufficient_candidates",
                    }
                )
                continue

            top_ids = [hit.quota_id for hit in hits]
            expected_rank = next((rank for rank, quota_id in enumerate(top_ids, 1) if quota_id in expected), None)
            query_signal = _query_signal(row)
            if expected_rank is None:
                reject_counts["expected_id_not_in_topk"] += 1
                recall_gap_family_counts[query_signal.family or "<empty>"] += 1
                recall_gap_rows.append(
                    {
                        "split": "dev",
                        "group_id": gid,
                        "sample_id": _row_id(row, group_seq),
                        "source_file": row["source_file"],
                        "source_region": row["source_region"],
                        "source_family": row["source_family"],
                        "oof_fold": row["oof_fold"],
                        "province": selected_province,
                        "query": _query_text(row),
                        "expected_ids": sorted(expected),
                        "known_expected_count": known_expected_count,
                        "query_family": query_signal.family,
                        "candidate_count": len(hits),
                        "top_ids": top_ids[:10],
                        "top": _top_snapshot(hits, limit=5),
                        "recall_gap_reason": "expected_id_not_in_topk",
                    }
                )
                continue

            group_feature_rows = []
            for hit_rank, hit in enumerate(hits, 1):
                if hit.quota_id not in searcher.index.by_quota_id:
                    continue
                feature_row = _build_feature_row(
                    split="dev",
                    row=row,
                    row_index=group_seq,
                    hit_rank=hit_rank,
                    hit=hit,
                    searcher=searcher,
                    query_signal=query_signal,
                    expected=expected,
                )
                feature_row["group_id"] = gid
                feature_row["source_region"] = row["source_region"]
                feature_row["source_family"] = row["source_family"]
                feature_row["source_file_hash"] = row["source_file_hash"]
                feature_row["source_pair_index"] = row["source_pair_index"]
                feature_row["oof_fold"] = row["oof_fold"]
                feature_row["known_expected_count"] = known_expected_count
                group_feature_rows.append(feature_row)

            group_positive_rows = sum(1 for item in group_feature_rows if int(item.get("label") or 0) == 1)
            if not group_feature_rows or group_positive_rows <= 0:
                reject_counts["positive_filtered_after_feature_build"] += 1
                continue

            accepted_groups += 1
            positive_rows += group_positive_rows
            baseline_hit1 += int(expected_rank == 1)
            baseline_hit5 += int(expected_rank <= 5)
            positive_rank_counts[str(expected_rank)] += 1
            candidate_count_counts[str(len(group_feature_rows))] += 1
            accepted_family_counts[query_signal.family or "<empty>"] += 1
            fold_group_counts[str(row["oof_fold"])] += 1
            feature_rows.extend(group_feature_rows)
            group_sizes.append(len(group_feature_rows))
            group_meta.append(
                {
                    "split": "dev",
                    "group_id": gid,
                    "rows": len(group_feature_rows),
                    "positive_count": group_positive_rows,
                    "positive_rank": expected_rank,
                    "sample_id": _row_id(row, group_seq),
                    "source_file": row["source_file"],
                    "source_region": row["source_region"],
                    "source_family": row["source_family"],
                    "source_file_hash": row["source_file_hash"],
                    "source_pair_index": row["source_pair_index"],
                    "oof_fold": row["oof_fold"],
                    "project_name": clean_text(row.get("project_name")),
                    "province": selected_province,
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "known_expected_count": known_expected_count,
                    "anchor_status": row["anchor_status"],
                    "anchor_type": "accepted_oss_xml_expected_id_anchor",
                    "query_family": query_signal.family,
                    "candidate_count": len(group_feature_rows),
                }
            )

            if args.max_accepted_groups > 0 and accepted_groups >= args.max_accepted_groups:
                break
        if args.max_accepted_groups > 0 and accepted_groups >= args.max_accepted_groups:
            break
        if args.progress_every > 0 and file_index % args.progress_every == 0:
            print(
                f"[13.4] files={file_index}/{len(selected_files)} sampled_pairs={sampled_pairs} "
                f"accepted={accepted_groups} recall_gap={len(recall_gap_rows)} excluded={len(anchor_excluded_rows)}",
                file=sys.stderr,
            )

    feature_jsonl = output_dir / "ltr_features_dev.jsonl"
    matrix_csv = output_dir / "ltr_matrix_dev.csv"
    group_txt = output_dir / "ltr_group_dev.txt"
    group_jsonl = output_dir / "ltr_group_dev.jsonl"
    recall_gap_jsonl = output_dir / "recall_gap_dev.jsonl"
    anchor_excluded_jsonl = output_dir / "anchor_excluded_dev.jsonl"
    file_selection_csv = output_dir / "file_selection.csv"
    source_split_manifest_csv = output_dir / "source_split_manifest.csv"
    leakage_checks_csv = output_dir / "leakage_checks.csv"
    source_balance_checks_csv = output_dir / "source_balance_checks.csv"
    whitelist_out = output_dir / "ltr_feature_whitelist_oss_source_aware_v1.json"

    _write_jsonl(feature_jsonl, feature_rows)
    matrix_issues = _write_matrix_csv(matrix_csv, feature_rows, training_features)
    group_txt.write_text("\n".join(str(size) for size in group_sizes) + ("\n" if group_sizes else ""), encoding="utf-8")
    _write_jsonl(group_jsonl, group_meta)
    _write_jsonl(recall_gap_jsonl, recall_gap_rows)
    _write_jsonl(anchor_excluded_jsonl, anchor_excluded_rows)
    _write_csv(
        file_selection_csv,
        file_selection_rows,
        ["path", "relative_path", "source_region", "source_family", "unique_name_size_key", "oof_fold", "size_bytes", "parsed_pairs", "sampled_pairs", "parse_elapsed_sec", "parse_error"],
    )
    source_manifest_rows = []
    accepted_source_family_counts = Counter(str(row.get("source_family") or "<empty>") for row in group_meta)
    for key, count in accepted_source_family_counts.items():
        folds = sorted({str(row["oof_fold"]) for row in group_meta if row["source_family"] == key})
        source_manifest_rows.append(
            {
                "source_family": key,
                "accepted_groups": count,
                "oof_folds": "|".join(folds),
                "fold_count": len(folds),
            }
        )
    source_manifest_rows.sort(key=lambda row: (-int(row["accepted_groups"]), row["source_family"]))
    _write_csv(source_split_manifest_csv, source_manifest_rows, ["source_family", "accepted_groups", "oof_folds", "fold_count"])

    training_feature_set = set(training_features)
    forbidden_feature_leaks = sorted(training_feature_set & FORBIDDEN_TRAINING_FEATURES)
    same_file_folds: dict[str, set[int]] = defaultdict(set)
    for row in group_meta:
        same_file_folds[str(row["source_file"])].add(int(row["oof_fold"]))
    same_file_cross_fold_violations = sum(1 for folds in same_file_folds.values() if len(folds) > 1)
    matrix_rows_match_group = sum(group_sizes) == len(feature_rows)
    selected_unique_keys = [clean_text(row.get("unique_name_size_key")) for row in selected_files if clean_text(row.get("unique_name_size_key"))]
    duplicate_selected_unique_name_size = len(selected_unique_keys) - len(set(selected_unique_keys))
    source_file_group_counts = Counter(str(row.get("source_file") or "<empty>") for row in group_meta)
    source_family_group_counts = Counter(str(row.get("source_family") or "<empty>") for row in group_meta)
    fold_group_counts_full = Counter(str(row.get("oof_fold") or 0) for row in group_meta)
    max_source_file_group_share = round((max(source_file_group_counts.values()) / len(group_meta)), 6) if group_meta and source_file_group_counts else 0.0
    max_source_family_group_share = round((max(source_family_group_counts.values()) / len(group_meta)), 6) if group_meta and source_family_group_counts else 0.0
    fold_values = sorted(fold_group_counts_full.values())
    median_fold_groups = fold_values[len(fold_values) // 2] if fold_values else 0
    min_fold_groups = min(fold_values) if fold_values else 0
    min_fold_to_median_ratio = round(min_fold_groups / median_fold_groups, 6) if median_fold_groups else 0.0
    leakage_rows = [
        {"check": "forbidden_training_feature_intersection", "value": len(forbidden_feature_leaks), "status": "pass" if not forbidden_feature_leaks else "fail", "details": "|".join(forbidden_feature_leaks)},
        {"check": "source_file_not_in_matrix_features", "value": int("source_file" in training_feature_set), "status": "pass" if "source_file" not in training_feature_set else "fail", "details": ""},
        {"check": "expected_ids_not_in_matrix_features", "value": int("expected_ids" in training_feature_set), "status": "pass" if "expected_ids" not in training_feature_set else "fail", "details": ""},
        {"check": "quota_id_not_in_matrix_features", "value": int("quota_id" in training_feature_set), "status": "pass" if "quota_id" not in training_feature_set else "fail", "details": ""},
        {"check": "same_source_file_cross_fold_violations", "value": same_file_cross_fold_violations, "status": "pass" if same_file_cross_fold_violations == 0 else "fail", "details": ""},
        {"check": "matrix_rows_match_group", "value": int(matrix_rows_match_group), "status": "pass" if matrix_rows_match_group else "fail", "details": f"group_sum={sum(group_sizes)} rows={len(feature_rows)}"},
        {"check": "heldout_hard_used_for_selection", "value": 0, "status": "pass", "details": "dev/OOF-only matrix build"},
        {"check": "goal_searcher_changed", "value": 0, "status": "pass", "details": "offline search calls only; no code/config change"},
    ]
    source_balance_rows = [
        {"check": "duplicate_unique_name_size_selected", "value": duplicate_selected_unique_name_size, "status": "pass" if duplicate_selected_unique_name_size == 0 else "fail", "details": ""},
        {"check": "max_source_file_group_share", "value": max_source_file_group_share, "status": "pass" if max_source_file_group_share <= args.max_source_file_group_share else "warn", "details": f"target<={args.max_source_file_group_share}"},
        {"check": "max_source_family_group_share", "value": max_source_family_group_share, "status": "pass" if max_source_family_group_share <= args.max_source_family_group_share else "warn", "details": f"target<={args.max_source_family_group_share}"},
        {"check": "observed_oof_fold_count", "value": len(fold_group_counts_full), "status": "pass" if len(fold_group_counts_full) >= args.oof_folds else "warn", "details": f"target={args.oof_folds}"},
        {"check": "min_fold_to_median_group_ratio", "value": min_fold_to_median_ratio, "status": "pass" if min_fold_to_median_ratio >= args.min_fold_median_ratio else "warn", "details": f"min={min_fold_groups}; median={median_fold_groups}; target>={args.min_fold_median_ratio}"},
    ]
    _write_csv(leakage_checks_csv, leakage_rows, ["check", "value", "status", "details"])
    _write_csv(source_balance_checks_csv, source_balance_rows, ["check", "value", "status", "details"])
    _copy_whitelist(whitelist_out, training_features, Path(args.feature_whitelist))

    stage = "13.8 OSS XML expanded/rebalanced matrix rebuild" if "expanded" in str(args.output_dir).lower() or "expanded" in str(args.report_json).lower() else "13.4 OSS XML source-aware training matrix build"
    summary = {
        "stage": stage,
        "metrics": {
            "oss_xml_root": str(root),
            "inventory_rows": len(inventory),
            "selected_file_count": len(file_selection_rows),
            "parsed_pairs": parsed_pairs,
            "sampled_pairs": sampled_pairs,
            "eligible_pairs": eligible_pairs,
            "searched_pairs": searched_pairs,
            "accepted_groups": accepted_groups,
            "matrix_rows": len(feature_rows),
            "group_sum": sum(group_sizes),
            "matrix_rows_match_group": matrix_rows_match_group,
            "positive_rows": positive_rows,
            "topk_recall_rate": round(accepted_groups / eligible_pairs, 6) if eligible_pairs else 0.0,
            "baseline_hit1_groups": baseline_hit1,
            "baseline_hit1_rate_on_matrix_groups": round(baseline_hit1 / accepted_groups, 6) if accepted_groups else 0.0,
            "baseline_hit5_groups": baseline_hit5,
            "baseline_hit5_rate_on_matrix_groups": round(baseline_hit5 / accepted_groups, 6) if accepted_groups else 0.0,
            "recall_gap_groups": len(recall_gap_rows),
            "anchor_excluded_groups": len(anchor_excluded_rows),
            "duplicate_unique_name_size_selected": duplicate_selected_unique_name_size,
            "max_source_file_group_share": max_source_file_group_share,
            "max_source_family_group_share": max_source_family_group_share,
            "observed_oof_fold_count": len(fold_group_counts_full),
            "min_fold_to_median_group_ratio": min_fold_to_median_ratio,
            "training_executed": False,
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "goal_searcher_changed": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
        "config": {
            "top_k": args.top_k,
            "oof_folds": args.oof_folds,
            "max_total_files": args.max_total_files,
            "max_files_per_source_family": args.max_files_per_source_family,
            "max_pairs_per_file": args.max_pairs_per_file,
            "max_accepted_groups": args.max_accepted_groups,
            "regions": args.regions,
            "dedupe_unique_name_size": args.dedupe_unique_name_size,
            "max_source_file_group_share": args.max_source_file_group_share,
            "max_source_family_group_share": args.max_source_family_group_share,
            "min_fold_median_ratio": args.min_fold_median_ratio,
            "db_config": db_config,
            "province_candidates_by_region": province_candidates,
            "missing_region_db": missing_region_db,
        },
        "diagnostics": {
            "reject_counts": dict(reject_counts),
            "province_counts": _top_counter(province_counts),
            "source_family_counts": _top_counter(accepted_source_family_counts),
            "fold_group_counts": dict(sorted(fold_group_counts.items(), key=lambda item: int(item[0]))),
            "accepted_family_counts": _top_counter(accepted_family_counts),
            "recall_gap_family_counts": _top_counter(recall_gap_family_counts),
            "positive_rank_counts": dict(sorted(positive_rank_counts.items(), key=lambda item: int(item[0]))),
            "candidate_count_counts": dict(candidate_count_counts),
            "feature_coverage": _feature_coverage(feature_rows, training_features),
            "matrix_issue_count": len(matrix_issues),
            "matrix_issues": matrix_issues[:20],
            "leakage_checks": leakage_rows,
            "source_balance_checks": source_balance_rows,
        },
        "artifacts": {
            "output_dir": str(output_dir),
            "feature_jsonl": str(feature_jsonl),
            "matrix_csv": str(matrix_csv),
            "group_txt": str(group_txt),
            "group_jsonl": str(group_jsonl),
            "recall_gap_jsonl": str(recall_gap_jsonl),
            "anchor_excluded_jsonl": str(anchor_excluded_jsonl),
            "file_selection_csv": str(file_selection_csv),
            "source_split_manifest_csv": str(source_split_manifest_csv),
            "leakage_checks_csv": str(leakage_checks_csv),
            "source_balance_checks_csv": str(source_balance_checks_csv),
            "feature_whitelist": str(whitelist_out),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "decision": "Built a reproducible OSS XML source-aware dev/OOF matrix. This enables a future reranker experiment, but no model training or release has been performed in this stage.",
        "anti_drift_conclusion": "No heldout/hard selection, no online integration, no GoalSearcher modification, no threshold tuning, and no source/id/provenance fields in the training matrix.",
        "next_stage": {
            "recommended": "13.5 OSS XML source-aware dev/OOF offline reranker training authorization/execution",
            "requires_explicit_go": True,
            "default_without_go": "review 13.4 matrix and leakage/source split checks only",
        },
    }
    return summary


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    d = report["diagnostics"]
    lines = [
        f"# {report['stage']}",
        "",
        "Bounded matrix-build stage for OSS XML mother-data. No model was trained and no online code path was changed.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| selected_file_count | {m['selected_file_count']} |",
        f"| parsed_pairs | {m['parsed_pairs']} |",
        f"| sampled_pairs | {m['sampled_pairs']} |",
        f"| eligible_pairs | {m['eligible_pairs']} |",
        f"| accepted_groups | {m['accepted_groups']} |",
        f"| matrix_rows | {m['matrix_rows']} |",
        f"| topk_recall_rate | {m['topk_recall_rate']} |",
        f"| baseline_hit1_rate_on_matrix_groups | {m['baseline_hit1_rate_on_matrix_groups']} |",
        f"| recall_gap_groups | {m['recall_gap_groups']} |",
        f"| duplicate_unique_name_size_selected | {m['duplicate_unique_name_size_selected']} |",
        f"| max_source_file_group_share | {m['max_source_file_group_share']} |",
        f"| max_source_family_group_share | {m['max_source_family_group_share']} |",
        f"| observed_oof_fold_count | {m['observed_oof_fold_count']} |",
        f"| min_fold_to_median_group_ratio | {m['min_fold_to_median_group_ratio']} |",
        "",
        "## Source / Fold",
        "",
        "| bucket | count |",
        "| --- | --- |",
    ]
    for row in d["source_family_counts"][:12]:
        lines.append(f"| {row['key']} | {row['count']} |")
    lines.extend(
        [
            "",
            "## Leakage Checks",
            "",
            "| check | status | value |",
            "| --- | --- | --- |",
        ]
    )
    for row in d["leakage_checks"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['value']} |")
    lines.extend(
        [
            "",
            "## Source Balance Checks",
            "",
            "| check | status | value |",
            "| --- | --- | --- |",
        ]
    )
    for row in d.get("source_balance_checks", []):
        lines.append(f"| {row['check']} | {row['status']} | {row['value']} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        f"当前阶段：{report['stage']} 已完成。\n"
        f"结果：selected_files={m['selected_file_count']}，sampled_pairs={m['sampled_pairs']}，"
        f"accepted_groups={m['accepted_groups']}，matrix_rows={m['matrix_rows']}，"
        f"topk_recall_rate={m['topk_recall_rate']}，baseline_hit1_rate={m['baseline_hit1_rate_on_matrix_groups']}，"
        f"max_source_file_group_share={m['max_source_file_group_share']}，max_source_family_group_share={m['max_source_family_group_share']}。\n"
        "下一步建议：13.9 expanded matrix acceptance gate。只读复核 expanded matrix 的去重、source/fold balance、recall 和 leakage checks；通过后才允许下一轮 dev/OOF training。\n"
        "禁止：使用 heldout/hard 做选择、上线、改 GoalSearcher、改阈值、把 source_file/expected_id/quota_id/provenance 当训练特征。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if f"{report['stage']} summary" not in text:
        rows = f"""          <tr>
            <td>{report['stage']} summary</td>
            <td>Bounded dev/OOF matrix from OSS XML bill-quota pairs with source/province/source_family split checks.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>{report['stage']} artifacts</td>
            <td>LTR matrix, feature diagnostics, group/fold metadata, source split manifest, and leakage checks.</td>
            <td><code>{_safe_rel(report['artifacts']['output_dir'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.3 OSS XML mother-data manifest summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 13.4 OSS XML source-aware reranker matrix")
    parser.add_argument("--oss-xml-root", default=str(DEFAULT_OSS_XML_ROOT))
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--db-dir", default="")
    parser.add_argument("--regions", default="FJ,ZJ,JS,BJ")
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--oof-folds", type=int, default=5)
    parser.add_argument("--max-total-files", type=int, default=16)
    parser.add_argument("--max-files-per-source-family", type=int, default=2)
    parser.add_argument("--max-pairs-per-file", type=int, default=120)
    parser.add_argument("--max-accepted-groups", type=int, default=600)
    parser.add_argument("--dedupe-unique-name-size", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-source-file-group-share", type=float, default=0.08)
    parser.add_argument("--max-source-family-group-share", type=float, default=0.25)
    parser.add_argument("--min-fold-median-ratio", type=float, default=0.60)
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args()

    report = _build_matrix(args)
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": args.report_json, "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
