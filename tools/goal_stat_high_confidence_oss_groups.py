from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_INPUT_JSONL = PROJECT_ROOT / "data" / "goal_search" / "ltr_features_dev.jsonl"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_high_confidence_oss_group_stats_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_high_confidence_oss_group_stats_summary.md"
DEFAULT_ACCEPTED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_high_confidence_oss_group_stats_accepted.csv"
DEFAULT_REJECTED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_high_confidence_oss_group_stats_rejected.csv"

QUERY_PARAM_FIELDS = (
    "dn_query_present",
    "cable_section_query_present",
    "cable_cores_query_present",
    "circuits_query_present",
    "concrete_grade_query_present",
    "thickness_query_present",
    "width_height_query_present",
)

EXACT_PARAM_FIELDS = (
    "dn_exact",
    "cable_section_exact",
    "cable_cores_exact",
    "circuits_exact",
    "concrete_grade_exact",
    "thickness_exact",
    "width_height_exact",
)

CONFLICT_FIELDS = (
    "family_conflict",
    "book_conflict",
    "unit_conflict",
    "domain_conflict_count",
    "param_conflict_count",
    "pipe_device_false_trigger",
    "has_domain_conflict",
    "has_family_conflict_reason",
    "has_book_conflict_reason",
    "has_unit_conflict_reason",
    "has_param_conflict_reason",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid json: {exc}") from exc


def _iter_groups(path: Path) -> Iterable[list[dict[str, Any]]]:
    current_key: tuple[str, str] | None = None
    current_rows: list[dict[str, Any]] = []
    for row in _iter_jsonl(path):
        key = (_clean(row.get("split")), _clean(row.get("group_id")))
        if current_key is not None and key != current_key:
            yield current_rows
            current_rows = []
        current_key = key
        current_rows.append(row)
    if current_rows:
        yield current_rows


def _has_any(row: dict[str, Any], fields: Iterable[str]) -> bool:
    return any(_int(row.get(field)) > 0 for field in fields)


def _has_conflict(row: dict[str, Any]) -> bool:
    return _has_any(row, CONFLICT_FIELDS)


def _has_query_param(row: dict[str, Any]) -> bool:
    return _has_any(row, QUERY_PARAM_FIELDS)


def _has_exact_param(row: dict[str, Any]) -> bool:
    return _int(row.get("param_exact_count")) > 0 or _has_any(row, EXACT_PARAM_FIELDS)


def _positive_evidence_score(row: dict[str, Any], group_rows: list[dict[str, Any]]) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    penalties: list[str] = []
    rank = _int(row.get("candidate_rank") or row.get("base_rank"))
    bm25_values = [_float(item.get("bm25_score")) for item in group_rows]
    bm25_median = statistics.median(bm25_values) if bm25_values else 0.0

    if rank == 1:
        score += 3
        reasons.append("positive_rank_top1")
    elif rank == 2:
        score += 2
        reasons.append("positive_rank_top2")

    if _int(row.get("family_match")) > 0:
        score += 2
        reasons.append("family_match")
    if _int(row.get("book_requested")) > 0 and _int(row.get("book_match")) > 0:
        score += 2
        reasons.append("book_match_when_requested")
    if _has_exact_param(row):
        score += 2
        reasons.append("exact_param_match")
    if _float(row.get("token_overlap")) > 0:
        score += 1
        reasons.append("token_overlap")
    if _float(row.get("bm25_score")) >= bm25_median:
        score += 1
        reasons.append("bm25_at_or_above_group_median")

    if _has_conflict(row):
        score -= 3
        penalties.append("positive_has_conflict")
    if _int(row.get("param_tier_up_count")) > 0 and not _has_exact_param(row):
        score -= 2
        penalties.append("tier_up_without_exact_param")

    return score, reasons, penalties


def _negative_is_useful(positive: dict[str, Any], negative: dict[str, Any]) -> tuple[bool, str]:
    if _int(negative.get("label")) > 0:
        return False, "not_negative"
    if _int(negative.get("candidate_family_present")) == 0 and _int(positive.get("query_family_present")) == 0:
        return False, "weak_family_on_both_sides"
    if _int(negative.get("has_param_conflict_reason")) > 0 or _int(negative.get("param_conflict_count")) > 0:
        return True, "negative_param_conflict"
    if _int(negative.get("family_conflict")) > 0 or _int(negative.get("has_family_conflict_reason")) > 0:
        return True, "negative_family_conflict"
    if _int(negative.get("book_conflict")) > 0 or _int(negative.get("has_book_conflict_reason")) > 0:
        return True, "negative_book_conflict"
    if _int(positive.get("family_match")) > _int(negative.get("family_match")):
        return True, "positive_family_better"
    if _int(positive.get("param_exact_count")) > _int(negative.get("param_exact_count")):
        return True, "positive_param_better"
    if _int(positive.get("book_match")) > _int(negative.get("book_match")):
        return True, "positive_book_better"
    if _float(positive.get("current_score")) > _float(negative.get("current_score")) and _int(negative.get("candidate_rank")) <= 10:
        return True, "positive_score_above_top_negative"
    return False, "ambiguous_negative"


def _reject(reason: str, group_rows: list[dict[str, Any]], positive: dict[str, Any] | None = None) -> dict[str, Any]:
    first = group_rows[0] if group_rows else {}
    pos = positive or {}
    return {
        "reject_reason": reason,
        "split": _clean(first.get("split")),
        "group_id": _clean(first.get("group_id")),
        "sample_id": _clean(first.get("sample_id")),
        "source_file": _clean(first.get("source_file")),
        "project_name": _clean(first.get("project_name")),
        "province": _clean(first.get("province")),
        "query": _clean(first.get("query")),
        "query_family": _clean(first.get("query_family")),
        "positive_id": _clean(pos.get("quota_id")),
        "positive_rank": _clean(pos.get("candidate_rank")),
    }


def _evaluate_group(group_rows: list[dict[str, Any]], score_threshold: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    positives = [row for row in group_rows if _int(row.get("label")) > 0]
    if not positives:
        return None, _reject("positive_not_in_top80", group_rows)
    if len(positives) != 1:
        return None, _reject("multiple_positive_candidates", group_rows, positives[0])

    positive = positives[0]
    rank = _int(positive.get("candidate_rank") or positive.get("base_rank"))
    if rank not in {1, 2}:
        return None, _reject("positive_rank_not_top1_or_top2", group_rows, positive)
    if _int(positive.get("query_family_present")) <= 0:
        return None, _reject("query_family_missing", group_rows, positive)
    if _int(positive.get("candidate_family_present")) <= 0:
        return None, _reject("positive_family_missing", group_rows, positive)
    if _int(positive.get("family_match")) <= 0:
        return None, _reject("positive_family_not_match", group_rows, positive)
    if _has_conflict(positive):
        return None, _reject("positive_has_conflict", group_rows, positive)
    if _has_query_param(positive) and not _has_exact_param(positive):
        return None, _reject("query_param_without_positive_exact", group_rows, positive)
    if _int(positive.get("book_requested")) > 0 and _int(positive.get("book_match")) <= 0:
        return None, _reject("book_requested_but_positive_not_match", group_rows, positive)

    score, reasons, penalties = _positive_evidence_score(positive, group_rows)
    if score < score_threshold:
        rejected = _reject("positive_evidence_score_below_threshold", group_rows, positive)
        rejected["positive_evidence_score"] = score
        rejected["score_reasons"] = "|".join(reasons)
        rejected["score_penalties"] = "|".join(penalties)
        return None, rejected

    negative_reasons: Counter[str] = Counter()
    negative_count = 0
    for row in group_rows:
        keep, reason = _negative_is_useful(positive, row)
        negative_reasons[reason] += 1
        if keep:
            negative_count += 1
    if negative_count <= 0:
        rejected = _reject("no_useful_negative_candidates", group_rows, positive)
        rejected["positive_evidence_score"] = score
        return None, rejected

    accepted = {
        "split": _clean(positive.get("split")),
        "group_id": _clean(positive.get("group_id")),
        "sample_id": _clean(positive.get("sample_id")),
        "source_file": _clean(positive.get("source_file")),
        "project_name": _clean(positive.get("project_name")),
        "province": _clean(positive.get("province")),
        "query": _clean(positive.get("query")),
        "query_family": _clean(positive.get("query_family")),
        "positive_id": _clean(positive.get("quota_id")),
        "positive_name": _clean(positive.get("quota_name")),
        "positive_rank": rank,
        "positive_evidence_score": score,
        "accept_reasons": "|".join(reasons),
        "score_penalties": "|".join(penalties),
        "negative_count": negative_count,
        "top_negative_reasons": "|".join(f"{key}:{value}" for key, value in negative_reasons.most_common(5)),
        "candidate_count": len(group_rows),
        "expected_ids": _clean(positive.get("expected_ids")),
    }
    return accepted, None


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(accepted: list[dict[str, Any]], rejected: list[dict[str, Any]], top_limit: int) -> dict[str, Any]:
    total = len(accepted) + len(rejected)
    family = Counter(row["query_family"] or "<empty>" for row in accepted)
    source = Counter(row["source_file"] or "<empty>" for row in accepted)
    province = Counter(row["province"] or "<empty>" for row in accepted)
    reject_reason = Counter(row["reject_reason"] for row in rejected)
    largest_source = source.most_common(1)[0] if source else ("", 0)
    largest_family = family.most_common(1)[0] if family else ("", 0)
    return {
        "input_groups": total,
        "accepted_groups": len(accepted),
        "accepted_rate": _rate(len(accepted), total),
        "rejected_groups": len(rejected),
        "rejected_rate": _rate(len(rejected), total),
        "useful_negative_pairs": sum(_int(row.get("negative_count")) for row in accepted),
        "distinct_sources": len(source),
        "distinct_project_names": len({row["project_name"] for row in accepted if row.get("project_name")}),
        "distinct_families": len(family),
        "distinct_provinces": len(province),
        "largest_source": largest_source[0],
        "largest_source_count": largest_source[1],
        "largest_source_rate": _rate(largest_source[1], len(accepted)),
        "largest_family": largest_family[0],
        "largest_family_count": largest_family[1],
        "largest_family_rate": _rate(largest_family[1], len(accepted)),
        "passes_stage_4_8_gate": (
            len(accepted) >= 300
            and len(source) >= 5
            and _rate(largest_source[1], len(accepted)) <= 0.4
            and len(family) >= 10
            and _rate(largest_family[1], len(accepted)) <= 0.35
        ),
        "by_family": _counter_items(family, len(accepted), top_limit),
        "by_source_file": _counter_items(source, len(accepted), top_limit),
        "by_province": _counter_items(province, len(accepted), top_limit),
        "by_reject_reason": _counter_items(reject_reason, len(rejected), top_limit),
    }


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]]) -> list[list[object]]:
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in items]]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal High-Confidence OSS Group Stats",
        "",
        "Stage 4.9 eval-only statistic. It mines clean-looking OSS groups from Top80 LTR features and does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["input_groups", summary["input_groups"]],
                ["accepted_groups", summary["accepted_groups"]],
                ["accepted_rate", summary["accepted_rate"]],
                ["rejected_groups", summary["rejected_groups"]],
                ["useful_negative_pairs", summary["useful_negative_pairs"]],
                ["distinct_sources", summary["distinct_sources"]],
                ["distinct_families", summary["distinct_families"]],
                ["distinct_provinces", summary["distinct_provinces"]],
                ["largest_source", summary["largest_source"]],
                ["largest_source_rate", summary["largest_source_rate"]],
                ["largest_family", summary["largest_family"]],
                ["largest_family_rate", summary["largest_family_rate"]],
                ["passes_stage_4_8_gate", summary["passes_stage_4_8_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Accepted Families",
        "",
        _md_table(_counter_table(summary["by_family"])),
        "",
        "## Accepted Sources",
        "",
        _md_table(_counter_table(summary["by_source_file"])),
        "",
        "## Reject Reasons",
        "",
        _md_table(_counter_table(summary["by_reject_reason"])),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 4.9 eval-only stats for high-confidence OSS Top80 groups")
    parser.add_argument("--input-jsonl", default=str(DEFAULT_INPUT_JSONL))
    parser.add_argument("--score-threshold", type=int, default=7)
    parser.add_argument("--limit-groups", type=int, default=0)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--accepted-csv", default=str(DEFAULT_ACCEPTED_CSV))
    parser.add_argument("--rejected-csv", default=str(DEFAULT_REJECTED_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    input_path = Path(args.input_jsonl)
    if not input_path.exists():
        raise FileNotFoundError(f"Top80 feature file not found: {input_path}")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for group_index, group_rows in enumerate(_iter_groups(input_path), 1):
        if args.limit_groups and group_index > args.limit_groups:
            break
        ok, bad = _evaluate_group(group_rows, args.score_threshold)
        if ok is not None:
            accepted.append(ok)
        if bad is not None:
            rejected.append(bad)

    summary = _summarize(accepted, rejected, args.top_limit)
    accepted_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "positive_id",
        "positive_name",
        "positive_rank",
        "positive_evidence_score",
        "negative_count",
        "accept_reasons",
        "score_penalties",
        "top_negative_reasons",
        "candidate_count",
        "expected_ids",
    ]
    rejected_fields = [
        "reject_reason",
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "positive_id",
        "positive_rank",
        "positive_evidence_score",
        "score_reasons",
        "score_penalties",
    ]
    _write_csv(Path(args.accepted_csv), accepted, accepted_fields)
    _write_csv(Path(args.rejected_csv), rejected, rejected_fields)

    artifacts = {
        "accepted_csv": args.accepted_csv,
        "rejected_csv": args.rejected_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.9 high-confidence OSS group stats",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input_jsonl": str(input_path),
        "score_threshold": args.score_threshold,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    **{key: summary[key] for key in (
                        "input_groups",
                        "accepted_groups",
                        "accepted_rate",
                        "useful_negative_pairs",
                        "distinct_sources",
                        "distinct_families",
                        "passes_stage_4_8_gate",
                    )},
                },
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
