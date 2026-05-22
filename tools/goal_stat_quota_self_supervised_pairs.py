from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search.national_index import extract_signal, is_pipe_device_false_trigger  # noqa: E402

DEFAULT_PROVINCE_ROOT = PROJECT_ROOT / "db" / "provinces"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_summary.md"
DEFAULT_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_family.csv"
DEFAULT_PROVINCE_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_province_family.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _pair_count(total: int) -> int:
    return total * (total - 1) // 2 if total >= 2 else 0


def _contrast_pairs(counter: Counter[str]) -> int:
    total = sum(counter.values())
    if total < 2 or len(counter) < 2:
        return 0
    same_value_pairs = sum(_pair_count(count) for count in counter.values())
    return _pair_count(total) - same_value_pairs


def _column_names(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("pragma table_info(quotas)").fetchall()}


def _select_clause(columns: set[str]) -> str:
    wanted = [
        "quota_id",
        "name",
        "unit",
        "book",
        "chapter",
        "specialty",
        "work_type",
        "material",
        "connection",
        "dn",
        "cable_section",
        "circuits",
    ]
    parts = [column if column in columns else f"NULL AS {column}" for column in wanted]
    return ", ".join(parts)


def _iter_quota_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = _column_names(conn)
        if "quotas" not in {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}:
            return
        sql = f"select {_select_clause(columns)} from quotas"
        for row in conn.execute(sql):
            yield dict(row)
    finally:
        conn.close()


def _quota_dbs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("quota.db") if path.is_file())


def _province_name(db_path: Path, root: Path) -> str:
    try:
        return str(db_path.parent.relative_to(root))
    except ValueError:
        return db_path.parent.name


def _param_value(signal: Any, row: dict[str, Any]) -> str:
    param_type = _clean(signal.param_type)
    if not param_type:
        return ""
    value = getattr(signal, param_type, None)
    if value is None and param_type in row:
        value = row.get(param_type)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _clean(value)


def _subtype_key(signal: Any) -> str:
    parts = [
        _clean(signal.action),
        _clean(signal.material),
        _clean(signal.connection),
        _clean(signal.install_method),
    ]
    return "|".join(part for part in parts if part)


def _book(row: dict[str, Any]) -> str:
    return _clean(row.get("book")) or _clean(row.get("specialty")) or _clean(row.get("work_type"))


