from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.run_real_eval import (  # noqa: E402
    _bill_item_from_record,
    _configure_logging,
    _detail_from_result,
    _read_jsonl,
    _runtime_profile,
)


DEFAULT_DATASET = PROJECT_ROOT / "reports" / "agent_state" / "v36_probe_core_100.jsonl"
DEFAULT_SUMMARY_OUT = PROJECT_ROOT / "reports" / "agent_state" / "v36_speed_probe_core_100_summary.json"
DEFAULT_DETAILS_OUT = PROJECT_ROOT / "reports" / "agent_state" / "v36_speed_probe_core_100_details.jsonl"
DETAIL_SCOPE_SLOWEST_10 = "slowest_10"
DETAIL_SCOPE_ALL = "all"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _top_stage(stages: dict) -> tuple[str, float]:
    if not isinstance(stages, dict) or not stages:
        return "", 0.0
    name, elapsed = max(stages.items(), key=lambda item: float(item[1] or 0.0))
    return str(name), float(elapsed or 0.0)


def _result_timing(result: dict, wall_elapsed: float | None = None) -> dict:
    performance = result.get("performance") if isinstance(result, dict) else {}
    performance = performance if isinstance(performance, dict) else {}
    stages = performance.get("stages") if isinstance(performance.get("stages"), dict) else {}
    internal_total = performance.get("total")
    if internal_total is None and stages:
        internal_total = sum(float(value or 0.0) for value in stages.values())
    top_stage, top_stage_sec = _top_stage(stages)
    return {
        "internal_total_sec": float(internal_total) if internal_total is not None else None,
        "wall_elapsed_sec": wall_elapsed,
        "top_stage": top_stage,
        "top_stage_sec": top_stage_sec,
        "stages": stages,
    }


def _build_item_row(record: dict, result: dict, timing: dict) -> dict:
    detail = _detail_from_result(record, result)
    retriever = dict(detail.get("retriever") or {})
    resolution = dict(retriever.get("search_resolution") or {})
    mixed_totals = dict(resolution.get("mixed_search_totals") or {})
    return {
        "sample_id": _clean_text(record.get("sample_id")),
        "probe_bucket": _clean_text(record.get("probe_bucket")),
        "province": _clean_text(record.get("province")),
        "bill_name": _clean_text(record.get("bill_name")),
        "specialty": _clean_text(record.get("specialty")),
        "oracle_quota_ids": list(record.get("oracle_quota_ids") or []),
        "algo_id": _clean_text(detail.get("algo_id")),
        "algo_name": _clean_text(detail.get("algo_name")),
        "is_match": bool(detail.get("is_match")),
        "cause": _clean_text(detail.get("cause")),
        "oracle_in_candidates": bool(detail.get("oracle_in_candidates")),
        "all_candidate_ids": list(detail.get("all_candidate_ids") or []),
        "miss_stage": _clean_text(detail.get("miss_stage")),
        "error_stage": _clean_text(detail.get("error_stage")),
        "error_type": _clean_text(detail.get("error_type")),
        "candidate_count": int(detail.get("candidate_count", 0) or 0),
        "match_source": _clean_text(detail.get("match_source")),
        "confidence": detail.get("confidence"),
        "reasoning_decision": dict(detail.get("reasoning_decision") or {}),
        "ranker": dict(detail.get("ranker") or {}),
        "candidate_snapshots": list(detail.get("candidate_snapshots") or []),
        "candidate_lifecycle_trace": list(detail.get("candidate_lifecycle_trace") or []),
        "pre_ltr_top1_id": _clean_text(detail.get("pre_ltr_top1_id")),
        "post_ltr_top1_id": _clean_text(detail.get("post_ltr_top1_id")),
        "post_cgr_top1_id": _clean_text(detail.get("post_cgr_top1_id")),
        "post_arbiter_top1_id": _clean_text(detail.get("post_arbiter_top1_id")),
        "post_explicit_top1_id": _clean_text(detail.get("post_explicit_top1_id")),
        "post_anchor_top1_id": _clean_text(detail.get("post_anchor_top1_id")),
        "post_final_top1_id": _clean_text(detail.get("post_final_top1_id")),
        "rank_decision_owner": _clean_text(detail.get("rank_decision_owner")),
        "rank_top1_flip_count": int(detail.get("rank_top1_flip_count", 0) or 0),
        "internal_total_sec": _round(timing.get("internal_total_sec")),
        "top_stage": _clean_text(timing.get("top_stage")),
        "top_stage_sec": _round(timing.get("top_stage_sec")),
        "performance_stages": timing.get("stages") or {},
        "mixed_search_totals": mixed_totals,
        "mixed_search_traces": list(resolution.get("mixed_search_traces") or []),
    }


