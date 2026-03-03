# -*- coding: utf-8 -*-
"""
第三轮批量导出：9个新省份的全套定额（安装+建筑+市政+园林+装饰）
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._batch_export_missing import export_single_quota

DB30 = r"D:\广联达\数据库30.0"

TARGETS = [
    # ===== 天津 2020 =====
    {
        "province": "天津",
        "version_name": "天津市安装工程预算基价(2020)",
        "db_base": os.path.join(DB30, r"天津\定额库\天津2020序列定额\定额库"),
        "quota_names": ["天津市安装工程预算基价(2020)"],
    },
    {
        "province": "天津",
        "version_name": "天津市建筑工程预算基价(2020)",
        "db_base": os.path.join(DB30, r"天津\定额库\天津2020序列定额\定额库"),
        "quota_names": ["天津市建筑工程预算基价(2020)"],
    },
    {
        "province": "天津",
        "version_name": "天津市市政工程预算基价(2020)",
        "db_base": os.path.join(DB30, r"天津\定额库\天津2020序列定额\定额库"),
        "quota_names": ["天津市市政工程预算基价(2020)"],
    },
    {
        "province": "天津",
        "version_name": "天津市装饰装修工程预算基价(2020)",
        "db_base": os.path.join(DB30, r"天津\定额库\天津2020序列定额\定额库"),
        "quota_names": ["天津市装饰装修工程预算基价(2020)"],
    },
    {
        "province": "天津",
        "version_name": "天津市仿古建筑及园林工程预算基价(2020)",
        "db_base": os.path.join(DB30, r"天津\定额库\天津2020序列定额\定额库"),
        "quota_names": ["天津市仿古建筑及园林工程预算基价(2020)"],
    },

    # ===== 河北 2022 =====
    {
        "province": "河北",
        "version_name": "河北省建设工程消耗量标准(2022)-安装工程",
        "db_base": os.path.join(DB30, r"河北\定额库\河北省2022序列定额\定额库"),
        "quota_names": ["河北省建设工程消耗量标准(2022)-安装工程"],
    },
    {
        "province": "河北",
        "version_name": "河北省建设工程消耗量标准(2022)-建筑工程",
        "db_base": os.path.join(DB30, r"河北\定额库\河北省2022序列定额\定额库"),
        "quota_names": ["河北省建设工程消耗量标准(2022)-建筑工程"],
    },
    {
        "province": "河北",
        "version_name": "河北省建设工程消耗量标准(2022)-市政工程",
        "db_base": os.path.join(DB30, r"河北\定额库\河北省2022序列定额\定额库"),
        "quota_names": ["河北省建设工程消耗量标准(2022)-市政工程"],
    },
    {
        "province": "河北",
        "version_name": "河北省建设工程消耗量标准(2022)-装饰装修工程",
        "db_base": os.path.join(DB30, r"河北\定额库\河北省2022序列定额\定额库"),
        "quota_names": ["河北省建设工程消耗量标准(2022)-装饰装修工程"],
    },
    {
        "province": "河北",
        "version_name": "河北省建设工程消耗量标准(2023)-园林绿化工程",
        "db_base": os.path.join(DB30, r"河北\定额库\河北省2022序列定额\定额库"),
        "quota_names": ["河北省建设工程消耗量标准(2023)-园林绿化工程"],
    },

    # ===== 山西 2018 =====
    {
        "province": "山西",
        "version_name": "山西省安装工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"山西\定额库\山西省2018序列定额\定额库"),
        "quota_names": ["山西省安装工程预算定额(2018)"],
    },
    {
        "province": "山西",
        "version_name": "山西省建筑工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"山西\定额库\山西省2018序列定额\定额库"),
        "quota_names": ["山西省建筑工程预算定额(2018)"],
    },
    {
        "province": "山西",
        "version_name": "山西省市政工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"山西\定额库\山西省2018序列定额\定额库"),
        "quota_names": ["山西省市政工程预算定额(2018)"],
    },
    {
        "province": "山西",
        "version_name": "山西省装饰工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"山西\定额库\山西省2018序列定额\定额库"),
        "quota_names": ["山西省装饰工程预算定额(2018)"],
    },
    {
        "province": "山西",
        "version_name": "山西省园林绿化工程预算定额(2018)",
        "db_base": os.path.join(DB30, r"山西\定额库\山西省2018序列定额\定额库"),
        "quota_names": ["山西省园林绿化工程预算定额(2018)"],
    },

    # ===== 吉林 2024（最新！）=====
    {
        "province": "吉林",
        "version_name": "吉林省安装工程计价定额(2024)",
        "db_base": os.path.join(DB30, r"吉林\定额库\吉林2024序列定额\定额库"),
        "quota_names": ["吉林省安装工程计价定额(2024)"],
    },
    {
        "province": "吉林",
        "version_name": "吉林省建筑装饰工程计价定额(2024)",
        "db_base": os.path.join(DB30, r"吉林\定额库\吉林2024序列定额\定额库"),
        "quota_names": ["吉林省建筑装饰工程计价定额(2024)"],
    },
    {
        "province": "吉林",
        "version_name": "吉林省市政工程计价定额(2024)",
        "db_base": os.path.join(DB30, r"吉林\定额库\吉林2024序列定额\定额库"),
        "quota_names": ["吉林省市政工程计价定额(2024)"],
    },
    {
        "province": "吉林",
        "version_name": "吉林省园林仿古工程计价定额(2024)",
        "db_base": os.path.join(DB30, r"吉林\定额库\吉林2024序列定额\定额库"),
        "quota_names": ["吉林省园林仿古工程计价定额(2024)"],
    },

    # ===== 安徽 2018 =====
    {
        "province": "安徽",
        "version_name": "安徽省安装工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"安徽\定额库\安徽2018序列定额\定额库"),
        "quota_names": ["安徽省安装工程计价定额(2018)"],
    },
    {
        "province": "安徽",
        "version_name": "安徽省建筑工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"安徽\定额库\安徽2018序列定额\定额库"),
        "quota_names": ["安徽省建筑工程计价定额(2018)"],
    },
    {
        "province": "安徽",
        "version_name": "安徽省市政工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"安徽\定额库\安徽2018序列定额\定额库"),
        "quota_names": ["安徽省市政工程计价定额(2018)"],
    },
    {
        "province": "安徽",
        "version_name": "安徽省园林绿化工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"安徽\定额库\安徽2018序列定额\定额库"),
        "quota_names": ["安徽省园林绿化工程计价定额(2018)"],
    },
    {
        "province": "安徽",
        "version_name": "安徽省装饰装修工程计价定额(2018)",
        "db_base": os.path.join(DB30, r"安徽\定额库\安徽2018序列定额\定额库"),
        "quota_names": ["安徽省装饰装修工程计价定额(2018)"],
    },

    # ===== 贵州 2016 =====
    {
        "province": "贵州",
        "version_name": "贵州省通用安装工程计价定额(2016)",
        "db_base": os.path.join(DB30, r"贵州\定额库\贵州2016序列定额\定额库"),
        "quota_names": ["贵州省通用安装工程计价定额(2016)"],
    },
    {
        "province": "贵州",
        "version_name": "贵州省建筑与装饰工程计价定额(2016)",
        "db_base": os.path.join(DB30, r"贵州\定额库\贵州2016序列定额\定额库"),
        "quota_names": ["贵州省建筑与装饰工程计价定额(2016)"],
    },
    {
        "province": "贵州",
        "version_name": "贵州省市政工程计价定额(2016)",
        "db_base": os.path.join(DB30, r"贵州\定额库\贵州2016序列定额\定额库"),
        "quota_names": ["贵州省市政工程计价定额(2016)"],
    },
    {
        "province": "贵州",
        "version_name": "贵州省园林绿化工程计价定额(2016)",
        "db_base": os.path.join(DB30, r"贵州\定额库\贵州2016序列定额\定额库"),
        "quota_names": ["贵州省园林绿化工程计价定额(2016)"],
    },

    # ===== 海南 2024(安装+建筑) + 2017(市政+园林) =====
    {
        "province": "海南",
        "version_name": "海南省安装工程综合定额(2024)",
        "db_base": os.path.join(DB30, r"海南\定额库\海南2024序列定额\定额库"),
        "quota_names": ["海南省安装工程综合定额(2024)"],
    },
    {
        "province": "海南",
        "version_name": "海南省房屋建筑与装饰工程综合定额(2024)",
        "db_base": os.path.join(DB30, r"海南\定额库\海南2024序列定额\定额库"),
        "quota_names": ["海南省房屋建筑与装饰工程综合定额(2024)"],
    },
    {
        "province": "海南",
        "version_name": "海南省市政工程综合定额(2017)",
        "db_base": os.path.join(DB30, r"海南\定额库\海南2017序列定额\定额库"),
        "quota_names": ["海南省市政工程综合定额(2017)"],
    },
    {
        "province": "海南",
        "version_name": "海南省园林绿化工程综合定额(2019)",
        "db_base": os.path.join(DB30, r"海南\定额库\海南2017序列定额\定额库"),
        "quota_names": ["海南省园林绿化工程综合定额(2019)"],
    },

    # ===== 青海 2020 =====
    {
        "province": "青海",
        "version_name": "青海省通用安装工程计价定额(2020)",
        "db_base": os.path.join(DB30, r"青海\定额库\青海2020序列定额\定额库"),
        "quota_names": ["青海省通用安装工程计价定额(2020)"],
    },
    {
        "province": "青海",
        "version_name": "青海省房屋建筑与装饰工程计价定额(2020)",
        "db_base": os.path.join(DB30, r"青海\定额库\青海2020序列定额\定额库"),
        "quota_names": ["青海省房屋建筑与装饰工程计价定额(2020)"],
    },
    {
        "province": "青海",
        "version_name": "青海省市政工程计价定额(2020)",
        "db_base": os.path.join(DB30, r"青海\定额库\青海2020序列定额\定额库"),
        "quota_names": ["青海省市政工程计价定额(2020)"],
    },
    {
        "province": "青海",
        "version_name": "青海省园林绿化工程计价定额(2020)",
        "db_base": os.path.join(DB30, r"青海\定额库\青海2020序列定额\定额库"),
        "quota_names": ["青海省园林绿化工程计价定额(2020)"],
    },

    # ===== 新疆 2020（乌鲁木齐版，定额条目全国统一只是价格不同）=====
    {
        "province": "新疆",
        "version_name": "全统安装工程消耗量定额乌鲁木齐估价汇总表(2020)",
        "db_base": os.path.join(DB30, r"新疆\定额库\新疆2020序列定额\定额库"),
        "quota_names": ["全统安装工程消耗量定额乌鲁木齐估价汇总表(2020)"],
    },
    {
        "province": "新疆",
        "version_name": "新疆房屋建筑与装饰工程消耗量定额乌鲁木齐估价汇总表(2020)",
        "db_base": os.path.join(DB30, r"新疆\定额库\新疆2020序列定额\定额库"),
        "quota_names": ["新疆房屋建筑与装饰工程消耗量定额乌鲁木齐估价汇总表(2020)"],
    },
    {
        "province": "新疆",
        "version_name": "新疆市政工程消耗量定额乌鲁木齐估价汇总表(2020)",
        "db_base": os.path.join(DB30, r"新疆\定额库\新疆2020序列定额\定额库"),
        "quota_names": ["新疆市政工程消耗量定额乌鲁木齐估价汇总表(2020)"],
    },
]


def main():
    print("=" * 60)
    print("第三轮批量导出：9个新省份全套定额")
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
