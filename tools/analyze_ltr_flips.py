from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flag(row: dict, key: str) -> bool:
    try:
        return int((row or {}).get(key, 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _find_snapshot(record: dict, quota_id: str) -> dict:
    target = str(quota_id or "").strip()
    if not target:
        return {}
    for snapshot in record.get("candidate_snapshots") or []:
        if str(snapshot.get("quota_id", "")).strip() == target:
            return snapshot
    return {}


def _flip_class(pre_correct: bool, post_correct: bool) -> str:
    if pre_correct and not post_correct:
        return "bad_flip"
    if not pre_correct and post_correct:
        return "good_flip"
    return "neutral_flip"


def _semantic_gap(incumbent: dict, challenger: dict) -> float:
    incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
    challenger_row = challenger.get("ltr_feature_snapshot") or {}
    return _safe_float(challenger_row.get("semantic_rerank_zscore")) - _safe_float(
        incumbent_row.get("semantic_rerank_zscore")
    )


def _hybrid_gap(incumbent: dict, challenger: dict) -> float:
    incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
    challenger_row = challenger.get("ltr_feature_snapshot") or {}
    return _safe_float(challenger_row.get("hybrid_zscore")) - _safe_float(
        incumbent_row.get("hybrid_zscore")
    )


def _param_gap(incumbent: dict, challenger: dict) -> float:
    return _safe_float(challenger.get("param_score")) - _safe_float(incumbent.get("param_score"))


def _feature_gap(incumbent: dict, challenger: dict) -> float:
    return _safe_float(challenger.get("feature_alignment_score")) - _safe_float(
        incumbent.get("feature_alignment_score")
    )


def _build_tags(incumbent: dict, challenger: dict) -> list[str]:
    incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
    challenger_row = challenger.get("ltr_feature_snapshot") or {}

    tags: list[str] = []

    if _flag(incumbent_row, "entity_match"):
        tags.append("incumbent_entity_match")
    if _flag(incumbent_row, "canonical_name_match"):
        tags.append("incumbent_canonical_match")
    if _flag(incumbent_row, "system_match"):
        tags.append("incumbent_system_match")
    if _flag(incumbent_row, "family_match"):
        tags.append("incumbent_family_match")

    if _flag(challenger_row, "entity_conflict"):
        tags.append("challenger_entity_conflict")
    if _flag(challenger_row, "canonical_name_conflict"):
        tags.append("challenger_canonical_conflict")
    if _flag(challenger_row, "system_conflict"):
        tags.append("challenger_system_conflict")
    if _flag(challenger_row, "family_conflict"):
        tags.append("challenger_family_conflict")

    if _safe_float(incumbent.get("param_score")) >= 0.98:
        tags.append("incumbent_param_strong")
    if _safe_float(incumbent.get("feature_alignment_score")) >= 0.95:
        tags.append("incumbent_feature_strong")

    if _semantic_gap(incumbent, challenger) >= 0.5:
        tags.append("challenger_semantic_advantage")
    if _hybrid_gap(incumbent, challenger) >= 0.5:
        tags.append("challenger_hybrid_advantage")
    if _param_gap(incumbent, challenger) <= -0.05:
        tags.append("challenger_param_weaker")
    if _feature_gap(incumbent, challenger) <= -0.15:
        tags.append("challenger_feature_weaker")

    incumbent_sparse = not any(
        _flag(incumbent_row, key)
        for key in ("entity_match", "canonical_name_match", "system_match", "family_match")
    )
    challenger_sparse = not any(
        _flag(challenger_row, key)
        for key in ("entity_match", "canonical_name_match", "system_match", "family_match")
    )
    if incumbent_sparse and challenger_sparse:
        tags.append("both_structure_sparse")

    if (
        _flag(incumbent_row, "entity_match")
        and _flag(incumbent_row, "canonical_name_match")
        and _flag(incumbent_row, "system_match")
    ):
        tags.append("incumbent_exact_anchor")
    elif (
        _flag(incumbent_row, "entity_match")
        and _flag(incumbent_row, "family_match")
        and _flag(incumbent_row, "system_match")
    ):
        tags.append("incumbent_family_anchor")

    return tags


def _bucket_for_bad_flip(tags: list[str]) -> str:
    tag_set = set(tags)
    if "challenger_entity_conflict" in tag_set or "challenger_canonical_conflict" in tag_set:
        return "challenger_struct_conflict"
    if "incumbent_exact_anchor" in tag_set:
        return "incumbent_exact_anchor_overturned"
    if "incumbent_family_anchor" in tag_set:
        return "incumbent_family_anchor_overturned"
    if "challenger_semantic_advantage" in tag_set and "challenger_feature_weaker" in tag_set:
        return "semantic_over_structure"
    if "challenger_hybrid_advantage" in tag_set and "challenger_param_weaker" in tag_set:
        return "hybrid_over_param"
    if "both_structure_sparse" in tag_set:
        return "both_structure_sparse"
    return "other"


def _annotate_flip(record: dict) -> dict | None:
    pre_id = str(record.get("pre_ltr_top1_id", "")).strip()
    post_id = str(record.get("post_ltr_top1_id", "")).strip()
    if not pre_id or not post_id or pre_id == post_id:
        return None

    oracle_ids = {str(value).strip() for value in (record.get("oracle_quota_ids") or []) if str(value).strip()}
    pre_correct = pre_id in oracle_ids
    post_correct = post_id in oracle_ids
    incumbent = _find_snapshot(record, pre_id)
    challenger = _find_snapshot(record, post_id)
    if not incumbent or not challenger:
        return None

    tags = _build_tags(incumbent, challenger)
    flip_class = _flip_class(pre_correct, post_correct)
    bad_flip_bucket = _bucket_for_bad_flip(tags) if flip_class == "bad_flip" else ""

    incumbent_row = incumbent.get("ltr_feature_snapshot") or {}
    challenger_row = challenger.get("ltr_feature_snapshot") or {}

    return {
        "sample_id": str(record.get("sample_id") or ""),
        "province": str(record.get("province") or ""),
        "source": str(record.get("source") or ""),
        "bill_name": str(record.get("bill_name") or ""),
        "bill_text": str(record.get("bill_text") or ""),
        "specialty": str(record.get("specialty") or ""),
        "oracle_quota_ids": sorted(oracle_ids),
        "pre_ltr_top1_id": pre_id,
        "post_ltr_top1_id": post_id,
        "pre_ltr_name": str(incumbent.get("name") or ""),
        "post_ltr_name": str(challenger.get("name") or ""),
        "pre_correct": pre_correct,
        "post_correct": post_correct,
        "flip_class": flip_class,
        "bad_flip_bucket": bad_flip_bucket,
        "miss_stage": str(record.get("miss_stage") or ""),
        "error_stage": str(record.get("error_stage") or ""),
        "error_type": str(record.get("error_type") or ""),
        "incumbent_param_score": _safe_float(incumbent.get("param_score")),
        "challenger_param_score": _safe_float(challenger.get("param_score")),
        "incumbent_feature_alignment_score": _safe_float(incumbent.get("feature_alignment_score")),
        "challenger_feature_alignment_score": _safe_float(challenger.get("feature_alignment_score")),
        "incumbent_rerank_score": _safe_float(incumbent.get("rerank_score")),
        "challenger_rerank_score": _safe_float(challenger.get("rerank_score")),
        "incumbent_ltr_score": _safe_float(incumbent.get("ltr_score")),
        "challenger_ltr_score": _safe_float(challenger.get("ltr_score")),
        "semantic_z_gap": round(_semantic_gap(incumbent, challenger), 6),
        "hybrid_z_gap": round(_hybrid_gap(incumbent, challenger), 6),
        "param_gap": round(_param_gap(incumbent, challenger), 6),
        "feature_gap": round(_feature_gap(incumbent, challenger), 6),
        "incumbent_entity_match": _flag(incumbent_row, "entity_match"),
        "incumbent_canonical_match": _flag(incumbent_row, "canonical_name_match"),
        "incumbent_system_match": _flag(incumbent_row, "system_match"),
        "incumbent_family_match": _flag(incumbent_row, "family_match"),
        "challenger_entity_conflict": _flag(challenger_row, "entity_conflict"),
        "challenger_canonical_conflict": _flag(challenger_row, "canonical_name_conflict"),
        "challenger_system_conflict": _flag(challenger_row, "system_conflict"),
        "challenger_family_conflict": _flag(challenger_row, "family_conflict"),
        "tags": tags,
    }


def _top_examples(rows: list[dict], limit: int = 10) -> list[dict]:
    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("bad_flip_bucket", ""),
            -(row.get("semantic_z_gap") or 0.0),
            -(row.get("hybrid_z_gap") or 0.0),
        ),
    )
    return [
        {
            "sample_id": row["sample_id"],
            "province": row["province"],
            "bill_text": row["bill_text"],
            "pre_ltr_top1_id": row["pre_ltr_top1_id"],
            "post_ltr_top1_id": row["post_ltr_top1_id"],
            "bad_flip_bucket": row["bad_flip_bucket"],
            "tags": row["tags"],
        }
        for row in ordered[:limit]
    ]


