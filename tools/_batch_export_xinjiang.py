# -*- coding: utf-8 -*-
"""
新疆全地区批量导出：每个地区单独文件夹（新疆-乌鲁木齐、新疆-伊犁...）
新疆特殊：每个地区一套定额（编号相同，价格不同），需按地区分别建文件夹
"""
import sys
import os
import re
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._batch_export_missing import export_single_quota

DB30 = r"D:\广联达\数据库30.0"
DB_BASE = os.path.join(DB30, r"新疆\定额库\新疆2020序列定额\定额库")

# 已导入的乌鲁木齐版（需要移到"新疆-乌鲁木齐"文件夹，先跳过后手动处理）
ALREADY_IMPORTED = {
    "全统安装工程消耗量定额乌鲁木齐估价汇总表(2020)",
    "新疆房屋建筑与装饰工程消耗量定额乌鲁木齐估价汇总表(2020)",
    "新疆市政工程消耗量定额乌鲁木齐估价汇总表(2020)",
}

# 从定额名称提取地区名
# 例如："全统安装工程消耗量定额伊犁估价汇总表(2022)" → "伊犁"
# 例如："全统安装工程消耗量定额塔城地区乌苏市估价汇总表(2022)" → "塔城地区乌苏市"
def extract_region(version_name):
    """从版本名称提取地区"""
    # 安装类：...定额{地区}估价汇总表(年份)
    m = re.search(r'定额(.+?)估价汇总表', version_name)
    if m:
        return m.group(1)
    return None


def discover_targets():
    """自动扫描新疆2020序列所有安装/建筑/市政定额，按地区分组"""
    targets = []
    if not os.path.isdir(DB_BASE):
        print(f"错误: 路径不存在 {DB_BASE}")
        return targets

    for name in sorted(os.listdir(DB_BASE)):
        # 只要安装/建筑/市政
        is_install = "安装" in name
        is_building = "建筑" in name and "装配式" not in name and "文物" not in name
        is_municipal = "市政" in name and "排水" not in name

        if not (is_install or is_building or is_municipal):
            continue

        # 跳过已导入的乌鲁木齐版（后面会把旧数据迁移到新文件夹）
        if name in ALREADY_IMPORTED:
            continue

        index_path = os.path.join(DB_BASE, name, "数据", "子目索引.Index")
        if not os.path.isfile(index_path):
            continue

        region = extract_region(name)
        if not region:
            print(f"  警告: 无法提取地区 '{name}'，跳过")
            continue

        # 按地区建文件夹：新疆-伊犁、新疆-塔城 等
        province_folder = f"新疆-{region}"

        targets.append({
            "province": province_folder,
            "version_name": name,
            "db_base": DB_BASE,
            "quota_names": [name],
        })

    return targets


def main():
    targets = discover_targets()

    # 按地区分组统计
    regions = {}
    for t in targets:
        r = t["province"]
        regions.setdefault(r, []).append(t["version_name"])

    print("=" * 60)
    print(f"新疆全地区批量导出")
    print(f"共 {len(regions)} 个地区，{len(targets)} 个定额")
    print("=" * 60)
    for region in sorted(regions):
        print(f"  {region}: {len(regions[region])}个")

    grand_total = 0
    results = []

    for i, target in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}]", end=" ")
        count = export_single_quota(target)
        grand_total += count
        results.append((target["province"], target["version_name"], count))

    print(f"\n{'='*60}")
    print(f"导出汇总")
    print(f"{'='*60}")

    current_region = None
    region_total = 0
    for region, name, count in results:
        if region != current_region:
            if current_region:
                print(f"  小计: {region_total}条")
            current_region = region
            region_total = 0
            print(f"\n{region}:")
        region_total += count
        print(f"  {name}: {count}条")
    if current_region:
        print(f"  小计: {region_total}条")

    print(f"\n总计: {grand_total}条")


if __name__ == "__main__":
    main()
