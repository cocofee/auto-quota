# -*- coding: utf-8 -*-
"""
第四轮批量导出：补齐缺口省份（广西建筑+园林、重庆园林、浙江建筑、江苏市政2021）
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._batch_export_missing import export_single_quota

DB30 = r"D:\广联达\数据库30.0"

TARGETS = [
    # ===== 广西 补建筑+园林 =====
    {
        "province": "广西",
        "version_name": "广西建筑装饰装修工程消耗量定额(2024)",
        "db_base": os.path.join(DB30, r"广西\定额库\广西2022序列定额\定额库"),
        "quota_names": ["广西建筑装饰装修工程消耗量定额(2024)"],
    },
    {
        "province": "广西",
        "version_name": "广西园林绿化及仿古建筑工程消耗量定额(2021)",
        "db_base": os.path.join(DB30, r"广西\定额库\广西2022序列定额\定额库"),
        "quota_names": ["广西园林绿化及仿古建筑工程消耗量定额(2021)"],
    },

    # ===== 重庆 补园林 =====
    {
        "province": "重庆",
        "version_name": "重庆市园林绿化工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"重庆\定额库\重庆市2018序列定额\定额库"),
        "quota_names": ["重庆市园林绿化工程计价定额(2018)"],
    },

    # ===== 浙江 补建筑（之前目录存在但0条）=====
    {
        "province": "浙江",
        "version_name": "浙江省房屋建筑与装饰工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"浙江\定额库\浙江省2018序列定额\定额库"),
        "quota_names": ["浙江省房屋建筑与装饰工程预算定额(2018)"],
    },

    # ===== 江苏 补新版市政2021 =====
    {
        "province": "江苏",
        "version_name": "江苏省市政工程消耗量定额(2021年)",
        "db_base": os.path.join(DB30, r"江苏\定额库\江苏省2022序列定额\定额库"),
        "quota_names": ["江苏省市政工程消耗量定额(2021年)"],
    },
]


def main():
    print("=" * 60)
    print("第四轮批量导出：补齐缺口省份")
    print(f"共 {len(TARGETS)} 个定额")
    print("=" * 60)

    grand_total = 0
    results = []

    for target in TARGETS:
        if not os.path.isdir(target["db_base"]):
            print(f"\n跳过 {target['province']}/{target['version_name']}: 路径不存在")
            results.append((target["province"], target["version_name"], 0, "路径不存在"))
            continue

        count = export_single_quota(target)
        grand_total += count
        results.append((target["province"], target["version_name"], count, "成功"))

    print(f"\n{'='*60}")
    print(f"导出汇总")
    print(f"{'='*60}")
    current_province = None
    for province, version, count, status in results:
        if province != current_province:
            current_province = province
            print(f"\n{province}:")
        print(f"  {version}: {count}条 ({status})")
    print(f"\n总计: {grand_total}条")


if __name__ == "__main__":
    main()
