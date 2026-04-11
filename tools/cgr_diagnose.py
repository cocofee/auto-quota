from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_real_eval import (  # noqa: E402
    PROFILE_DEFAULTS,
    _bill_item_from_record,
    _clean_text,
    _configure_logging,
    _read_jsonl,
    _runtime_profile,
)


DEFAULT_DATASET_PATH = PROJECT_ROOT / "output" / "real_eval" / "real_eval_smoke_install_only.jsonl"
DEFAULT_SUMMARY_PATH = PROJECT_ROOT / "output" / "real_eval" / "cgr_diagnose.summary.json"
DEFAULT_DETAILS_PATH = PROJECT_ROOT / "output" / "real_eval" / "cgr_diagnose.details.jsonl"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_snapshot(snapshots: list[dict], quota_ids: list[str]) -> dict:
    quota_id_set = {str(value).strip() for value in quota_ids if str(value).strip()}
    if not quota_id_set:
        return {}
    for snapshot in snapshots or []:
        if str(snapshot.get("quota_id", "")).strip() in quota_id_set:
            return snapshot
    return {}


def _detail_from_result(record: dict, result: dict) -> dict:
    oracle_ids = [str(value).strip() for value in (record.get("oracle_quota_ids") or []) if str(value).strip()]
    snapshots = list(result.get("candidate_snapshots") or [])
    quotas = list(result.get("quotas") or [])
    ltr_meta = dict(result.get("ltr_rerank") or {})
    cgr_meta = dict(ltr_meta.get("cgr") or {})
    query_summary = dict(cgr_meta.get("query_summary") or {})

    top1_id = str((quotas[0].get("quota_id", "") if quotas else "") or "")
    post_ltr_top1_id = str(result.get("post_ltr_top1_id", "") or "")
    post_cgr_top1_id = str(ltr_meta.get("post_cgr_top1_id", "") or post_ltr_top1_id or "")
    oracle_snapshot = _find_snapshot(snapshots, oracle_ids)
    top1_snapshot = _find_snapshot(snapshots, [top1_id])
    incumbent_snapshot = _find_snapshot(snapshots, [post_ltr_top1_id])
    oracle_in_candidates = any(
        str(snapshot.get("quota_id", "")).strip() in set(oracle_ids)
        for snapshot in snapshots
    )
    oracle_feasible = bool(oracle_snapshot.get("cgr_feasible", True)) if oracle_snapshot else None
    oracle_cgr_found = bool(oracle_snapshot)
    top1_correct = bool(top1_id and top1_id in oracle_ids)

    return {
        "sample_id": str(record.get("sample_id") or ""),
        "province": _clean_text(record.get("province")),
        "bill_name": _clean_text(record.get("bill_name")),
        "bill_text": _clean_text(record.get("bill_text")),
        "specialty": _clean_text(record.get("specialty")),
        "oracle_quota_ids": oracle_ids,
        "top1_quota_id": top1_id,
        "pre_ltr_top1_id": str(result.get("pre_ltr_top1_id", "") or ""),
        "post_ltr_top1_id": post_ltr_top1_id,
        "post_cgr_top1_id": post_cgr_top1_id,
        "top1_correct": top1_correct,
        "oracle_in_snapshots": oracle_in_candidates,
        "oracle_snapshot_found": oracle_cgr_found,
        "incumbent_feasible": incumbent_snapshot.get("cgr_feasible"),
        "incumbent_probability": incumbent_snapshot.get("cgr_probability"),
        "incumbent_score": incumbent_snapshot.get("cgr_score"),
        "incumbent_param_match": incumbent_snapshot.get("param_match"),
        "incumbent_rank_stage": incumbent_snapshot.get("rank_stage"),
        "incumbent_rank_score_source": incumbent_snapshot.get("rank_score_source"),
        "incumbent_ltr_score": incumbent_snapshot.get("ltr_score"),
        "incumbent_manual_score": incumbent_snapshot.get("manual_structured_score"),
        "incumbent_fatal_hard_conflict": incumbent_snapshot.get("cgr_fatal_hard_conflict"),
        "incumbent_high_conf_wrong_book": incumbent_snapshot.get("cgr_high_conf_wrong_book"),
        "incumbent_high_conf_family_book_conflict": incumbent_snapshot.get("cgr_high_conf_family_book_conflict"),
        "incumbent_tier_penalty": incumbent_snapshot.get("cgr_tier_penalty"),
        "incumbent_generic_penalty": incumbent_snapshot.get("cgr_generic_penalty"),
        "incumbent_soft_conflict_penalty": incumbent_snapshot.get("cgr_soft_conflict_penalty"),
        "oracle_feasible": oracle_feasible,
        "oracle_probability": oracle_snapshot.get("cgr_probability"),
        "oracle_score": oracle_snapshot.get("cgr_score"),
        "oracle_fatal_hard_conflict": oracle_snapshot.get("cgr_fatal_hard_conflict"),
        "oracle_high_conf_wrong_book": oracle_snapshot.get("cgr_high_conf_wrong_book"),
        "oracle_high_conf_family_book_conflict": oracle_snapshot.get("cgr_high_conf_family_book_conflict"),
        "oracle_tier_penalty": oracle_snapshot.get("cgr_tier_penalty"),
        "oracle_generic_penalty": oracle_snapshot.get("cgr_generic_penalty"),
        "oracle_soft_conflict_penalty": oracle_snapshot.get("cgr_soft_conflict_penalty"),
        "top1_feasible": top1_snapshot.get("cgr_feasible"),
        "top1_probability": top1_snapshot.get("cgr_probability"),
        "top1_score": top1_snapshot.get("cgr_score"),
        "top1_fatal_hard_conflict": top1_snapshot.get("cgr_fatal_hard_conflict"),
        "top1_high_conf_wrong_book": top1_snapshot.get("cgr_high_conf_wrong_book"),
        "top1_high_conf_family_book_conflict": top1_snapshot.get("cgr_high_conf_family_book_conflict"),
        "top1_tier_penalty": top1_snapshot.get("cgr_tier_penalty"),
        "top1_generic_penalty": top1_snapshot.get("cgr_generic_penalty"),
        "top1_soft_conflict_penalty": top1_snapshot.get("cgr_soft_conflict_penalty"),
        "challenger_minus_incumbent_probability": (
            _safe_float(top1_snapshot.get("cgr_probability"), 0.0)
            - _safe_float(incumbent_snapshot.get("cgr_probability"), 0.0)
        ) if top1_snapshot and incumbent_snapshot else None,
        "challenger_minus_incumbent_score": (
            _safe_float(top1_snapshot.get("cgr_score"), 0.0)
            - _safe_float(incumbent_snapshot.get("cgr_score"), 0.0)
        ) if top1_snapshot and incumbent_snapshot else None,
        "cgr_applied": bool(cgr_meta.get("applied")),
        "cgr_empty_feasible_set": bool(cgr_meta.get("empty_feasible_set")),
        "cgr_gate": _safe_float(cgr_meta.get("gate"), 0.0),
        "cgr_top_probability": _safe_float(cgr_meta.get("top_probability"), 0.0),
        "cgr_top_quota_id": str(cgr_meta.get("top_quota_id", "") or ""),
        "route": str(query_summary.get("route") or ""),
        "query_param_coverage": _safe_float(query_summary.get("query_param_coverage"), 0.0),
        "group_ambiguity_score": _safe_float(query_summary.get("group_ambiguity_score"), 0.0),
        "family_confidence": _safe_float(query_summary.get("family_confidence"), 0.0),
        "candidate_count": int(result.get("candidate_count", 0) or 0),
    }


