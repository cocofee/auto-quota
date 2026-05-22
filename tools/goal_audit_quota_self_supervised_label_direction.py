from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_PRETRAIN_SUMMARY_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pretrain_dry_run_summary.json"
DEFAULT_GENERATOR_SCRIPT = PROJECT_ROOT / "tools" / "goal_generate_quota_self_supervised_pair_candidates.py"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_summary.md"
DEFAULT_BY_FAMILY_PAIR_TYPE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_by_family_pair_type.csv"
DEFAULT_PARAM_DIRECTION_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_param.csv"
DEFAULT_SUBTYPE_TRANSITIONS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_subtype_transitions.csv"
DEFAULT_DIRECTION_SAMPLES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_samples.csv"

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _safe_float(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        match = NUMBER_RE.search(text)
        if not match:
            return None
        try:
            number = float(match.group(0))
        except ValueError:
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _load_pretrain_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    return {
        "available": True,
        "train_hit1_rate": summary.get("train_eval", {}).get("hit1_rate"),
        "valid_hit1_rate": summary.get("valid_eval", {}).get("hit1_rate"),
        "valid_random_like": summary.get("valid_random_like"),
        "passes_pretrain_dry_run_gate": summary.get("passes_pretrain_dry_run_gate"),
        "failure_reasons": summary.get("failure_reasons", []),
    }


def _source_direction_evidence(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "static_random_assignment_detected": False}
    text = path.read_text(encoding="utf-8")
    patterns = {
        "random_value_order": "left, right = rng.sample(values, 2)",
        "positive_from_left": "positive = rng.choice(value_to_records[left])",
        "negative_from_right": "negative = rng.choice(value_to_records[right])",
        "unordered_duplicate_guard": "unordered_ids = \"|\".join(sorted((positive[\"quota_id\"], negative[\"quota_id\"])))",
    }
    hits = {key: pattern in text for key, pattern in patterns.items()}
    return {
        "available": True,
        "script": str(path),
        **hits,
        "static_random_assignment_detected": hits["random_value_order"] and hits["positive_from_left"] and hits["negative_from_right"],
        "unordered_duplicate_guard_detected": hits["unordered_duplicate_guard"],
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _signed_balance(left_count: int, right_count: int) -> float:
    total = left_count + right_count
    return round((left_count - right_count) / total, 6) if total else 0.0


def _direction_verdict(summary: dict[str, Any]) -> str:
    numeric_count = int(summary.get("numeric_param_pairs", 0))
    positive_greater_rate = float(summary.get("positive_numeric_greater_rate", 0))
    positive_less_rate = float(summary.get("positive_numeric_less_rate", 0))
    valid_hit1 = summary.get("pretrain_valid_hit1_rate")
    source_random = bool(summary.get("static_random_assignment_detected"))
    balanced_numeric = numeric_count >= 1000 and abs(positive_greater_rate - positive_less_rate) <= 0.1
    random_like_model = isinstance(valid_hit1, (int, float)) and 0.45 <= float(valid_hit1) <= 0.55
    if source_random and balanced_numeric and random_like_model:
        return "random_order_not_rank_supervision"
    if source_random and random_like_model:
        return "likely_random_order_not_rank_supervision"
    if balanced_numeric and random_like_model:
        return "data_direction_unstable"
    return "needs_manual_review"


def _audit_pairs(rows: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    total = len(rows)
    by_pair_type = Counter(_clean(row.get("pair_type")) for row in rows)
    by_family = Counter(_clean(row.get("family")) for row in rows)
    by_training_mode = Counter(_clean(row.get("training_mode")) for row in rows)
    id_lex_positive_less = 0
    id_lex_positive_greater = 0
    positive_name_longer = 0
    positive_name_shorter = 0
    name_delta_values: list[float] = []
    numeric_param_pairs = 0
    positive_numeric_greater = 0
    positive_numeric_less = 0
    positive_numeric_equal = 0
    numeric_delta_values: list[float] = []
    subtype_rows = 0
    subtype_transition_counts: Counter[tuple[str, str]] = Counter()
    subtype_unordered_direction: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    family_pair_type_stats: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    param_direction_stats: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    samples: list[dict[str, Any]] = []

    for row in rows:
        family = _clean(row.get("family"))
        pair_type = _clean(row.get("pair_type"))
        contrast_field = _clean(row.get("contrast_field"))
        key = (family, pair_type)
        family_pair_type_stats[key]["pairs"] += 1

        positive_id = _clean(row.get("positive_id"))
        negative_id = _clean(row.get("negative_id"))
        if positive_id and negative_id:
            if positive_id < negative_id:
                id_lex_positive_less += 1
                family_pair_type_stats[key]["positive_id_lex_less"] += 1
            elif positive_id > negative_id:
                id_lex_positive_greater += 1
                family_pair_type_stats[key]["positive_id_lex_greater"] += 1

        positive_name = _clean(row.get("positive_name"))
        negative_name = _clean(row.get("negative_name"))
        name_delta = len(positive_name) - len(negative_name)
        name_delta_values.append(float(name_delta))
        if name_delta > 0:
            positive_name_longer += 1
            family_pair_type_stats[key]["positive_name_longer"] += 1
        elif name_delta < 0:
            positive_name_shorter += 1
            family_pair_type_stats[key]["positive_name_shorter"] += 1

        if pair_type == "param_contrast":
            positive_value = _safe_float(row.get("positive_contrast_value"))
            negative_value = _safe_float(row.get("negative_contrast_value"))
            param_key = (family, contrast_field, _clean(row.get("training_mode")))
            param_direction_stats[param_key]["pairs"] += 1
            if positive_value is not None and negative_value is not None:
                numeric_param_pairs += 1
                family_pair_type_stats[key]["numeric_param_pairs"] += 1
                param_direction_stats[param_key]["numeric_pairs"] += 1
                delta = positive_value - negative_value
                numeric_delta_values.append(delta)
                if delta > 0:
                    positive_numeric_greater += 1
                    family_pair_type_stats[key]["positive_numeric_greater"] += 1
                    param_direction_stats[param_key]["positive_numeric_greater"] += 1
                elif delta < 0:
                    positive_numeric_less += 1
                    family_pair_type_stats[key]["positive_numeric_less"] += 1
                    param_direction_stats[param_key]["positive_numeric_less"] += 1
                else:
                    positive_numeric_equal += 1
                    family_pair_type_stats[key]["positive_numeric_equal"] += 1
                    param_direction_stats[param_key]["positive_numeric_equal"] += 1
        elif pair_type == "subtype_contrast":
            subtype_rows += 1
            positive_value = _clean(row.get("positive_contrast_value")) or _clean(row.get("positive_subtype_key"))
            negative_value = _clean(row.get("negative_contrast_value")) or _clean(row.get("negative_subtype_key"))
            subtype_transition_counts[(positive_value, negative_value)] += 1
            unordered = tuple(sorted((positive_value, negative_value)))
            direction = f"{positive_value}>{negative_value}"
            subtype_unordered_direction[unordered][direction] += 1

        if len(samples) < top_limit and (
            pair_type == "param_contrast"
            or _clean(row.get("positive_contrast_value")) != _clean(row.get("negative_contrast_value"))
        ):
            samples.append(
                {
                    "family": family,
                    "pair_type": pair_type,
                    "contrast_field": contrast_field,
                    "positive_id": positive_id,
                    "negative_id": negative_id,
                    "positive_contrast_value": _clean(row.get("positive_contrast_value")),
                    "negative_contrast_value": _clean(row.get("negative_contrast_value")),
                    "positive_name": positive_name,
                    "negative_name": negative_name,
                }
            )

    bidirectional_unordered = 0
    one_way_unordered = 0
    bidirectional_direction_rows = 0
    for directions in subtype_unordered_direction.values():
        row_count = sum(directions.values())
        if len(directions) >= 2:
            bidirectional_unordered += 1
            bidirectional_direction_rows += row_count
        else:
            one_way_unordered += 1

    family_rows: list[dict[str, Any]] = []
    for (family, pair_type), counts in family_pair_type_stats.items():
        pairs = counts["pairs"]
        numeric_pairs = counts["numeric_param_pairs"]
        family_rows.append(
            {
                "family": family,
                "pair_type": pair_type,
                "pairs": pairs,
                "positive_id_lex_less_rate": _rate(counts["positive_id_lex_less"], counts["positive_id_lex_less"] + counts["positive_id_lex_greater"]),
                "positive_name_longer_rate": _rate(counts["positive_name_longer"], counts["positive_name_longer"] + counts["positive_name_shorter"]),
                "numeric_param_pairs": numeric_pairs,
                "positive_numeric_greater_rate": _rate(counts["positive_numeric_greater"], numeric_pairs),
                "positive_numeric_less_rate": _rate(counts["positive_numeric_less"], numeric_pairs),
                "numeric_direction_balance": _signed_balance(counts["positive_numeric_greater"], counts["positive_numeric_less"]),
                "direction_evidence": _family_direction_evidence(pair_type, counts),
            }
        )
    family_rows.sort(key=lambda row: (-int(row["pairs"]), row["family"], row["pair_type"]))

    param_rows: list[dict[str, Any]] = []
    for (family, contrast_field, training_mode), counts in param_direction_stats.items():
        numeric_pairs = counts["numeric_pairs"]
        param_rows.append(
            {
                "family": family,
                "contrast_field": contrast_field,
                "training_mode": training_mode,
                "pairs": counts["pairs"],
                "numeric_pairs": numeric_pairs,
                "positive_numeric_greater": counts["positive_numeric_greater"],
                "positive_numeric_less": counts["positive_numeric_less"],
                "positive_numeric_equal": counts["positive_numeric_equal"],
                "positive_numeric_greater_rate": _rate(counts["positive_numeric_greater"], numeric_pairs),
                "positive_numeric_less_rate": _rate(counts["positive_numeric_less"], numeric_pairs),
                "numeric_direction_balance": _signed_balance(counts["positive_numeric_greater"], counts["positive_numeric_less"]),
                "direction_evidence": "balanced_random_like" if numeric_pairs and abs(_signed_balance(counts["positive_numeric_greater"], counts["positive_numeric_less"])) <= 0.1 else "biased_or_low_support",
            }
        )
    param_rows.sort(key=lambda row: (-int(row["numeric_pairs"]), row["family"], row["contrast_field"]))

    transition_rows: list[dict[str, Any]] = []
    for (positive_value, negative_value), count in subtype_transition_counts.most_common(top_limit * 5):
        unordered = tuple(sorted((positive_value, negative_value)))
        reverse_key = (negative_value, positive_value)
        reverse_count = subtype_transition_counts.get(reverse_key, 0)
        transition_rows.append(
            {
                "positive_value": positive_value,
                "negative_value": negative_value,
                "count": count,
                "reverse_count": reverse_count,
                "has_reverse_direction": str(reverse_count > 0).lower(),
                "direction_balance": _signed_balance(count, reverse_count),
                "unordered_key": " <> ".join(unordered),
            }
        )

    summary = {
        "pairs": total,
        "by_pair_type": _counter_items(by_pair_type, total, top_limit),
        "by_family": _counter_items(by_family, total, top_limit),
        "by_training_mode": _counter_items(by_training_mode, total, top_limit),
        "numeric_param_pairs": numeric_param_pairs,
        "positive_numeric_greater": positive_numeric_greater,
        "positive_numeric_less": positive_numeric_less,
        "positive_numeric_equal": positive_numeric_equal,
        "positive_numeric_greater_rate": _rate(positive_numeric_greater, numeric_param_pairs),
        "positive_numeric_less_rate": _rate(positive_numeric_less, numeric_param_pairs),
        "positive_numeric_equal_rate": _rate(positive_numeric_equal, numeric_param_pairs),
        "numeric_direction_balance": _signed_balance(positive_numeric_greater, positive_numeric_less),
        "numeric_delta_mean": _mean(numeric_delta_values),
        "name_length_delta_mean": _mean(name_delta_values),
        "positive_name_longer_rate": _rate(positive_name_longer, positive_name_longer + positive_name_shorter),
        "positive_id_lex_less_rate": _rate(id_lex_positive_less, id_lex_positive_less + id_lex_positive_greater),
        "subtype_rows": subtype_rows,
        "subtype_unordered_value_pairs": len(subtype_unordered_direction),
        "subtype_bidirectional_unordered_pairs": bidirectional_unordered,
        "subtype_one_way_unordered_pairs": one_way_unordered,
        "subtype_bidirectional_unordered_pair_rate": _rate(bidirectional_unordered, len(subtype_unordered_direction)),
        "subtype_bidirectional_row_rate": _rate(bidirectional_direction_rows, subtype_rows),
    }
    return summary, family_rows, param_rows, transition_rows, samples


def _family_direction_evidence(pair_type: str, counts: Counter[str]) -> str:
    if pair_type == "param_contrast":
        numeric_pairs = counts["numeric_param_pairs"]
        if numeric_pairs <= 0:
            return "no_numeric_param_evidence"
        balance = abs(_signed_balance(counts["positive_numeric_greater"], counts["positive_numeric_less"]))
        if balance <= 0.1:
            return "balanced_random_like"
        return "biased_or_low_support"
    return "subtype_direction_needs_transition_audit"


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]]) -> list[list[Any]]:
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in items]]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    top_param = [["family", "field", "numeric_pairs", "greater_rate", "less_rate", "balance"]]
    for row in report["top_param_direction"][:12]:
        top_param.append(
            [
                row["family"],
                row["contrast_field"],
                row["numeric_pairs"],
                row["positive_numeric_greater_rate"],
                row["positive_numeric_less_rate"],
                row["numeric_direction_balance"],
            ]
        )
    lines = [
        "# Goal Self-Supervised Label Direction Audit",
        "",
        "Stage 6.1 eval-only audit. It checks whether positive/negative labels from self-supervised quota pairs represent ranking direction or random generation order. It does not train or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["pairs", summary["pairs"]],
                ["numeric_param_pairs", summary["numeric_param_pairs"]],
                ["positive_numeric_greater_rate", summary["positive_numeric_greater_rate"]],
                ["positive_numeric_less_rate", summary["positive_numeric_less_rate"]],
                ["numeric_direction_balance", summary["numeric_direction_balance"]],
                ["subtype_bidirectional_unordered_pair_rate", summary["subtype_bidirectional_unordered_pair_rate"]],
                ["pretrain_valid_hit1_rate", summary["pretrain_valid_hit1_rate"]],
                ["static_random_assignment_detected", summary["static_random_assignment_detected"]],
                ["direction_verdict", summary["direction_verdict"]],
                ["ranking_supervision_allowed", summary["ranking_supervision_allowed"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Pair Type",
        "",
        _md_table(_counter_table(summary["by_pair_type"])),
        "",
        "## Param Direction",
        "",
        _md_table(top_param),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6.1 eval-only self-supervised label direction audit")
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--pretrain-summary-json", default=str(DEFAULT_PRETRAIN_SUMMARY_JSON))
    parser.add_argument("--generator-script", default=str(DEFAULT_GENERATOR_SCRIPT))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--by-family-pair-type-csv", default=str(DEFAULT_BY_FAMILY_PAIR_TYPE_CSV))
    parser.add_argument("--param-direction-csv", default=str(DEFAULT_PARAM_DIRECTION_CSV))
    parser.add_argument("--subtype-transitions-csv", default=str(DEFAULT_SUBTYPE_TRANSITIONS_CSV))
    parser.add_argument("--direction-samples-csv", default=str(DEFAULT_DIRECTION_SAMPLES_CSV))
    parser.add_argument("--top-limit", type=int, default=30)
    args = parser.parse_args()

    started = time.perf_counter()
    rows = _read_csv(Path(args.pair_whitelist_csv))
    pretrain = _load_pretrain_summary(Path(args.pretrain_summary_json))
    source_evidence = _source_direction_evidence(Path(args.generator_script))
    pair_summary, family_rows, param_rows, transition_rows, samples = _audit_pairs(rows, args.top_limit)

    pair_summary["pretrain_valid_hit1_rate"] = pretrain.get("valid_hit1_rate")
    pair_summary["pretrain_valid_random_like"] = pretrain.get("valid_random_like")
    pair_summary["pretrain_gate_passed"] = pretrain.get("passes_pretrain_dry_run_gate")
    pair_summary["static_random_assignment_detected"] = source_evidence.get("static_random_assignment_detected", False)
    pair_summary["unordered_duplicate_guard_detected"] = source_evidence.get("unordered_duplicate_guard_detected", False)
    pair_summary["direction_verdict"] = _direction_verdict(pair_summary)
    pair_summary["ranking_supervision_allowed"] = pair_summary["direction_verdict"] not in {
        "random_order_not_rank_supervision",
        "likely_random_order_not_rank_supervision",
        "data_direction_unstable",
    }
    pair_summary["recommended_data_use"] = (
        "do_not_use_positive_negative_as_ranking_labels"
        if not pair_summary["ranking_supervision_allowed"]
        else "manual_review_required_before_training"
    )

    _write_csv(
        Path(args.by_family_pair_type_csv),
        family_rows,
        [
            "family",
            "pair_type",
            "pairs",
            "positive_id_lex_less_rate",
            "positive_name_longer_rate",
            "numeric_param_pairs",
            "positive_numeric_greater_rate",
            "positive_numeric_less_rate",
            "numeric_direction_balance",
            "direction_evidence",
        ],
    )
    _write_csv(
        Path(args.param_direction_csv),
        param_rows,
        [
            "family",
            "contrast_field",
            "training_mode",
            "pairs",
            "numeric_pairs",
            "positive_numeric_greater",
            "positive_numeric_less",
            "positive_numeric_equal",
            "positive_numeric_greater_rate",
            "positive_numeric_less_rate",
            "numeric_direction_balance",
            "direction_evidence",
        ],
    )
    _write_csv(
        Path(args.subtype_transitions_csv),
        transition_rows,
        ["positive_value", "negative_value", "count", "reverse_count", "has_reverse_direction", "direction_balance", "unordered_key"],
    )
    _write_csv(
        Path(args.direction_samples_csv),
        samples,
        [
            "family",
            "pair_type",
            "contrast_field",
            "positive_id",
            "negative_id",
            "positive_contrast_value",
            "negative_contrast_value",
            "positive_name",
            "negative_name",
        ],
    )

    report = {
        "stage": "Goal LTR v1 / stage 6.1 self-supervised label direction audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
        "pretrain_summary_json": str(Path(args.pretrain_summary_json)),
        "source_evidence": source_evidence,
        "pretrain_evidence": pretrain,
        "summary": pair_summary,
        "top_param_direction": param_rows[: args.top_limit],
        "top_subtype_transitions": transition_rows[: args.top_limit],
        "artifacts": {
            "by_family_pair_type_csv": str(Path(args.by_family_pair_type_csv)),
            "param_direction_csv": str(Path(args.param_direction_csv)),
            "subtype_transitions_csv": str(Path(args.subtype_transitions_csv)),
            "direction_samples_csv": str(Path(args.direction_samples_csv)),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "pairs": pair_summary["pairs"],
                    "numeric_param_pairs": pair_summary["numeric_param_pairs"],
                    "positive_numeric_greater_rate": pair_summary["positive_numeric_greater_rate"],
                    "positive_numeric_less_rate": pair_summary["positive_numeric_less_rate"],
                    "numeric_direction_balance": pair_summary["numeric_direction_balance"],
                    "pretrain_valid_hit1_rate": pair_summary["pretrain_valid_hit1_rate"],
                    "static_random_assignment_detected": pair_summary["static_random_assignment_detected"],
                    "direction_verdict": pair_summary["direction_verdict"],
                    "ranking_supervision_allowed": pair_summary["ranking_supervision_allowed"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
