"""
批量匹配结果消化工具

功能：扫描 output/batch/results/ 下的匹配结果JSON，按置信度分档统计，
高置信度结果可导出为经验库候选层导入格式。

用法：
    python tools/batch_digest.py scan                    # 扫描统计所有省份
    python tools/batch_digest.py scan --province 上海    # 只看某省
    python tools/batch_digest.py export --province 上海  # 导出高置信度结果

输出：
    - 终端打印汇总报告
    - output/temp/batch_digest_report.json  保存详细统计
    - output/temp/batch_digest_export_<省>.json  导出结果（export模式）
"""

import os
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# 把项目根目录加入搜索路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 常量配置
# ============================================================

# 批量结果目录
RESULTS_DIR = PROJECT_ROOT / "output" / "batch" / "results"
# 报告输出目录
REPORT_DIR = PROJECT_ROOT / "output" / "temp"
# 报告文件路径
REPORT_PATH = REPORT_DIR / "batch_digest_report.json"

# 置信度分档阈值
HIGH_THRESHOLD = 85   # >= 85 为高置信度
MID_THRESHOLD = 60    # >= 60 为中置信度，< 60 为低置信度


# ============================================================
# 核心逻辑
# ============================================================

def scan_results(province_filter: str = None) -> dict:
    """
    扫描所有匹配结果JSON，按省份和置信度分档统计。

    参数:
        province_filter: 只扫描指定省份（模糊匹配，如"上海"匹配"上海"目录）

    返回:
        {
            "scan_time": "...",
            "total_files": 10,
            "total_items": 500,
            "provinces": {
                "北京": {
                    "files": 3,
                    "total": 200,
                    "high": 150,      # >= 85
                    "mid": 30,        # 60-84
                    "low": 20,        # < 60
                    "high_pct": 75.0,
                    "avg_confidence": 82.5
                }
            },
            "overall": { "total": ..., "high": ..., "mid": ..., "low": ... },
            "errors": [...]  # 解析失败的文件
        }
    """
    if not RESULTS_DIR.exists():
        print(f"错误：结果目录不存在 {RESULTS_DIR}")
        return {}

    # 按省份统计
    province_stats = defaultdict(lambda: {
        "files": 0, "total": 0,
        "high": 0, "mid": 0, "low": 0,
        "confidence_sum": 0,  # 用于计算平均值
        "file_list": [],      # 文件名列表
    })

    errors = []         # 解析出错的文件
    total_files = 0

    # 遍历省份子目录
    for entry in sorted(RESULTS_DIR.iterdir()):
        if not entry.is_dir():
            continue

        province_name = entry.name  # 目录名就是省份名

        # 省份过滤（模糊匹配）
        if province_filter and province_filter not in province_name:
            continue

        # 遍历该省份下的JSON文件
        for json_file in sorted(entry.glob("*.json")):
            total_files += 1
            try:
                data = _load_result_file(json_file)
            except Exception as e:
                errors.append({
                    "file": str(json_file.relative_to(RESULTS_DIR)),
                    "error": str(e)
                })
                continue

            # 从JSON中提取省份（优先用文件中的province字段，兜底用目录名）
            file_province = data.get("province", province_name)
            # 统一用目录名作为省份key（避免同目录下文件province字段不一致）
            prov_key = province_name

            stats = province_stats[prov_key]
            stats["files"] += 1
            stats["file_list"].append(json_file.name)

            results = data.get("results", [])
            for item in results:
                # 容错：跳过缺少关键字段的条目
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                confidence = item.get("confidence")

                # 跳过无效条目（没有匹配结果或置信度）
                if confidence is None:
                    continue

                # 确保confidence是数字
                try:
                    confidence = int(confidence)
                except (ValueError, TypeError):
                    continue

                stats["total"] += 1
                stats["confidence_sum"] += confidence

                if confidence >= HIGH_THRESHOLD:
                    stats["high"] += 1
                elif confidence >= MID_THRESHOLD:
                    stats["mid"] += 1
                else:
                    stats["low"] += 1

    # 也扫描results目录根下的JSON（不在子目录中的）
    for json_file in sorted(RESULTS_DIR.glob("*.json")):
        total_files += 1
        try:
            data = _load_result_file(json_file)
        except Exception as e:
            errors.append({
                "file": json_file.name,
                "error": str(e)
            })
            continue

        prov_key = data.get("province", "未知省份")
        # 省份过滤
        if province_filter and province_filter not in prov_key:
            continue

        stats = province_stats[prov_key]
        stats["files"] += 1
        stats["file_list"].append(json_file.name)

        results = data.get("results", [])
        for item in results:
            if not isinstance(item, dict):
                continue
            confidence = item.get("confidence")
            if confidence is None:
                continue
            try:
                confidence = int(confidence)
            except (ValueError, TypeError):
                continue

            stats["total"] += 1
            stats["confidence_sum"] += confidence
            if confidence >= HIGH_THRESHOLD:
                stats["high"] += 1
            elif confidence >= MID_THRESHOLD:
                stats["mid"] += 1
            else:
                stats["low"] += 1

    # 计算百分比和平均值
    provinces_output = {}
    overall = {"total": 0, "high": 0, "mid": 0, "low": 0, "files": total_files}

    for prov, stats in sorted(province_stats.items()):
        total = stats["total"]
        provinces_output[prov] = {
            "files": stats["files"],
            "total": total,
            "high": stats["high"],
            "mid": stats["mid"],
            "low": stats["low"],
            "high_pct": round(stats["high"] / total * 100, 1) if total > 0 else 0,
            "avg_confidence": round(stats["confidence_sum"] / total, 1) if total > 0 else 0,
        }
        overall["total"] += total
        overall["high"] += stats["high"]
        overall["mid"] += stats["mid"]
        overall["low"] += stats["low"]

    # 总体百分比
    if overall["total"] > 0:
        overall["high_pct"] = round(overall["high"] / overall["total"] * 100, 1)
        overall["mid_pct"] = round(overall["mid"] / overall["total"] * 100, 1)
        overall["low_pct"] = round(overall["low"] / overall["total"] * 100, 1)
        overall["avg_confidence"] = round(
            sum(s["confidence_sum"] for s in province_stats.values()) / overall["total"], 1
        )
    else:
        overall["high_pct"] = overall["mid_pct"] = overall["low_pct"] = 0
        overall["avg_confidence"] = 0

    report = {
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_files": total_files,
        "total_items": overall["total"],
        "thresholds": {"high": f">={HIGH_THRESHOLD}", "mid": f"{MID_THRESHOLD}-{HIGH_THRESHOLD-1}", "low": f"<{MID_THRESHOLD}"},
        "provinces": provinces_output,
        "overall": overall,
        "errors": errors,
    }

    return report


