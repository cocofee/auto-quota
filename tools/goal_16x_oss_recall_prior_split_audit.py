from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.national_index import clean_text, extract_signal
from src.goal_search.oss_recall_prior import OssRecallPriorSource, reset_oss_recall_prior_source
from src.goal_search.oss_alias_prior import normalize_alias_text
from src.goal_search.searcher import GoalSearcher, _apply_strong_name_signal, clear_goal_search_cache
from tools.goal_16x_local_assets_guarded_alias_ab_validation import (
    CORE_FAMILIES,
    DEFAULT_DB_DIR,
    _configure_db_root,
    _evaluate_split,
    _read_jsonl,
    _searcher_prior_texts,
    _write_csv,
    _write_json,
)

DEFAULT_SPLIT = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "dev_validation.jsonl"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index.jsonl"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_16x_oss_recall_prior_dev_split_audit"


def _split_expected_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        return [clean_text(item) for item in value.split("|") if clean_text(item)]
    return []


def _normalize_split_rows(rows: list[dict], *, variant_filter: str) -> list[dict]:
    normalized: list[dict] = []
    seen_group_ids: set[str] = set()
    for row in rows:
        if variant_filter and clean_text(row.get("variant")) != variant_filter:
            continue
        out = dict(row)
        if not clean_text(out.get("bill_name") or out.get("name")) and clean_text(out.get("query")):
            out["bill_name"] = clean_text(out.get("query"))
        out["expected_ids"] = _split_expected_ids(out.get("expected_ids"))
        group_id = clean_text(out.get("anchor_group_id") or out.get("group_id"))
        if group_id:
            out["anchor_group_id"] = group_id
            if group_id in seen_group_ids:
                continue
            seen_group_ids.add(group_id)
        if out["expected_ids"]:
            normalized.append(out)
    return normalized


def _fast_exact_name_impacted_rows(
    rows: list[dict],
    source: OssRecallPriorSource,
    *,
    split_name: str,
    progress_every: int,
) -> tuple[list[dict], dict[str, str]]:
    source._load()
    province_cache: dict[str, str] = {}
    impacted: list[dict] = []
    support_name_keys_by_province: dict[str, set[str]] = {}
    support_name_keys_all: set[str] = set()
    for province, family in source._by_scope:
        if family != "support":
            continue
        keys: set[str] = set()
        for candidate in source._by_scope[(province, family)]:
            keys.update(key for key in candidate.bill_name_keys if key)
            if candidate.bill_name_key:
                keys.add(candidate.bill_name_key)
        if keys:
            support_name_keys_by_province[province] = keys
            support_name_keys_all.update(keys)

    for ordinal, row in enumerate(rows, start=1):
        if progress_every > 0 and (ordinal == 1 or ordinal % progress_every == 0):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {split_name} impacted scan: {ordinal}/{len(rows)}", flush=True)
        bill_name_key = normalize_alias_text(row.get("bill_name") or row.get("name"))
        if bill_name_key:
            query_keys = {bill_name_key}
        else:
            query_keys = {normalize_alias_text(text) for text in _searcher_prior_texts(row) if normalize_alias_text(text)}
        if not query_keys.intersection(support_name_keys_all):
            continue
        raw_province = clean_text(row.get("province"))
        resolved = province_cache.setdefault(raw_province, config.resolve_province(raw_province))
        support_name_keys = support_name_keys_by_province.get(resolved)
        if support_name_keys and query_keys.intersection(support_name_keys):
            query = GoalSearcher._coerce_item(row)
            query_text = " ".join(x for x in [query.bill_name, query.text, query.specialty, query.unit] if x)
            query_signal = _apply_strong_name_signal(extract_signal(query_text), query.bill_name)
            if query_signal.family != "support":
                continue
            impacted.append(row)
    return impacted, province_cache


