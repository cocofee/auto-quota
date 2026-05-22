from __future__ import annotations

import argparse
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
    DEFAULT_INPUTS,
    TARGET_REASONS,
    _clean,
    _expected_families,
    _load_inputs,
    _query_family,
    _read_jsonl,
    _top_family,
    classify,
)


DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_family_gap_report.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_family_gap_report.md"

INSTALL_FAMILIES = {
    "pipe",
    "valve",
    "duct",
    "fan",
    "cable",
    "cable_head",
    "wire",
    "conduit",
    "bridge",
    "electrical_box",
    "support",
    "sleeve",
    "sanitary",
    "pump",
    "instrument",
    "lamp",
    "socket",
    "switch",
}
NON_INSTALL_FAMILIES = {"concrete", "rebar", "formwork"}
INSTALL_PROVINCE_MARKERS = ("安装工程", "通用安装")
NON_INSTALL_PROVINCE_MARKERS = ("房屋建筑", "建筑与装饰", "市政", "园林")
INSTALL_QUERY_MARKERS = (
    "管道",
    "钢管",
    "塑料管",
    "阀",
    "桥架",
    "线槽",
    "配管",
    "配线",
    "穿线",
    "电缆",
    "电线",
    "配电箱",
    "控制箱",
    "开关柜",
    "灯",
    "插座",
    "风管",
    "风机",
    "水泵",
    "泵",
    "仪表",
    "传感器",
    "水表",
    "热量表",
    "套管",
    "支架",
    "吊架",
    "卫生器具",
    "洁具",
    "过滤器",
    "接头",
    "避雷器",
    "控制器",
    "模块",
)
NON_INSTALL_QUERY_MARKERS = (
    "土方",
    "石方",
    "回填",
    "填方",
    "挖",
    "砌",
    "墙",
    "砖",
    "门",
    "窗",
    "楼地面",
    "楼（地）面",
    "地面",
    "屋面",
    "防水",
    "抹灰",
    "油漆",
    "涂料",
    "砂浆",
    "块料",
    "垂直运输",
    "散水",
    "坡道",
    "栏杆",
    "窗帘盒",
    "栽植",
    "乔木",
    "灌木",
    "绿化",
    "平石",
    "基层",
    "路面",
    "拆除",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker and marker in text]


def _expected_names(row: dict[str, Any]) -> list[str]:
    return [_clean(signal.get("name")) for signal in row.get("expected_signals") or [] if _clean(signal.get("name"))]


def _family_set(row: dict[str, Any]) -> set[str]:
    families = set(_expected_families(row))
    for family in (_query_family(row), _top_family(row)):
        if family:
            families.add(family)
    return families


