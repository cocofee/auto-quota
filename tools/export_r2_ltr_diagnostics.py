from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "reports" / "attribution" / "r1_2_zhejiang_only_latest_after_neighbor.json"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "reports" / "attribution" / "r2_ltr_diagnostics.csv"
DEFAULT_SUMMARY_JSON = PROJECT_ROOT / "reports" / "attribution" / "r2_ltr_diagnostics_summary.json"

CSV_FIELDS = [
    "province",
    "bill_id",
    "bill_name",
    "specialty",
    "correct_quota_id",
    "correct_quota_name",
    "predicted_quota_id",
    "predicted_quota_name",
    "pre_ltr_top1_id",
    "post_ltr_top1_id",
    "post_cgr_top1_id",
    "post_final_top1_id",
    "recall_rank",
    "candidate_count",
    "correct_snapshot_rank",
    "selected_snapshot_rank",
    "r2_type",
    "bucket",
    "correct_name",
    "selected_name",
    "correct_param_score",
    "selected_param_score",
    "param_gap_selected_minus_correct",
    "correct_feature_alignment_score",
    "selected_feature_alignment_score",
    "feature_gap_selected_minus_correct",
    "correct_manual_structured_score",
    "selected_manual_structured_score",
    "manual_structured_gap_selected_minus_correct",
    "correct_rerank_score",
    "selected_rerank_score",
    "rerank_gap_selected_minus_correct",
    "correct_ltr_score",
    "selected_ltr_score",
    "ltr_gap_selected_minus_correct",
    "correct_hybrid_zscore",
    "selected_hybrid_zscore",
    "hybrid_z_gap_selected_minus_correct",
    "correct_semantic_zscore",
    "selected_semantic_zscore",
    "semantic_z_gap_selected_minus_correct",
    "correct_struct_anchor_count",
    "selected_struct_conflict_count",
    "tags",
]


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        import orjson

        return orjson.loads(raw)
    except Exception:
        return json.loads(raw.decode("utf-8"))


