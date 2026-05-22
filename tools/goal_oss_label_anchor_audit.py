from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
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
from src.goal_search.national_index import extract_signal, tokenize  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_SPLIT_DIR = DEFAULT_DATA_DIR / "splits_expanded"
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR / "anchor_audit"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_oss_label_anchor_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_oss_label_anchor_audit_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_oss_label_anchor_audit_details.csv"

DOMAIN_BUCKETS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("waterproof_joint", ("防水", "防潮", "涂膜", "卷材", "砂浆防水", "变形缝", "施工缝", "止水"), ("防水", "防潮", "涂膜", "卷材", "砂浆", "变形缝", "施工缝", "止水")),
    ("earthwork", ("挖一般土方", "挖一般石方", "土方", "石方", "回填", "填方"), ("土方", "石方", "回填", "填方", "挖")),
    ("decoration_finish", ("块料", "墙面", "楼地面", "地面", "面砖", "瓷砖", "收边条", "踢脚线", "窗帘盒", "天棚"), ("块料", "墙面", "楼地面", "地面", "面砖", "瓷砖", "收边条", "踢脚线", "窗帘盒", "天棚")),
    ("electrical_box", ("配电箱", "配电柜", "开关柜", "控制箱", "配电屏", "T接箱"), ("配电", "柜", "箱", "屏", "盘")),
    ("valve_meter_filter", ("阀门", "止回阀", "闸阀", "蝶阀", "球阀", "防火阀", "过滤器", "水表", "热量表", "倒流防止器", "真空破坏器"), ("阀", "过滤器", "水表", "热量表", "倒流防止器", "真空破坏器")),
    ("instrument_sensor", ("流量计", "传感器", "探测器", "液位计", "仪表"), ("流量计", "传感器", "探测器", "液位计", "仪表")),
    ("pipe", ("管道", "钢管", "塑料管", "镀锌钢管"), ("管", "DN", "De", "直径")),
    ("duct", ("风管", "风口", "风阀", "风幕"), ("风管", "风口", "风阀", "风幕")),
    ("wire_cable", ("电缆", "电线", "穿线", "配线", "桥架", "线槽"), ("电缆", "电线", "穿线", "配线", "桥架", "线槽")),
    ("lamp_socket_switch", ("灯", "照明", "插座", "开关", "地插"), ("灯", "照明", "插座", "开关")),
    ("support", ("支架", "吊架", "支吊架"), ("支架", "吊架", "支吊架")),
    ("sanitary", ("大便器", "小便器", "坐便", "洗脸盆", "地漏", "卫生器具"), ("大便器", "小便器", "坐便", "洗脸盆", "地漏", "卫生器具")),
    ("mechanical_equipment", ("泵", "风机", "塔器", "机柜", "设备安装"), ("泵", "风机", "塔", "机柜", "设备")),
)

WEAK_TOKENS = {
    "安装",
    "工程",
    "项目",
    "名称",
    "规格",
    "型号",
    "以内",
    "以下",
    "以上",
    "普通",
    "一般",
}


@dataclass
class ExpectedRecord:
    quota_id: str
    name: str
    unit: str = ""
    chapter: str = ""
    book: str = ""
    search_text: str = ""
    count: int = 0

    @property
    def text(self) -> str:
        return " ".join(part for part in (self.quota_id, self.name, self.unit, self.chapter, self.search_text) if part)


