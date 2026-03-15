"""
F盘文件筛选器 — 从 F:/jarvis/ 筛选出能跑的房建安装清单Excel

筛选规则：
1. 只要 .xlsx 和 .xls（排除 .GBQ6 等）
2. 去重（排除 _wx(2)、_wxwork(2) 等副本）
3. 排除非房建项目（地铁、铁路、水利、农田等）
4. 排除非清单文件（设计变更、审核表、报价单、材料表等）
5. 按专业分类输出

用法：
    python tools/scan_jarvis_files.py              # 扫描并输出统计
    python tools/scan_jarvis_files.py --output      # 输出筛选后的文件列表到 output/temp/
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict

JARVIS_DIR = Path("F:/jarvis")

# 非房建项目关键词（文件名包含这些就排除）
NON_BUILDING_KEYWORDS = [
    # 市政/道路
    "地铁", "轨道", "隧道", "高速", "公路", "桥梁", "立交",
    # 水利/农田
    "水库", "渠道", "灌溉", "灌排", "堤防", "水利", "农田", "土地整理",
    # 工业/特殊
    "石化", "炼油", "催化", "蒸馏", "化工装置",
    "矿山", "采矿", "选矿",
    "铁路", "站场", "轨枕",
    # 电力（输变电，不是房建电气）
    "输电线路", "杆塔", "变电站改造", "升压站",
    # 通信
    "基站", "光缆", "通信管道",
]

# 非清单文件关键词（文件名包含这些就排除）
NON_BILL_KEYWORDS = [
    "设计变更", "变更对比", "签证", "审核表", "结算审核",
    "报价单", "询价", "材料表", "材料清单",
    "图纸", "说明书", "方案", "投标书",
    "模板", "计价模板", "样表",
    "收费", "投票", "通讯录", "考勤",
    "预算书",  # 预算书格式不同于工程量清单
]

# 要扫描的专业目录（房建安装相关）
SCAN_DIRS = [
    "给排水",
    "电气",
    "消防",
    "通风空调",
    "智能化",
    "综合",
]


def is_duplicate(filename):
    """判断是否是重复文件（微信下载副本）"""
    # 匹配 xxx(2).xlsx、xxx_wx(2).xlsx 等
    if re.search(r'\(\d+\)\.(xlsx?|xls)$', filename, re.IGNORECASE):
        return True
    return False


def is_non_building(filename):
    """判断是否是非房建项目"""
    name_lower = filename.lower()
    for kw in NON_BUILDING_KEYWORDS:
        if kw in filename:
            return True
    return False


def is_non_bill(filename):
    """判断是否是非清单文件"""
    for kw in NON_BILL_KEYWORDS:
        if kw in filename:
            return True
    return False


def is_too_small(filepath):
    """文件太小（<5KB），可能是空文件或损坏文件"""
    try:
        return os.path.getsize(filepath) < 5120
    except OSError:
        return True


def scan_directory(dir_path, specialty):
    """扫描一个专业目录，返回筛选结果"""
    results = {
        "total": 0,
        "valid": [],
        "skipped_format": 0,     # 非Excel格式
        "skipped_duplicate": 0,  # 重复文件
        "skipped_non_building": 0,  # 非房建
        "skipped_non_bill": 0,   # 非清单
        "skipped_too_small": 0,  # 太小
    }

    for root, dirs, files in os.walk(dir_path):
        for filename in files:
            filepath = os.path.join(root, filename)

            # 只要 Excel 文件
            if not filename.lower().endswith(('.xlsx', '.xls')):
                results["skipped_format"] += 1
                continue

            results["total"] += 1

            # 跳过临时文件
            if filename.startswith('~') or filename.startswith('.'):
                results["skipped_non_bill"] += 1
                continue

            # 去重
            if is_duplicate(filename):
                results["skipped_duplicate"] += 1
                continue

            # 排除非房建
            if is_non_building(filename):
                results["skipped_non_building"] += 1
                continue

            # 排除非清单
            if is_non_bill(filename):
                results["skipped_non_bill"] += 1
                continue

            # 排除太小的文件
            if is_too_small(filepath):
                results["skipped_too_small"] += 1
                continue

            results["valid"].append({
                "path": filepath,
                "name": filename,
                "specialty": specialty,
                "size_kb": round(os.path.getsize(filepath) / 1024, 1),
            })

    return results


def main():
    output_mode = "--output" in sys.argv

    print("🔍 扫描 F:/jarvis/ 目录...")
    print("=" * 60)

    all_valid = []
    total_stats = defaultdict(int)

    for specialty in SCAN_DIRS:
        dir_path = JARVIS_DIR / specialty
        if not dir_path.exists():
            print(f"⚠️  目录不存在: {dir_path}")
            continue

        results = scan_directory(dir_path, specialty)
        valid_count = len(results["valid"])
        all_valid.extend(results["valid"])

        print(f"\n📁 {specialty}:")
        print(f"   Excel总数: {results['total']}")
        print(f"   ✅ 通过筛选: {valid_count}")
        print(f"   ❌ 重复文件: {results['skipped_duplicate']}")
        print(f"   ❌ 非房建: {results['skipped_non_building']}")
        print(f"   ❌ 非清单: {results['skipped_non_bill']}")
        print(f"   ❌ 太小: {results['skipped_too_small']}")
        print(f"   ❌ 非Excel: {results['skipped_format']}")

        total_stats["total"] += results["total"]
        total_stats["valid"] += valid_count
        total_stats["duplicate"] += results["skipped_duplicate"]
        total_stats["non_building"] += results["skipped_non_building"]
        total_stats["non_bill"] += results["skipped_non_bill"]

    print(f"\n{'=' * 60}")
    print(f"📊 汇总:")
    print(f"   扫描目录: {len(SCAN_DIRS)} 个专业")
    print(f"   Excel总数: {total_stats['total']}")
    print(f"   ✅ 通过筛选: {total_stats['valid']}")
    print(f"   ❌ 排除: {total_stats['total'] - total_stats['valid']}")
    print(f"      重复: {total_stats['duplicate']}")
    print(f"      非房建: {total_stats['non_building']}")
    print(f"      非清单: {total_stats['non_bill']}")

    if output_mode and all_valid:
        # 输出文件列表
        output_dir = Path("output/temp")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "jarvis_valid_files.txt"

        with open(output_path, "w", encoding="utf-8") as f:
            for item in all_valid:
                f.write(f"{item['specialty']}\t{item['path']}\t{item['size_kb']}KB\n")

        print(f"\n📄 文件列表已保存: {output_path}")

    # 按专业统计通过筛选的文件数
    by_specialty = defaultdict(int)
    for item in all_valid:
        by_specialty[item["specialty"]] += 1

    print(f"\n📋 各专业可用文件数:")
    for sp, count in sorted(by_specialty.items(), key=lambda x: -x[1]):
        print(f"   {sp}: {count} 个")


if __name__ == "__main__":
    main()