def _normalize_ids(values: Iterable[Any] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _first(values: Iterable[Any] | None) -> str:
    for value in values or []:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _round(value: float) -> float:
    return round(value, 6)


def _expected_ids(detail: dict[str, Any]) -> list[str]:
    return _normalize_ids(detail.get("stored_ids") or detail.get("expected_quota_ids") or [])


def _recall_rank(detail: dict[str, Any]) -> int | None:
    if detail.get("recall_rank") is not None:
        return _safe_int(detail.get("recall_rank"), default=-1)

    match_source = str(detail.get("match_source", "") or "").strip().lower()
    if match_source == "experience_exact":
        return None

    expected = set(_expected_ids(detail))
    recall_topk_ids = _normalize_ids(detail.get("recall_topk_ids") or detail.get("all_candidate_ids") or [])
    for index, quota_id in enumerate(recall_topk_ids, start=1):
        if quota_id in expected:
            return index
    return -1


def _candidate_snapshots(detail: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in detail.get("candidate_snapshots") or []:
        if not isinstance(candidate, dict):
            continue
        quota_id = str(candidate.get("quota_id") or candidate.get("id") or "").strip()
        if not quota_id or quota_id in seen:
            continue
        seen.add(quota_id)
        snapshots.append(candidate)
    return snapshots


def _find_candidate(candidates: list[dict[str, Any]], quota_ids: Iterable[str]) -> tuple[dict[str, Any], int]:
    targets = {str(value or "").strip() for value in quota_ids if str(value or "").strip()}
    if not targets:
        return {}, 0
    for index, candidate in enumerate(candidates, start=1):
        quota_id = str(candidate.get("quota_id") or candidate.get("id") or "").strip()
        if quota_id in targets:
            return candidate, index
    return {}, 0


def _candidate_name(candidate: dict[str, Any]) -> str:
    return str(candidate.get("name") or candidate.get("quota_name") or "")


def _feature_row(candidate: dict[str, Any]) -> dict[str, Any]:
    row = candidate.get("ltr_feature_snapshot") or {}
    return row if isinstance(row, dict) else {}


def _score(candidate: dict[str, Any], key: str) -> float:
    if key in candidate:
        return _safe_float(candidate.get(key))
    return _safe_float(_feature_row(candidate).get(key))


def _flag(candidate: dict[str, Any], key: str) -> bool:
    return _safe_float(_feature_row(candidate).get(key)) > 0


def _struct_anchor_count(candidate: dict[str, Any]) -> int:
    keys = ("entity_match", "canonical_name_match", "system_match", "family_match")
    return sum(1 for key in keys if _flag(candidate, key))


def _struct_conflict_count(candidate: dict[str, Any]) -> int:
    keys = ("entity_conflict", "canonical_name_conflict", "system_conflict", "family_conflict")
    return sum(1 for key in keys if _flag(candidate, key))


def _is_r2_detail(detail: dict[str, Any]) -> tuple[bool, str]:
    if detail.get("is_match"):
        return False, ""

    expected = set(_expected_ids(detail))
    if not expected:
        return False, ""

    recall_rank = _recall_rank(detail)
    if recall_rank is None or recall_rank == -1:
        return False, ""

    pre_ltr_top1_id = str(detail.get("pre_ltr_top1_id") or "").strip()
    post_ltr_top1_id = str(detail.get("post_ltr_top1_id") or "").strip()
    if post_ltr_top1_id in expected:
        return False, ""
    if pre_ltr_top1_id in expected:
        return True, "ltr_bad_flip_pre_correct"
    return True, "in_pool_not_ltr_top1"


def classify_r2_bucket(
    *,
    r2_type: str,
    recall_rank: int | None,
    candidates: list[dict[str, Any]],
    correct_candidate: dict[str, Any],
    selected_candidate: dict[str, Any],
    correct_rank: int,
    selected_rank: int,
) -> tuple[str, list[str]]:
    tags: list[str] = []
    if not correct_candidate:
        if recall_rank and recall_rank > len(candidates):
            return "oracle_beyond_snapshot_window", ["positive_not_in_top_snapshot_window"]
        return "oracle_missing_from_snapshot", ["positive_not_in_candidate_snapshots"]

    correct_param = _score(correct_candidate, "param_score")
    selected_param = _score(selected_candidate, "param_score")
    correct_feature = _score(correct_candidate, "feature_alignment_score")
    selected_feature = _score(selected_candidate, "feature_alignment_score")
    correct_manual = _score(correct_candidate, "manual_structured_score")
    selected_manual = _score(selected_candidate, "manual_structured_score")
    correct_semantic = _score(correct_candidate, "semantic_rerank_zscore")
    selected_semantic = _score(selected_candidate, "semantic_rerank_zscore")
    correct_hybrid = _score(correct_candidate, "hybrid_zscore")
    selected_hybrid = _score(selected_candidate, "hybrid_zscore")
    correct_anchor_count = _struct_anchor_count(correct_candidate)
    selected_conflict_count = _struct_conflict_count(selected_candidate)

    if r2_type == "ltr_bad_flip_pre_correct":
        tags.append("pre_ltr_was_correct")
    if correct_rank:
        tags.append(f"correct_snapshot_rank_{correct_rank}")
    if selected_rank == 1:
        tags.append("selected_snapshot_top1")
    if selected_conflict_count:
        tags.append("selected_has_struct_conflict")
    if correct_anchor_count >= 3:
        tags.append("correct_strong_struct_anchor")
    elif correct_anchor_count >= 2:
        tags.append("correct_partial_struct_anchor")
    if correct_param - selected_param >= 0.10:
        tags.append("correct_param_stronger")
    if correct_feature - selected_feature >= 0.15:
        tags.append("correct_feature_stronger")
    if correct_manual - selected_manual >= 0.15:
        tags.append("correct_manual_structured_stronger")
    if selected_semantic - correct_semantic >= 0.50:
        tags.append("selected_semantic_advantage")
    if selected_hybrid - correct_hybrid >= 0.50:
        tags.append("selected_hybrid_advantage")

    if selected_conflict_count:
        return "selected_struct_conflict", tags
    if r2_type == "ltr_bad_flip_pre_correct":
        if correct_anchor_count >= 2:
            return "pre_ltr_correct_anchor_overturned", tags
        return "pre_ltr_correct_overturned", tags
    if correct_anchor_count >= 3 and (selected_semantic - correct_semantic >= 0.50):
        return "semantic_over_struct_anchor", tags
    if correct_param - selected_param >= 0.10 and (selected_hybrid - correct_hybrid >= 0.50):
        return "hybrid_over_param", tags
    if correct_feature - selected_feature >= 0.15 and (selected_semantic - correct_semantic >= 0.50):
        return "semantic_over_feature_alignment", tags
    if correct_anchor_count == 0 and selected_conflict_count == 0:
        return "structure_signal_sparse", tags
    if correct_rank and correct_rank > 10:
        return "correct_low_in_snapshot", tags
    return "other_in_pool_ltr_miss", tags


def build_r2_ltr_diagnostics(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bucket_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    province_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    bucket_province_counts: dict[str, Counter[str]] = defaultdict(Counter)
    total_details = 0

    for province_result in payload.get("results", []) or []:
        province = str(province_result.get("province") or "")
        for detail in province_result.get("details", []) or []:
            if not isinstance(detail, dict):
                continue
            total_details += 1
            is_r2, r2_type = _is_r2_detail(detail)
            if not is_r2:
                continue

            expected_ids = _expected_ids(detail)
            expected_set = set(expected_ids)
            candidates = _candidate_snapshots(detail)
            correct_candidate, correct_rank = _find_candidate(candidates, expected_ids)
            post_ltr_top1_id = str(detail.get("post_ltr_top1_id") or detail.get("algo_id") or "").strip()
            selected_candidate, selected_rank = _find_candidate(candidates, [post_ltr_top1_id])
            if not selected_candidate and candidates:
                selected_candidate = candidates[0]
                selected_rank = 1

            recall_rank = _recall_rank(detail)
            bucket, tags = classify_r2_bucket(
                r2_type=r2_type,
                recall_rank=recall_rank,
                candidates=candidates,
                correct_candidate=correct_candidate,
                selected_candidate=selected_candidate,
                correct_rank=correct_rank,
                selected_rank=selected_rank,
            )

            correct_quota_id = _first(expected_ids)
            correct_name = _candidate_name(correct_candidate) or str(_first(detail.get("stored_names") or []))
            selected_name = _candidate_name(selected_candidate) or str(detail.get("algo_name") or "")

            correct_param = _score(correct_candidate, "param_score")
            selected_param = _score(selected_candidate, "param_score")
            correct_feature = _score(correct_candidate, "feature_alignment_score")
            selected_feature = _score(selected_candidate, "feature_alignment_score")
            correct_manual = _score(correct_candidate, "manual_structured_score")
            selected_manual = _score(selected_candidate, "manual_structured_score")
            correct_rerank = _score(correct_candidate, "rerank_score")
            selected_rerank = _score(selected_candidate, "rerank_score")
            correct_ltr = _score(correct_candidate, "ltr_score")
            selected_ltr = _score(selected_candidate, "ltr_score")
            correct_hybrid_z = _score(correct_candidate, "hybrid_zscore")
            selected_hybrid_z = _score(selected_candidate, "hybrid_zscore")
            correct_semantic_z = _score(correct_candidate, "semantic_rerank_zscore")
            selected_semantic_z = _score(selected_candidate, "semantic_rerank_zscore")

            row = {
                "province": province,
                "bill_id": str(detail.get("bill_id") or detail.get("sample_id") or ""),
                "bill_name": str(detail.get("bill_name") or ""),
                "specialty": str(detail.get("specialty") or ""),
                "correct_quota_id": correct_quota_id,
                "correct_quota_name": str(_first(detail.get("stored_names") or [])),
                "predicted_quota_id": str(detail.get("algo_id") or ""),
                "predicted_quota_name": str(detail.get("algo_name") or ""),
                "pre_ltr_top1_id": str(detail.get("pre_ltr_top1_id") or ""),
                "post_ltr_top1_id": post_ltr_top1_id,
                "post_cgr_top1_id": str(detail.get("post_cgr_top1_id") or ""),
                "post_final_top1_id": str(detail.get("post_final_top1_id") or detail.get("algo_id") or ""),
                "recall_rank": recall_rank if recall_rank is not None else "",
                "candidate_count": len(candidates),
                "correct_snapshot_rank": correct_rank,
                "selected_snapshot_rank": selected_rank,
                "r2_type": r2_type,
                "bucket": bucket,
                "correct_name": correct_name,
                "selected_name": selected_name,
                "correct_param_score": _round(correct_param),
                "selected_param_score": _round(selected_param),
                "param_gap_selected_minus_correct": _round(selected_param - correct_param),
                "correct_feature_alignment_score": _round(correct_feature),
                "selected_feature_alignment_score": _round(selected_feature),
                "feature_gap_selected_minus_correct": _round(selected_feature - correct_feature),
                "correct_manual_structured_score": _round(correct_manual),
                "selected_manual_structured_score": _round(selected_manual),
                "manual_structured_gap_selected_minus_correct": _round(selected_manual - correct_manual),
                "correct_rerank_score": _round(correct_rerank),
                "selected_rerank_score": _round(selected_rerank),
                "rerank_gap_selected_minus_correct": _round(selected_rerank - correct_rerank),
                "correct_ltr_score": _round(correct_ltr),
                "selected_ltr_score": _round(selected_ltr),
                "ltr_gap_selected_minus_correct": _round(selected_ltr - correct_ltr),
                "correct_hybrid_zscore": _round(correct_hybrid_z),
                "selected_hybrid_zscore": _round(selected_hybrid_z),
                "hybrid_z_gap_selected_minus_correct": _round(selected_hybrid_z - correct_hybrid_z),
                "correct_semantic_zscore": _round(correct_semantic_z),
                "selected_semantic_zscore": _round(selected_semantic_z),
                "semantic_z_gap_selected_minus_correct": _round(selected_semantic_z - correct_semantic_z),
                "correct_struct_anchor_count": _struct_anchor_count(correct_candidate),
                "selected_struct_conflict_count": _struct_conflict_count(selected_candidate),
                "tags": "|".join(tags),
            }
            rows.append(row)
            bucket_counts[bucket] += 1
            type_counts[r2_type] += 1
            province_counts[province] += 1
            bucket_province_counts[bucket][province] += 1
            tag_counts.update(tags)

    rows.sort(
        key=lambda row: (
            str(row["bucket"]),
            str(row["r2_type"]),
            str(row["province"]),
            str(row["bill_id"]),
        )
    )
    summary = {
        "total_details": total_details,
        "r2_total": len(rows),
        "r2_type_counts": dict(sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))),
        "bucket_counts": dict(sorted(bucket_counts.items(), key=lambda item: (-item[1], item[0]))),
        "province_counts": dict(sorted(province_counts.items(), key=lambda item: (-item[1], item[0]))),
        "tag_counts": dict(sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))),
        "bucket_top_provinces": {
            bucket: dict(counter.most_common(5))
            for bucket, counter in sorted(bucket_province_counts.items())
        },
    }
    return rows, summary


def write_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_summary(summary: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export compact R2/LTR diagnostics from benchmark latest_result JSON.")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT), help="Benchmark latest_result JSON path")
    parser.add_argument("--output-csv", type=str, default=str(DEFAULT_OUTPUT_CSV), help="Output diagnostics CSV path")
    parser.add_argument("--summary-output", type=str, default=str(DEFAULT_SUMMARY_JSON), help="Output summary JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = _load_json(Path(args.input))
    rows, summary = build_r2_ltr_diagnostics(payload)
    csv_path = write_csv(rows, Path(args.output_csv))
    summary_path = write_summary(summary, Path(args.summary_output))
    print(f"[R2_LTR_DIAG] input: {args.input}")
    print(f"[R2_LTR_DIAG] r2_total: {summary['r2_total']}")
    print(f"[R2_LTR_DIAG] r2_type_counts: {summary['r2_type_counts']}")
    print(f"[R2_LTR_DIAG] bucket_counts: {summary['bucket_counts']}")
    print(f"[R2_LTR_DIAG] output_csv: {csv_path}")
    print(f"[R2_LTR_DIAG] summary_json: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
