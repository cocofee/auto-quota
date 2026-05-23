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

DEFAULT_OOF_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_OOF_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_EVAL_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "frozen_eval_details.jsonl"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_summary.md"
DEFAULT_RESIDUAL_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_rows.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_buckets.csv"
DEFAULT_EXAMPLE_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_examples.jsonl"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if line:
                yield line_no, json.loads(line)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _clean(value).lower() in {"1", "true", "yes"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


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


def _margin_bucket(margin: float) -> str:
    if margin < 0:
        return "lt_0"
    if margin < 0.5:
        return "0_0_5"
    if margin < 1.25:
        return "0_5_1_25"
    if margin < 2.0:
        return "1_25_2"
    return "gte_2"


def _load_selected_gate(summary: dict[str, Any]) -> dict[str, Any]:
    gate = ((summary.get("selection") or {}).get("selected_gate") or {})
    if not gate.get("name"):
        raise ValueError("OOF summary missing selection.selected_gate.name")
    return gate


def _residual_outcome(row: dict[str, Any]) -> str:
    baseline_hit1 = _to_bool(row.get("baseline_hit1"))
    raw_hit1 = _to_bool(row.get("raw_ltr_hit1"))
    gated_hit1 = _to_bool(row.get("gated_hit1"))
    if baseline_hit1 and not raw_hit1 and not gated_hit1:
        return "residual_loss"
    if not baseline_hit1 and raw_hit1 and not gated_hit1:
        return "blocked_gain"
    if baseline_hit1 and not raw_hit1 and gated_hit1:
        return "saved_loss"
    if not baseline_hit1 and raw_hit1 and gated_hit1:
        return "passed_gain"
    return "neutral"


def _diagnosis(row: dict[str, Any], selected_margin: float) -> str:
    outcome = _residual_outcome(row)
    gate_reason = _clean(row.get("gate_reason"))
    same_family_strong = _to_bool(row.get("same_family_strong"))
    same_family_or_empty = _to_bool(row.get("same_family_or_empty"))
    same_book_or_unknown = _to_bool(row.get("same_book_or_unknown"))
    no_family_conflict = _to_bool(row.get("no_family_conflict"))
    no_book_conflict = _to_bool(row.get("no_book_conflict"))
    no_param_conflict = _to_bool(row.get("no_param_conflict"))
    query_family_conflict = _to_bool(row.get("query_family_conflict"))
    model_family_empty = _to_bool(row.get("model_family_empty"))
    strong_family_book_param = _to_bool(row.get("strong_family_book_param"))
    ltr_param_support = _to_bool(row.get("ltr_param_support"))
    baseline_param_support = _to_bool(row.get("baseline_param_support"))
    lexical_over_structure = _to_bool(row.get("lexical_over_structure"))
    query_family = _clean(row.get("query_family"))
    baseline_family = _clean(row.get("baseline_top_family"))
    raw_family = _clean(row.get("raw_ltr_top_family"))
    margin = _to_float(row.get("score_margin"))

    if outcome == "blocked_gain":
        if query_family_conflict or not no_family_conflict:
            return "blocked_gain_real_family_conflict_or_family_rule_gap"
        if not no_param_conflict:
            return "blocked_gain_param_conflict_rule_gap"
        if same_family_or_empty and same_book_or_unknown and not same_family_strong:
            if query_family in {"<empty>", ""} and baseline_family == "<empty>" and raw_family == "<empty>":
                return "blocked_gain_non_install_empty_family_feature_gap"
            return "blocked_gain_family_empty_or_missing_feature_gap"
        if no_family_conflict and no_book_conflict and no_param_conflict and margin < selected_margin:
            return "blocked_gain_margin_too_high_gate_too_coarse"
        return "blocked_gain_other_gate_too_coarse"

    if outcome == "residual_loss":
        if gate_reason == "strong_same_family_book_no_conflict":
            if not ltr_param_support and not baseline_param_support:
                return "residual_loss_same_family_book_missing_param_subtype_feature"
            if ltr_param_support != baseline_param_support:
                return "residual_loss_param_support_direction_missing"
            return "residual_loss_same_family_book_gate_too_broad"
        if gate_reason == "large_margin_no_family_param_conflict":
            if lexical_over_structure:
                return "residual_loss_large_margin_lexical_over_structure"
            if model_family_empty:
                return "residual_loss_large_margin_family_empty"
            return "residual_loss_large_margin_gate_too_broad"
        if strong_family_book_param:
            return "residual_loss_strong_rule_too_broad"
        return "residual_loss_other"

    if outcome == "saved_loss":
        return "saved_loss_gate_helpful"
    if outcome == "passed_gain":
        return "passed_gain_gate_helpful"
    return "neutral"


def _load_rows(paths: list[Path], selected_variant: str, selected_margin: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line_no, row in _iter_jsonl(path):
            if _clean(row.get("variant")) != selected_variant:
                continue
            outcome = _residual_outcome(row)
            if outcome == "neutral":
                continue
            raw_rank = _to_int(row.get("raw_ltr_top_rank"))
            margin = _to_float(row.get("score_margin"))
            item = {
                "split": _clean(row.get("split")),
                "outcome": outcome,
                "diagnosis": _diagnosis(row, selected_margin),
                "line_no": line_no,
                "group_id": _clean(row.get("group_id")),
                "sample_id": _clean(row.get("sample_id")),
                "source_file": _clean(row.get("source_file")),
                "province": _clean(row.get("province")),
                "query_family": _clean(row.get("query_family")) or "<empty>",
                "query": _clean(row.get("query")),
                "expected_ids": _clean(row.get("expected_ids")),
                "gate_reason": _clean(row.get("gate_reason")),
                "gate_allowed": _to_bool(row.get("gate_allowed")),
                "score_margin": round(margin, 8),
                "margin_bucket": _margin_bucket(margin),
                "raw_ltr_top_rank": raw_rank,
                "raw_rank_bucket": _rank_bucket(raw_rank),
                "same_family_strong": _to_bool(row.get("same_family_strong")),
                "same_family_or_empty": _to_bool(row.get("same_family_or_empty")),
                "same_book_or_unknown": _to_bool(row.get("same_book_or_unknown")),
                "no_family_conflict": _to_bool(row.get("no_family_conflict")),
                "no_book_conflict": _to_bool(row.get("no_book_conflict")),
                "no_param_conflict": _to_bool(row.get("no_param_conflict")),
                "query_family_conflict": _to_bool(row.get("query_family_conflict")),
                "model_family_empty": _to_bool(row.get("model_family_empty")),
                "strong_family_book_param": _to_bool(row.get("strong_family_book_param")),
                "ltr_param_support": _to_bool(row.get("ltr_param_support")),
                "baseline_param_support": _to_bool(row.get("baseline_param_support")),
                "lexical_over_structure": _to_bool(row.get("lexical_over_structure")),
                "baseline_positive_rank": row.get("baseline_positive_rank"),
                "raw_ltr_positive_rank": row.get("raw_ltr_positive_rank"),
                "gated_positive_rank": row.get("gated_positive_rank"),
                "baseline_top_id": _clean(row.get("baseline_top_id")),
                "baseline_top_name": _clean(row.get("baseline_top_name")),
                "baseline_top_family": _clean(row.get("baseline_top_family")) or "<empty>",
                "baseline_top_book": _clean(row.get("baseline_top_book")) or "<empty>",
                "raw_ltr_top_id": _clean(row.get("raw_ltr_top_id")),
                "raw_ltr_top_name": _clean(row.get("raw_ltr_top_name")),
                "raw_ltr_top_family": _clean(row.get("raw_ltr_top_family")) or "<empty>",
                "raw_ltr_top_book": _clean(row.get("raw_ltr_top_book")) or "<empty>",
                "gated_top_id": _clean(row.get("gated_top_id")),
                "gated_top_name": _clean(row.get("gated_top_name")),
            }
            rows.append(item)
    return rows


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "diagnosis",
        "query_family",
        "gate_reason",
        "margin_bucket",
        "raw_rank_bucket",
        "source_file",
        "province",
        "raw_ltr_top_family",
        "raw_ltr_top_book",
    ]
    counters: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[tuple[str, str]] = Counter()
    for row in rows:
        split = row["split"]
        outcome = row["outcome"]
        totals[(split, outcome)] += 1
        for field in fields:
            counters[(split, outcome, field)][_clean(row.get(field)) or "<empty>"] += 1

    result: list[dict[str, Any]] = []
    for (split, outcome, field), counter in sorted(counters.items()):
        total = totals[(split, outcome)]
        for key, count in counter.most_common(20):
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


def _summary(rows: list[dict[str, Any]], selected_gate: dict[str, Any]) -> dict[str, Any]:
    split_rows: list[dict[str, Any]] = []
    for split in sorted({row["split"] for row in rows}):
        subset = [row for row in rows if row["split"] == split]
        counts = Counter(row["outcome"] for row in subset)
        diag_blocked = Counter(row["diagnosis"] for row in subset if row["outcome"] == "blocked_gain")
        diag_loss = Counter(row["diagnosis"] for row in subset if row["outcome"] == "residual_loss")
        split_rows.append(
            {
                "split": split,
                "blocked_gain": counts["blocked_gain"],
                "residual_loss": counts["residual_loss"],
                "saved_loss": counts["saved_loss"],
                "passed_gain": counts["passed_gain"],
                "top_blocked_gain_diagnosis": diag_blocked.most_common(3),
                "top_residual_loss_diagnosis": diag_loss.most_common(3),
            }
        )

    blocked = [row for row in rows if row["outcome"] == "blocked_gain"]
    losses = [row for row in rows if row["outcome"] == "residual_loss"]
    blocked_feature_gap = sum(1 for row in blocked if "feature_gap" in row["diagnosis"])
    blocked_gate_coarse = sum(1 for row in blocked if "gate_too_coarse" in row["diagnosis"])
    residual_feature_gap = sum(1 for row in losses if "missing" in row["diagnosis"] or "direction" in row["diagnosis"])
    residual_gate_broad = sum(1 for row in losses if "gate_too_broad" in row["diagnosis"])

    if blocked and blocked_gate_coarse >= blocked_feature_gap:
        main_blocked = "gate_too_coarse_or_margin_too_high"
    else:
        main_blocked = "feature_gap_first"
    if losses and residual_feature_gap >= residual_gate_broad:
        main_loss = "feature_missing_subtype_param_first"
    else:
        main_loss = "gate_rule_too_broad_first"

    return {
        "selected_gate": selected_gate,
        "split_rows": split_rows,
        "total_rows": len(rows),
        "blocked_gain_total": len(blocked),
        "residual_loss_total": len(losses),
        "blocked_gain_feature_gap": blocked_feature_gap,
        "blocked_gain_gate_coarse": blocked_gate_coarse,
        "residual_loss_feature_gap": residual_feature_gap,
        "residual_loss_gate_broad": residual_gate_broad,
        "blocked_gain_main_read": main_blocked,
        "residual_loss_main_read": main_loss,
        "recommendation": "Do not connect eval-only switch yet; audit subtype/param features and redesign gate to reduce blocked gains before another freeze.",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _examples(rows: list[dict[str, Any]], limit_per_key: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str]] = Counter()
    priority = {"residual_loss": 0, "blocked_gain": 1, "saved_loss": 2, "passed_gain": 3}
    for row in sorted(rows, key=lambda item: (priority.get(item["outcome"], 9), item["split"], item["diagnosis"])):
        key = (row["split"], row["outcome"], row["diagnosis"])
        if seen[key] >= limit_per_key:
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
    split_rows = summary["split_rows"]
    loss_bucket_rows = [
        row
        for row in report["bucket_rows"]
        if row["outcome"] in {"residual_loss", "blocked_gain"} and row["bucket"] == "diagnosis"
    ][:30]
    lines = [
        "# Goal Query-Anchored LTR OOF Safety Gate Residual Audit",
        "",
        "Stage 7.2 audits the OOF-selected safety gate residual losses and blocked gains. No training, no tuning, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_gate", summary["selected_gate"].get("name")],
                ["margin", summary["selected_gate"].get("margin")],
                ["blocked_gain_total", summary["blocked_gain_total"]],
                ["residual_loss_total", summary["residual_loss_total"]],
                ["blocked_gain_main_read", summary["blocked_gain_main_read"]],
                ["residual_loss_main_read", summary["residual_loss_main_read"]],
                ["recommendation", summary["recommendation"]],
            ]
        ),
        "",
        "## Split Outcomes",
        "",
        _md_table(
            [
                ["split", "blocked_gain", "residual_loss", "saved_loss", "passed_gain", "top_blocked_gain", "top_residual_loss"],
                *[
                    [
                        row["split"],
                        row["blocked_gain"],
                        row["residual_loss"],
                        row["saved_loss"],
                        row["passed_gain"],
                        row["top_blocked_gain_diagnosis"],
                        row["top_residual_loss_diagnosis"],
                    ]
                    for row in split_rows
                ],
            ]
        ),
        "",
        "## Diagnosis Buckets",
        "",
        _md_table(
            [["split", "outcome", "diagnosis", "count", "rate"]]
            + [[row["split"], row["outcome"], row["key"], row["count"], row["rate"]] for row in loss_bucket_rows]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _residual_fields() -> list[str]:
    return [
        "split",
        "outcome",
        "diagnosis",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query_family",
        "query",
        "expected_ids",
        "gate_reason",
        "gate_allowed",
        "score_margin",
        "margin_bucket",
        "raw_ltr_top_rank",
        "raw_rank_bucket",
        "same_family_strong",
        "same_family_or_empty",
        "same_book_or_unknown",
        "no_family_conflict",
        "no_book_conflict",
        "no_param_conflict",
        "query_family_conflict",
        "model_family_empty",
        "strong_family_book_param",
        "ltr_param_support",
        "baseline_param_support",
        "lexical_over_structure",
        "baseline_positive_rank",
        "raw_ltr_positive_rank",
        "gated_positive_rank",
        "baseline_top_id",
        "baseline_top_name",
        "baseline_top_family",
        "baseline_top_book",
        "raw_ltr_top_id",
        "raw_ltr_top_name",
        "raw_ltr_top_family",
        "raw_ltr_top_book",
        "gated_top_id",
        "gated_top_name",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.2 audit OOF-selected safety gate residual losses and blocked gains")
    parser.add_argument("--oof-summary", default=str(DEFAULT_OOF_SUMMARY))
    parser.add_argument("--oof-details", default=str(DEFAULT_OOF_DETAILS))
    parser.add_argument("--eval-details", default=str(DEFAULT_EVAL_DETAILS))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--residual-csv", default=str(DEFAULT_RESIDUAL_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--example-jsonl", default=str(DEFAULT_EXAMPLE_JSONL))
    parser.add_argument("--examples-per-bucket", type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    oof_summary = _read_json(Path(args.oof_summary))
    selected_gate = _load_selected_gate(oof_summary)
    selected_variant = _clean(selected_gate.get("name"))
    selected_margin = _to_float(selected_gate.get("margin"))
    rows = _load_rows(
        [Path(args.oof_details), Path(args.eval_details)],
        selected_variant=selected_variant,
        selected_margin=selected_margin,
    )
    bucket_rows = _bucket_rows(rows)
    summary = _summary(rows, selected_gate)
    examples = _examples(rows, args.examples_per_bucket)
    report = {
        "stage": "Goal LTR v1 / stage 7.2 OOF safety gate residual audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "oof_summary": str(Path(args.oof_summary)),
        "selected_variant": selected_variant,
        "summary": summary,
        "bucket_rows": bucket_rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "residual_csv": str(Path(args.residual_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "example_jsonl": str(Path(args.example_jsonl)),
        },
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.residual_csv), rows, _residual_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, ["split", "outcome", "bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.example_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": True,
                    "no_training": True,
                    "no_search_integration": True,
                    "selected_variant": selected_variant,
                    "elapsed_sec": report["elapsed_sec"],
                    **summary,
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
