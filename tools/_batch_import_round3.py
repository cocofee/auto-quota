# -*- coding: utf-8 -*-
"""
批量导入第三轮导出的9省39个定额版本
"""
import sys
import os
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src.quota_db import QuotaDB, detect_specialty_from_excel
from pathlib import Path

VERSIONS_TO_IMPORT = [
    # 天津
    "天津市安装工程预算基价(2020)",
    "天津市建筑工程预算基价(2020)",
    "天津市市政工程预算基价(2020)",
    "天津市装饰装修工程预算基价(2020)",
    "天津市仿古建筑及园林工程预算基价(2020)",
    # 河北
    "河北省建设工程消耗量标准(2022)-安装工程",
    "河北省建设工程消耗量标准(2022)-建筑工程",
    "河北省建设工程消耗量标准(2022)-市政工程",
    "河北省建设工程消耗量标准(2022)-装饰装修工程",
    "河北省建设工程消耗量标准(2023)-园林绿化工程",
    # 山西
    "山西省安装工程预算定额(2018)",
    "山西省建筑工程预算定额(2018)",
    "山西省市政工程预算定额(2018)",
    "山西省装饰工程预算定额(2018)",
    "山西省园林绿化工程预算定额(2018)",
    # 吉林
    "吉林省安装工程计价定额(2024)",
    "吉林省建筑装饰工程计价定额(2024)",
    "吉林省市政工程计价定额(2024)",
    "吉林省园林仿古工程计价定额(2024)",
    # 安徽
    "安徽省安装工程计价定额(2018)",
    "安徽省建筑工程计价定额(2018)",
    "安徽省市政工程计价定额(2018)",
    "安徽省园林绿化工程计价定额(2018)",
    "安徽省装饰装修工程计价定额(2018)",
    # 贵州
    "贵州省通用安装工程计价定额(2016)",
    "贵州省建筑与装饰工程计价定额(2016)",
    "贵州省市政工程计价定额(2016)",
    "贵州省园林绿化工程计价定额(2016)",
    # 海南
    "海南省安装工程综合定额(2024)",
    "海南省房屋建筑与装饰工程综合定额(2024)",
    "海南省市政工程综合定额(2017)",
    "海南省园林绿化工程综合定额(2019)",
    # 青海
    "青海省通用安装工程计价定额(2020)",
    "青海省房屋建筑与装饰工程计价定额(2020)",
    "青海省市政工程计价定额(2020)",
    "青海省园林绿化工程计价定额(2020)",
    # 新疆
    "全统安装工程消耗量定额乌鲁木齐估价汇总表(2020)",
    "新疆房屋建筑与装饰工程消耗量定额乌鲁木齐估价汇总表(2020)",
    "新疆市政工程消耗量定额乌鲁木齐估价汇总表(2020)",
]


def import_single_version(version_name):
    """导入单个定额版本"""
    try:
        resolved = config.resolve_province(version_name, interactive=False, scope="data")
    except ValueError as e:
        print(f"  错误: 无法解析 '{version_name}': {e}")
        return 0

    quota_dir = config.get_quota_data_dir(resolved)
    if not quota_dir.exists():
        print(f"  错误: 目录不存在 {quota_dir}")
        return 0

    xlsx_files = sorted(quota_dir.glob("*.xlsx"))
    if not xlsx_files:
        print(f"  错误: 没有xlsx文件 {quota_dir}")
        return 0

    db = QuotaDB(province=resolved)
    total = 0
    imported = {}
    for xlsx_file in xlsx_files:
        specialty = detect_specialty_from_excel(str(xlsx_file))
        is_first = specialty not in imported
        try:
            count = db.import_excel(str(xlsx_file), specialty=specialty, clear_existing=is_first)
            imported[specialty] = imported.get(specialty, 0) + count
            total += count
            try:
                db.record_import(str(xlsx_file), specialty, count)
            except Exception:
                pass
        except Exception as e:
            print(f"    导入失败 {xlsx_file.name}: {e}")

    return total


def main():
    print("=" * 60)
    print("批量导入9省39个定额版本")
    print("=" * 60)

    results = []
    grand_total = 0
    start_time = time.time()

    for i, version in enumerate(VERSIONS_TO_IMPORT, 1):
        print(f"\n[{i}/{len(VERSIONS_TO_IMPORT)}] 导入: {version}")
        t0 = time.time()
        count = import_single_version(version)
        elapsed = time.time() - t0
        grand_total += count
        results.append((version, count, elapsed))
        print(f"  完成: {count}条 ({elapsed:.1f}秒)")

    total_elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"导入汇总")
    print(f"{'='*60}")
    for version, count, elapsed in results:
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {version}: {count}条 ({elapsed:.1f}s)")
    print(f"\n总计: {grand_total}条, 耗时 {total_elapsed:.0f}秒")


if __name__ == "__main__":
    main()
