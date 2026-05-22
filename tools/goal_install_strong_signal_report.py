from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tools.goal_error_sampling_report import (  # noqa: E402
    TARGET_REASONS,
    _clean,
    _expected_families,
    _load_inputs,
    _query_family,
    _read_jsonl,
    classify,
)
from tools.goal_family_gap_report import _classify_family_gap  # noqa: E402


DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_install_strong_signal_report.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_install_strong_signal_report.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_install_strong_signal_samples.csv"
PRIORITY_FAMILIES = ("support", "pipe", "valve", "bridge", "conduit", "sleeve", "lamp")
FAMILY_LABELS = {
    "support": "支吊架/支架",
    "pipe": "管道/管件",
    "valve": "阀门/过滤器/接头",
    "bridge": "桥架/线槽",
    "conduit": "配管/导管/接线盒",
    "sleeve": "套管",
    "lamp": "灯具/照明",
}
QUERY_KEYWORDS = {
    "support": ("支吊架", "支架", "吊架", "基础型钢", "型钢"),
    "pipe": ("管道", "钢管", "塑料管", "铜管", "水管", "风管", "分歧管", "分歧器"),
    "valve": ("阀", "过滤器", "倒流防止器", "真空破坏器", "软接头", "补偿器"),
    "bridge": ("桥架", "线槽"),
    "conduit": ("配管", "电线管", "导管", "线管", "接线盒"),
    "sleeve": ("套管",),
    "lamp": ("灯具", "灯", "照明", "筒灯", "装饰灯"),
}
EXPECTED_KEYWORDS = {
    family: keywords for family, keywords in QUERY_KEYWORDS.items()
}


def _expected_names(row: dict[str, Any]) -> list[str]:
    return [_clean(signal.get("name")) for signal in row.get("expected_signals") or [] if _clean(signal.get("name"))]


