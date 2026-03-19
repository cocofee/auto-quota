"""
Batch error report generator.

Scans `output/batch/results/*.json` and produces:
- aggregate confidence stats
- low-confidence pattern clustering
- standardized reason breakdowns
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reason_reporting import (
    normalize_reason_tags,
    primary_reason_of,
    reason_bucket,
    summarize_reason_bucket,
    update_reason_bucket,
)


RESULTS_DIR = Path(__file__).resolve().parent.parent / "output" / "batch" / "results"
REPORT_PATH = Path(__file__).resolve().parent.parent / "output" / "batch" / "error_report.json"

HIGH_CONFIDENCE = 85
LOW_CONFIDENCE = 60


def collect_results(province_filter: str | None = None) -> list[dict]:
    if not RESULTS_DIR.exists():
        print("结果目录不存在，请先运行 batch_runner.py")
        return []

    all_results: list[dict] = []
    for json_path in RESULTS_DIR.rglob("*.json"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"  警告: 读取结果文件失败 {json_path}: {exc}")
            continue

        if province_filter and data.get("province") != province_filter:
            continue
        all_results.append(data)
    return all_results


def analyze_patterns(all_results: list[dict], top_n: int = 100) -> dict:
    total_files = len(all_results)
    total_items = 0
    skipped_measures = 0
    high_count = 0
    mid_count = 0
    low_count = 0
    conf_sum = 0

    by_province = defaultdict(lambda: {"files": 0, "items": 0, "conf_sum": 0, "high": 0, "low": 0})
    by_specialty = defaultdict(lambda: {"items": 0, "conf_sum": 0, "high": 0, "low": 0})

    overall_reasons = reason_bucket()
    low_reasons = reason_bucket()
    province_reasons = defaultdict(reason_bucket)
    specialty_reasons = defaultdict(reason_bucket)

    low_conf_items: list[dict] = []

    for file_data in all_results:
        province = file_data.get("province", "未知")
        specialty = file_data.get("specialty", "未知")
        results = file_data.get("results", []) or []

        by_province[province]["files"] += 1

        for result in results:
            if not isinstance(result, dict):
                continue

            match_source = str(result.get("match_source") or "")
            if match_source == "skip_measure":
                skipped_measures += 1
                continue

            confidence = _safe_int(result.get("confidence"))
            total_items += 1
            conf_sum += confidence

            by_province[province]["items"] += 1
            by_province[province]["conf_sum"] += confidence
            by_specialty[specialty]["items"] += 1
            by_specialty[specialty]["conf_sum"] += confidence

            update_reason_bucket(overall_reasons, result)
            update_reason_bucket(province_reasons[province], result)
            update_reason_bucket(specialty_reasons[specialty], result)

            if confidence >= HIGH_CONFIDENCE:
                high_count += 1
                by_province[province]["high"] += 1
                by_specialty[specialty]["high"] += 1
            elif confidence < LOW_CONFIDENCE:
                low_count += 1
                by_province[province]["low"] += 1
                by_specialty[specialty]["low"] += 1
                update_reason_bucket(low_reasons, result)
                low_conf_items.append({
                    "name": _result_name(result),
                    "description": _result_description(result),
                    "matched_quota_id": _matched_quota_id(result),
                    "matched_quota_name": _matched_quota_name(result),
                    "confidence": confidence,
                    "match_source": match_source,
                    "province": province,
                    "specialty": specialty,
                    "primary_reason": primary_reason_of(result),
                    "reason_tags": normalize_reason_tags(result),
                })
            else:
                mid_count += 1

    province_stats = {}
    for province, stats in sorted(by_province.items(), key=lambda x: -x[1]["items"]):
        items = stats["items"]
        avg = stats["conf_sum"] / items if items else 0
        province_stats[province] = {
            "files": stats["files"],
            "items": items,
            "avg_confidence": round(avg, 1),
            "high_rate": round(stats["high"] / items * 100, 1) if items else 0,
            "low_rate": round(stats["low"] / items * 100, 1) if items else 0,
            "top_reasons": summarize_reason_bucket(province_reasons[province], total=items, top_n=5),
        }

    specialty_stats = {}
    for specialty, stats in sorted(by_specialty.items(), key=lambda x: -x[1]["items"]):
        items = stats["items"]
        avg = stats["conf_sum"] / items if items else 0
        specialty_stats[specialty] = {
            "items": items,
            "avg_confidence": round(avg, 1),
            "high_rate": round(stats["high"] / items * 100, 1) if items else 0,
            "low_rate": round(stats["low"] / items * 100, 1) if items else 0,
            "top_reasons": summarize_reason_bucket(specialty_reasons[specialty], total=items, top_n=5),
        }

    patterns = _cluster_low_confidence(low_conf_items, top_n)
    coverage = _build_coverage_matrix(province_stats)
    avg_conf = conf_sum / total_items if total_items else 0

    return {
        "report_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "algo_version": _get_algo_version(),
        "summary": {
            "total_files": total_files,
            "total_items": total_items,
            "skipped_measures": skipped_measures,
            "high_confidence": high_count,
            "mid_confidence": mid_count,
            "low_confidence": low_count,
            "avg_confidence": round(avg_conf, 1),
            "high_rate": round(high_count / total_items * 100, 1) if total_items else 0,
            "low_rate": round(low_count / total_items * 100, 1) if total_items else 0,
        },
        "by_province": province_stats,
        "by_specialty": specialty_stats,
        "reason_summary": summarize_reason_bucket(overall_reasons, total=total_items, top_n=10),
        "low_confidence_reason_summary": summarize_reason_bucket(low_reasons, total=low_count, top_n=10),
        "low_confidence_patterns": patterns,
        "province_coverage": coverage,
    }


def _cluster_low_confidence(items: list[dict], top_n: int) -> list[dict]:
    pattern_groups = defaultdict(list)
    for item in items:
        core = _extract_core_name(item.get("name", ""))
        if core:
            pattern_groups[core].append(item)

    sorted_patterns = sorted(pattern_groups.items(), key=lambda x: -len(x[1]))[:top_n]
    clustered = []
    for pattern, group_items in sorted_patterns:
        provinces = sorted({it.get("province", "") for it in group_items if it.get("province")})
        typical = group_items[0]
        clustered.append({
            "pattern": pattern,
            "count": len(group_items),
            "provinces": provinces,
            "typical_bill": typical.get("name", ""),
            "typical_match": typical.get("matched_quota_name", ""),
            "avg_confidence": round(sum(_safe_int(it.get("confidence")) for it in group_items) / len(group_items), 1),
            "root_cause_guess": _guess_root_cause(group_items),
        })
    return clustered


def _extract_core_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"[（(][^()（）]*[)）]", "", text)
    text = re.sub(r"\d+[\*×xX]\d+", "", text)
    text = re.sub(r"DN\d+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+(?:mm|m|kg|t|kv|kw|kva|度|层)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z]", "", text)
    text = text.strip()
    return text if len(text) >= 2 else ""


def _guess_root_cause(items: list[dict]) -> str:
    primary_counter = Counter()
    tag_counter = Counter()
    for item in items:
        primary = str(item.get("primary_reason") or "").strip()
        if primary:
            primary_counter[primary] += 1
        for tag in item.get("reason_tags") or []:
            tag = str(tag or "").strip()
            if tag:
                tag_counter[tag] += 1

    if primary_counter:
        return primary_counter.most_common(1)[0][0]
    if tag_counter:
        return f"tag:{tag_counter.most_common(1)[0][0]}"

    for item in items[:5]:
        matched = item.get("matched_quota_name", "")
        bill_name = item.get("name", "")
        if not matched:
            return "search_miss"
        if len(set(bill_name) & set(matched)) < 2:
            return "synonym_gap"
    return "wrong_tier"


def _build_coverage_matrix(province_stats: dict) -> dict:
    provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
    has_db = set()
    if provinces_dir.exists():
        for entry in provinces_dir.iterdir():
            if entry.is_dir():
                has_db.add(re.sub(r"\d+$", "", entry.name))

    has_files = set(province_stats.keys())
    return {
        "有定额库且有文件": sorted(has_db & has_files),
        "有文件无定额库": sorted(has_files - has_db),
        "有定额库无文件": sorted(has_db - has_files),
    }


def _get_algo_version() -> str:
    try:
        from tools.batch_scanner import ALGORITHM_VERSION

        return ALGORITHM_VERSION
    except Exception:
        return "unknown"


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _result_name(result: dict) -> str:
    bill = result.get("bill_item") or {}
    return str(result.get("name") or result.get("bill_name") or bill.get("name") or "")


def _result_description(result: dict) -> str:
    bill = result.get("bill_item") or {}
    return str(result.get("description") or bill.get("description") or "")


def _matched_quota_id(result: dict) -> str:
    direct = str(result.get("matched_quota_id") or "")
    if direct:
        return direct
    quotas = result.get("quotas") or []
    if quotas and isinstance(quotas[0], dict):
        return str(quotas[0].get("quota_id") or "")
    return ""


def _matched_quota_name(result: dict) -> str:
    direct = str(result.get("matched_quota_name") or "")
    if direct:
        return direct
    quotas = result.get("quotas") or []
    if quotas and isinstance(quotas[0], dict):
        return str(quotas[0].get("name") or "")
    return ""


def generate_report(province_filter: str | None = None, top_n: int = 100):
    print("收集匹配结果...")
    all_results = collect_results(province_filter)
    if not all_results:
        print("没有匹配结果。")
        return

    print(f"共 {len(all_results)} 个文件的结果")
    print("分析错误模式...")
    report = analyze_patterns(all_results, top_n)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存到: {REPORT_PATH}")

    summary = report["summary"]
    print(f"\n{'=' * 50}")
    print("错误分析报告摘要")
    print(f"{'=' * 50}")
    print(f"  总文件: {summary['total_files']} | 总清单: {summary['total_items']} | 措施项已跳过: {summary.get('skipped_measures', 0)}")
    print(f"  绿灯(>=85%): {summary['high_confidence']} ({summary['high_rate']}%)")
    print(f"  红灯(<60%): {summary['low_confidence']} ({summary['low_rate']}%)")
    print(f"  平均置信度: {summary['avg_confidence']}%")

    _print_reason_block("整体主因 TOP 5", (report.get("reason_summary") or {}).get("primary", [])[:5])
    _print_reason_block("低置信主因 TOP 5", (report.get("low_confidence_reason_summary") or {}).get("primary", [])[:5])

    if report["low_confidence_patterns"]:
        print("\n低置信度 TOP 10 模式:")
        for i, pattern in enumerate(report["low_confidence_patterns"][:10], start=1):
            print(
                f"  {i}. {pattern['pattern']} x {pattern['count']} | "
                f"均{pattern['avg_confidence']}% | {pattern['root_cause_guess']} | "
                f"省份: {','.join(pattern['provinces'][:3])}"
            )

    if report["by_province"]:
        print("\n各省低置信度率:")
        for province, stats in sorted(report["by_province"].items(), key=lambda x: -x[1]["low_rate"])[:10]:
            print(
                f"  {province:8s} 低{stats['low_rate']:5.1f}% | "
                f"高{stats['high_rate']:5.1f}% | {stats['items']}条"
            )
            top_primary = ((stats.get("top_reasons") or {}).get("primary") or [])[:1]
            if top_primary:
                row = top_primary[0]
                print(f"           主因: {row['key']} x {row['count']}")


def _print_reason_block(title: str, rows: list[dict]):
    if not rows:
        return
    print(f"\n{title}:")
    for i, row in enumerate(rows, start=1):
        print(f"  {i}. {row['key']} x {row['count']} ({row.get('rate', 0)}%)")


def main():
    parser = argparse.ArgumentParser(description="错误分析报告生成器")
    parser.add_argument("--province", help="只看某省")
    parser.add_argument("--top", type=int, default=100, help="只看 TOP N 错误模式")
    args = parser.parse_args()

    generate_report(province_filter=args.province, top_n=args.top)


if __name__ == "__main__":
    main()
