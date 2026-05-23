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

DEFAULT_RESIDUAL_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_residual_audit_rows.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_sleeve_blocked_gain_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_sleeve_blocked_gain_audit_summary.md"
DEFAULT_ROWS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_sleeve_blocked_gain_audit_rows.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_sleeve_blocked_gain_audit_buckets.csv"
DEFAULT_EXAMPLES_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_sleeve_blocked_gain_audit_examples.jsonl"

SLEEVE_TOKEN = "\u5957\u7ba1"
CLOSED_WALL_TOKENS = ("\u5bc6\u95ed\u7a7f\u5899", "\u7a7f\u5899\u5bc6\u95ed")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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


def _target_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _clean(row.get("outcome")) == "blocked_gain"
        and _clean(row.get("diagnosis")) == "blocked_gain_real_family_conflict_or_family_rule_gap"
        and _clean(row.get("query_family")) == "sleeve"
    ]


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _classify(row: dict[str, Any]) -> tuple[str, str]:
    query = _clean(row.get("query"))
    raw_name = _clean(row.get("raw_ltr_top_name"))
    raw_family = _clean(row.get("raw_ltr_top_family"))
    raw_book = _clean(row.get("raw_ltr_top_book"))
    baseline_family = _clean(row.get("baseline_top_family"))

    if SLEEVE_TOKEN not in query and raw_family != "support":
        return "query_family_false_positive", "query_family=sleeve but query/raw candidate point to another object family"

    if raw_family == "support" and SLEEVE_TOKEN in raw_name:
        if raw_book in {"10", "C10"}:
            return "candidate_family_misclassified_support", "expected sleeve candidate is labeled support in book 10/C10"
        return "candidate_family_support_alias", "expected sleeve candidate is labeled support outside book 10/C10"

    if raw_family == "duct" and (_has_any(query, CLOSED_WALL_TOKENS) or _has_any(raw_name, CLOSED_WALL_TOKENS)):
        return "sleeve_duct_semantic_alias", "closed wall sleeve is represented as duct/closed wall pipe item"

    if baseline_family == raw_family:
        return "query_family_conflict_too_strict", "baseline and raw candidate share family but conflict with query_family=sleeve"

    return "unclear_family_gap", "needs manual review"


