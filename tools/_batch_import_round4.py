# -*- coding: utf-8 -*-
"""
批量导入第四轮导出的5个定额版本（补齐缺口）
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
    # 广西
    "广西建筑装饰装修工程消耗量定额(2024)",
    "广西园林绿化及仿古建筑工程消耗量定额(2021)",
    # 重庆
    "重庆市园林绿化工程计价定额(2018)",
    # 浙江
    "浙江省房屋建筑与装饰工程预算定额(2018)",
    # 江苏
    "江苏省市政工程消耗量定额(2021年)",
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
    print("批量导入第四轮：补齐缺口省份")
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