def _classify_family_gap(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    province = _clean(row.get("province"))
    query = _clean(row.get("query"))
    expected_names = " ".join(_expected_names(row))
    text = f"{query} {expected_names}"
    families = _family_set(row)

    install_families = sorted(families & INSTALL_FAMILIES)
    non_install_families = sorted(families & NON_INSTALL_FAMILIES)
    install_markers = _contains_any(text, INSTALL_QUERY_MARKERS)
    non_install_markers = _contains_any(text, NON_INSTALL_QUERY_MARKERS)
    install_province = any(marker in province for marker in INSTALL_PROVINCE_MARKERS)
    non_install_province = any(marker in province for marker in NON_INSTALL_PROVINCE_MARKERS)

    notes: list[str] = []
    if install_families:
        notes.append(f"install_family={','.join(install_families)}")
    if non_install_families:
        notes.append(f"non_install_family={','.join(non_install_families)}")
    if install_markers:
        notes.append(f"install_terms={','.join(install_markers[:3])}")
    if non_install_markers:
        notes.append(f"non_install_terms={','.join(non_install_markers[:3])}")
    if install_province:
        notes.append("install_province")
    if non_install_province:
        notes.append("non_install_province")

    strong_non_install = bool(non_install_markers) or bool(non_install_families and non_install_province)
    strong_install = bool(install_families) or bool(install_markers and install_province)

    if strong_install and not strong_non_install:
        return "install_high_freq_missed", "安装高频对象族漏识别", notes
    if strong_install and strong_non_install and install_province and not non_install_province:
        return "install_high_freq_missed", "安装高频对象族漏识别", notes + ["mixed_signals_install_province_wins"]
    if install_province and not non_install_province and not non_install_markers:
        return "install_high_freq_missed", "安装高频对象族漏识别", notes + ["install_province_fallback"]
    return "non_install_civil_municipal_gap", "非安装/土建市政对象族覆盖缺口", notes or ["no_install_signal"]


def _sample(row: dict[str, Any], *, gap_key: str, gap_label: str, notes: list[str]) -> dict[str, Any]:
    return {
        "split": row.get("split"),
        "province": row.get("province"),
        "sample_id": row.get("sample_id"),
        "bucket": row.get("bucket"),
        "miss_reason": row.get("miss_reason"),
        "gap_key": gap_key,
        "gap_label": gap_label,
        "notes": notes,
        "query": row.get("query"),
        "query_family": _query_family(row),
        "top_family": _top_family(row),
        "expected_families": sorted(_expected_families(row)),
        "expected_ids": row.get("expected_ids"),
        "expected_names": _expected_names(row)[:2],
        "top_ids": row.get("top_ids"),
    }


def _counter_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _collect(inputs: list[tuple[str, Path]], *, sample_limit: int, bucket_limit: int) -> dict[str, Any]:
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
            gap_key, gap_label, notes = _classify_family_gap(row)
            row["_gap_key"] = gap_key
            row["_gap_label"] = gap_label
            row["_gap_notes"] = notes
            rows.append(row)

    gap_counts: Counter[str] = Counter(_clean(row.get("_gap_key")) for row in rows)
    province_counts: Counter[str] = Counter(_clean(row.get("province")) for row in rows)
    bucket_counts: Counter[str] = Counter(_clean(row.get("bucket")) for row in rows)
    miss_reason_counts: Counter[str] = Counter(_clean(row.get("miss_reason")) for row in rows)

    by_gap: dict[str, Any] = {}
    for gap_key, gap_count in gap_counts.most_common():
        gap_rows = [row for row in rows if row["_gap_key"] == gap_key]
        province_counter: Counter[str] = Counter()
        bucket_counter: Counter[str] = Counter()
        family_counter: Counter[str] = Counter()
        group_counter: Counter[str] = Counter()
        note_counter: Counter[str] = Counter()
        for row in gap_rows:
            province = _clean(row.get("province"))
            bucket = _clean(row.get("bucket"))
            family = _query_family(row) or ",".join(sorted(_expected_families(row))) or "<empty>"
            province_counter[province] += 1
            bucket_counter[bucket] += 1
            family_counter[family] += 1
            group_counter[f"{family}|{province}"] += 1
            note_counter.update(row.get("_gap_notes") or [])

        group_rank = {item["key"]: idx for idx, item in enumerate(_counter_items(group_counter, bucket_limit))}
        sorted_rows = sorted(
            gap_rows,
            key=lambda row: (
                group_rank.get(f"{_query_family(row) or ','.join(sorted(_expected_families(row))) or '<empty>'}|{_clean(row.get('province'))}", 9999),
                _clean(row.get("split")),
                _clean(row.get("province")),
                _clean(row.get("sample_id")),
            ),
        )
        by_gap[gap_key] = {
            "label": gap_rows[0]["_gap_label"] if gap_rows else gap_key,
            "rows": gap_count,
            "province_counts": _counter_items(province_counter, bucket_limit),
            "source_bucket_counts": _counter_items(bucket_counter, bucket_limit),
            "family_counts": _counter_items(family_counter, bucket_limit),
            "top_groups": _counter_items(group_counter, bucket_limit),
            "note_counts": _counter_items(note_counter, bucket_limit),
            "samples": [
                _sample(row, gap_key=gap_key, gap_label=row["_gap_label"], notes=row["_gap_notes"])
                for row in sorted_rows[:sample_limit]
            ],
        }

    return {
        "summary": {
            "inputs": [{"split": split, "path": str(path)} for split, path in inputs],
            "target_definition": "miss_reason in wrong_other/wrong_rank/wrong_family and primary_category == family_unrecognized",
            "rows": len(rows),
            "gap_counts": _counter_items(gap_counts, bucket_limit),
            "miss_reason_counts": _counter_items(miss_reason_counts, bucket_limit),
            "source_bucket_counts": _counter_items(bucket_counts, bucket_limit),
            "province_counts": _counter_items(province_counts, bucket_limit),
        },
        "classification_rules": {
            "install_high_freq_missed": {
                "label": "安装高频对象族漏识别",
                "install_families": sorted(INSTALL_FAMILIES),
                "install_province_markers": list(INSTALL_PROVINCE_MARKERS),
                "install_query_markers": list(INSTALL_QUERY_MARKERS),
            },
            "non_install_civil_municipal_gap": {
                "label": "非安装/土建市政对象族覆盖缺口",
                "non_install_families": sorted(NON_INSTALL_FAMILIES),
                "non_install_province_markers": list(NON_INSTALL_PROVINCE_MARKERS),
                "non_install_query_markers": list(NON_INSTALL_QUERY_MARKERS),
            },
        },
        "by_gap": by_gap,
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


def _write_markdown(path: Path, report: dict[str, Any], *, sample_limit: int) -> None:
    lines: list[str] = []
    summary = report["summary"]
    lines.append("# Goal Expanded Family Gap Report")
    lines.append("")
    lines.append("只读拆分报告：只拆 `对象族未识别/错识别`，不调参、不改搜索逻辑。")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        _md_table(
            [["metric", "value"], ["rows", summary["rows"]], ["target_definition", summary["target_definition"]]]
        )
    )
    lines.append("")
    lines.append("## Gap Counts")
    lines.append("")
    lines.append(
        _md_table(
            [["gap", "label", "count"]]
            + [
                [item["key"], report["by_gap"].get(item["key"], {}).get("label", item["key"]), item["count"]]
                for item in summary["gap_counts"]
            ]
        )
    )
    lines.append("")
    lines.append("## Source Buckets")
    lines.append("")
    lines.append(_md_table([["bucket", "count"]] + [[item["key"], item["count"]] for item in summary["source_bucket_counts"]]))
    lines.append("")

    for gap_key, data in report["by_gap"].items():
        lines.append(f"## {data['label']}")
        lines.append("")
        lines.append(f"Rows: {data['rows']}")
        lines.append("")
        lines.append("Top groups:")
        lines.append("")
        lines.append(_md_table([["family_or_signal|province", "count"]] + [[item["key"], item["count"]] for item in data["top_groups"][:10]]))
        lines.append("")
        lines.append("Top notes:")
        lines.append("")
        lines.append(_md_table([["note", "count"]] + [[item["key"], item["count"]] for item in data["note_counts"][:10]]))
        lines.append("")
        lines.append(f"Samples (top {sample_limit}):")
        lines.append("")
        sample_rows = [["split", "province", "bucket", "miss_reason", "query", "expected_family", "expected_name", "notes"]]
        for sample in data["samples"][:sample_limit]:
            sample_rows.append(
                [
                    sample.get("split", ""),
                    sample.get("province", ""),
                    sample.get("bucket", ""),
                    sample.get("miss_reason", ""),
                    sample.get("query", ""),
                    ",".join(sample.get("expected_families") or []) or "<empty>",
                    "; ".join(sample.get("expected_names") or []),
                    "; ".join(sample.get("notes") or []),
                ]
            )
        lines.append(_md_table(sample_rows))
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Split family-unrecognized errors into install vs non-install gaps")
    parser.add_argument("--input", action="append", default=[], help="split=details.jsonl. Defaults to expanded heldout/hard details")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--sample-limit", type=int, default=16)
    parser.add_argument("--bucket-limit", type=int, default=20)
    args = parser.parse_args()

    inputs = _load_inputs(args.input)
    report = _collect(inputs, sample_limit=args.sample_limit, bucket_limit=args.bucket_limit)
    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report, sample_limit=args.sample_limit)
    print(
        json.dumps(
            {
                "json_output": str(json_output),
                "md_output": args.md_output,
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
