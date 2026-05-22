from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_TRIAL_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial"
DEFAULT_TRIAL_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_summary.md"
DEFAULT_FLIP_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_flips.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_buckets.csv"
DEFAULT_GATE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_safety_gate_candidates.csv"
DEFAULT_EXAMPLE_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_gain_loss_audit_examples.jsonl"

PARAM_EVIDENCE_FEATURES = {
    "param_exact_count",
    "param_tier_up_count",
    "dn_exact",
    "dn_tier_up",
    "cable_section_exact",
    "cable_section_tier_up",
    "cable_cores_exact",
    "circuits_exact",
    "circuits_tier_up",
    "concrete_grade_exact",
    "thickness_exact",
    "thickness_tier_up",
    "width_height_exact",
    "width_height_tier_match",
}

STRUCTURAL_FEATURES = {
    "field_score",
    "numeric_score",
    "domain_rule_score",
    "family_match",
    "action_match",
    "material_match",
    "connection_match",
    "install_method_match",
    "candidate_family_present",
    "param_exact_count",
    "dn_exact",
    "width_height_exact",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if line:
                yield line_no, json.loads(line)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _delta_map(row: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in ((row.get("feature_delta") or {}).get("deltas") or []):
        feature = _clean(item.get("feature"))
        if feature:
            result[feature] = _to_float(item.get("delta"))
    return result


def _candidate(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def _score_margin(row: dict[str, Any]) -> float:
    baseline = _candidate(row, "baseline_top")
    model = _candidate(row, "model_top")
    return round(_to_float(model.get("ltr_score")) - _to_float(baseline.get("ltr_score")), 8)


def _family_relation(row: dict[str, Any]) -> str:
    feature_delta = row.get("feature_delta") or {}
    same_family = bool(feature_delta.get("same_family"))
    baseline_family = _clean(_candidate(row, "baseline_top").get("candidate_family"))
    model_family = _clean(_candidate(row, "model_top").get("candidate_family"))
    query_family = _clean(row.get("query_family"))
    if same_family:
        return "same_family"
    if not model_family:
        return "model_family_empty"
    if query_family and model_family != query_family:
        return "cross_family_override"
    if baseline_family and model_family != baseline_family:
        return "baseline_family_changed"
    return "family_changed_unclear"


def _book_relation(row: dict[str, Any]) -> str:
    feature_delta = row.get("feature_delta") or {}
    same_book = bool(feature_delta.get("same_book"))
    baseline_book = _clean(_candidate(row, "baseline_top").get("quota_book"))
    model_book = _clean(_candidate(row, "model_top").get("quota_book"))
    if same_book:
        return "same_book"
    if not baseline_book or not model_book:
        return "book_unknown"
    return "cross_book_override"


def _rank_bucket(rank: int) -> str:
    if rank <= 1:
        return "rank1"
    if rank <= 3:
        return "rank2_3"
    if rank <= 5:
        return "rank4_5"
    if rank <= 10:
        return "rank6_10"
    if rank <= 20:
        return "rank11_20"
    return "rank_gt20"


def _param_relation(row: dict[str, Any], deltas: dict[str, float]) -> str:
    feature_delta = row.get("feature_delta") or {}
    model_param_conflict = int(_to_float(feature_delta.get("model_param_conflict_count")))
    if model_param_conflict > 0:
        return "model_param_conflict"
    lost = [feature for feature in PARAM_EVIDENCE_FEATURES if deltas.get(feature, 0.0) < 0]
    gained = [feature for feature in PARAM_EVIDENCE_FEATURES if deltas.get(feature, 0.0) > 0]
    if lost and gained:
        return "mixed_param_evidence"
    if lost:
        return "param_evidence_lost"
    if gained:
        return "param_evidence_gained"
    return "no_param_delta"


def _override_pattern(row: dict[str, Any], deltas: dict[str, float]) -> str:
    family_relation = _family_relation(row)
    book_relation = _book_relation(row)
    param_relation = _param_relation(row, deltas)
    model_rank = int(_to_float(_candidate(row, "model_top").get("candidate_rank")))
    lexical_gain = deltas.get("bm25_score", 0.0) > 0.10 or deltas.get("token_overlap", 0.0) > 0.05
    structural_loss = (
        any(deltas.get(feature, 0.0) < 0 for feature in STRUCTURAL_FEATURES)
        or deltas.get("family_conflict", 0.0) > 0
        or int(_to_float((row.get("feature_delta") or {}).get("model_family_conflict"))) > 0
    )
    if family_relation in {"cross_family_override", "baseline_family_changed"}:
        return "cross_family_override"
    if family_relation == "model_family_empty":
        return "model_family_empty_override"
    if book_relation == "cross_book_override":
        return "cross_book_override"
    if param_relation in {"model_param_conflict", "param_evidence_lost"}:
        return param_relation
    if lexical_gain and structural_loss:
        return "lexical_over_structure"
    if model_rank >= 10:
        return "deep_rank_override"
    if family_relation == "same_family" and book_relation == "same_book":
        return "same_family_book_sort"
    return "other"


def _safety_conditions(row: dict[str, Any], enriched: dict[str, Any]) -> dict[str, bool]:
    deltas = _delta_map(row)
    feature_delta = row.get("feature_delta") or {}
    model_rank = int(_to_float(_candidate(row, "model_top").get("candidate_rank")))
    lexical_gain = deltas.get("bm25_score", 0.0) > 0.10 or deltas.get("token_overlap", 0.0) > 0.05
    structural_loss = (
        any(deltas.get(feature, 0.0) < 0 for feature in STRUCTURAL_FEATURES)
        or deltas.get("family_conflict", 0.0) > 0
        or int(_to_float(feature_delta.get("model_family_conflict"))) > 0
    )
    return {
        "family_conflict_block": (
            enriched["family_relation"] in {"cross_family_override", "baseline_family_changed"}
            or int(_to_float(feature_delta.get("model_family_conflict"))) > 0
        ),
        "book_conflict_block": enriched["book_relation"] == "cross_book_override" or int(_to_float(feature_delta.get("model_book_conflict"))) > 0,
        "param_loss_block": enriched["param_relation"] in {"model_param_conflict", "param_evidence_lost"},
        "lexical_structure_block": lexical_gain and structural_loss,
        "deep_unsafe_block": model_rank >= 10 and (
            enriched["family_relation"] != "same_family"
            or enriched["param_relation"] in {"model_param_conflict", "param_evidence_lost"}
            or lexical_gain and structural_loss
        ),
        "strict_safe_override_only": not (
            enriched["family_relation"] == "same_family"
            and enriched["book_relation"] in {"same_book", "book_unknown"}
            and enriched["param_relation"] not in {"model_param_conflict", "param_evidence_lost"}
        ),
    }


def _enrich_flip(row: dict[str, Any]) -> dict[str, Any]:
    deltas = _delta_map(row)
    baseline = _candidate(row, "baseline_top")
    model = _candidate(row, "model_top")
    hit1_delta = int(_to_float(row.get("hit1_delta")))
    outcome = "gain" if hit1_delta > 0 else "loss"
    family_relation = _family_relation(row)
    book_relation = _book_relation(row)
    param_relation = _param_relation(row, deltas)
    model_rank = int(_to_float(model.get("candidate_rank")))
    baseline_rank = int(_to_float(baseline.get("candidate_rank")))
    enriched = {
        "split": _clean(row.get("split")),
        "outcome": outcome,
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": _clean(row.get("source_file")),
        "project_name": _clean(row.get("project_name")),
        "province": _clean(row.get("province")),
        "query_family": _clean(row.get("query_family")) or "<empty>",
        "query": _clean(row.get("query")),
        "expected_ids": "|".join(str(value) for value in (row.get("expected_ids") or [])) if isinstance(row.get("expected_ids"), list) else _clean(row.get("expected_ids")),
        "baseline_positive_rank": row.get("baseline_positive_rank"),
        "ltr_positive_rank": row.get("ltr_positive_rank"),
        "baseline_quota_id": _clean(baseline.get("quota_id")),
        "baseline_name": _clean(baseline.get("quota_name")),
        "baseline_rank": baseline_rank,
        "baseline_family": _clean(baseline.get("candidate_family")) or "<empty>",
        "baseline_book": _clean(baseline.get("quota_book")) or "<empty>",
        "baseline_ltr_score": _to_float(baseline.get("ltr_score")),
        "model_quota_id": _clean(model.get("quota_id")),
        "model_name": _clean(model.get("quota_name")),
        "model_rank": model_rank,
        "model_rank_bucket": _rank_bucket(model_rank),
        "model_family": _clean(model.get("candidate_family")) or "<empty>",
        "model_book": _clean(model.get("quota_book")) or "<empty>",
        "model_ltr_score": _to_float(model.get("ltr_score")),
        "score_margin": _score_margin(row),
        "rank_delta": model_rank - baseline_rank,
        "family_relation": family_relation,
        "book_relation": book_relation,
        "param_relation": param_relation,
        "override_pattern": _override_pattern(row, deltas),
        "model_family_conflict": int(_to_float((row.get("feature_delta") or {}).get("model_family_conflict"))),
        "model_book_conflict": int(_to_float((row.get("feature_delta") or {}).get("model_book_conflict"))),
        "model_param_conflict_count": int(_to_float((row.get("feature_delta") or {}).get("model_param_conflict_count"))),
        "bm25_delta": round(deltas.get("bm25_score", 0.0), 6),
        "token_overlap_delta": round(deltas.get("token_overlap", 0.0), 6),
        "field_score_delta": round(deltas.get("field_score", 0.0), 6),
        "numeric_score_delta": round(deltas.get("numeric_score", 0.0), 6),
        "param_exact_delta": round(deltas.get("param_exact_count", 0.0), 6),
        "dn_exact_delta": round(deltas.get("dn_exact", 0.0), 6),
        "important_deltas": json.dumps((row.get("feature_delta") or {}).get("deltas") or [], ensure_ascii=False),
    }
    for name, value in _safety_conditions(row, enriched).items():
        enriched[name] = value
    return enriched


def _load_flips(trial_dir: Path, splits: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in splits:
        path = trial_dir / f"eval_{split}_hit1_flips.jsonl"
        for _line_no, row in _iter_jsonl(path):
            rows.append(_enrich_flip(row))
    return rows


def _bucket_rows(flips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_fields = [
        "query_family",
        "province",
        "source_file",
        "family_relation",
        "book_relation",
        "param_relation",
        "override_pattern",
        "model_rank_bucket",
    ]
    counters: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[tuple[str, str]] = Counter()
    for row in flips:
        split = row["split"]
        outcome = row["outcome"]
        totals[(split, outcome)] += 1
        for field in bucket_fields:
            counters[(split, outcome, field)][_clean(row.get(field)) or "<empty>"] += 1

    result: list[dict[str, Any]] = []
    for (split, outcome, field), counter in sorted(counters.items()):
        total = totals[(split, outcome)]
        for key, count in counter.most_common(30):
            result.append(
                {
                    "split": split,
                    "outcome": outcome,
                    "bucket": field,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )
    return result


def _safety_gate_rows(flips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gate_names = [
        "family_conflict_block",
        "book_conflict_block",
        "param_loss_block",
        "lexical_structure_block",
        "deep_unsafe_block",
        "strict_safe_override_only",
    ]
    rows: list[dict[str, Any]] = []
    for gate in gate_names:
        for split in sorted({row["split"] for row in flips}):
            split_rows = [row for row in flips if row["split"] == split]
            saved_loss = sum(1 for row in split_rows if row["outcome"] == "loss" and bool(row.get(gate)))
            blocked_gain = sum(1 for row in split_rows if row["outcome"] == "gain" and bool(row.get(gate)))
            losses = sum(1 for row in split_rows if row["outcome"] == "loss")
            gains = sum(1 for row in split_rows if row["outcome"] == "gain")
            rows.append(
                {
                    "gate": gate,
                    "split": split,
                    "saved_loss": saved_loss,
                    "blocked_gain": blocked_gain,
                    "net_flip_effect": saved_loss - blocked_gain,
                    "loss_coverage": _rate(saved_loss, losses),
                    "gain_block_rate": _rate(blocked_gain, gains),
                    "recommendation": _gate_recommendation(gate, saved_loss, blocked_gain, losses, gains),
                }
            )
    return rows


def _gate_recommendation(gate: str, saved_loss: int, blocked_gain: int, losses: int, gains: int) -> str:
    if saved_loss <= 0:
        return "not_useful_on_flips"
    if blocked_gain == 0:
        return "good_candidate"
    if saved_loss > blocked_gain and blocked_gain / max(gains, 1) <= 0.15:
        return "candidate_needs_full_whatif"
    if gate == "strict_safe_override_only":
        return "too_broad_use_as_upper_bound_only"
    return "too_much_gain_loss_tradeoff"


def _summary(trial_summary: dict[str, Any], flips: list[dict[str, Any]], gate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_stats: list[dict[str, Any]] = []
    eval_by_split = {item.get("split"): item for item in trial_summary.get("evaluations", [])}
    for split in sorted({row["split"] for row in flips}):
        split_rows = [row for row in flips if row["split"] == split]
        gains = sum(1 for row in split_rows if row["outcome"] == "gain")
        losses = sum(1 for row in split_rows if row["outcome"] == "loss")
        eval_row = eval_by_split.get(split, {})
        split_stats.append(
            {
                "split": split,
                "gain": gains,
                "loss": losses,
                "net": gains - losses,
                "matrix_groups": eval_row.get("groups"),
                "eligible_anchor_rows": eval_row.get("eligible_anchor_rows"),
                "baseline_top1_on_eligible": eval_row.get("baseline_hit1_rate_on_eligible"),
                "ltr_top1_on_eligible": eval_row.get("ltr_hit1_rate_on_eligible"),
                "top80_recall_rate": eval_row.get("top80_recall_rate"),
            }
        )
    useful_gates = [
        row
        for row in gate_rows
        if row["recommendation"] in {"good_candidate", "candidate_needs_full_whatif"} and row["saved_loss"] > 0
    ]
    return {
        "splits": split_stats,
        "total_flips": len(flips),
        "total_gain": sum(1 for row in flips if row["outcome"] == "gain"),
        "total_loss": sum(1 for row in flips if row["outcome"] == "loss"),
        "raw_ltr_has_net_gain": all(item["net"] > 0 for item in split_stats),
        "needs_safety_gate": any(item["loss"] > 0 for item in split_stats),
        "candidate_gate_count": len(useful_gates),
        "candidate_gates": useful_gates,
        "recommended_next_stage": "Stage 6.9 eval-only safety gate what-if on all heldout/hard groups, not only flips.",
    }


def _example_rows(flips: list[dict[str, Any]], limit_per_bucket: int = 3) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str]] = Counter()
    priority = ["loss", "gain"]
    for outcome in priority:
        for row in flips:
            if row["outcome"] != outcome:
                continue
            key = (row["split"], outcome, row["override_pattern"])
            if seen[key] >= limit_per_bucket:
                continue
            seen[key] += 1
            result.append(row)
    return result


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Query-Anchored LTR Gain/Loss Audit",
        "",
        "Stage 6.8 eval-only audit. It analyzes heldout/hard Top1 gains and losses from the offline LTR trial. No training, no search integration, no rerank switch.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["total_gain", summary["total_gain"]],
                ["total_loss", summary["total_loss"]],
                ["raw_ltr_has_net_gain", summary["raw_ltr_has_net_gain"]],
                ["needs_safety_gate", summary["needs_safety_gate"]],
                ["candidate_gate_count", summary["candidate_gate_count"]],
                ["recommended_next_stage", summary["recommended_next_stage"]],
            ]
        ),
        "",
        "## Split Metrics",
        "",
        _md_table(
            [
                ["split", "gain", "loss", "net", "baseline_top1_all", "ltr_top1_all", "top80_recall"],
                *[
                    [
                        item["split"],
                        item["gain"],
                        item["loss"],
                        item["net"],
                        item["baseline_top1_on_eligible"],
                        item["ltr_top1_on_eligible"],
                        item["top80_recall_rate"],
                    ]
                    for item in summary["splits"]
                ],
            ]
        ),
        "",
        "## Loss Buckets",
        "",
        _md_table(
            [["split", "bucket", "key", "count", "rate"]]
            + [
                [row["split"], row["bucket"], row["key"], row["count"], row["rate"]]
                for row in report["bucket_rows"]
                if row["outcome"] == "loss"
            ][:40]
        ),
        "",
        "## Safety Gate Candidates",
        "",
        _md_table(
            [["gate", "split", "saved_loss", "blocked_gain", "net", "recommendation"]]
            + [
                [row["gate"], row["split"], row["saved_loss"], row["blocked_gain"], row["net_flip_effect"], row["recommendation"]]
                for row in report["safety_gate_rows"]
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6.8 audit LTR gain/loss flips without training")
    parser.add_argument("--trial-dir", default=str(DEFAULT_TRIAL_DIR))
    parser.add_argument("--trial-summary", default=str(DEFAULT_TRIAL_SUMMARY))
    parser.add_argument("--splits", default="heldout,hard")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--flip-csv", default=str(DEFAULT_FLIP_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--gate-csv", default=str(DEFAULT_GATE_CSV))
    parser.add_argument("--example-jsonl", default=str(DEFAULT_EXAMPLE_JSONL))
    args = parser.parse_args()

    started = time.perf_counter()
    splits = [part.strip() for part in args.splits.split(",") if part.strip()]
    trial_dir = Path(args.trial_dir)
    trial_summary = _read_json(Path(args.trial_summary))
    flips = _load_flips(trial_dir, splits)
    bucket_rows = _bucket_rows(flips)
    gate_rows = _safety_gate_rows(flips)
    examples = _example_rows(flips)
    summary = _summary(trial_summary, flips, gate_rows)
    report = {
        "stage": "Goal LTR v1 / stage 6.8 gain/loss audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "trial_dir": str(trial_dir),
        "trial_summary": str(Path(args.trial_summary)),
        "splits": splits,
        "summary": summary,
        "bucket_rows": bucket_rows,
        "safety_gate_rows": gate_rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "flip_csv": str(Path(args.flip_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "gate_csv": str(Path(args.gate_csv)),
            "example_jsonl": str(Path(args.example_jsonl)),
        },
    }

    flip_fields = [
        "split",
        "outcome",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query_family",
        "query",
        "expected_ids",
        "baseline_positive_rank",
        "ltr_positive_rank",
        "baseline_quota_id",
        "baseline_name",
        "baseline_rank",
        "baseline_family",
        "baseline_book",
        "baseline_ltr_score",
        "model_quota_id",
        "model_name",
        "model_rank",
        "model_rank_bucket",
        "model_family",
        "model_book",
        "model_ltr_score",
        "score_margin",
        "rank_delta",
        "family_relation",
        "book_relation",
        "param_relation",
        "override_pattern",
        "model_family_conflict",
        "model_book_conflict",
        "model_param_conflict_count",
        "bm25_delta",
        "token_overlap_delta",
        "field_score_delta",
        "numeric_score_delta",
        "param_exact_delta",
        "dn_exact_delta",
        "family_conflict_block",
        "book_conflict_block",
        "param_loss_block",
        "lexical_structure_block",
        "deep_unsafe_block",
        "strict_safe_override_only",
        "important_deltas",
    ]
    _write_csv(Path(args.flip_csv), flips, flip_fields)
    _write_csv(Path(args.bucket_csv), bucket_rows, ["split", "outcome", "bucket", "key", "count", "rate"])
    _write_csv(Path(args.gate_csv), gate_rows, ["gate", "split", "saved_loss", "blocked_gain", "net_flip_effect", "loss_coverage", "gain_block_rate", "recommendation"])
    _write_jsonl(Path(args.example_jsonl), examples)
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "no_training": report["no_training"],
                    "total_gain": summary["total_gain"],
                    "total_loss": summary["total_loss"],
                    "raw_ltr_has_net_gain": summary["raw_ltr_has_net_gain"],
                    "needs_safety_gate": summary["needs_safety_gate"],
                    "candidate_gate_count": summary["candidate_gate_count"],
                    "elapsed_sec": report["elapsed_sec"],
                    "recommended_next_stage": summary["recommended_next_stage"],
                },
                "splits": summary["splits"],
                "candidate_gates": summary["candidate_gates"],
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
