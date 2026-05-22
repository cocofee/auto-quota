from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_local_missing.csv"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_empty_family_recall_review.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_empty_family_recall_review_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_empty_family_recall_review_summary.md"

INSTALL_FAMILIES = {
    "bridge",
    "cable",
    "cable_head",
    "conduit",
    "duct",
    "electrical_box",
    "fan",
    "instrument",
    "lamp",
    "pipe",
    "pump",
    "sanitary",
    "socket",
    "sleeve",
    "support",
    "switch",
    "valve",
    "wire",
}

INSTALL_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("valve_meter_filter", ("阀", "过滤器", "水表", "热量表", "倒流防止器", "真空破坏器")),
    ("electrical_box", ("配电箱", "控制箱", "配电屏", "开关柜", "配电柜")),
    ("equipment_mechanical", ("塔器", "机柜", "电器", "设备安装", "机械设备")),
    ("weak_current_equipment", ("扩声", "存储设备", "网络", "服务器", "交换机", "技术服务", "广播", "对讲")),
    ("instrument_sensor", ("传感器", "流量计", "液位计", "变送器", "探测器", "仪表")),
    ("pipe_duct", ("管道", "钢管", "塑料管", "风管", "套管", "软管", "联箱")),
    ("pump_fan", ("泵", "风机", "风扇")),
    ("lamp_socket_switch", ("灯", "插座", "开关", "照明")),
    ("bridge_wire_conduit", ("桥架", "线槽", "配管", "穿线", "电缆", "电线")),
    ("sanitary", ("坐便", "大便器", "小便器", "洗脸盆", "地漏", "卫生器具")),
    ("support", ("支架", "吊架", "支吊架")),
)

CIVIL_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earthwork", ("挖", "土方", "石方", "回填", "填方")),
    ("waterproof_joint", ("防水", "涂膜", "卷材", "变形缝", "施工缝", "止水")),
    ("decoration_finish", ("墙面", "楼地面", "地面", "块料", "面砖", "收边条", "背景墙", "窗帘盒", "轨道", "天棚", "吊顶")),
    ("door_window", ("木质门", "门带套", "门窗", "窗")),
    ("demolition", ("拆除", "铲除")),
    ("pile_foundation", ("桩头", "截桩", "凿桩", "桩")),
    ("concrete_rebar", ("混凝土", "钢筋", "模板", "基层", "面层")),
    ("municipal_road", ("道路", "人行道", "路面", "井", "雨水口")),
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,|]", _clean(value)) if part.strip()]


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _first_bucket(text: str, buckets: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, str]:
    for bucket, tokens in buckets:
        for token in tokens:
            if token in text:
                return bucket, token
    return "", ""


def _install_family_hit(expected_families: list[str]) -> str:
    hits = [family for family in expected_families if family in INSTALL_FAMILIES]
    return ",".join(hits)