def export_high_confidence(province_filter: str) -> str:
    """
    导出指定省份的高置信度结果，格式可用于经验库候选层导入。

    参数:
        province_filter: 省份名（必填，模糊匹配）

    返回:
        导出文件路径
    """
    if not province_filter:
        print("错误：export 必须指定 --province 参数")
        return ""

    if not RESULTS_DIR.exists():
        print(f"错误：结果目录不存在 {RESULTS_DIR}")
        return ""

    # 收集高置信度条目
    export_items = []

    # 先扫描子目录
    for entry in sorted(RESULTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if province_filter not in entry.name:
            continue

        for json_file in sorted(entry.glob("*.json")):
            try:
                data = _load_result_file(json_file)
            except Exception:
                continue
            _collect_high_items(data, entry.name, export_items)

    # 再扫描根目录JSON
    for json_file in sorted(RESULTS_DIR.glob("*.json")):
        try:
            data = _load_result_file(json_file)
        except Exception:
            continue
        prov = data.get("province", "")
        if province_filter not in prov:
            continue
        _collect_high_items(data, prov, export_items)

    if not export_items:
        print(f"没有找到 {province_filter} 的高置信度(>={HIGH_THRESHOLD})结果")
        return ""

    # 去重：同省份+同清单文本+同定额编号 只保留置信度最高的
    dedup = {}
    for item in export_items:
        key = f"{item['province']}|{item['bill_text']}|{item['quota_ids']}"
        if key not in dedup or item["confidence"] > dedup[key]["confidence"]:
            dedup[key] = item
    export_items = list(dedup.values())

    # 保存导出文件
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


def _collect_high_items(data: dict, province: str, out_list: list):
    """从一个结果文件中提取高置信度条目，追加到 out_list"""
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
        except (ValueError, TypeError):
            continue

        if confidence < HIGH_THRESHOLD:
            continue

        # 清单名称和描述拼成完整文本
        name = item.get("name", "").strip()
        desc = item.get("description", "").strip()

        # 跳过名称明显无效的条目（纯数字、空白等）
        if not name or name.isdigit():
            continue

        bill_text = f"{name} {desc}".strip() if desc else name
        quota_id = item.get("matched_quota_id", "")
        quota_name = item.get("matched_quota_name", "")

        if not quota_id:
            continue

        out_list.append({
            "bill_text": bill_text,
            "quota_ids": [quota_id],
            "quota_names": [quota_name],
            "confidence": confidence,
            "province": file_province,
            "specialty": specialty,
            "source": "batch_digest",
            "source_file": source_file,
        })


def _load_result_file(path: Path) -> dict:
    """
    加载并校验一个匹配结果JSON文件。
    格式不对或缺少关键字段时抛异常。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("JSON根元素不是对象")

    if "results" not in data:
        raise ValueError("缺少results字段")

    if not isinstance(data["results"], list):
        raise ValueError("results字段不是数组")

    return data


# ============================================================
# 打印报告（终端友好格式）
# ============================================================

def print_report(report: dict):
    """把统计报告打印成可读的表格"""
    if not report:
        return

    overall = report.get("overall", {})
    provinces = report.get("provinces", {})
    errors = report.get("errors", [])

    print(f"\n{'='*70}")
    print(f"  批量匹配结果消化报告  {report.get('scan_time', '')}")
    print(f"{'='*70}")
    print(f"  扫描文件: {report.get('total_files', 0)} 个")
    print(f"  匹配条目: {overall.get('total', 0)} 条")
    print(f"  平均置信度: {overall.get('avg_confidence', 0)}%")
    print(f"  分档阈值: 高>={HIGH_THRESHOLD}  中{MID_THRESHOLD}-{HIGH_THRESHOLD-1}  低<{MID_THRESHOLD}")
    print()

    # 总体分布
    print(f"  总体分布:")
    print(f"    高置信度: {overall.get('high', 0):>6} 条  ({overall.get('high_pct', 0)}%)")
    print(f"    中置信度: {overall.get('mid', 0):>6} 条  ({overall.get('mid_pct', 0)}%)")
    print(f"    低置信度: {overall.get('low', 0):>6} 条  ({overall.get('low_pct', 0)}%)")
    print()

    # 分省统计表
    if provinces:
        print(f"  {'省份':<8} {'文件':>4} {'总条数':>6} {'高':>6} {'中':>6} {'低':>6} {'高占比':>7} {'平均':>5}")
        print(f"  {'-'*56}")
        for prov, s in provinces.items():
            print(f"  {prov:<8} {s['files']:>4} {s['total']:>6} "
                  f"{s['high']:>6} {s['mid']:>6} {s['low']:>6} "
                  f"{s['high_pct']:>6.1f}% {s['avg_confidence']:>5.1f}")
        print()

    # 解析错误
    if errors:
        print(f"  解析失败: {len(errors)} 个文件")
        for e in errors[:10]:  # 最多显示10个
            print(f"    {e['file']}: {e['error']}")
        if len(errors) > 10:
            print(f"    ... 还有 {len(errors)-10} 个")
        print()

    print(f"{'='*70}")


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="批量匹配结果消化工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # scan 子命令：扫描统计
    scan_parser = subparsers.add_parser("scan", help="扫描统计所有结果")
    scan_parser.add_argument("--province", type=str, default=None, help="只看指定省份（模糊匹配）")

    # export 子命令：导出高置信度结果
    export_parser = subparsers.add_parser("export", help="导出高置信度结果")
    export_parser.add_argument("--province", type=str, required=True, help="指定省份（必填，模糊匹配）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "scan":
        # 扫描统计
        report = scan_results(province_filter=args.province)
        if not report:
            return

        # 打印终端报告
        print_report(report)

        # 保存JSON报告
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  报告已保存: {REPORT_PATH}")

    elif args.command == "export":
        # 先扫描统计
        report = scan_results(province_filter=args.province)
        if report:
            print_report(report)

        # 导出高置信度结果
        export_path = export_high_confidence(province_filter=args.province)
        if export_path:
            # 读取导出文件获取统计
            with open(export_path, "r", encoding="utf-8") as f:
                export_data = json.load(f)
            count = export_data.get("count", 0)
            print(f"\n  已导出 {count} 条高置信度结果 → {export_path}")
            print(f"  （这些结果可以用经验库导入工具写入候选层，但本工具只导出不导入）")


if __name__ == "__main__":
    main()
