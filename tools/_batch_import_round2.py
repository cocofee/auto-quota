# -*- coding: utf-8 -*-
"""
批量导入第二轮导出的19个省份定额版本
逐个导入，跳过索引重建（最后统一重建）
"""
import sys
import os
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src.quota_db import QuotaDB, detect_specialty_from_excel
from pathlib import Path

# 待导入的版本列表（按省份分组）
VERSIONS_TO_IMPORT = [
    # 上海
    "上海市市政工程预算定额(2016)",
    "上海市建筑和装饰工程预算定额(2016)",
    "上海市园林工程预算定额(2016)",
    # 深圳
    "深圳市市政工程消耗量标准(2024)",
    "深圳市建筑工程消耗量标准(2024)",
    "深圳市园林建筑绿化工程消耗量定额(2017)",
    # 云南
    "云南省市政工程计价标准(2020)",
    "云南省建筑工程计价标准(2020)",
    "云南省园林绿化工程计价标准(2020)",
    # 内蒙古
    "内蒙古市政工程预算定额(2017)",
    "内蒙古房屋建筑与装饰工程预算定额(2017)",
    "内蒙古园林绿化工程预算定额(2017)",
    # 甘肃
    "甘肃省市政工程预算定额(2018)",
    "甘肃省建筑与装饰工程预算定额(2013)",
    # 福建
    "福建省市政工程预算定额(2017)",
    "福建省房屋建筑与装饰工程预算定额(2017)",
    "福建省园林绿化工程预算定额(2017)",
    # 浙江
    "浙江省市政工程预算定额(2018)",
    "浙江省园林绿化及仿古建筑工程预算定额(2018)",
]


def import_single_version(version_name):
    """导入单个定额版本（跳过索引重建）"""
    # 解析省份，找到数据目录
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
        print(f"  错误: 目录下没有xlsx文件 {quota_dir}")
        return 0

    # 创建数据库实例
    db = QuotaDB(province=resolved)

    # 导入每个xlsx文件
    total = 0
    imported = {}
    for xlsx_file in xlsx_files:
        specialty = detect_specialty_from_excel(str(xlsx_file))
        is_first = specialty not in imported
        mode = "clear+import" if is_first else "append"
        try:
            count = db.import_excel(str(xlsx_file), specialty=specialty, clear_existing=is_first)
            imported[specialty] = imported.get(specialty, 0) + count
            total += count
            # 记录导入历史
            try:
                db.record_import(str(xlsx_file), specialty, count)
            except Exception:
                pass
        except Exception as e:
            print(f"    导入失败 {xlsx_file.name}: {e}")

    return total


def main():
    print("=" * 60)
    print("批量导入19个定额版本（跳过索引重建）")
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

    # 汇总
    print(f"\n{'='*60}")
    print(f"导入汇总")
    print(f"{'='*60}")
    for version, count, elapsed in results:
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {version}: {count}条 ({elapsed:.1f}s)")
    print(f"\n总计: {grand_total}条, 耗时 {total_elapsed:.0f}秒")
    print(f"\n注意: 索引未重建，需要对每个省份单独运行索引重建")
    print(f"或者使用: python tools/import_all.py --province '版本名' 重新导入（会自动建索引）")


if __name__ == "__main__":
    main()