def summarize(annotated: list[dict]) -> dict:
    by_class = Counter(row["flip_class"] for row in annotated)
    by_province = Counter(row["province"] for row in annotated)
    bad_rows = [row for row in annotated if row["flip_class"] == "bad_flip"]
    good_rows = [row for row in annotated if row["flip_class"] == "good_flip"]
    neutral_rows = [row for row in annotated if row["flip_class"] == "neutral_flip"]

    bad_bucket_counts = Counter(row["bad_flip_bucket"] for row in bad_rows if row["bad_flip_bucket"])
    bad_tag_counts = Counter(tag for row in bad_rows for tag in row["tags"])
    good_tag_counts = Counter(tag for row in good_rows for tag in row["tags"])

    return {
        "total_flips": len(annotated),
        "flip_class_counts": dict(sorted(by_class.items())),
        "province_flip_counts": dict(sorted(by_province.items())),
        "bad_flip_bucket_counts": dict(sorted(bad_bucket_counts.items(), key=lambda item: (-item[1], item[0]))),
        "bad_flip_tag_counts": dict(sorted(bad_tag_counts.items(), key=lambda item: (-item[1], item[0]))),
        "good_flip_tag_counts": dict(sorted(good_tag_counts.items(), key=lambda item: (-item[1], item[0]))),
        "bad_flip_examples": _top_examples(bad_rows, limit=12),
        "good_flip_examples": _top_examples(good_rows, limit=8),
        "neutral_flip_examples": _top_examples(neutral_rows, limit=8),
    }