def main() -> int:
    parser = argparse.ArgumentParser(description="16.x OSS recall prior one-split dev/OOF audit")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--split-name", default="dev")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--impacted-only", action="store_true")
    parser.add_argument("--fast-exact-name-impacted", action="store_true")
    parser.add_argument("--variant-filter", default="")
    parser.add_argument("--recall-min-support", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 6) or 6))
    parser.add_argument("--recall-min-source-families", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", 2) or 2))
    parser.add_argument("--recall-min-overlap", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", 2) or 2))
    parser.add_argument("--recall-intervention-mode", choices=("broad", "exact_name"), default="exact_name")
    parser.add_argument("--recall-core-families", default="support")
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    core_families = {part.strip() for part in args.recall_core_families.split(",") if part.strip()} or set(CORE_FAMILIES)
    config.OSS_RECALL_INDEX_PATH = str(args.index)
    config.OSS_RECALL_INDEX_MIN_SUPPORT = args.recall_min_support
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = args.recall_min_source_families
    config.OSS_RECALL_INDEX_MIN_OVERLAP = args.recall_min_overlap
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = args.recall_intervention_mode
    config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(core_families))
    reset_oss_recall_prior_source()
    clear_goal_search_cache()

    rows = _normalize_split_rows(_read_jsonl(args.split), variant_filter=args.variant_filter)
    total_rows = len(rows)
    if args.limit > 0:
        rows = rows[: args.limit]
    scanned_rows = len(rows)
    province_cache: dict[str, str] = {}
    source = OssRecallPriorSource(
        args.index,
        min_support=args.recall_min_support,
        min_source_families=args.recall_min_source_families,
        min_overlap=args.recall_min_overlap,
        intervention_mode=args.recall_intervention_mode,
        core_families=core_families,
    )
    if args.impacted_only:
        if args.fast_exact_name_impacted and args.recall_intervention_mode == "exact_name" and core_families == {"support"}:
            rows, province_cache = _fast_exact_name_impacted_rows(
                rows,
                source,
                split_name=args.split_name,
                progress_every=args.progress_every,
            )
        else:
            from tools.goal_16x_local_assets_guarded_alias_ab_validation import _impacted_rows

            rows = _impacted_rows(rows, source, "recall", province_cache)
        print(f"impacted-only rows: {len(rows)}/{scanned_rows}", flush=True)

    if rows:
        audit_rows, scorecard = _evaluate_split(
            args.split_name,
            rows,
            source,
            "recall",
            progress_every=args.progress_every,
            province_cache=province_cache,
        )
        head = next(row for row in scorecard if row["slice"] == "all")
    else:
        audit_rows = []
        scorecard = []
        head = {
            "slice": "all",
            "groups": 0,
            "baseline_top1": 0,
            "treatment_top1": 0,
            "delta_top1": 0,
            "baseline_top5": 0,
            "treatment_top5": 0,
            "delta_top5": 0,
            "baseline_top20": 0,
            "treatment_top20": 0,
            "delta_top20": 0,
            "baseline_top80": 0,
            "treatment_top80": 0,
            "delta_top80": 0,
            "top1_wins": 0,
            "top1_losses": 0,
            "top80_gains": 0,
            "top80_losses": 0,
            "prior_generated_candidates": 0,
            "prior_positive_candidates": 0,
            "prior_false_candidates": 0,
            "prior_false_candidate_rate": 0.0,
        }
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    report = {
        "stage": "16.x OSS recall prior one-split audit",
        "split": args.split_name,
        "split_path": str(args.split),
        "total_rows_before_filter": total_rows,
        "scanned_rows": scanned_rows,
        "evaluated_rows": len(rows),
        "impacted_only": args.impacted_only,
        "fast_exact_name_impacted": args.fast_exact_name_impacted,
        "candidate": {
            "intervention_mode": args.recall_intervention_mode,
            "core_families": sorted(core_families),
            "min_support": args.recall_min_support,
            "min_source_families": args.recall_min_source_families,
            "min_overlap": args.recall_min_overlap,
            "top_k": int(getattr(config, "OSS_RECALL_INDEX_TOP_K", 8) or 8),
        },
        "headline": head,
        "scorecard": scorecard,
        "artifacts": {
            "summary_json": str(summary_json),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
        },
        "trained": False,
        "online_default_changed": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_csv(scorecard_csv, scorecard or [head], list(head.keys()))
    _write_csv(row_csv, audit_rows, list(audit_rows[0].keys()) if audit_rows else ["split", "row_ordinal"])
    config.OSS_RECALL_INDEX_ENABLED = False
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    print(json.dumps({"summary": str(summary_json), "headline": head}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