def summarize_details(details: list[dict]) -> dict:
    total = len(details)
    oracle_in_snapshots = sum(1 for row in details if row.get("oracle_in_snapshots"))
    top1_correct = sum(1 for row in details if row.get("top1_correct"))
    cgr_applied = sum(1 for row in details if row.get("cgr_applied"))
    empty_feasible_set = sum(1 for row in details if row.get("cgr_empty_feasible_set"))
    oracle_feasible_false = sum(1 for row in details if row.get("oracle_feasible") is False)
    oracle_not_found = sum(1 for row in details if row.get("oracle_in_snapshots") and not row.get("oracle_snapshot_found"))
    post_ltr_to_cgr_change = sum(
        1
        for row in details
        if row.get("post_ltr_top1_id") and row.get("post_cgr_top1_id")
        and row.get("post_ltr_top1_id") != row.get("post_cgr_top1_id")
    )
    ltr_correct_cgr_wrong = [
        row for row in details
        if row.get("oracle_quota_ids")
        and row.get("post_ltr_top1_id") in set(row.get("oracle_quota_ids") or [])
        and not row.get("top1_correct")
    ]
    oracle_killed_rows = [row for row in details if row.get("oracle_feasible") is False]
    buckets = Counter()
    route_buckets = Counter()
    for row in oracle_killed_rows:
        flags = []
        if row.get("oracle_fatal_hard_conflict"):
            flags.append("fatal_hard_conflict")
        if row.get("oracle_high_conf_wrong_book"):
            flags.append("high_conf_wrong_book")
        if row.get("oracle_high_conf_family_book_conflict"):
            flags.append("high_conf_family_book_conflict")
        buckets["+".join(flags) or "unknown"] += 1
        route_buckets[str(row.get("route") or "")] += 1

    gate_values = [_safe_float(row.get("cgr_gate"), 0.0) for row in details if row.get("cgr_applied")]
    gate_by_route: dict[str, list[float]] = defaultdict(list)
    for row in details:
        if row.get("cgr_applied"):
            gate_by_route[str(row.get("route") or "")].append(_safe_float(row.get("cgr_gate"), 0.0))

    def _mean(values: list[float]) -> float:
        return round(sum(values) / max(len(values), 1), 4) if values else 0.0

    return {
        "total": total,
        "top1_correct": top1_correct,
        "hit_rate": round(top1_correct / max(total, 1) * 100, 1),
        "cgr_applied_count": cgr_applied,
        "empty_feasible_set_count": empty_feasible_set,
        "oracle_in_snapshots_count": oracle_in_snapshots,
        "oracle_feasible_false_count": oracle_feasible_false,
        "oracle_not_found_in_snapshots_count": oracle_not_found,
        "post_ltr_to_cgr_change_count": post_ltr_to_cgr_change,
        "ltr_correct_cgr_wrong_count": len(ltr_correct_cgr_wrong),
        "oracle_killed_flag_buckets": dict(buckets),
        "oracle_killed_route_buckets": dict(route_buckets),
        "mean_gate": _mean(gate_values),
        "mean_gate_by_route": {
            route: _mean(values)
            for route, values in sorted(gate_by_route.items())
        },
    }


