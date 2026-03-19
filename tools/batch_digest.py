"""
Batch result digest tool.

Usage:
    python tools/batch_digest.py scan
    python tools/batch_digest.py scan --province 上海
    python tools/batch_digest.py export --province 上海
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.reason_reporting import reason_bucket, summarize_reason_bucket, update_reason_bucket


RESULTS_DIR = PROJECT_ROOT / "output" / "batch" / "results"
REPORT_DIR = PROJECT_ROOT / "output" / "temp"
REPORT_PATH = REPORT_DIR / "batch_digest_report.json"

HIGH_THRESHOLD = 85
MID_THRESHOLD = 60


def scan_results(province_filter: str | None = None) -> dict:
    if not RESULTS_DIR.exists():
        print(f"错误: 结果目录不存在 {RESULTS_DIR}")
        return {}

    province_stats = defaultdict(lambda: {
        "files": 0,
        "total": 0,
        "high": 0,
        "mid": 0,
        "low": 0,
        "confidence_sum": 0,
        "file_list": [],
        "reasons": reason_bucket(),
        "low_reasons": reason_bucket(),
    })
    errors: list[dict] = []
    total_files = 0

    for entry in sorted(RESULTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        province_name = entry.name
        if province_filter and province_filter not in province_name:
            continue
        for json_file in sorted(entry.glob("*.json")):
            total_files += 1
            try:
                data = _load_result_file(json_file)
            except Exception as exc:
                errors.append({"file": str(json_file.relative_to(RESULTS_DIR)), "error": str(exc)})
                continue
            _accumulate_file(province_stats[province_name], data, json_file.name)

    for json_file in sorted(RESULTS_DIR.glob("*.json")):
        total_files += 1
        try:
            data = _load_result_file(json_file)
        except Exception as exc:
            errors.append({"file": json_file.name, "error": str(exc)})
            continue
        province_name = data.get("province", "未知省份")
        if province_filter and province_filter not in province_name:
            continue
        _accumulate_file(province_stats[province_name], data, json_file.name)

    provinces_output = {}
    overall = {
        "total": 0,
        "high": 0,
        "mid": 0,
        "low": 0,
        "files": total_files,
        "reasons": reason_bucket(),
        "low_reasons": reason_bucket(),
    }

    for province, stats in sorted(province_stats.items()):
        total = stats["total"]
        provinces_output[province] = {
            "files": stats["files"],
            "total": total,
            "high": stats["high"],
            "mid": stats["mid"],
            "low": stats["low"],
            "high_pct": round(stats["high"] / total * 100, 1) if total else 0,
            "avg_confidence": round(stats["confidence_sum"] / total, 1) if total else 0,
            "reason_summary": summarize_reason_bucket(stats["reasons"], total=total, top_n=5),
            "low_reason_summary": summarize_reason_bucket(stats["low_reasons"], total=stats["low"], top_n=5),
        }
        overall["total"] += total
        overall["high"] += stats["high"]
        overall["mid"] += stats["mid"]
        overall["low"] += stats["low"]
        _merge_bucket(overall["reasons"], stats["reasons"])
        _merge_bucket(overall["low_reasons"], stats["low_reasons"])

    if overall["total"] > 0:
        overall["high_pct"] = round(overall["high"] / overall["total"] * 100, 1)
        overall["mid_pct"] = round(overall["mid"] / overall["total"] * 100, 1)
        overall["low_pct"] = round(overall["low"] / overall["total"] * 100, 1)
        overall["avg_confidence"] = round(
            sum(stats["confidence_sum"] for stats in province_stats.values()) / overall["total"], 1
        )
    else:
        overall["high_pct"] = 0
        overall["mid_pct"] = 0
        overall["low_pct"] = 0
        overall["avg_confidence"] = 0

    overall["reason_summary"] = summarize_reason_bucket(overall["reasons"], total=overall["total"], top_n=10)
    overall["low_reason_summary"] = summarize_reason_bucket(overall["low_reasons"], total=overall["low"], top_n=10)
    overall.pop("reasons", None)
    overall.pop("low_reasons", None)

    return {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": total_files,
        "total_items": overall["total"],
        "thresholds": {
            "high": f">={HIGH_THRESHOLD}",
            "mid": f"{MID_THRESHOLD}-{HIGH_THRESHOLD - 1}",
            "low": f"<{MID_THRESHOLD}",
        },
        "provinces": provinces_output,
        "overall": overall,
        "errors": errors,
    }


def export_high_confidence(province_filter: str) -> str:
    if not province_filter:
        print("错误: export 必须指定 --province 参数")
        return ""
    if not RESULTS_DIR.exists():
        print(f"错误: 结果目录不存在 {RESULTS_DIR}")
        return ""

    export_items: list[dict] = []

    for entry in sorted(RESULTS_DIR.iterdir()):
        if not entry.is_dir() or province_filter not in entry.name:
            continue
        for json_file in sorted(entry.glob("*.json")):
            try:
                data = _load_result_file(json_file)
            except Exception:
                continue
            _collect_high_items(data, entry.name, export_items)

    for json_file in sorted(RESULTS_DIR.glob("*.json")):
        try:
            data = _load_result_file(json_file)
        except Exception:
            continue
        province = data.get("province", "")
        if province_filter not in province:
            continue
        _collect_high_items(data, province, export_items)

    if not export_items:
        print(f"没有找到 {province_filter} 的高置信度(>={HIGH_THRESHOLD})结果")
        return ""

    dedup = {}
    for item in export_items:
        key = f"{item['province']}|{item['bill_text']}|{item['quota_ids']}"
        if key not in dedup or item["confidence"] > dedup[key]["confidence"]:
            dedup[key] = item
    export_items = list(dedup.values())

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = province_filter.replace("/", "_").replace("\\", "_")
    export_path = REPORT_DIR / f"batch_digest_export_{safe_name}.json"
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump({
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "province": province_filter,
            "threshold": HIGH_THRESHOLD,
            "count": len(export_items),
            "items": export_items,
        }, f, ensure_ascii=False, indent=2)
    return str(export_path)


def _accumulate_file(stats: dict, data: dict, file_name: str):
    stats["files"] += 1
    stats["file_list"].append(file_name)

    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence")
        if confidence is None:
            continue
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            continue

        stats["total"] += 1
        stats["confidence_sum"] += confidence
        update_reason_bucket(stats["reasons"], item)

        if confidence >= HIGH_THRESHOLD:
            stats["high"] += 1
        elif confidence >= MID_THRESHOLD:
            stats["mid"] += 1
        else:
            stats["low"] += 1
            update_reason_bucket(stats["low_reasons"], item)


def _collect_high_items(data: dict, province: str, out_list: list):
    file_province = data.get("province", province)
    specialty = data.get("specialty", "")
    source_file = data.get("file_path", "")

    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        confidence = item.get("confidence")
        if confidence is None:
            continue
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            continue
        if confidence < HIGH_THRESHOLD:
            continue

        name = str(item.get("name") or (item.get("bill_item") or {}).get("name") or "").strip()
        desc = str(item.get("description") or (item.get("bill_item") or {}).get("description") or "").strip()
        if not name or name.isdigit():
            continue

        quota_id, quota_name = _matched_quota(item)
        if not quota_id:
            continue

        bill_text = f"{name} {desc}".strip() if desc else name
        out_list.append({
            "bill_text": bill_text,
            "quota_ids": [quota_id],
            "quota_names": [quota_name],
            "confidence": confidence,
            "province": file_province,
            "specialty": specialty,
            "source": "batch_digest",
            "source_file": source_file,
            "primary_reason": str(item.get("primary_reason") or ""),
            "reason_tags": item.get("reason_tags") or [],
        })


def _matched_quota(item: dict) -> tuple[str, str]:
    quota_id = str(item.get("matched_quota_id") or "")
    quota_name = str(item.get("matched_quota_name") or "")
    if quota_id:
        return quota_id, quota_name
    quotas = item.get("quotas") or []
    if quotas and isinstance(quotas[0], dict):
        return str(quotas[0].get("quota_id") or ""), str(quotas[0].get("name") or "")
    return "", ""


def _merge_bucket(target: dict, source: dict):
    for group in ("primary", "tags"):
        for key, count in (source.get(group) or {}).items():
            target[group][key] += count


def _load_result_file(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("JSON 根元素不是对象")
    if "results" not in data:
        raise ValueError("缺少 results 字段")
    if not isinstance(data["results"], list):
        raise ValueError("results 字段不是数组")
    return data


def print_report(report: dict):
    if not report:
        return

    overall = report.get("overall", {})
    provinces = report.get("provinces", {})
    errors = report.get("errors", [])

    print(f"\n{'=' * 70}")
    print(f"  批量匹配结果消化报告  {report.get('scan_time', '')}")
    print(f"{'=' * 70}")
    print(f"  扫描文件: {report.get('total_files', 0)} 个")
    print(f"  匹配条目: {overall.get('total', 0)} 条")
    print(f"  平均置信度: {overall.get('avg_confidence', 0)}%")
    print(f"  分档阈值: 高>={HIGH_THRESHOLD}  中{MID_THRESHOLD}-{HIGH_THRESHOLD - 1}  低<{MID_THRESHOLD}")
    print()

    print("  整体分布:")
    print(f"    高置信度: {overall.get('high', 0):>6} 条 ({overall.get('high_pct', 0)}%)")
    print(f"    中置信度: {overall.get('mid', 0):>6} 条 ({overall.get('mid_pct', 0)}%)")
    print(f"    低置信度: {overall.get('low', 0):>6} 条 ({overall.get('low_pct', 0)}%)")
    print()

    _print_reason_rows("  整体主因 TOP 5", ((overall.get("reason_summary") or {}).get("primary") or [])[:5], indent="    ")
    _print_reason_rows("  低置信主因 TOP 5", ((overall.get("low_reason_summary") or {}).get("primary") or [])[:5], indent="    ")

    if provinces:
        print(f"\n  {'省份':<8} {'文件':>4} {'总条数':>6} {'高':>6} {'中':>6} {'低':>6} {'高占比':>7} {'平均':>5}")
        print(f"  {'-' * 56}")
        for province, stats in provinces.items():
            print(
                f"  {province:<8} {stats['files']:>4} {stats['total']:>6} "
                f"{stats['high']:>6} {stats['mid']:>6} {stats['low']:>6} "
                f"{stats['high_pct']:>6.1f}% {stats['avg_confidence']:>5.1f}"
            )
            top_primary = ((stats.get("low_reason_summary") or {}).get("primary") or [])[:1]
            if top_primary:
                row = top_primary[0]
                print(f"           低置信主因: {row['key']} x {row['count']}")
        print()

    if errors:
        print(f"  解析失败: {len(errors)} 个文件")
        for row in errors[:10]:
            print(f"    {row['file']}: {row['error']}")
        if len(errors) > 10:
            print(f"    ... 还有 {len(errors) - 10} 个")
        print()

    print(f"{'=' * 70}")


def _print_reason_rows(title: str, rows: list[dict], *, indent: str = ""):
    if not rows:
        return
    print(title)
    for i, row in enumerate(rows, start=1):
        print(f"{indent}{i}. {row['key']} x {row['count']} ({row.get('rate', 0)}%)")


def main():
    parser = argparse.ArgumentParser(description="批量匹配结果消化工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    scan_parser = subparsers.add_parser("scan", help="扫描统计所有结果")
    scan_parser.add_argument("--province", type=str, default=None, help="只看指定省份（模糊匹配）")

    export_parser = subparsers.add_parser("export", help="导出高置信度结果")
    export_parser.add_argument("--province", type=str, required=True, help="指定省份（必填，模糊匹配）")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "scan":
        report = scan_results(province_filter=args.province)
        if not report:
            return
        print_report(report)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  报告已保存: {REPORT_PATH}")
        return

    if args.command == "export":
        report = scan_results(province_filter=args.province)
        if report:
            print_report(report)
        export_path = export_high_confidence(province_filter=args.province)
        if export_path:
            with open(export_path, "r", encoding="utf-8") as f:
                export_data = json.load(f)
            count = export_data.get("count", 0)
            print(f"\n  已导出 {count} 条高置信度结果 -> {export_path}")
            print("  这些结果可作为经验库候选层导入源。")


if __name__ == "__main__":
    main()