def _default_output_path(details_path: Path, suffix: str) -> Path:
    stem = details_path.name
    if stem.endswith(".jsonl"):
        stem = stem[:-6]
    return details_path.with_name(f"{stem}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze LTR flips from real-eval details jsonl")
    parser.add_argument(
        "--details",
        required=True,
        help="real-eval details jsonl generated by tools/run_real_eval.py --details-out",
    )
    parser.add_argument(
        "--summary-out",
        default="",
        help="optional summary json path",
    )
    parser.add_argument(
        "--annotated-out",
        default="",
        help="optional annotated flip jsonl path",
    )
    args = parser.parse_args()

    details_path = Path(args.details)
    rows = _read_jsonl(details_path)
    annotated = [row for row in (_annotate_flip(record) for record in rows) if row is not None]
    summary = summarize(annotated)
    summary["details_path"] = str(details_path)

    summary_path = Path(args.summary_out) if args.summary_out else _default_output_path(
        details_path,
        ".ltr_flip.summary.json",
    )
    annotated_path = Path(args.annotated_out) if args.annotated_out else _default_output_path(
        details_path,
        ".ltr_flip.details.jsonl",
    )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    annotated_path.parent.mkdir(parents=True, exist_ok=True)
    with annotated_path.open("w", encoding="utf-8") as handle:
        for row in annotated:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] summary saved to: {summary_path}")
    print(f"[OK] annotated details saved to: {annotated_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
