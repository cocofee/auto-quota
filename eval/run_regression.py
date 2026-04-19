from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.accuracy_tracker import AccuracyTracker
from tools.export_real_eval_set import DEFAULT_DB_PATH, export_real_eval_set
from tools.run_real_eval import _strip_details, run_real_eval


DEFAULT_GOLDEN_SET_PATH = PROJECT_ROOT / "eval" / "golden_set.jsonl"
DEFAULT_SUMMARY_PATH = PROJECT_ROOT / "output" / "regression" / "latest_regression_summary.json"
FASTPATH_REASONS = {"accept_head_confident", "high_confidence"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _normalize_probability(confidence: Any) -> float:
    value = _safe_float(confidence, 0.0)
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _round_metric(value: float) -> float:
    return round(float(value or 0.0), 4)


def _flatten_details(payload: dict) -> list[dict]:
    details: list[dict] = []
    for province_result in payload.get("province_results", []) or []:
        details.extend(list(province_result.get("details", []) or []))
    return details


def _is_fastpath(detail: dict) -> bool:
    reasoning = dict(detail.get("reasoning_decision") or {})
    reason = str(detail.get("accept_reason") or reasoning.get("reason") or "").strip()
    match_source = str(detail.get("match_source") or "").strip().lower()
    return reason in FASTPATH_REASONS or match_source == "agent_fastpath"


def _compute_topk_accuracy(details: list[dict], k: int) -> float:
    if not details:
        return 0.0
    hits = 0
    for detail in details:
        oracle_ids = {str(value).strip() for value in (detail.get("oracle_quota_ids") or []) if str(value).strip()}
        candidates = [
            str(value).strip()
            for value in (detail.get("all_candidate_ids") or [])[: max(int(k or 0), 1)]
            if str(value).strip()
        ]
        if oracle_ids and any(candidate in oracle_ids for candidate in candidates):
            hits += 1
    return _round_metric(hits / len(details))


def _compute_fastpath_precision(details: list[dict]) -> tuple[float, int]:
    fastpath_rows = [detail for detail in details if _is_fastpath(detail)]
    if not fastpath_rows:
        return 0.0, 0
    correct = sum(1 for detail in fastpath_rows if detail.get("is_match"))
    return _round_metric(correct / len(fastpath_rows)), len(fastpath_rows)


def _compute_confidence_calibration_ece(details: list[dict], bins: int = 10) -> float:
    if not details:
        return 0.0
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    bucket_count = max(int(bins or 0), 1)
    for detail in details:
        probability = _normalize_probability(detail.get("confidence", 0.0))
        correct = 1.0 if detail.get("is_match") else 0.0
        index = min(int(probability * bucket_count), bucket_count - 1)
        buckets[index].append((probability, correct))

    total = len(details)
    ece = 0.0
    for rows in buckets.values():
        avg_conf = sum(item[0] for item in rows) / len(rows)
        avg_acc = sum(item[1] for item in rows) / len(rows)
        ece += (len(rows) / total) * abs(avg_acc - avg_conf)
    return _round_metric(ece)


def _build_per_specialty_accuracy(details: list[dict]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for detail in details:
        specialty = str(detail.get("specialty") or "").strip() or "UNKNOWN"
        grouped[specialty].append(detail)

    summary: dict[str, dict[str, float | int]] = {}
    for specialty in sorted(grouped):
        rows = grouped[specialty]
        total = len(rows)
        top1_hits = sum(1 for row in rows if row.get("is_match"))
        top3_accuracy = _compute_topk_accuracy(rows, 3)
        summary[specialty] = {
            "total": total,
            "top1_accuracy": _round_metric(top1_hits / total) if total else 0.0,
            "top3_accuracy": top3_accuracy,
        }
    return summary


def _build_delta(current: dict, baseline: dict | None) -> tuple[str, dict]:
    if not baseline:
        return "", {}
    return str(baseline.get("pipeline_version") or ""), {
        "top1_accuracy": _round_metric(current.get("top1_accuracy", 0.0) - baseline.get("top1_accuracy", 0.0)),
        "top3_accuracy": _round_metric(current.get("top3_accuracy", 0.0) - baseline.get("top3_accuracy", 0.0)),
        "fastpath_precision": _round_metric(
            current.get("fastpath_precision", 0.0) - baseline.get("fastpath_precision", 0.0)
        ),
        "confidence_calibration_ece": _round_metric(
            current.get("confidence_calibration_ece", 0.0) - baseline.get("confidence_calibration_ece", 0.0)
        ),
    }


def export_golden_set(
    *,
    dataset_path: str | Path = DEFAULT_GOLDEN_SET_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    min_confidence: int = 95,
    min_confirm_count: int = 1,
    max_per_province: int | None = 100,
    sources: list[str] | None = None,
) -> tuple[Path, dict]:
    return export_real_eval_set(
        db_path,
        dataset_path,
        min_confidence=min_confidence,
        min_confirm_count=min_confirm_count,
        max_per_province=max_per_province,
        sources=sources or ["project_import", "user_confirmed", "user_correction"],
    )


def evaluate_on_golden_set(
    pipeline_version: str,
    *,
    dataset_path: str | Path = DEFAULT_GOLDEN_SET_PATH,
    profile: str = "dev",
    with_experience: bool = False,
    skip_unavailable_provinces: bool = False,
    tracker: AccuracyTracker | None = None,
    persist: bool = True,
) -> dict:
    payload = run_real_eval(
        dataset_path,
        profile=profile,
        with_experience=with_experience,
        skip_unavailable_provinces=skip_unavailable_provinces,
    )
    details = _flatten_details(payload)
    total = len(details)
    top1_hits = sum(1 for detail in details if detail.get("is_match"))
    top3_accuracy = _compute_topk_accuracy(details, 3)
    fastpath_precision, fastpath_count = _compute_fastpath_precision(details)
    per_specialty_accuracy = _build_per_specialty_accuracy(details)
    confidence_calibration_ece = _compute_confidence_calibration_ece(details)

    metrics = {
        "pipeline_version": str(pipeline_version or ""),
        "dataset_path": str(Path(dataset_path)),
        "profile": str(profile or payload.get("profile") or ""),
        "eval_mode": str(payload.get("eval_mode") or ""),
        "total": total,
        "top1_accuracy": _round_metric(top1_hits / total) if total else 0.0,
        "top3_accuracy": top3_accuracy,
        "fastpath_precision": fastpath_precision,
        "fastpath_count": fastpath_count,
        "confidence_calibration_ece": confidence_calibration_ece,
        "per_specialty_accuracy": per_specialty_accuracy,
        "skipped_provinces": list(payload.get("skipped_provinces", []) or []),
        "real_eval_summary": _strip_details(payload),
    }

    resolved_tracker = tracker or AccuracyTracker()
    baseline = resolved_tracker.get_latest_regression_run(
        dataset_path=str(Path(dataset_path)),
        eval_mode=str(payload.get("eval_mode") or ""),
        profile=str(profile or payload.get("profile") or ""),
        exclude_pipeline_version=str(pipeline_version or ""),
    )
    baseline_version, deltas = _build_delta(metrics, baseline)

    result = dict(metrics)
    result["baseline_version"] = baseline_version
    result["delta"] = deltas

    if persist:
        resolved_tracker.record_regression_run(
            pipeline_version=str(pipeline_version or ""),
            metrics=result,
            dataset_path=str(Path(dataset_path)),
            eval_mode=str(payload.get("eval_mode") or ""),
            profile=str(profile or payload.get("profile") or ""),
            baseline_version=baseline_version,
            deltas=deltas,
        )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden-set regression and persist per-change metrics.")
    parser.add_argument("--pipeline-version", default="", help="version tag for this pipeline change")
    parser.add_argument("--dataset", default=str(DEFAULT_GOLDEN_SET_PATH), help="golden-set jsonl path")
    parser.add_argument("--summary-out", default=str(DEFAULT_SUMMARY_PATH), help="summary json output path")
    parser.add_argument("--profile", default="dev", help="runtime preset passed to real eval")
    parser.add_argument("--with-experience", action="store_true", help="evaluate with ExperienceDB enabled")
    parser.add_argument("--skip-unavailable-provinces", action="store_true", help="skip unavailable province indexes")
    parser.add_argument("--build-golden-set", action="store_true", help="export golden_set.jsonl before regression")
    parser.add_argument("--build-golden-set-only", action="store_true", help="only export golden_set.jsonl and exit")
    parser.add_argument("--golden-set-db", default=str(DEFAULT_DB_PATH), help="experience DB path used to export golden set")
    parser.add_argument("--golden-set-min-confidence", type=int, default=95, help="minimum confidence for exported golden samples")
    parser.add_argument("--golden-set-min-confirm-count", type=int, default=1, help="minimum confirm_count for exported golden samples")
    parser.add_argument("--golden-set-max-per-province", type=int, default=100, help="cap exported samples per province")
    parser.add_argument("--golden-set-source", action="append", dest="golden_set_sources", help="repeatable source filter")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if args.build_golden_set or args.build_golden_set_only:
        written_path, manifest = export_golden_set(
            dataset_path=dataset_path,
            db_path=args.golden_set_db,
            min_confidence=args.golden_set_min_confidence,
            min_confirm_count=args.golden_set_min_confirm_count,
            max_per_province=args.golden_set_max_per_province,
            sources=list(args.golden_set_sources or []),
        )
        print(
            f"[GOLDEN-SET] wrote {written_path} count={manifest.get('count', 0)} "
            f"provinces={len(manifest.get('by_province', {}))}"
        )
        if args.build_golden_set_only:
            return 0

    if not dataset_path.exists():
        raise SystemExit(f"golden set not found: {dataset_path}")
    if not args.pipeline_version:
        raise SystemExit("--pipeline-version is required when running regression")

    result = evaluate_on_golden_set(
        args.pipeline_version,
        dataset_path=dataset_path,
        profile=args.profile,
        with_experience=bool(args.with_experience),
        skip_unavailable_provinces=bool(args.skip_unavailable_provinces),
    )

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[REGRESSION] version={result['pipeline_version']} total={result['total']} "
        f"top1={result['top1_accuracy']:.4f} top3={result['top3_accuracy']:.4f} "
        f"fastpath_precision={result['fastpath_precision']:.4f} "
        f"ece={result['confidence_calibration_ece']:.4f}"
    )
    if result.get("baseline_version"):
        delta = result.get("delta") or {}
        print(
            f"[DELTA] vs={result['baseline_version']} "
            f"top1={delta.get('top1_accuracy', 0.0):+.4f} "
            f"top3={delta.get('top3_accuracy', 0.0):+.4f} "
            f"fastpath_precision={delta.get('fastpath_precision', 0.0):+.4f} "
            f"ece={delta.get('confidence_calibration_ece', 0.0):+.4f}"
        )
    print(f"[OK] summary saved to: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