def _scan(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.province_root)
    db_paths = _quota_dbs(root)
    if args.limit_dbs > 0:
        db_paths = db_paths[: args.limit_dbs]

    records_by_family: Counter[str] = Counter()
    records_by_province_family: Counter[tuple[str, str]] = Counter()
    province_by_family: dict[str, set[str]] = defaultdict(set)
    param_groups: dict[tuple[str, str, str, str, str], Counter[str]] = defaultdict(Counter)
    subtype_groups: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    skipped: Counter[str] = Counter()
    db_row_counts: Counter[str] = Counter()

    for db_index, db_path in enumerate(db_paths, 1):
        province = _province_name(db_path, root)
        if args.skip_test_provinces and ("test" in province.lower() or province.startswith("{")):
            skipped["test_or_synthetic_province"] += 1
            continue
        try:
            rows = _iter_quota_rows(db_path)
            if rows is None:
                skipped["missing_quotas_table"] += 1
                continue
            for row in rows:
                name = _clean(row.get("name"))
                if not name:
                    skipped["empty_name"] += 1
                    continue
                if is_pipe_device_false_trigger(name):
                    skipped["pipe_device_false_trigger"] += 1
                    continue
                text = " ".join(
                    part
                    for part in (
                        name,
                        _clean(row.get("chapter")),
                        _clean(row.get("specialty")),
                        _clean(row.get("work_type")),
                    )
                    if part
                )
                signal = extract_signal(text)
                family = _clean(signal.family)
                if not family:
                    skipped["empty_family"] += 1
                    continue

                unit = _clean(row.get("unit"))
                book = _book(row)
                param_type = _clean(signal.param_type)
                param_value = _param_value(signal, row)
                subtype_key = _subtype_key(signal)

                records_by_family[family] += 1
                records_by_province_family[(province, family)] += 1
                province_by_family[family].add(province)
                db_row_counts[province] += 1
                if param_type and param_value:
                    param_groups[(province, family, book, unit, param_type)][param_value] += 1
                if subtype_key:
                    subtype_groups[(province, family, book, unit)][subtype_key] += 1
        except sqlite3.DatabaseError:
            skipped["sqlite_error"] += 1

        if args.progress_every > 0 and db_index % args.progress_every == 0:
            print(f"processed {db_index}/{len(db_paths)} quota.db files", file=sys.stderr)

    family_param_pairs: Counter[str] = Counter()
    family_subtype_pairs: Counter[str] = Counter()
    family_param_group_count: Counter[str] = Counter()
    family_subtype_group_count: Counter[str] = Counter()
    province_family_param_pairs: Counter[tuple[str, str]] = Counter()
    province_family_subtype_pairs: Counter[tuple[str, str]] = Counter()

    for (province, family, _book_value, _unit, _param_type), counter in param_groups.items():
        pairs = _contrast_pairs(counter)
        if pairs:
            family_param_pairs[family] += pairs
            province_family_param_pairs[(province, family)] += pairs
            family_param_group_count[family] += 1

    for (province, family, _book_value, _unit), counter in subtype_groups.items():
        pairs = _contrast_pairs(counter)
        if pairs:
            family_subtype_pairs[family] += pairs
            province_family_subtype_pairs[(province, family)] += pairs
            family_subtype_group_count[family] += 1

    family_rows: list[dict[str, Any]] = []
    for family, records in records_by_family.most_common():
        param_pairs = family_param_pairs[family]
        subtype_pairs = family_subtype_pairs[family]
        total_pairs = param_pairs + subtype_pairs
        family_rows.append(
            {
                "family": family,
                "quota_records": records,
                "province_count": len(province_by_family[family]),
                "param_contrast_pairs": param_pairs,
                "subtype_contrast_pairs": subtype_pairs,
                "total_self_supervised_pairs": total_pairs,
                "param_contrast_groups": family_param_group_count[family],
                "subtype_contrast_groups": family_subtype_group_count[family],
            }
        )

    province_family_rows: list[dict[str, Any]] = []
    for (province, family), records in records_by_province_family.most_common():
        param_pairs = province_family_param_pairs[(province, family)]
        subtype_pairs = province_family_subtype_pairs[(province, family)]
        province_family_rows.append(
            {
                "province": province,
                "family": family,
                "quota_records": records,
                "param_contrast_pairs": param_pairs,
                "subtype_contrast_pairs": subtype_pairs,
                "total_self_supervised_pairs": param_pairs + subtype_pairs,
            }
        )

    return {
        "db_paths": db_paths,
        "family_rows": family_rows,
        "province_family_rows": province_family_rows,
        "skipped": skipped,
        "db_row_counts": db_row_counts,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _top_rows(rows: list[dict[str, Any]], key: str, limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: int(row.get(key) or 0), reverse=True)[:limit]


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(scan: dict[str, Any], top_limit: int) -> dict[str, Any]:
    family_rows = scan["family_rows"]
    province_family_rows = scan["province_family_rows"]
    total_records = sum(int(row["quota_records"]) for row in family_rows)
    total_param_pairs = sum(int(row["param_contrast_pairs"]) for row in family_rows)
    total_subtype_pairs = sum(int(row["subtype_contrast_pairs"]) for row in family_rows)
    total_pairs = total_param_pairs + total_subtype_pairs
    families_with_pairs = sum(1 for row in family_rows if int(row["total_self_supervised_pairs"]) > 0)
    provinces_with_pairs = len({row["province"] for row in province_family_rows if int(row["total_self_supervised_pairs"]) > 0})
    return {
        "quota_db_files_found": len(scan["db_paths"]),
        "quota_records_with_family": total_records,
        "family_count": len(family_rows),
        "families_with_pairs": families_with_pairs,
        "provinces_with_pairs": provinces_with_pairs,
        "param_contrast_pairs": total_param_pairs,
        "subtype_contrast_pairs": total_subtype_pairs,
        "total_self_supervised_pairs": total_pairs,
        "skipped": _counter_items(scan["skipped"], sum(scan["skipped"].values()), top_limit),
        "top_families_by_pairs": _top_rows(family_rows, "total_self_supervised_pairs", top_limit),
        "top_families_by_records": _top_rows(family_rows, "quota_records", top_limit),
        "top_province_families_by_pairs": _top_rows(province_family_rows, "total_self_supervised_pairs", top_limit),
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


def _row_table(rows: list[dict[str, Any]], fields: list[str]) -> list[list[object]]:
    return [fields, *[[row.get(field, "") for field in fields] for row in rows]]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    family_fields = [
        "family",
        "quota_records",
        "province_count",
        "param_contrast_pairs",
        "subtype_contrast_pairs",
        "total_self_supervised_pairs",
    ]
    province_family_fields = [
        "province",
        "family",
        "quota_records",
        "param_contrast_pairs",
        "subtype_contrast_pairs",
        "total_self_supervised_pairs",
    ]
    lines = [
        "# Goal Quota Self-Supervised Pair Stats",
        "",
        "Stage 4.9 eval-only statistic. It estimates clean same-province same-family hard pairs from quota.db files. It does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["quota_db_files_found", summary["quota_db_files_found"]],
                ["quota_records_with_family", summary["quota_records_with_family"]],
                ["family_count", summary["family_count"]],
                ["families_with_pairs", summary["families_with_pairs"]],
                ["provinces_with_pairs", summary["provinces_with_pairs"]],
                ["param_contrast_pairs", summary["param_contrast_pairs"]],
                ["subtype_contrast_pairs", summary["subtype_contrast_pairs"]],
                ["total_self_supervised_pairs", summary["total_self_supervised_pairs"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Top Families By Pairs",
        "",
        _md_table(_row_table(summary["top_families_by_pairs"], family_fields)),
        "",
        "## Top Province-Families By Pairs",
        "",
        _md_table(_row_table(summary["top_province_families_by_pairs"], province_family_fields)),
        "",
        "## Skipped",
        "",
        _md_table([["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in summary["skipped"]]]),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 4.9 eval-only stats for quota.db self-supervised hard pairs")
    parser.add_argument("--province-root", default=str(DEFAULT_PROVINCE_ROOT))
    parser.add_argument("--skip-test-provinces", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit-dbs", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--family-csv", default=str(DEFAULT_FAMILY_CSV))
    parser.add_argument("--province-family-csv", default=str(DEFAULT_PROVINCE_FAMILY_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    scan = _scan(args)
    summary = _summarize(scan, args.top_limit)
    family_rows = scan["family_rows"]
    province_family_rows = scan["province_family_rows"]

    family_fields = [
        "family",
        "quota_records",
        "province_count",
        "param_contrast_pairs",
        "subtype_contrast_pairs",
        "total_self_supervised_pairs",
        "param_contrast_groups",
        "subtype_contrast_groups",
    ]
    province_family_fields = [
        "province",
        "family",
        "quota_records",
        "param_contrast_pairs",
        "subtype_contrast_pairs",
        "total_self_supervised_pairs",
    ]
    _write_csv(Path(args.family_csv), family_rows, family_fields)
    _write_csv(Path(args.province_family_csv), province_family_rows, province_family_fields)

    artifacts = {
        "family_csv": args.family_csv,
        "province_family_csv": args.province_family_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.9 quota self-supervised pair stats",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "province_root": str(Path(args.province_root)),
        "skip_test_provinces": args.skip_test_provinces,
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
                        "quota_db_files_found",
                        "quota_records_with_family",
                        "family_count",
                        "families_with_pairs",
                        "provinces_with_pairs",
                        "total_self_supervised_pairs",
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
