# -*- coding: utf-8 -*-
"""
Jarvis 定额查询工具 - 从定额库中查询定额编号和名称

用法：
    python tools/jarvis_lookup.py "焊接钢管" "明配"
    python tools/jarvis_lookup.py --id "C4-11-30"
    python tools/jarvis_lookup.py "终端头" --section "C4-8"
    python tools/jarvis_lookup.py "桥架" --type "槽式"

支持：
    1. 关键词搜索（多个关键词取交集）
    2. 按定额编号精确查找
    3. 按章节过滤（如 C4-8, C4-11）
    4. 输出完整的档位列表
"""
import sys
import os
import argparse

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.quota_search import search_quota_db, search_by_id_prefix, search_series


def lookup_by_keywords(keywords: list, section: str = None, type_filter: str = None,
                       province: str = None, limit: int = 50) -> list:
    """按关键词搜索定额（多关键词取交集）"""
    all_keywords = list(keywords)
    if type_filter:
        all_keywords.append(type_filter)
    return search_quota_db(all_keywords, section=section, province=province, limit=limit)


def lookup_by_id(quota_id: str, province: str = None) -> list:
    """按定额编号查找（支持前缀匹配）"""
    return search_by_id_prefix(quota_id, province=province)


def lookup_series(quota_id: str, province: str = None) -> list:
    """查看某条定额所在的整个系列（同名不同档位的所有定额）"""
    return search_series(quota_id, province=province)


def print_results(results: list, title: str = "查询结果"):
    """格式化输出结果"""
    if not results:
        print("未找到匹配的定额")
        return

    print(f"\n{title}（共{len(results)}条）")
    print("-" * 80)
    for quota_id, name, unit in results:
        print(f"  {quota_id}\t{name}\t{unit}")
    print("-" * 80)


def main():
    parser = argparse.ArgumentParser(description="Jarvis 定额查询工具")
    parser.add_argument("keywords", nargs="*", help="搜索关键词（多个取交集）")
    parser.add_argument("--id", dest="quota_id", help="按定额编号查找")
    parser.add_argument("--series", help="查看某条定额的完整系列（所有档位）")
    parser.add_argument("--section", help="按章节过滤（如 C4-8, C4-11）")
    parser.add_argument("--type", dest="type_filter", help="按类型过滤（如 槽式、焊接）")
    parser.add_argument("--province", default=None, help="省份（默认使用config配置）")
    parser.add_argument("--limit", type=int, default=50, help="最大返回条数")
    args = parser.parse_args()

    if args.quota_id:
        results = lookup_by_id(args.quota_id, args.province)
        print_results(results, f"定额编号查找: {args.quota_id}")

    elif args.series:
        results = lookup_series(args.series, args.province)
        print_results(results, f"定额系列: {args.series}")

    elif args.keywords:
        results = lookup_by_keywords(args.keywords, args.section, args.type_filter,
                                     args.province, args.limit)
        print_results(results, f"关键词搜索: {' + '.join(args.keywords)}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
