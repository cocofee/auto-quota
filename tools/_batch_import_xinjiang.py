# -*- coding: utf-8 -*-
"""
新疆全地区批量导入：自动扫描data/quota_data/新疆-*/下所有版本
"""
import sys
import os
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.quota_db import QuotaDB, detect_specialty_from_excel


def discover_versions():
    """扫描所有新疆-*文件夹下的定额版本"""
    data_base = Path(__file__).parent.parent / "data" / "quota_data"
    versions = []

    for region_dir in sorted(data_base.glob("新疆-*")):
        if not region_dir.is_dir():
            continue
        region = region_dir.name  # 例如 "新疆-伊犁"

        for version_dir in sorted(region_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            xlsx_files = sorted(version_dir.glob("*.xlsx"))
            if xlsx_files:
                versions.append({
                    "region": region,
                    "version_name": version_dir.name,
                    "version_dir": version_dir,
                    "xlsx_files": xlsx_files,
                })

    return versions


def import_single_version(info):
    """导入单个版本"""
    db = QuotaDB(province=info["version_name"])
    total = 0
    imported = {}

    for xlsx_file in info["xlsx_files"]:
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
    versions = discover_versions()

    # 按地区分组统计
    regions = {}
    for v in versions:
        regions.setdefault(v["region"], []).append(v["version_name"])

    print("=" * 60)
    print(f"新疆全地区批量导入")
    print(f"共 {len(regions)} 个地区，{len(versions)} 个定额版本")
    print("=" * 60)
    for region in sorted(regions):
        print(f"  {region}: {len(regions[region])}个")

    grand_total = 0
    results = []
    start_time = time.time()

    for i, info in enumerate(versions, 1):
        print(f"\n[{i}/{len(versions)}] {info['region']} / {info['version_name']}")
        t0 = time.time()
        count = import_single_version(info)
        elapsed = time.time() - t0
        grand_total += count
        results.append((info["region"], info["version_name"], count, elapsed))
        print(f"  完成: {count}条 ({elapsed:.1f}秒)")

    total_elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"导入汇总")
    print(f"{'='*60}")

    current_region = None
    region_total = 0
    for region, name, count, elapsed in results:
        if region != current_region:
            if current_region:
                print(f"  小计: {region_total}条")
            current_region = region
            region_total = 0
            print(f"\n{region}:")
        region_total += count
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {name}: {count}条 ({elapsed:.1f}s)")
    if current_region:
        print(f"  小计: {region_total}条")

    print(f"\n总计: {grand_total}条, 耗时 {total_elapsed:.0f}秒")


if __name__ == "__main__":
    main()
