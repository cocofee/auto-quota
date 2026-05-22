from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_FEATURES = PROJECT_ROOT / "data" / "goal_search" / "ltr_features_heldout.jsonl"
DEFAULT_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_eval_heldout_details.jsonl"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_heldout_gain_loss_audit.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_heldout_gain_loss_audit.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_heldout_gain_loss_audit.csv"


PARAM_BUCKETS = (
    ("dn", "DN"),
    ("width_height", "宽高/周长"),
    ("cable_section", "电缆截面"),
    ("cable_cores", "芯数"),
    ("circuits", "回路"),
    ("concrete_grade", "混凝土强度"),
    ("thickness", "厚度"),
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _change_type(detail: dict[str, Any]) -> str:
    delta = int(detail.get("hit1_delta") or 0)
    if delta > 0:
        return "gain"
    if delta < 0:
        return "loss"
    return "same"


def _load_changed_details(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(path):
        change = _change_type(row)
        if change not in {"gain", "loss"}:
            continue
        row = dict(row)
        row["change_type"] = change
        result[_clean(row.get("group_id"))] = row
    return result


def _collect_feature_rows(path: Path, target_group_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _iter_jsonl(path):
        group_id = _clean(row.get("group_id"))
        if group_id in target_group_ids:
            rows[group_id].append(row)
    for group_rows in rows.values():
        group_rows.sort(key=lambda item: int(item.get("candidate_rank") or 0))
    return rows


def _row_by_rank(rows: list[dict[str, Any]], rank: int | None) -> dict[str, Any]:
    if not rank:
        return {}
    for row in rows:
        if int(row.get("candidate_rank") or 0) == rank:
            return row
    return {}


def _positive_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if int(row.get("label") or 0) == 1]


def _join_unique(values: list[Any]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _clean(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return "; ".join(result)


def _param_bucket(row: dict[str, Any]) -> str:
    if not row:
        return "unknown"
    hits: list[str] = []
    for key, label in PARAM_BUCKETS:
        if int(row.get(f"{key}_query_present") or 0) or int(row.get(f"{key}_candidate_present") or 0):
            hits.append(label)
    return "+".join(hits) if hits else "no_param"


def _param_match_bucket(row: dict[str, Any]) -> str:
    if not row:
        return "unknown"
    matches: list[str] = []
    conflicts: list[str] = []
    for key, label in PARAM_BUCKETS:
        exact = int(row.get(f"{key}_exact") or 0)
        tier = int(row.get(f"{key}_tier_up") or 0) if f"{key}_tier_up" in row else 0
        gap = float(row.get(f"{key}_gap_ratio") or row.get(f"{key}_gap") or 0)
        if exact:
            matches.append(f"{label}:exact")
        elif tier:
            matches.append(f"{label}:tier")
        elif gap:
            conflicts.append(f"{label}:gap")
    if matches:
        return "+".join(matches)
    if conflicts:
        return "+".join(conflicts)
    return "no_param_match"


def _book(row: dict[str, Any]) -> str:
    return _clean(row.get("quota_book"))


def _family(row: dict[str, Any]) -> str:
    return _clean(row.get("candidate_family")) or "<empty>"


def _candidate_label(row: dict[str, Any]) -> str:
    if not row:
        return ""
    return f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip()


def _build_item(group_id: str, detail: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_top = _row_by_rank(rows, 1)
    ltr_top = _row_by_rank(rows, int(detail.get("ltr_top_original_rank") or 0))
    baseline_positive = _row_by_rank(rows, detail.get("baseline_positive_rank"))
    ltr_positive = _row_by_rank(rows, detail.get("ltr_positive_rank"))
    positives = _positive_rows(rows)
    expected_ref = baseline_positive or ltr_positive or (positives[0] if positives else {})
    ref = baseline_top or ltr_top or expected_ref or {}
    expected_families = _join_unique([row.get("candidate_family") for row in positives])
    expected_books = _join_unique([row.get("quota_book") for row in positives])
    expected_ids = _join_unique([row.get("quota_id") for row in positives]) or _clean(ref.get("expected_ids"))
    change_type = detail["change_type"]

    return {
        "change_type": change_type,
        "group_id": group_id,
        "group_index": detail.get("group_index"),
        "sample_id": _clean(ref.get("sample_id") or detail.get("sample_id")),
        "province": _clean(ref.get("province") or detail.get("province")),
        "query": _clean(ref.get("query") or detail.get("query")),
        "query_family": _clean(ref.get("query_family")) or "<empty>",
        "expected_ids": expected_ids,
        "expected_families": expected_families or "<empty>",
        "expected_books": expected_books or "<empty>",
        "baseline_positive_rank": detail.get("baseline_positive_rank"),
        "ltr_positive_rank": detail.get("ltr_positive_rank"),
        "ltr_top_original_rank": detail.get("ltr_top_original_rank"),
        "baseline_top_id": _clean(baseline_top.get("quota_id")),
        "baseline_top_name": _clean(baseline_top.get("quota_name")),
        "baseline_top_family": _family(baseline_top),
        "baseline_top_book": _book(baseline_top),
        "ltr_top_id": _clean(ltr_top.get("quota_id")),
        "ltr_top_name": _clean(ltr_top.get("quota_name")),
        "ltr_top_family": _family(ltr_top),
        "ltr_top_book": _book(ltr_top),
        "expected_ref_id": _clean(expected_ref.get("quota_id")),
        "expected_ref_name": _clean(expected_ref.get("quota_name")),
        "expected_ref_family": _family(expected_ref),
        "expected_ref_book": _book(expected_ref),
        "param_bucket": _param_bucket(expected_ref or ref),
        "expected_param_match": _param_match_bucket(expected_ref),
        "ltr_top_param_match": _param_match_bucket(ltr_top),
        "family_transition": f"{_family(baseline_top)} -> {_family(ltr_top)} / expected {expected_families or '<empty>'}",
        "book_transition": f"{_book(baseline_top) or '<empty>'} -> {_book(ltr_top) or '<empty>'} / expected {expected_books or '<empty>'}",
        "baseline_top": _candidate_label(baseline_top),
        "ltr_top": _candidate_label(ltr_top),
        "expected_ref": _candidate_label(expected_ref),
        "baseline_reasons": _clean(baseline_top.get("reasons")),
        "ltr_reasons": _clean(ltr_top.get("reasons")),
    }


def _counter_items(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _group_counter(rows: list[dict[str, Any]], change_type: str, key: str) -> Counter[str]:
    return Counter(row.get(key) or "<empty>" for row in rows if row["change_type"] == change_type)


def _transition_counter(rows: list[dict[str, Any]], change_type: str, key: str) -> Counter[str]:
    return Counter(row.get(key) or "<empty>" for row in rows if row["change_type"] == change_type)


def _build_report(details: dict[str, dict[str, Any]], feature_groups: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing_groups: list[str] = []
    for group_id, detail in sorted(details.items(), key=lambda item: int(item[1].get("group_index") or 0)):
        group_rows = feature_groups.get(group_id)
        if not group_rows:
            missing_groups.append(group_id)
            continue
        rows.append(_build_item(group_id, detail, group_rows))

    change_counts = Counter(row["change_type"] for row in rows)
    summary = {
        "scope": "heldout Top1 gain/loss audit only; no search integration",
        "rows": len(rows),
        "gain_rows": change_counts.get("gain", 0),
        "loss_rows": change_counts.get("loss", 0),
        "missing_feature_groups": missing_groups,
        "by_change": {
            "gain": {
                "province": _counter_items(_group_counter(rows, "gain", "province")),
                "query_family": _counter_items(_group_counter(rows, "gain", "query_family")),
                "expected_family": _counter_items(_group_counter(rows, "gain", "expected_ref_family")),
                "expected_book": _counter_items(_group_counter(rows, "gain", "expected_ref_book")),
                "param_bucket": _counter_items(_group_counter(rows, "gain", "param_bucket")),
                "family_transition": _counter_items(_transition_counter(rows, "gain", "family_transition")),
                "book_transition": _counter_items(_transition_counter(rows, "gain", "book_transition")),
            },
            "loss": {
                "province": _counter_items(_group_counter(rows, "loss", "province")),
                "query_family": _counter_items(_group_counter(rows, "loss", "query_family")),
                "expected_family": _counter_items(_group_counter(rows, "loss", "expected_ref_family")),
                "expected_book": _counter_items(_group_counter(rows, "loss", "expected_ref_book")),
                "param_bucket": _counter_items(_group_counter(rows, "loss", "param_bucket")),
                "family_transition": _counter_items(_transition_counter(rows, "loss", "family_transition")),
                "book_transition": _counter_items(_transition_counter(rows, "loss", "book_transition")),
            },
        },
    }
    report = {"summary": summary, "rows": rows}
    return report, rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "change_type",
        "group_index",
        "sample_id",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_families",
        "expected_books",
        "baseline_positive_rank",
        "ltr_positive_rank",
        "ltr_top_original_rank",
        "baseline_top_family",
        "ltr_top_family",
        "expected_ref_family",
        "baseline_top_book",
        "ltr_top_book",
        "expected_ref_book",
        "param_bucket",
        "expected_param_match",
        "ltr_top_param_match",
        "baseline_top",
        "ltr_top",
        "expected_ref",
        "family_transition",
        "book_transition",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


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


def _items_table(items: list[dict[str, Any]], key_header: str) -> str:
    return _md_table([[key_header, "count"]] + [[item["key"], item["count"]] for item in items])


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines: list[str] = [
        "# Goal LTR Heldout Gain/Loss Audit",
        "",
        "Stage 2.1 only: audits heldout Top1 gains and losses from offline LTR reranking. No search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["gain_rows", summary["gain_rows"]],
                ["loss_rows", summary["loss_rows"]],
                ["missing_feature_groups", len(summary["missing_feature_groups"])],
            ]
        ),
        "",
    ]

    for change in ("gain", "loss"):
        data = summary["by_change"][change]
        title = "Gains" if change == "gain" else "Losses"
        lines.extend(
            [
                f"## {title}",
                "",
                "### Province",
                "",
                _items_table(data["province"], "province"),
                "",
                "### Query Family",
                "",
                _items_table(data["query_family"], "query_family"),
                "",
                "### Expected Family",
                "",
                _items_table(data["expected_family"], "expected_family"),
                "",
                "### Expected Book",
                "",
                _items_table(data["expected_book"], "expected_book"),
                "",
                "### Param Bucket",
                "",
                _items_table(data["param_bucket"], "param_bucket"),
                "",
                "### Family Transition",
                "",
                _items_table(data["family_transition"][:10], "transition"),
                "",
                "### Book Transition",
                "",
                _items_table(data["book_transition"][:10], "transition"),
                "",
            ]
        )

    lines.extend(["## Loss Samples", ""])
    loss_rows = [row for row in report["rows"] if row["change_type"] == "loss"]
    sample_table = [["province", "sample_id", "query", "baseline_top", "ltr_top", "expected", "family_transition", "book_transition"]]
    for row in loss_rows:
        sample_table.append(
            [
                row["province"],
                row["sample_id"],
                row["query"],
                row["baseline_top"],
                row["ltr_top"],
                row["expected_ref"],
                row["family_transition"],
                row["book_transition"],
            ]
        )
    lines.extend([_md_table(sample_table), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit heldout Top1 gains/losses from Goal LTR offline rerank")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES))
    parser.add_argument("--details", default=str(DEFAULT_DETAILS))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    details = _load_changed_details(Path(args.details))
    feature_groups = _collect_feature_rows(Path(args.features), set(details))
    report, rows = _build_report(details, feature_groups)

    json_path = Path(args.json_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report)
    _write_csv(Path(args.csv_output), rows)
    print(
        json.dumps(
            {
                "json_output": args.json_output,
                "md_output": args.md_output,
                "csv_output": args.csv_output,
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
