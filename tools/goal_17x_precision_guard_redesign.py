from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.national_index import clean_text
from src.goal_search.oss_recall_prior import OssRecallPriorSource, reset_oss_recall_prior_source
from tools.goal_16x_local_assets_guarded_alias_ab_validation import DEFAULT_DB_DIR, _query_family


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"
DEFAULT_ROW_AUDIT = AGENT_STATE / "goal_17x_oss_multifield_dev_oof_shadow_row_audit.csv"
DEFAULT_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_precision_guard_redesign"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _configure_db_root(db_dir: Path) -> None:
    config.DB_DIR = db_dir
    config.COMMON_DB_DIR = db_dir / "common"
    config.PROVINCES_DB_DIR = db_dir / "provinces"


def _split_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        return [clean_text(item) for item in value.split("|") if clean_text(item)]
    return []


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _quota_lookup(province: str, quota_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not quota_ids:
        return {}
    db_path = config.get_quota_db_path(province)
    if not db_path.exists():
        return {}
    placeholders = ",".join("?" for _ in quota_ids)
    query = (
        "select quota_id, name, chapter, unit, material, connection, dn, cable_section, circuits "
        f"from quotas where quota_id in ({placeholders})"
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, sorted(quota_ids)).fetchall()
    finally:
        conn.close()
    return {clean_text(row["quota_id"]): {key: row[key] for key in row.keys()} for row in rows}


def _guard_specs() -> dict[str, Callable[[dict[str, Any]], bool]]:
    return {
        "current_broad": lambda c: True,
        "top1_per_row": lambda c: c["candidate_order"] <= 1,
        "top2_per_row": lambda c: c["candidate_order"] <= 2,
        "top3_per_row": lambda c: c["candidate_order"] <= 3,
        "top3_quota_specific1": lambda c: c["candidate_order"] <= 3 and c["quota_specific_overlap"] >= 1,
        "top5_quota_specific1": lambda c: c["candidate_order"] <= 5 and c["quota_specific_overlap"] >= 1,
        "sf2": lambda c: c["source_family_count"] >= 2,
        "support4": lambda c: c["support_count"] >= 4,
        "quota_overlap1": lambda c: c["quota_name_overlap"] >= 1,
        "quota_specific1": lambda c: c["quota_specific_overlap"] >= 1,
        "sf2_quota_specific1": lambda c: c["source_family_count"] >= 2 and c["quota_specific_overlap"] >= 1,
        "sf2_support4_quota_specific1": lambda c: (
            c["source_family_count"] >= 2 and c["support_count"] >= 4 and c["quota_specific_overlap"] >= 1
        ),
        "family_precision_v1": lambda c: (
            (
                c["query_family"] == "concrete"
                and c["source_family_count"] >= 2
                and c["quota_specific_overlap"] >= 1
                and c["quota_name_overlap"] >= 1
            )
            or (
                c["query_family"] == "pipe"
                and c["source_family_count"] >= 2
                and c["quota_specific_overlap"] >= 2
                and c["quota_name_overlap"] >= 1
            )
            or (
                c["query_family"] in {"pump", "rebar", "support"}
                and c["source_family_count"] >= 2
                and c["quota_specific_overlap"] >= 1
            )
        ),
        "family_topn_precision_v2": lambda c: (
            (
                c["query_family"] == "concrete"
                and c["candidate_order"] <= 5
                and c["quota_specific_overlap"] >= 1
            )
            or (
                c["query_family"] == "pipe"
                and c["candidate_order"] <= 1
                and c["quota_specific_overlap"] >= 1
            )
            or (
                c["query_family"] in {"pump", "rebar", "support"}
                and c["candidate_order"] <= 3
                and c["quota_specific_overlap"] >= 1
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="17.3 precision guard redesign from 17.x OOF shadow false candidates")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--row-audit", type=Path, default=DEFAULT_ROW_AUDIT)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    config.OSS_RECALL_INDEX_PATH = str(args.index)
    config.OSS_RECALL_INDEX_TOP_K = 8
    config.OSS_RECALL_INDEX_MIN_SUPPORT = 2
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = 1
    config.OSS_RECALL_INDEX_MIN_OVERLAP = 2
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = "broad"
    config.OSS_RECALL_INDEX_CORE_FAMILIES = ("concrete", "pipe", "pump", "rebar", "support")
    reset_oss_recall_prior_source()
    source = OssRecallPriorSource(
        args.index,
        min_support=2,
        min_source_families=1,
        min_overlap=2,
        intervention_mode="broad",
        core_families=set(config.OSS_RECALL_INDEX_CORE_FAMILIES),
    )

    audit_rows = _read_csv(args.row_audit)
    oof_rows = {
        clean_text(row.get("anchor_group_id") or row.get("group_id")): row
        for row in _read_jsonl(args.oof)
        if clean_text(row.get("variant")) == "baseline_only"
    }

    candidate_audit: list[dict[str, Any]] = []
    rows_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quota_cache: dict[str, dict[str, dict[str, Any]]] = {}
    for audit in audit_rows:
        group_id = clean_text(audit.get("anchor_group_id"))
        oof = oof_rows.get(group_id, {})
        province = clean_text(audit.get("resolved_province") or audit.get("raw_province"))
        expected = set(_split_ids(audit.get("expected_ids")))
        candidate_ids = _split_ids(audit.get("prior_candidate_ids"))
        row_item = dict(oof)
        if not clean_text(row_item.get("bill_name")):
            row_item["bill_name"] = clean_text(row_item.get("query"))
        row_item["expected_ids"] = sorted(expected)
        query_text = " ".join(
            part
            for part in (
                clean_text(row_item.get("bill_name") or row_item.get("query")),
                clean_text(row_item.get("bill_text") or row_item.get("description") or row_item.get("feature_text")),
            )
            if part
        )
        query_family = clean_text(audit.get("query_family")) or _query_family(row_item)
        collected = {
            clean_text(row.get("quota_id")): row
            for row in source.collect(
                province=province,
                query_text=query_text,
                query_family=query_family,
                item=row_item,
                top_k=8,
            )
        }
        all_quota_ids = set(candidate_ids) | expected
        quota_cache.setdefault(province, _quota_lookup(province, all_quota_ids))
        quota_rows = quota_cache[province]
        expected_names = [clean_text(quota_rows.get(quota_id, {}).get("name")) for quota_id in expected]
        expected_chapters = [clean_text(quota_rows.get(quota_id, {}).get("chapter")) for quota_id in expected]
        for candidate_order, candidate_id in enumerate(candidate_ids, start=1):
            details = collected.get(candidate_id, {})
            quota = quota_rows.get(candidate_id, {})
            row = {
                "group_id": group_id,
                "sample_id": clean_text(audit.get("sample_id")),
                "province": province,
                "query": clean_text(row_item.get("bill_name") or row_item.get("query")),
                "query_family": query_family,
                "baseline_rank": _safe_int(audit.get("baseline_rank")),
                "treatment_rank": _safe_int(audit.get("treatment_rank")),
                "top1_win": _safe_int(audit.get("top1_win")),
                "top1_loss": _safe_int(audit.get("top1_loss")),
                "top80_gain": _safe_int(audit.get("top80_gain")),
                "expected_ids": "|".join(sorted(expected)),
                "expected_names": " / ".join(name for name in expected_names if name),
                "expected_chapters": " / ".join(chapter for chapter in expected_chapters if chapter),
                "candidate_id": candidate_id,
                "candidate_order": candidate_order,
                "candidate_name": clean_text(quota.get("name")),
                "candidate_chapter": clean_text(quota.get("chapter")),
                "is_positive": int(candidate_id in expected),
                "support_count": _safe_int(details.get("oss_recall_support_count")),
                "source_family_count": _safe_int(details.get("oss_recall_source_family_count")),
                "overlap": _safe_int(details.get("oss_recall_overlap")),
                "quota_name_overlap": _safe_int(details.get("oss_recall_quota_name_overlap")),
                "specific_overlap": _safe_int(details.get("oss_recall_specific_overlap")),
                "quota_specific_overlap": _safe_int(details.get("oss_recall_quota_specific_overlap")),
                "exact_name": int(bool(details.get("oss_recall_exact_name"))),
                "source_families": "|".join(details.get("oss_recall_source_families") or []),
            }
            candidate_audit.append(row)
            rows_by_group[group_id].append(row)

    guard_rows: list[dict[str, Any]] = []
    guard_specs = _guard_specs()
    for guard_name, predicate in guard_specs.items():
        kept = [row for row in candidate_audit if predicate(row)]
        groups = {row["group_id"] for row in kept}
        positives = [row for row in kept if row["is_positive"]]
        false = [row for row in kept if not row["is_positive"]]
        row_positive_groups = {row["group_id"] for row in positives}
        row_false_only_groups = {
            group_id
            for group_id in groups
            if group_id not in row_positive_groups and any(not row["is_positive"] for row in rows_by_group[group_id] if predicate(row))
        }
        retained_top1_win_groups = {
            row["group_id"] for row in positives if row["top1_win"]
        }
        retained_top80_gain_groups = {
            row["group_id"] for row in positives if row["top80_gain"]
        }
        generated = len(kept)
        guard_rows.append(
            {
                "guard": guard_name,
                "groups_with_candidates": len(groups),
                "generated": generated,
                "positive": len(positives),
                "false": len(false),
                "false_rate": round(len(false) / generated, 6) if generated else 0.0,
                "positive_groups": len(row_positive_groups),
                "false_only_groups": len(row_false_only_groups),
                "retained_top1_win_groups": len(retained_top1_win_groups),
                "retained_top80_gain_groups": len(retained_top80_gain_groups),
            }
        )

    by_family = Counter(row["query_family"] for row in candidate_audit)
    false_by_family = Counter(row["query_family"] for row in candidate_audit if not row["is_positive"])
    positive_by_family = Counter(row["query_family"] for row in candidate_audit if row["is_positive"])
    family_rows = []
    for family in sorted(by_family):
        generated = by_family[family]
        false = false_by_family[family]
        family_rows.append(
            {
                "query_family": family,
                "generated": generated,
                "positive": positive_by_family[family],
                "false": false,
                "false_rate": round(false / generated, 6) if generated else 0.0,
            }
        )

    selected_guard = "top3_per_row"
    selected = next(row for row in guard_rows if row["guard"] == selected_guard)
    summary = {
        "stage": "17.3 OSS multifield precision guard redesign",
        "decision": "define_top3_precision_guard_and_request_dev_oof_execution",
        "input_row_audit": str(args.row_audit),
        "index": str(args.index),
        "candidate_rows": len(candidate_audit),
        "families": family_rows,
        "guard_scorecard": guard_rows,
        "selected_guard": selected_guard,
        "selected_guard_metrics": selected,
        "recommended_next_stage": "17.4 implement/run top3 precision-guarded dev/OOF shadow",
        "anti_drift_conclusion": (
            "17.3 is a redesign/audit stage. It did not train, tune, use heldout/hard, enable online behavior, "
            "overwrite the 16.x artifact, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    guard_csv = args.output_prefix.with_name(args.output_prefix.name + "_guard_scorecard.csv")
    family_csv = args.output_prefix.with_name(args.output_prefix.name + "_family_false_candidate_audit.csv")
    candidate_csv = args.output_prefix.with_name(args.output_prefix.name + "_candidate_audit.csv")
    _write_json(summary_json, summary)
    _write_csv(
        guard_csv,
        guard_rows,
        [
            "guard",
            "groups_with_candidates",
            "generated",
            "positive",
            "false",
            "false_rate",
            "positive_groups",
            "false_only_groups",
            "retained_top1_win_groups",
            "retained_top80_gain_groups",
        ],
    )
    _write_csv(family_csv, family_rows, ["query_family", "generated", "positive", "false", "false_rate"])
    _write_csv(
        candidate_csv,
        candidate_audit,
        [
            "group_id",
            "sample_id",
            "province",
            "query",
            "query_family",
            "baseline_rank",
            "treatment_rank",
            "top1_win",
            "top1_loss",
            "top80_gain",
            "expected_ids",
            "expected_names",
            "expected_chapters",
            "candidate_id",
            "candidate_order",
            "candidate_name",
            "candidate_chapter",
            "is_positive",
            "support_count",
            "source_family_count",
            "overlap",
            "quota_name_overlap",
            "specific_overlap",
            "quota_specific_overlap",
            "exact_name",
            "source_families",
        ],
    )
    print(json.dumps({"summary": str(summary_json), "selected_guard": selected_guard, "metrics": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