class ProvinceQuotaLookup:
    def __init__(self, province: str):
        self.province = province
        self.path = Path(config.get_quota_db_path(province))
        self.cache: dict[str, list[ExpectedRecord]] = {}
        self.columns: set[str] | None = None

    def get(self, quota_id: str) -> list[ExpectedRecord]:
        if quota_id not in self.cache:
            self._fetch(quota_id)
        return self.cache.get(quota_id, [])

    def _fetch(self, quota_id: str) -> None:
        self.cache[quota_id] = []
        if not self.path.exists():
            return
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            if self.columns is None:
                self.columns = {row["name"] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            optional = ["chapter", "book", "search_text"]
            select_cols = ["quota_id", "name", "unit"] + [col for col in optional if col in self.columns]
            rows = conn.execute(f"select {', '.join(select_cols)} from quotas where quota_id = ?", (quota_id,)).fetchall()
        finally:
            conn.close()

        count = len(rows)
        records: list[ExpectedRecord] = []
        for row in rows:
            data = {key: _clean(row[key]) for key in row.keys()}
            records.append(
                ExpectedRecord(
                    quota_id=_clean(data.get("quota_id")),
                    name=_clean(data.get("name")),
                    unit=_clean(data.get("unit")),
                    chapter=_clean(data.get("chapter")),
                    book=_clean(data.get("book")) or _quota_book(quota_id),
                    search_text=_clean(data.get("search_text")),
                    count=count,
                )
            )
        self.cache[quota_id] = records


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _expected_ids(row: dict[str, Any]) -> list[str]:
    raw = row.get("expected_ids") or row.get("expected_id") or row.get("quota_id")
    values: list[str] = []
    if isinstance(raw, list):
        values.extend(str(item) for item in raw)
    elif raw:
        values.append(str(raw))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value).split("|"):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                result.append(part)
    return result


def _query_text(row: dict[str, Any]) -> str:
    return " ".join(
        _clean(row.get(key))
        for key in ("bill_name", "name", "bill_text", "description", "specialty", "unit")
        if _clean(row.get(key))
    )


def _domain_bucket(text: str) -> tuple[str, str, tuple[str, ...]]:
    compact = _compact(text)
    for bucket, query_terms, expected_terms in DOMAIN_BUCKETS:
        for term in query_terms:
            if term in compact:
                return bucket, term, expected_terms
    return "", "", ()


def _token_overlap(query_text: str, expected_text: str) -> tuple[int, str]:
    query_tokens = {token for token in tokenize(query_text) if token not in WEAK_TOKENS and len(token) > 1}
    expected_tokens = {token for token in tokenize(expected_text) if token not in WEAK_TOKENS and len(token) > 1}
    overlap = sorted(query_tokens & expected_tokens)
    return len(overlap), ",".join(overlap[:12])


