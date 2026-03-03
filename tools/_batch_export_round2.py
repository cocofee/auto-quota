# -*- coding: utf-8 -*-
"""
第二轮批量导出：补齐7省的市政+建筑+园林定额
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._batch_export_missing import export_single_quota

DB30 = r"D:\广联达\数据库30.0"

# 每项: province, version_name(输出目录名), db_base(广联达定额库路径), quota_names(要导的子定额)
TARGETS = [
    # ===== 上海 2016 =====
    {
        "province": "上海",
        "version_name": "上海市市政工程预算定额(2016)",
        "db_base": os.path.join(DB30, r"上海\定额库\上海2016预算定额\定额库"),
        "quota_names": ["上海市市政工程预算定额(2016)"],
    },
    {
        "province": "上海",
        "version_name": "上海市建筑和装饰工程预算定额(2016)",
        "db_base": os.path.join(DB30, r"上海\定额库\上海2016预算定额\定额库"),
        "quota_names": ["上海市建筑和装饰工程预算定额(2016)"],
    },
    {
        "province": "上海",
        "version_name": "上海市园林工程预算定额(2016)",
        "db_base": os.path.join(DB30, r"上海\定额库\上海2016预算定额\定额库"),
        "quota_names": ["上海市园林工程预算定额(2016)"],
    },

    # ===== 深圳 2024 =====
    {
        "province": "深圳",
        "version_name": "深圳市市政工程消耗量标准(2024)",
        "db_base": os.path.join(DB30, r"深圳\定额库\深圳2024序列定额\定额库"),
        "quota_names": ["深圳市市政工程消耗量标准(2024)"],
    },
    {
        "province": "深圳",
        "version_name": "深圳市建筑工程消耗量标准(2024)",
        "db_base": os.path.join(DB30, r"深圳\定额库\深圳2024序列定额\定额库"),
        "quota_names": ["深圳市建筑工程消耗量标准(2024)"],
    },
    {
        # 深圳没有2024园林，用2016序列的2017版
        "province": "深圳",
        "version_name": "深圳市园林建筑绿化工程消耗量定额(2017)",
        "db_base": os.path.join(DB30, r"深圳\定额库\深圳2016序列定额\定额库"),
        "quota_names": ["深圳市园林建筑绿化工程消耗量定额(2017)"],
    },

    # ===== 云南 2020 =====
    {
        "province": "云南",
        "version_name": "云南省市政工程计价标准(2020)",
        "db_base": os.path.join(DB30, r"云南\定额库\云南省2020序列定额\定额库"),
        "quota_names": ["云南省市政工程计价标准(2020)"],
    },
    {
        "province": "云南",
        "version_name": "云南省建筑工程计价标准(2020)",
        "db_base": os.path.join(DB30, r"云南\定额库\云南省2020序列定额\定额库"),
        "quota_names": ["云南省建筑工程计价标准(2020)"],
    },
    {
        "province": "云南",
        "version_name": "云南省园林绿化工程计价标准(2020)",
        "db_base": os.path.join(DB30, r"云南\定额库\云南省2020序列定额\定额库"),
        "quota_names": ["云南省园林绿化工程计价标准(2020)"],
    },

    # ===== 内蒙古 2017 =====
    {
        "province": "内蒙古",
        "version_name": "内蒙古市政工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"内蒙古\定额库\内蒙古2017序列定额\定额库"),
        "quota_names": ["内蒙古市政工程预算定额(2017)"],
    },
    {
        "province": "内蒙古",
        "version_name": "内蒙古房屋建筑与装饰工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"内蒙古\定额库\内蒙古2017序列定额\定额库"),
        "quota_names": ["内蒙古房屋建筑与装饰工程预算定额(2017)"],
    },
    {
        "province": "内蒙古",
        "version_name": "内蒙古园林绿化工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"内蒙古\定额库\内蒙古2017序列定额\定额库"),
        "quota_names": ["内蒙古园林绿化工程预算定额(2017)"],
    },

    # ===== 甘肃 2013 =====
    {
        "province": "甘肃",
        "version_name": "甘肃省市政工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"甘肃\定额库\甘肃2013序列预算定额\定额库"),
        "quota_names": ["甘肃省市政工程预算定额(2018)"],
    },
    {
        "province": "甘肃",
        "version_name": "甘肃省建筑与装饰工程预算定额(2013)",
        "db_base": os.path.join(DB30, r"甘肃\定额库\甘肃2013序列预算定额\定额库"),
        "quota_names": ["甘肃省建筑与装饰工程预算定额(2013)"],
    },

    # ===== 福建 2017 =====
    {
        "province": "福建",
        "version_name": "福建省市政工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"福建\定额库\福建2017序列定额\定额库"),
        "quota_names": ["福建省市政工程预算定额(2017)"],
    },
    {
        "province": "福建",
        "version_name": "福建省房屋建筑与装饰工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"福建\定额库\福建2017序列定额\定额库"),
        "quota_names": ["福建省房屋建筑与装饰工程预算定额(2017)"],
    },
    {
        "province": "福建",
        "version_name": "福建省园林绿化工程预算定额(2017)",
        "db_base": os.path.join(DB30, r"福建\定额库\福建2017序列定额\定额库"),
        "quota_names": ["福建省园林绿化工程预算定额(2017)"],
    },

    # ===== 浙江 2018 =====
    {
        "province": "浙江",
        "version_name": "浙江省市政工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"浙江\定额库\浙江省2018序列定额\定额库"),
        "quota_names": ["浙江省市政工程预算定额(2018)"],
    },
    {
        "province": "浙江",
        "version_name": "浙江省园林绿化及仿古建筑工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"浙江\定额库\浙江省2018序列定额\定额库"),
        "quota_names": ["浙江省园林绿化及仿古建筑工程预算定额(2018)"],
    },
]


def main():
    print("=" * 60)
    print("第二轮批量导出：补齐7省市政+建筑+园林")
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
