from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config  # noqa: E402

DEFAULT_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_empty_family_recall_review.csv"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_waterproof_label_audit.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_waterproof_label_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_waterproof_label_audit_summary.md"

WATERPROOF_TERMS = ("防水", "防潮", "涂膜", "卷材", "砂浆", "聚氨酯", "沥青")
JOINT_TERMS = ("变形缝", "施工缝", "止水", "伸缩缝", "嵌缝")
INSTALL_CONTEXT_TERMS = ("防水型", "按钮", "检修盒", "室外", "配电", "控制箱", "开关")
INSTALL_EXPECTED_TERMS = ("按钮", "控制", "检修", "接线盒", "明装", "配电", "开关", "盘面", "防水")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _split_ids(value: str) -> list[str]:
    return [part.strip() for part in _clean(value).split("|") if part.strip()]


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _first_any(text: str, terms: tuple[str, ...]) -> str:
    return next((term for term in terms if term in text), "")


def _quota_book(quota_id: str) -> str:
    qid = _clean(quota_id)
    match = re.match(r"([A-Z]\d+)-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"([A-Z])-\d+-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"2-(\d+)-", qid)
    if match:
        return "2"
    match = re.match(r"(\d+)-", qid)
    if match:
        return match.group(1)
    return ""


class ProvinceQuotaLookup:
    def __init__(self, province: str):
        self.province = province
        self.path = Path(config.get_quota_db_path(province))
        self.cache: dict[str, dict[str, str] | None] = {}
        self.columns: set[str] | None = None

    def get(self, quota_id: str) -> dict[str, str] | None:
        if quota_id not in self.cache:
            self._fetch([quota_id])
        return self.cache.get(quota_id)

    def _fetch(self, quota_ids: list[str]) -> None:
        for quota_id in quota_ids:
            self.cache.setdefault(quota_id, None)
        if not self.path.exists():
            return
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            if self.columns is None:
                self.columns = {row["name"] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            optional = ["work_type", "specialty", "chapter", "book", "search_text"]
            select_cols = ["quota_id", "name", "unit"] + [col for col in optional if col in self.columns]
            placeholders = ",".join("?" for _ in quota_ids)
            rows = conn.execute(
                f"select {', '.join(select_cols)} from quotas where quota_id in ({placeholders})",
                quota_ids,
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            data = {key: _clean(row[key]) for key in row.keys()}
            data["book"] = data.get("book") or _quota_book(data.get("quota_id", ""))
            data["expected_text"] = " ".join(
                data.get(key, "")
                for key in ("quota_id", "name", "unit", "work_type", "specialty", "chapter", "search_text")
                if data.get(key)
            )
            self.cache[data["quota_id"]] = data


def _query_subtype(query: str) -> tuple[str, str]:
    compact = _compact(query)
    install = _first_any(compact, INSTALL_CONTEXT_TERMS)
    if install:
        return "install_context", install
    joint = _first_any(compact, JOINT_TERMS)
    if joint:
        return "joint", joint
    waterproof = _first_any(compact, WATERPROOF_TERMS)
    if waterproof:
        return "waterproof", waterproof
    return "unknown", ""


def _classify(query: str, expected: dict[str, str] | None) -> dict[str, str]:
    subtype, query_signal = _query_subtype(query)
    if expected is None:
        return {
            "query_subtype": subtype,
            "query_signal": query_signal,
            "label_class": "expected_not_in_local_db",
            "label_evidence": "expected_id_missing_from_target_quota_db",
        }

    expected_text = _compact(expected.get("expected_text", ""))
    if subtype == "install_context":
        signal = _first_any(expected_text, INSTALL_EXPECTED_TERMS)
        if signal:
            return {
                "query_subtype": subtype,
                "query_signal": query_signal,
                "label_class": "expected_semantic_correct_install_context",
                "label_evidence": f"expected_install_signal:{signal}",
            }
        return {
            "query_subtype": subtype,
            "query_signal": query_signal,
            "label_class": "suspected_oss_label_mismatch",
            "label_evidence": "install_query_but_expected_lacks_install_signal",
        }

    if subtype == "joint":
        signal = _first_any(expected_text, JOINT_TERMS)
        if signal:
            return {
                "query_subtype": subtype,
                "query_signal": query_signal,
                "label_class": "expected_semantic_correct",
                "label_evidence": f"expected_joint_signal:{signal}",
            }
        return {
            "query_subtype": subtype,
            "query_signal": query_signal,
            "label_class": "suspected_oss_label_mismatch",
            "label_evidence": "joint_query_but_expected_lacks_joint_signal",
        }

    if subtype == "waterproof":
        signal = _first_any(expected_text, WATERPROOF_TERMS)
        if signal:
            return {
                "query_subtype": subtype,
                "query_signal": query_signal,
                "label_class": "expected_semantic_correct",
                "label_evidence": f"expected_waterproof_signal:{signal}",
            }
        return {
            "query_subtype": subtype,
            "query_signal": query_signal,
            "label_class": "suspected_oss_label_mismatch",
            "label_evidence": "waterproof_query_but_expected_lacks_waterproof_signal",
        }

    return {
        "query_subtype": subtype,
        "query_signal": query_signal,
        "label_class": "needs_manual_review",
        "label_evidence": "unknown_query_subtype",
    }


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row.get("review_bucket") == "waterproof_joint"]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "label_class",
        "label_evidence",
        "query_subtype",
        "query_signal",
        "split",
        "sample_id",
        "province",
        "query",
        "expected_id",
        "expected_book",
        "expected_name",
        "expected_chapter",
        "expected_text",
        "source_file",
        "group_id",
        "top1_id",
        "top1_name",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _top_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common()]