def run_speed_probe(
    dataset_path: str | Path,
    *,
    profile: str = "dev",
    limit: int | None = None,
    with_experience: bool = False,
    skip_unavailable_provinces: bool = False,
    log_level: str = "WARNING",
) -> dict:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
    _configure_logging(log_level)

    records = _read_jsonl(dataset_path)
    if limit is not None and int(limit) > 0:
        records = records[: int(limit)]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        province = _clean_text(record.get("province"))
        if province:
            grouped[province].append(record)

    item_rows: list[dict] = []
    province_results: list[dict] = []
    skipped_provinces: list[dict] = []
    started = time.perf_counter()

    with _runtime_profile(profile):
        for province in sorted(grouped):
            province_records = grouped[province]
            try:
                from src.experience_db import ExperienceDB
                from src.match_engine import init_search_components, match_search_only

                init_started = time.perf_counter()
                searcher, validator = init_search_components(resolved_province=province)
                experience_db = ExperienceDB(province=province) if with_experience else None
                init_elapsed = time.perf_counter() - init_started

                bill_items = [
                    _bill_item_from_record(record, index)
                    for index, record in enumerate(province_records, start=1)
                ]
                match_started = time.perf_counter()
                results = match_search_only(
                    bill_items,
                    searcher,
                    validator,
                    experience_db=experience_db,
                    province=province,
                )
                match_elapsed = time.perf_counter() - match_started

                province_item_rows = []
                for record, result in zip(province_records, results):
                    timing = _result_timing(result)
                    row = _build_item_row(record, result, timing)
                    province_item_rows.append(row)
                    item_rows.append(row)

                item_totals = [
                    float(row["internal_total_sec"])
                    for row in province_item_rows
                    if row.get("internal_total_sec") is not None
                ]
                province_results.append({
                    "province": province,
                    "total": len(province_records),
                    "measured_item_count": len(item_totals),
                    "correct": sum(1 for row in province_item_rows if row.get("is_match")),
                    "hit_rate": round(
                        sum(1 for row in province_item_rows if row.get("is_match")) / max(len(province_item_rows), 1) * 100,
                        1,
                    ),
                    "init_elapsed_sec": _round(init_elapsed),
                    "match_wall_elapsed_sec": _round(match_elapsed),
                    "p95_internal_sec": _round(_percentile(item_totals, 0.95)),
                    "max_internal_sec": _round(max(item_totals) if item_totals else None),
                })
            except Exception as exc:
                if not skip_unavailable_provinces:
                    raise
                skipped_provinces.append({
                    "province": province,
                    "sample_count": len(province_records),
                    "reason": str(exc),
                })

    total_elapsed = time.perf_counter() - started
    measured_totals = [
        float(row["internal_total_sec"])
        for row in item_rows
        if row.get("internal_total_sec") is not None
    ]
    warm_match_total = sum(
        float(result.get("match_wall_elapsed_sec", 0.0) or 0.0)
        for result in province_results
    )
    slowest_rows = sorted(
        item_rows,
        key=lambda row: float(row.get("internal_total_sec") or 0.0),
        reverse=True,
    )[:10]
    top_stage_counts = Counter(row.get("top_stage") or "unknown" for row in slowest_rows)
    total = len(item_rows)
    correct = sum(1 for row in item_rows if row.get("is_match"))
    core_100_gate = warm_match_total <= 120.0 and total >= 100 and not skipped_provinces
    p95_value = _percentile(measured_totals, 0.95)
    max_value = max(measured_totals) if measured_totals else None

    return {
        "schema_version": "v36_speed_probe.v1",
        "dataset_path": str(Path(dataset_path)),
        "profile": profile,
        "with_experience": with_experience,
        "total_records_in_dataset": len(records),
        "evaluated_total": total,
        "correct": correct,
        "hit_rate": round(correct / max(total, 1) * 100, 1),
        "skipped_total": sum(item["sample_count"] for item in skipped_provinces),
        "skipped_provinces": skipped_provinces,
        "wall_total_sec": _round(total_elapsed),
        "warm_match_total_sec": _round(warm_match_total),
        "measured_item_count": len(measured_totals),
        "p95_internal_sec": _round(p95_value),
        "max_internal_sec": _round(max_value),
        "avg_internal_sec": _round(sum(measured_totals) / len(measured_totals) if measured_totals else None),
        "core_100_total_le_120s": bool(core_100_gate),
        "p95_le_3s": bool(p95_value is not None and p95_value <= 3.0),
        "max_le_8s_soft": bool(max_value is not None and max_value <= 8.0),
        "item_rows": item_rows,
        "slowest_10": slowest_rows,
        "slowest_10_top_stage_counts": dict(sorted(top_stage_counts.items())),
        "province_results": province_results,
        "timing_notes": [
            "wall_total_sec includes province engine initialization plus matching because this CLI is a cold process.",
            "warm_match_total_sec is the sum of province match_wall_elapsed_sec values and is the gate for warm-engine core_100.",
            "province match_wall_elapsed_sec excludes initialization and is the closer warm-engine total for each province batch.",
            "per-item P95/max use result.performance.total emitted by the existing match engine; this is diagnostics-only and does not change business logic.",
        ],
    }