def run_diagnosis(
    dataset_path: str | Path,
    *,
    profile: str = "smoke",
    limit: int | None = None,
    log_level: str = "WARNING",
) -> tuple[dict, list[dict]]:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
    _configure_logging(log_level)

    import config
    from src.match_engine import init_search_components, match_search_only

    records = _read_jsonl(dataset_path)
    if limit is not None and int(limit) > 0:
        records = records[: int(limit)]

    details: list[dict] = []
    with _runtime_profile(profile):
        config.LTR_V2_ENABLED = True
        config.CONSTRAINED_GATED_RANKER_ENABLED = True
        config.CGR_ACCEPT_HEAD_ENABLED = False
        grouped: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            province = _clean_text(record.get("province"))
            if province:
                grouped[province].append(record)

        for province in sorted(grouped):
            searcher, validator = init_search_components(resolved_province=province)
            bill_items = [
                _bill_item_from_record(record, index)
                for index, record in enumerate(grouped[province], start=1)
            ]
            results = match_search_only(
                bill_items,
                searcher,
                validator,
                experience_db=None,
                province=province,
            )
            for record, result in zip(grouped[province], results):
                details.append(_detail_from_result(record, result))

    summary = summarize_details(details)
    summary["dataset_path"] = str(Path(dataset_path))
    summary["profile"] = profile
    return summary, details


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose CGR feasible/gate errors on real eval dataset")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="real eval jsonl path")
    parser.add_argument("--summary-out", default=str(DEFAULT_SUMMARY_PATH), help="summary json path")
    parser.add_argument("--details-out", default=str(DEFAULT_DETAILS_PATH), help="details jsonl path")
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="smoke", help="runtime preset")
    parser.add_argument("--limit", type=int, default=None, help="global record limit")
    parser.add_argument("--log-level", default="WARNING", help="loguru log level")
    args = parser.parse_args()

    summary, details = run_diagnosis(
        args.dataset,
        profile=args.profile,
        limit=args.limit,
        log_level=args.log_level,
    )

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    details_path = Path(args.details_out)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8") as handle:
        for row in details:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] summary saved to: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