def _find_keyword_families(text: str, mapping: dict[str, tuple[str, ...]], source: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for family in PRIORITY_FAMILIES:
        matched = [keyword for keyword in mapping.get(family, ()) if keyword in text]
        if matched:
            hits.append((family, f"{source}:{','.join(matched[:3])}"))
    return hits


def _strong_family(row: dict[str, Any]) -> tuple[str | None, list[str]]:
    query = _clean(row.get("query"))
    expected_text = " ".join(_expected_names(row))
    evidence: list[tuple[str, str]] = []

    # Only visible query keywords decide the actionable bucket. Existing
    # query_family labels can be wrong in precisely this gap set, and expected
    # names are analysis-only because they are not available online.
    query_hits = _find_keyword_families(query, QUERY_KEYWORDS, "query_keyword")
    evidence.extend(query_hits)

    query_families: list[str] = []
    for family, _item in query_hits:
        if family not in query_families:
            query_families.append(family)

    query_family = _query_family(row)
    if query_family in PRIORITY_FAMILIES:
        evidence.append((query_family, f"query_family:{query_family}"))

    # Expected-name keywords are useful for analysis, but never used as an online prior.
    evidence.extend(_find_keyword_families(expected_text, EXPECTED_KEYWORDS, "expected_keyword"))

    expected_families = _expected_families(row)
    for family in PRIORITY_FAMILIES:
        if family in expected_families and any(keyword in expected_text for keyword in EXPECTED_KEYWORDS[family]):
            evidence.append((family, f"expected_family:{family}"))

    seen_evidence: list[str] = []
    for _family, item in evidence:
        if item not in seen_evidence:
            seen_evidence.append(item)
    if len(query_families) != 1:
        return None, seen_evidence
    return query_families[0], seen_evidence


def _iter_strong_rows(inputs: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split, path in inputs:
        for row in _read_jsonl(path):
            if row.get("hit1"):
                continue
            if _clean(row.get("miss_reason")) not in TARGET_REASONS:
                continue
            primary, _flags, _evidence = classify(row)
            if primary != "family_unrecognized":
                continue
            row = dict(row)
            row["split"] = split
            gap_key, _gap_label, gap_notes = _classify_family_gap(row)
            if gap_key != "install_high_freq_missed":
                continue
            if "install_province_fallback" in gap_notes:
                continue
            family, evidence = _strong_family(row)
            if family is None:
                continue
            row["_target_family"] = family
            row["_family_label"] = FAMILY_LABELS[family]
            row["_strong_evidence"] = evidence
            row["_gap_notes"] = gap_notes
            rows.append(row)
    return rows


def _sample(row: dict[str, Any]) -> dict[str, Any]:
    expected_signals = row.get("expected_signals") or []
    return {
        "split": row.get("split"),
        "province": row.get("province"),
        "sample_id": row.get("sample_id"),
        "bucket": row.get("bucket"),
        "miss_reason": row.get("miss_reason"),
        "target_family": row.get("_target_family"),
        "family_label": row.get("_family_label"),
        "query": row.get("query"),
        "query_family": _query_family(row),
        "expected_families": sorted(_expected_families(row)),
        "expected_ids": row.get("expected_ids"),
        "expected_names": [signal.get("name") for signal in expected_signals[:2]],
        "top_ids": row.get("top_ids"),
        "strong_evidence": row.get("_strong_evidence") or [],
        "gap_notes": row.get("_gap_notes") or [],
    }


def _counter_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _build_report(inputs: list[tuple[str, Path]], *, sample_limit: int, bucket_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _iter_strong_rows(inputs)
    family_counts: Counter[str] = Counter(row["_target_family"] for row in rows)
    bucket_counts: Counter[str] = Counter(_clean(row.get("bucket")) for row in rows)
    miss_reason_counts: Counter[str] = Counter(_clean(row.get("miss_reason")) for row in rows)
    province_counts: Counter[str] = Counter(_clean(row.get("province")) for row in rows)
    evidence_counts: Counter[str] = Counter()
    for row in rows:
        evidence_counts.update(row.get("_strong_evidence") or [])

    by_family: dict[str, Any] = {}
    for family in PRIORITY_FAMILIES:
        family_rows = [row for row in rows if row["_target_family"] == family]
        if not family_rows:
            continue
        group_counter: Counter[str] = Counter()
        province_counter: Counter[str] = Counter()
        bucket_counter: Counter[str] = Counter()
        reason_counter: Counter[str] = Counter()
        for row in family_rows:
            province = _clean(row.get("province"))
            bucket = _clean(row.get("bucket"))
            reason = _clean(row.get("miss_reason"))
            province_counter[province] += 1
            bucket_counter[bucket] += 1
            reason_counter[reason] += 1
            group_counter[f"{province}|{bucket}|{reason}"] += 1

        group_rank = {item["key"]: idx for idx, item in enumerate(_counter_items(group_counter, bucket_limit))}
        sorted_rows = sorted(
            family_rows,
            key=lambda row: (
                group_rank.get(f"{_clean(row.get('province'))}|{_clean(row.get('bucket'))}|{_clean(row.get('miss_reason'))}", 9999),
                _clean(row.get("split")),
                _clean(row.get("province")),
                _clean(row.get("sample_id")),
            ),
        )
        by_family[family] = {
            "label": FAMILY_LABELS[family],
            "rows": len(family_rows),
            "province_counts": _counter_items(province_counter, bucket_limit),
            "source_bucket_counts": _counter_items(bucket_counter, bucket_limit),
            "miss_reason_counts": _counter_items(reason_counter, bucket_limit),
            "top_groups": _counter_items(group_counter, bucket_limit),
            "samples": [_sample(row) for row in sorted_rows[:sample_limit]],
        }

    selected_family = family_counts.most_common(1)[0][0] if family_counts else ""
    report = {
        "summary": {
            "inputs": [{"split": split, "path": str(path)} for split, path in inputs],
            "source_definition": (
                "family_unrecognized + install_high_freq_missed, excluding install_province_fallback; "
                "requires exactly one explicit query keyword family in priority families; "
                "expected names are analysis-only evidence"
            ),
            "priority_families": list(PRIORITY_FAMILIES),
            "rows": len(rows),
            "selected_family": selected_family,
            "selected_family_label": FAMILY_LABELS.get(selected_family, ""),
            "family_counts": [
                {"key": family, "label": FAMILY_LABELS[family], "count": family_counts.get(family, 0)}
                for family in PRIORITY_FAMILIES
            ],
            "source_bucket_counts": _counter_items(bucket_counts, bucket_limit),
            "miss_reason_counts": _counter_items(miss_reason_counts, bucket_limit),
            "province_counts": _counter_items(province_counts, bucket_limit),
            "evidence_counts": _counter_items(evidence_counts, bucket_limit),
        },
        "by_family": by_family,
    }
    return report, [_sample(row) for row in rows]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "target_family",
        "family_label",
        "split",
        "province",
        "sample_id",
        "bucket",
        "miss_reason",
        "query",
        "query_family",
        "expected_families",
        "expected_ids",
        "expected_names",
        "top_ids",
        "strong_evidence",
        "gap_notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: "; ".join(str(item) for item in row.get(key, []))
                    if isinstance(row.get(key), list)
                    else row.get(key, "")
                    for key in fields
                }
            )


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


def _write_markdown(path: Path, report: dict[str, Any], *, sample_limit: int) -> None:
    summary = report["summary"]
    lines: list[str] = [
        "# Goal Expanded Install Strong Signal Report",
        "",
        "只读清单：不调参、不改搜索逻辑。",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["selected_family", f"{summary['selected_family']} / {summary['selected_family_label']}"],
                ["source_definition", summary["source_definition"]],
            ]
        ),
        "",
        "## Family Counts",
        "",
        _md_table([["family", "label", "count"]] + [[item["key"], item["label"], item["count"]] for item in summary["family_counts"]]),
        "",
        "## Source Buckets",
        "",
        _md_table([["bucket", "count"]] + [[item["key"], item["count"]] for item in summary["source_bucket_counts"]]),
        "",
    ]

    for family, data in report["by_family"].items():
        lines.extend(
            [
                f"## {family} / {data['label']}",
                "",
                f"Rows: {data['rows']}",
                "",
                "Top groups:",
                "",
                _md_table([["province|bucket|miss_reason", "count"]] + [[item["key"], item["count"]] for item in data["top_groups"][:10]]),
                "",
                f"Samples (top {sample_limit}):",
                "",
            ]
        )
        sample_rows = [["split", "province", "bucket", "miss_reason", "query", "expected", "evidence"]]
        for sample in data["samples"][:sample_limit]:
            sample_rows.append(
                [
                    sample.get("split", ""),
                    sample.get("province", ""),
                    sample.get("bucket", ""),
                    sample.get("miss_reason", ""),
                    sample.get("query", ""),
                    "; ".join(sample.get("expected_names") or []),
                    "; ".join(sample.get("strong_evidence") or []),
                ]
            )
        lines.extend([_md_table(sample_rows), ""])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="List install strong-signal family-unrecognized samples")
    parser.add_argument("--input", action="append", default=[], help="split=details.jsonl. Defaults to expanded heldout/hard details")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    parser.add_argument("--sample-limit", type=int, default=12)
    parser.add_argument("--bucket-limit", type=int, default=20)
    args = parser.parse_args()

    inputs = _load_inputs(args.input)
    report, rows = _build_report(inputs, sample_limit=args.sample_limit, bucket_limit=args.bucket_limit)

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report, sample_limit=args.sample_limit)
    _write_csv(Path(args.csv_output), rows)

    print(
        json.dumps(
            {
                "json_output": str(json_output),
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