def classify_empty_family(row: dict[str, str]) -> dict[str, str]:
    query = _clean(row.get("query"))
    expected_families = _split_values(row.get("expected_families", ""))
    expected_books = _split_values(row.get("expected_books", ""))

    install_bucket, install_keyword = _first_bucket(query, INSTALL_BUCKETS)
    civil_bucket, civil_keyword = _first_bucket(query, CIVIL_BUCKETS)
    expected_install_families = _install_family_hit(expected_families)

    if civil_bucket and not install_bucket:
        return {
            "review_class": "civil_municipal_decoration_empty_family",
            "review_bucket": civil_bucket,
            "review_signal": civil_keyword,
            "review_reason": "civil_keyword",
        }
    if install_bucket:
        return {
            "review_class": "installation_object_family_missed",
            "review_bucket": install_bucket,
            "review_signal": install_keyword,
            "review_reason": "install_keyword",
        }
    if expected_install_families:
        return {
            "review_class": "installation_object_family_missed",
            "review_bucket": "expected_install_family",
            "review_signal": expected_install_families,
            "review_reason": "expected_family",
        }
    if expected_books and any(book.upper().startswith("C") for book in expected_books):
        return {
            "review_class": "installation_object_family_missed",
            "review_bucket": "expected_install_book",
            "review_signal": ",".join(expected_books),
            "review_reason": "expected_book_c_series",
        }
    return {
        "review_class": "civil_municipal_decoration_empty_family",
        "review_bucket": civil_bucket or "civil_other_or_uncovered",
        "review_signal": civil_keyword,
        "review_reason": "default_non_install_empty_family",
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "review_class",
        "review_bucket",
        "review_signal",
        "review_reason",
        "split",
        "reason",
        "group_id",
        "sample_id",
        "province",
        "source_file",
        "query",
        "expected_ids",
        "expected_families",
        "expected_books",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _summarize(rows: list[dict[str, Any]], top_limit: int) -> dict[str, Any]:
    total = len(rows)
    by_class = Counter(row["review_class"] for row in rows)
    by_split_class = Counter(f"{row['split']}|{row['review_class']}" for row in rows)
    by_bucket = Counter(f"{row['review_class']}|{row['review_bucket']}" for row in rows)
    by_reason = Counter(f"{row['review_class']}|{row['review_reason']}" for row in rows)
    by_expected_family = Counter(f"{row['review_class']}|{row.get('expected_families') or '<empty>'}" for row in rows)
    by_expected_book = Counter(f"{row['review_class']}|{row.get('expected_books') or '<empty>'}" for row in rows)
    by_province = Counter(f"{row['review_class']}|{row.get('province') or '<empty>'}" for row in rows)

    split_tables: dict[str, dict[str, int]] = defaultdict(dict)
    for key, count in by_split_class.items():
        split, review_class = key.split("|", 1)
        split_tables[split][review_class] = count

    return {
        "total_empty_family_rows": total,
        "class_counts": [
            {"review_class": key, "count": count, "rate": _rate(count, total)}
            for key, count in by_class.most_common()
        ],
        "split_class_counts": [
            {
                "split": split,
                "civil_municipal_decoration_empty_family": values.get("civil_municipal_decoration_empty_family", 0),
                "installation_object_family_missed": values.get("installation_object_family_missed", 0),
            }
            for split, values in sorted(split_tables.items())
        ],
        "bucket_counts": _top_items(by_bucket, top_limit),
        "reason_counts": _top_items(by_reason, top_limit),
        "expected_family_counts": _top_items(by_expected_family, top_limit),
        "expected_book_counts": _top_items(by_expected_book, top_limit),
        "province_counts": _top_items(by_province, top_limit),
    }


def _examples(rows: list[dict[str, Any]], review_class: str, limit: int) -> list[dict[str, Any]]:
    selected = [row for row in rows if row["review_class"] == review_class]
    selected.sort(key=lambda row: (row["review_bucket"], row["split"], row["sample_id"]))
    return [
        {
            "split": row["split"],
            "bucket": row["review_bucket"],
            "query": row["query"],
            "expected_ids": row["expected_ids"],
            "expected_families": row["expected_families"],
            "top1": f"{row['top1_id']} {row['top1_name']}".strip(),
        }
        for row in selected[:limit]
    ]


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
    return [["key", "count"], *[[item["key"], item["count"]] for item in items]]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Empty-Family Recall Review",
        "",
        "Stage 3.3 read-only review. It only classifies Top80 local-missing rows whose query_family is empty. No rule change, no model tuning, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["review_class", "count", "rate"],
                *[[item["review_class"], item["count"], item["rate"]] for item in summary["class_counts"]],
            ]
        ),
        "",
        "## Split",
        "",
        _md_table(
            [
                ["split", "civil_municipal_decoration_empty_family", "installation_object_family_missed"],
                *[
                    [
                        item["split"],
                        item["civil_municipal_decoration_empty_family"],
                        item["installation_object_family_missed"],
                    ]
                    for item in summary["split_class_counts"]
                ],
            ]
        ),
        "",
        "## Buckets",
        "",
        _md_table(_counter_table(summary["bucket_counts"])),
        "",
        "## Expected Family",
        "",
        _md_table(_counter_table(summary["expected_family_counts"])),
        "",
        "## Examples: Installation Missed",
        "",
        _md_table(
            [["split", "bucket", "query", "expected_ids", "expected_families", "top1"]]
            + [
                [
                    item["split"],
                    item["bucket"],
                    item["query"],
                    item["expected_ids"],
                    item["expected_families"],
                    item["top1"],
                ]
                for item in report["installation_examples"]
            ]
        ),
        "",
        "## Examples: Civil / Municipal / Decoration",
        "",
        _md_table(
            [["split", "bucket", "query", "expected_ids", "expected_families", "top1"]]
            + [
                [
                    item["split"],
                    item["bucket"],
                    item["query"],
                    item["expected_ids"],
                    item["expected_families"],
                    item["top1"],
                ]
                for item in report["civil_examples"]
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], ["review_csv", report["review_csv"]]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review empty-family Top80 recall misses without changing search rules")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--top-limit", type=int, default=20)
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.input))
    empty_rows = [row for row in source_rows if not _clean(row.get("query_family"))]
    reviewed_rows: list[dict[str, Any]] = []
    for row in empty_rows:
        item = dict(row)
        item.update(classify_empty_family(row))
        reviewed_rows.append(item)

    _write_csv(Path(args.output_csv), reviewed_rows)
    report = {
        "stage": "Goal LTR v1 / stage 3.3 empty-family recall review",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input": args.input,
        "review_csv": args.output_csv,
        "summary": _summarize(reviewed_rows, args.top_limit),
        "installation_examples": _examples(reviewed_rows, "installation_object_family_missed", 12),
        "civil_examples": _examples(reviewed_rows, "civil_municipal_decoration_empty_family", 12),
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
                    "read_only": True,
                    "input_rows": len(source_rows),
                    "empty_family_rows": len(empty_rows),
                    "elapsed_sec": report["elapsed_sec"],
                },
                "class_counts": report["summary"]["class_counts"],
                "artifacts": {
                    "review_csv": args.output_csv,
                    "report_json": args.report_json,
                    "report_md": args.report_md,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