def _semantic_decision(primary_query: str, query_text: str, record: ExpectedRecord) -> dict[str, Any]:
    expected_text = record.text
    query_signal = extract_signal(primary_query)
    expected_signal = extract_signal(expected_text)
    query_family = _clean(query_signal.family)
    expected_family = _clean(expected_signal.family)
    bucket, bucket_term, expected_terms = _domain_bucket(primary_query)
    expected_compact = _compact(expected_text)
    overlap_count, overlap_tokens = _token_overlap(query_text, expected_text)

    if bucket:
        matched_term = next((term for term in expected_terms if term in expected_compact), "")
        if matched_term:
            return {
                "semantic_status": "semantic_supported",
                "semantic_reason": f"domain_anchor_match:{bucket}:{matched_term}",
                "query_family": query_family,
                "expected_family": expected_family,
                "domain_bucket": bucket,
                "domain_trigger": bucket_term,
                "token_overlap_count": overlap_count,
                "token_overlap": overlap_tokens,
            }
        if query_family and expected_family and query_family == expected_family:
            return {
                "semantic_status": "semantic_supported",
                "semantic_reason": f"family_match_without_domain_term:{query_family}",
                "query_family": query_family,
                "expected_family": expected_family,
                "domain_bucket": bucket,
                "domain_trigger": bucket_term,
                "token_overlap_count": overlap_count,
                "token_overlap": overlap_tokens,
            }
        if overlap_count >= 2:
            return {
                "semantic_status": "semantic_supported",
                "semantic_reason": "token_overlap_ge_2_without_domain_term",
                "query_family": query_family,
                "expected_family": expected_family,
                "domain_bucket": bucket,
                "domain_trigger": bucket_term,
                "token_overlap_count": overlap_count,
                "token_overlap": overlap_tokens,
            }
        return {
            "semantic_status": "label_suspect",
            "semantic_reason": f"domain_anchor_missing_in_expected:{bucket}:{bucket_term}",
            "query_family": query_family,
            "expected_family": expected_family,
            "domain_bucket": bucket,
            "domain_trigger": bucket_term,
            "token_overlap_count": overlap_count,
            "token_overlap": overlap_tokens,
        }

    if query_family and expected_family:
        if query_family == expected_family:
            return {
                "semantic_status": "semantic_supported",
                "semantic_reason": f"family_match:{query_family}",
                "query_family": query_family,
                "expected_family": expected_family,
                "domain_bucket": "",
                "domain_trigger": "",
                "token_overlap_count": overlap_count,
                "token_overlap": overlap_tokens,
            }
        if overlap_count >= 2:
            return {
                "semantic_status": "semantic_supported",
                "semantic_reason": "token_overlap_ge_2_despite_family_conflict",
                "query_family": query_family,
                "expected_family": expected_family,
                "domain_bucket": "",
                "domain_trigger": "",
                "token_overlap_count": overlap_count,
                "token_overlap": overlap_tokens,
            }
        return {
            "semantic_status": "label_suspect",
            "semantic_reason": f"family_conflict:{query_family}!={expected_family}",
            "query_family": query_family,
            "expected_family": expected_family,
            "domain_bucket": "",
            "domain_trigger": "",
            "token_overlap_count": overlap_count,
            "token_overlap": overlap_tokens,
        }

    if overlap_count >= 2:
        return {
            "semantic_status": "semantic_supported",
            "semantic_reason": "token_overlap_ge_2",
            "query_family": query_family,
            "expected_family": expected_family,
            "domain_bucket": "",
            "domain_trigger": "",
            "token_overlap_count": overlap_count,
            "token_overlap": overlap_tokens,
        }

    return {
        "semantic_status": "no_strong_conflict",
        "semantic_reason": "no_domain_or_family_conflict",
        "query_family": query_family,
        "expected_family": expected_family,
        "domain_bucket": "",
        "domain_trigger": "",
        "token_overlap_count": overlap_count,
        "token_overlap": overlap_tokens,
    }