def _write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _summary_payload(payload: dict) -> dict:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"slowest_10", "item_rows"}
    }


def _detail_rows(payload: dict, details_scope: str) -> list[dict]:
    if details_scope == DETAIL_SCOPE_ALL:
        return list(payload.get("item_rows") or [])
    return list(payload.get("slowest_10") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V36 fixed-probe speed diagnostics")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="V36 probe jsonl path")
    parser.add_argument("--summary-out", default=str(DEFAULT_SUMMARY_OUT), help="summary JSON output path")
    parser.add_argument("--details-out", default=str(DEFAULT_DETAILS_OUT), help="details JSONL output path")
    parser.add_argument(
        "--details-scope",
        choices=[DETAIL_SCOPE_SLOWEST_10, DETAIL_SCOPE_ALL],
        default=DETAIL_SCOPE_SLOWEST_10,
        help="write either the slowest 10 rows or all evaluated rows to --details-out",
    )
    parser.add_argument("--profile", choices=["smoke", "dev", "full"], default="dev", help="runtime preset")
    parser.add_argument("--limit", type=int, default=None, help="optional global record limit")
    parser.add_argument("--with-experience", action="store_true", help="enable experience DB during probe")
    parser.add_argument("--skip-unavailable-provinces", action="store_true", help="skip provinces whose local index is unavailable")
    parser.add_argument("--log-level", default="WARNING", help="loguru log level")
    args = parser.parse_args()

    payload = run_speed_probe(
        args.dataset,
        profile=args.profile,
        limit=args.limit,
        with_experience=args.with_experience,
        skip_unavailable_provinces=args.skip_unavailable_provinces,
        log_level=args.log_level,
    )
    detail_rows = _detail_rows(payload, args.details_scope)
    _write_json(args.summary_out, _summary_payload(payload))
    _write_jsonl(args.details_out, detail_rows)
    print(
        "[V36-SPEED] "
        f"total={payload['evaluated_total']} "
        f"hit_rate={payload['hit_rate']}% "
        f"wall_total={payload['wall_total_sec']}s "
        f"warm_total={payload['warm_match_total_sec']}s "
        f"p95={payload['p95_internal_sec']}s "
        f"max={payload['max_internal_sec']}s "
        f"core_100_le_120s={payload['core_100_total_le_120s']}"
    )
    print(f"[OK] summary saved to: {Path(args.summary_out)}")
    print(f"[OK] {args.details_scope} details saved to: {Path(args.details_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
