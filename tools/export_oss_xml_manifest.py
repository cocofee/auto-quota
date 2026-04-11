"""
Export an XML recovery manifest from historical oss_import records.

The manifest helps recover original XML files that were previously imported
into ExperienceDB but are no longer present in the current workspace.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import config
from db.sqlite import connect as db_connect


def _safe_dt(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    if isinstance(timestamp, str):
        return timestamp
    return datetime.fromtimestamp(float(timestamp)).isoformat(timespec="seconds")


def load_manifest_rows(db_path: Path) -> list[dict]:
    conn = db_connect(db_path, row_factory=True)
    try:
        rows = conn.execute(
            """
            SELECT
                project_name,
                province,
                COUNT(*) AS item_count,
                COUNT(DISTINCT specialty) AS specialty_count,
                MIN(created_at) AS first_seen_at,
                MAX(created_at) AS last_seen_at
            FROM experiences
            WHERE source='oss_import'
              AND project_name IS NOT NULL
              AND project_name != ''
            GROUP BY project_name, province
            ORDER BY item_count DESC, project_name ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def build_summary(rows: list[dict]) -> dict:
    province_counter = Counter()
    project_counter = set()
    item_count = 0
    item_by_project = defaultdict(int)
    first_seen = None
    last_seen = None

    for row in rows:
        project_name = row.get("project_name") or ""
        province = row.get("province") or ""
        count = int(row.get("item_count") or 0)
        project_counter.add(project_name)
        province_counter[province] += count
        item_count += count
        item_by_project[project_name] += count

        row_first = row.get("first_seen_at")
        row_last = row.get("last_seen_at")
        if row_first is not None:
            first_seen = row_first if first_seen is None else min(first_seen, row_first)
        if row_last is not None:
            last_seen = row_last if last_seen is None else max(last_seen, row_last)

    top_projects = [
        {"project_name": name, "item_count": count}
        for name, count in sorted(item_by_project.items(), key=lambda item: (-item[1], item[0]))[:20]
    ]
    top_provinces = [
        {"province": province, "item_count": count}
        for province, count in province_counter.most_common(20)
    ]

    return {
        "unique_project_names": len(project_counter),
        "project_province_rows": len(rows),
        "total_items": item_count,
        "first_seen_at": _safe_dt(first_seen),
        "last_seen_at": _safe_dt(last_seen),
        "top_projects": top_projects,
        "top_provinces": top_provinces,
    }


def export_manifest(rows: list[dict], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "oss_xml_manifest.json"
    csv_path = output_dir / "oss_xml_manifest.csv"
    summary_path = output_dir / "oss_xml_manifest_summary.md"

    normalized_rows = []
    for row in rows:
        normalized_rows.append(
            {
                "project_name": row.get("project_name") or "",
                "province": row.get("province") or "",
                "item_count": int(row.get("item_count") or 0),
                "specialty_count": int(row.get("specialty_count") or 0),
                "first_seen_at": _safe_dt(row.get("first_seen_at")),
                "last_seen_at": _safe_dt(row.get("last_seen_at")),
            }
        )

    summary = build_summary(normalized_rows)
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "rows": normalized_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "project_name",
                "province",
                "item_count",
                "specialty_count",
                "first_seen_at",
                "last_seen_at",
            ],
        )
        writer.writeheader()
        writer.writerows(normalized_rows)

    summary_lines = [
        "# OSS XML恢复清单",
        "",
        f"- 唯一项目名: {summary['unique_project_names']}",
        f"- 项目-省份记录: {summary['project_province_rows']}",
        f"- 总条目数: {summary['total_items']}",
        f"- 首次导入时间: {summary['first_seen_at']}",
        f"- 最后导入时间: {summary['last_seen_at']}",
        "",
        "## Top项目",
    ]
    for item in summary["top_projects"]:
        summary_lines.append(f"- {item['project_name']}: {item['item_count']}")
    summary_lines.append("")
    summary_lines.append("## Top省份")
    for item in summary["top_provinces"]:
        summary_lines.append(f"- {item['province']}: {item['item_count']}")
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "summary": str(summary_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a manifest of historical XML projects imported via oss_import."
    )
    parser.add_argument(
        "--db",
        default=str(config.get_experience_db_path()),
        help="Path to experience.db",
    )
    parser.add_argument(
        "--output-dir",
        default="output/xml_recovery",
        help="Directory for exported manifest files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(json.dumps({"error": f"experience db not found: {db_path}"}, ensure_ascii=False))
        return 1

    rows = load_manifest_rows(db_path)
    paths = export_manifest(rows, Path(args.output_dir))
    result = {
        "summary": build_summary(rows),
        "paths": paths,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