def _audit(input_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows = _read_rows(input_path)
    lookups: dict[str, ProvinceQuotaLookup] = {}
    output_rows: list[dict[str, Any]] = []
    for row in source_rows:
        province = _clean(row.get("province"))
        if province not in lookups:
            lookups[province] = ProvinceQuotaLookup(province)
        for expected_id in _split_ids(row.get("expected_ids", "")):
            expected = lookups[province].get(expected_id)
            classification = _classify(row.get("query", ""), expected)
            output_rows.append(
                {
                    **classification,
                    "split": _clean(row.get("split")),
                    "sample_id": _clean(row.get("sample_id")),
                    "province": province,
                    "query": _clean(row.get("query")),
                    "expected_id": expected_id,
                    "expected_book": _clean((expected or {}).get("book")) or _quota_book(expected_id),
                    "expected_name": _clean((expected or {}).get("name")),
                    "expected_chapter": _clean((expected or {}).get("chapter")),
                    "expected_text": _clean((expected or {}).get("expected_text")),
                    "source_file": _clean(row.get("source_file")),
                    "group_id": _clean(row.get("group_id")),
                    "top1_id": _clean(row.get("top1_id")),
                    "top1_name": _clean(row.get("top1_name")),
                }
            )

    class_counts = Counter(row["label_class"] for row in output_rows)
    subtype_counts = Counter(row["query_subtype"] for row in output_rows)
    split_class_counts = Counter(f"{row['split']}|{row['label_class']}" for row in output_rows)
    summary = {
        "source_rows": len(source_rows),
        "expected_rows": len(output_rows),
        "class_counts": [
            {"label_class": key, "count": count, "rate": _rate(count, len(output_rows))}
            for key, count in class_counts.most_common()
        ],
        "query_subtype_counts": _top_items(subtype_counts),
        "split_class_counts": _top_items(split_class_counts),
        "province_count": len(lookups),
    }
    return output_rows, summary


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


def _examples(rows: list[dict[str, Any]], label_class: str, limit: int = 12) -> list[list[object]]:
    selected = [row for row in rows if row["label_class"] == label_class]
    return [
        [
            row["split"],
            row["query"],
            row["expected_id"],
            row["expected_name"],
            row["label_evidence"],
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    rows = report["rows"]
    lines = [
        "# Goal Waterproof Label Audit",
        "",
        "Stage 3.6 read-only label consistency audit. It only checks `waterproof_joint` expected ids against target-province local quota names. No search change, no model tuning.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["label_class", "count", "rate"],
                *[[item["label_class"], item["count"], item["rate"]] for item in summary["class_counts"]],
            ]
        ),
        "",
        "## Query Subtype",
        "",
        _md_table([["query_subtype", "count"], *[[item["key"], item["count"]] for item in summary["query_subtype_counts"]]]),
        "",
        "## Suspected Mismatch Examples",
        "",
        _md_table([["split", "query", "expected_id", "expected_name", "evidence"]] + _examples(rows, "suspected_oss_label_mismatch")),
        "",
        "## Semantic Correct Examples",
        "",
        _md_table([["split", "query", "expected_id", "expected_name", "evidence"]] + _examples(rows, "expected_semantic_correct")),
        "",
        "## Install Context Examples",
        "",
        _md_table([["split", "query", "expected_id", "expected_name", "evidence"]] + _examples(rows, "expected_semantic_correct_install_context")),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], ["audit_csv", report["audit_csv"]]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit waterproof_joint expected label consistency")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    rows, summary = _audit(Path(args.input))
    _write_csv(Path(args.output_csv), rows)
    report = {
        "stage": "Goal LTR v1 / stage 3.6 waterproof_joint label consistency audit",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input": args.input,
        "audit_csv": args.output_csv,
        "summary": summary,
        "rows": rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    **summary,
                },
                "artifacts": {
                    "audit_csv": args.output_csv,
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