def _group_status(expected_rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not expected_rows:
        return "anchor_invalid", "no_expected_ids"
    if any(row["anchor_status"] != "anchor_unique" for row in expected_rows):
        return "anchor_invalid", "expected_id_not_unique_or_missing"
    if any(row["semantic_status"] == "semantic_supported" for row in expected_rows):
        return "anchor_reliable", "has_semantic_supported_expected"
    if all(row["semantic_status"] == "label_suspect" for row in expected_rows):
        return "label_suspect", "all_expected_ids_semantic_suspect"
    return "anchor_usable_no_strong_conflict", "unique_anchor_no_strong_conflict"


def _load_split_rows(split_dir: Path, split: str) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(split_dir / f"{split}.jsonl")
    result: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=1):
        sample_id = _clean(row.get("sample_id") or row.get("bill_id") or row.get("idx") or idx)
        group_id = f"{split}:{idx}:{sample_id}"
        result[group_id] = row
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "split",
        "group_id",
        "group_status",
        "group_reason",
        "recommended_for_validation",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "expected_id",
        "anchor_status",
        "anchor_count",
        "semantic_status",
        "semantic_reason",
        "query_family",
        "expected_family",
        "domain_bucket",
        "domain_trigger",
        "token_overlap_count",
        "token_overlap",
        "expected_book",
        "expected_name",
        "expected_chapter",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_group_outputs(
    output_dir: Path,
    split: str,
    group_rows: list[dict[str, Any]],
    split_full_rows: dict[str, dict[str, Any]],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reliable = [row["group_id"] for row in group_rows if row["recommended_for_validation"]]
    excluded = [row["group_id"] for row in group_rows if not row["recommended_for_validation"]]
    reliable_path = output_dir / f"{split}_validation_group_ids.txt"
    excluded_path = output_dir / f"{split}_excluded_group_ids.txt"
    reliable_path.write_text("\n".join(reliable) + ("\n" if reliable else ""), encoding="utf-8")
    excluded_path.write_text("\n".join(excluded) + ("\n" if excluded else ""), encoding="utf-8")

    status_by_group = {row["group_id"]: row for row in group_rows}
    validation_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    for group_id in reliable:
        row = dict(split_full_rows.get(group_id, {}))
        row["anchor_group_id"] = group_id
        row["anchor_status"] = status_by_group[group_id]["group_status"]
        row["anchor_reason"] = status_by_group[group_id]["group_reason"]
        validation_rows.append(row)
    for group_id in excluded:
        row = dict(split_full_rows.get(group_id, {}))
        row["anchor_group_id"] = group_id
        row["anchor_status"] = status_by_group[group_id]["group_status"]
        row["anchor_reason"] = status_by_group[group_id]["group_reason"]
        excluded_rows.append(row)
    validation_jsonl = output_dir / f"{split}_validation.jsonl"
    excluded_jsonl = output_dir / f"{split}_excluded.jsonl"
    _write_jsonl(validation_jsonl, validation_rows)
    _write_jsonl(excluded_jsonl, excluded_rows)
    return {
        "validation_group_ids": str(reliable_path),
        "excluded_group_ids": str(excluded_path),
        "validation_jsonl": str(validation_jsonl),
        "excluded_jsonl": str(excluded_jsonl),
    }


def _audit(args: argparse.Namespace) -> dict[str, Any]:
    data_dir = Path(args.data_dir)
    split_dir = Path(args.split_dir)
    output_dir = Path(args.output_dir)
    splits = args.splits
    lookups: dict[str, ProvinceQuotaLookup] = {}
    detail_rows: list[dict[str, Any]] = []
    split_summaries: list[dict[str, Any]] = []

    for split in splits:
        group_meta_rows = _read_jsonl(data_dir / f"ltr_group_{split}.jsonl")
        split_full_rows = _load_split_rows(split_dir, split)
        group_level_rows: list[dict[str, Any]] = []

        for meta in group_meta_rows:
            group_id = _clean(meta.get("group_id"))
            full_row = split_full_rows.get(group_id, meta)
            province = _clean(meta.get("province") or full_row.get("province"))
            query = _clean(meta.get("query") or full_row.get("bill_name") or full_row.get("name"))
            query_text = _query_text(full_row) or query
            if province not in lookups:
                lookups[province] = ProvinceQuotaLookup(province)
            lookup = lookups[province]

            expected_detail_rows: list[dict[str, Any]] = []
            for expected_id in _expected_ids(meta):
                records = lookup.get(expected_id)
                if not records:
                    expected_detail_rows.append(
                        {
                            "split": split,
                            "group_id": group_id,
                            "sample_id": _clean(meta.get("sample_id")),
                            "source_file": _clean(meta.get("source_file")),
                            "project_name": _clean(meta.get("project_name")),
                            "province": province,
                            "query": query,
                            "expected_id": expected_id,
                            "anchor_status": "anchor_missing",
                            "anchor_count": 0,
                            "semantic_status": "anchor_invalid",
                            "semantic_reason": "expected_id_not_found_in_target_province_db",
                            "query_family": "",
                            "expected_family": "",
                            "domain_bucket": "",
                            "domain_trigger": "",
                            "token_overlap_count": 0,
                            "token_overlap": "",
                            "expected_book": _quota_book(expected_id),
                            "expected_name": "",
                            "expected_chapter": "",
                        }
                    )
                    continue

                for record in records:
                    decision = _semantic_decision(query, query_text, record)
                    expected_detail_rows.append(
                        {
                            "split": split,
                            "group_id": group_id,
                            "sample_id": _clean(meta.get("sample_id")),
                            "source_file": _clean(meta.get("source_file")),
                            "project_name": _clean(meta.get("project_name")),
                            "province": province,
                            "query": query,
                            "expected_id": expected_id,
                            "anchor_status": "anchor_unique" if record.count == 1 else "anchor_duplicate",
                            "anchor_count": record.count,
                            **decision,
                            "expected_book": record.book,
                            "expected_name": record.name,
                            "expected_chapter": record.chapter,
                        }
                    )

            group_status, group_reason = _group_status(expected_detail_rows)
            recommended = group_status in {"anchor_reliable", "anchor_usable_no_strong_conflict"}
            for row in expected_detail_rows:
                row["group_status"] = group_status
                row["group_reason"] = group_reason
                row["recommended_for_validation"] = recommended
                detail_rows.append(row)
            group_level_rows.append(
                {
                    "group_id": group_id,
                    "group_status": group_status,
                    "group_reason": group_reason,
                    "recommended_for_validation": recommended,
                }
            )

        group_status_counts = Counter(row["group_status"] for row in group_level_rows)
        semantic_counts = Counter(row["semantic_status"] for row in detail_rows if row["split"] == split)
        anchor_counts = Counter(row["anchor_status"] for row in detail_rows if row["split"] == split)
        list_paths = _write_group_outputs(output_dir, split, group_level_rows, split_full_rows)
        total = len(group_level_rows)
        validation_groups = sum(1 for row in group_level_rows if row["recommended_for_validation"])
        split_summaries.append(
            {
                "split": split,
                "groups": total,
                "validation_groups": validation_groups,
                "excluded_groups": total - validation_groups,
                "validation_rate": _rate(validation_groups, total),
                "group_status_counts": dict(group_status_counts),
                "semantic_status_counts": dict(semantic_counts),
                "anchor_status_counts": dict(anchor_counts),
                **list_paths,
            }
        )

    _write_csv(Path(args.details_csv), detail_rows)
    return {
        "stage": "Goal LTR v1 / stage 3.7 OSS label primary-key anchor audit",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": str(data_dir),
        "split_dir": str(split_dir),
        "output_dir": str(output_dir),
        "splits_requested": splits,
        "province_lookup_count": len(lookups),
        "details_csv": args.details_csv,
        "splits": split_summaries,
    }


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["splits"]
    lines = [
        "# Goal OSS Label Anchor Audit",
        "",
        "Stage 3.7 read-only audit. It checks whether `province + expected_id` uniquely anchors into the target local `quota.db`, then marks strong semantic conflicts as label-suspect. No search change and no model tuning.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["split", "groups", "validation_groups", "excluded_groups", "validation_rate"],
                *[
                    [
                        item["split"],
                        item["groups"],
                        item["validation_groups"],
                        item["excluded_groups"],
                        item["validation_rate"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Group Status",
        "",
    ]
    for item in summaries:
        rows = [["status", "count"]] + [[key, value] for key, value in sorted(item["group_status_counts"].items())]
        lines.extend([f"### {item['split']}", "", _md_table(rows), ""])
    lines.extend(
        [
            "## Artifacts",
            "",
            _md_table(
                [["artifact", "path"]]
                + [["details_csv", report["details_csv"]]]
                + [[f"{item['split']}_validation_group_ids", item["validation_group_ids"]] for item in summaries]
                + [[f"{item['split']}_excluded_group_ids", item["excluded_group_ids"]] for item in summaries]
                + [[f"{item['split']}_validation_jsonl", item["validation_jsonl"]] for item in summaries]
                + [[f"{item['split']}_excluded_jsonl", item["excluded_jsonl"]] for item in summaries]
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit OSS labels by province + expected_id primary-key anchoring")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--split-dir", default=str(DEFAULT_SPLIT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    report = _audit(args)
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)

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
                    "elapsed_sec": report["elapsed_sec"],
                    "splits": report["splits"],
                },
                "artifacts": {
                    "details_csv": args.details_csv,
                    "report_json": args.report_json,
                    "report_md": args.report_md,
                    "output_dir": args.output_dir,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