def _enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        audit_class, audit_reason = _classify(row)
        item = dict(row)
        item["audit_class"] = audit_class
        item["audit_reason"] = audit_reason
        item["is_family_feature_issue"] = audit_class in {
            "candidate_family_misclassified_support",
            "candidate_family_support_alias",
            "sleeve_duct_semantic_alias",
        }
        item["is_gate_condition_too_strict"] = audit_class in {
            "query_family_conflict_too_strict",
            "candidate_family_misclassified_support",
            "candidate_family_support_alias",
            "sleeve_duct_semantic_alias",
        }
        item["is_query_family_issue"] = audit_class == "query_family_false_positive"
        result.append(item)
    return result


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "audit_class",
        "split",
        "province",
        "source_file",
        "raw_ltr_top_family",
        "raw_ltr_top_book",
        "baseline_top_family",
        "baseline_top_book",
        "margin_bucket",
        "raw_rank_bucket",
    ]
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    total = len(rows)
    for row in rows:
        for field in fields:
            counters[field][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for field in fields:
        for key, count in counters[field].most_common(30):
            result.append({"bucket": field, "key": key, "count": count, "rate": _rate(count, total)})
    return result


def _split_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for split in sorted({row["split"] for row in rows}):
        subset = [row for row in rows if row["split"] == split]
        classes = Counter(row["audit_class"] for row in subset)
        raw_families = Counter(row["raw_ltr_top_family"] for row in subset)
        result.append(
            {
                "split": split,
                "total": len(subset),
                "top_audit_class": classes.most_common(5),
                "raw_family": raw_families.most_common(5),
                "family_feature_issue": sum(1 for row in subset if row["is_family_feature_issue"]),
                "gate_condition_too_strict": sum(1 for row in subset if row["is_gate_condition_too_strict"]),
                "query_family_issue": sum(1 for row in subset if row["is_query_family_issue"]),
            }
        )
    return result


def _examples(rows: list[dict[str, Any]], limit_per_class: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()
    for row in sorted(rows, key=lambda item: (item["audit_class"], item["split"], item["group_id"])):
        audit_class = row["audit_class"]
        if seen[audit_class] >= limit_per_class:
            continue
        seen[audit_class] += 1
        result.append(row)
    return result


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes = Counter(row["audit_class"] for row in rows)
    raw_family = Counter(row["raw_ltr_top_family"] for row in rows)
    family_feature_issue = sum(1 for row in rows if row["is_family_feature_issue"])
    gate_condition_too_strict = sum(1 for row in rows if row["is_gate_condition_too_strict"])
    query_family_issue = sum(1 for row in rows if row["is_query_family_issue"])
    main_read = (
        "candidate_family_feature_mislabel_dominates"
        if family_feature_issue >= max(gate_condition_too_strict - family_feature_issue, query_family_issue)
        else "gate_condition_too_strict_dominates"
    )
    return {
        "target": "blocked_gain + family_rule_gap + query_family=sleeve",
        "total": len(rows),
        "audit_classes": [{"key": key, "count": count, "rate": _rate(count, len(rows))} for key, count in classes.most_common()],
        "raw_family": [{"key": key, "count": count, "rate": _rate(count, len(rows))} for key, count in raw_family.most_common()],
        "family_feature_issue": family_feature_issue,
        "gate_condition_too_strict": gate_condition_too_strict,
        "query_family_issue": query_family_issue,
        "main_read": main_read,
        "recommendation": "Do not train or wire switch. Next design should treat sleeve candidates labeled support/book10 as a taxonomy alias, then rerun OOF what-if.",
    }


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
    split_rows = report["split_rows"]
    lines = [
        "# Goal Query-Anchored LTR Sleeve Blocked-Gain Audit",
        "",
        "Stage 7.3 audits only blocked gains in the sleeve bucket caused by family-rule gaps. No training, no tuning, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target", summary["target"]],
                ["total", summary["total"]],
                ["family_feature_issue", summary["family_feature_issue"]],
                ["gate_condition_too_strict", summary["gate_condition_too_strict"]],
                ["query_family_issue", summary["query_family_issue"]],
                ["main_read", summary["main_read"]],
                ["recommendation", summary["recommendation"]],
            ]
        ),
        "",
        "## Audit Classes",
        "",
        _md_table([["class", "count", "rate"], *[[row["key"], row["count"], row["rate"]] for row in summary["audit_classes"]]]),
        "",
        "## Split View",
        "",
        _md_table(
            [
                ["split", "total", "top_class", "raw_family", "family_feature_issue", "gate_too_strict", "query_family_issue"],
                *[
                    [
                        row["split"],
                        row["total"],
                        row["top_audit_class"],
                        row["raw_family"],
                        row["family_feature_issue"],
                        row["gate_condition_too_strict"],
                        row["query_family_issue"],
                    ]
                    for row in split_rows
                ],
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


def _row_fields(base_fields: list[str]) -> list[str]:
    fields = ["audit_class", "audit_reason", "is_family_feature_issue", "is_gate_condition_too_strict", "is_query_family_issue"]
    return fields + [field for field in base_fields if field not in fields]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.3 audit sleeve blocked gains caused by family rule gaps")
    parser.add_argument("--residual-csv", default=str(DEFAULT_RESIDUAL_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--rows-csv", default=str(DEFAULT_ROWS_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--examples-jsonl", default=str(DEFAULT_EXAMPLES_JSONL))
    parser.add_argument("--examples-per-class", type=int, default=5)
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.residual_csv))
    target = _target_rows(source_rows)
    rows = _enrich(target)
    bucket_rows = _bucket_rows(rows)
    split_rows = _split_rows(rows)
    summary = _summary(rows)
    examples = _examples(rows, args.examples_per_class)

    report = {
        "stage": "Goal LTR v1 / stage 7.3 sleeve blocked-gain family gap audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "residual_csv": str(Path(args.residual_csv)),
        "summary": summary,
        "split_rows": split_rows,
        "bucket_rows": bucket_rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "rows_csv": str(Path(args.rows_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "examples_jsonl": str(Path(args.examples_jsonl)),
        },
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    base_fields = list(source_rows[0].keys()) if source_rows else []
    _write_csv(Path(args.rows_csv), rows, _row_fields(base_fields))
    _write_csv(Path(args.bucket_csv), bucket_rows, ["bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.examples_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": True,
                    "no_training": True,
                    "no_search_integration": True,
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
