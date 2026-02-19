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
import sqlite3
import argparse

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_quota_db_path


def lookup_by_keywords(keywords: list, section: str = None, type_filter: str = None,
                       province: str = "北京2024", limit: int = 50) -> list:
    """按关键词搜索定额（多关键词取交集）"""
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        print(f"错误: 定额库不存在: {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 构建查询条件：每个关键词都必须出现在name中
    conditions = []
    params = []
    for kw in keywords:
        conditions.append("name LIKE ?")
        params.append(f"%{kw}%")

    if section:
        conditions.append("quota_id LIKE ?")
        params.append(f"{section}%")

    if type_filter:
        conditions.append("name LIKE ?")
        params.append(f"%{type_filter}%")

    where = " AND ".join(conditions)
    sql = f"SELECT quota_id, name, unit FROM quotas WHERE {where} ORDER BY quota_id LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()
    return results


def lookup_by_id(quota_id: str, province: str = "北京2024") -> list:
    """按定额编号查找（支持前缀匹配）"""
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        print(f"错误: 定额库不存在: {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 先精确匹配
    cursor.execute("SELECT quota_id, name, unit FROM quotas WHERE quota_id = ?", (quota_id,))
    results = cursor.fetchall()

    # 如果没有精确匹配，尝试前缀匹配（查看整个系列）
    if not results:
        cursor.execute(
            "SELECT quota_id, name, unit FROM quotas WHERE quota_id LIKE ? ORDER BY quota_id LIMIT 30",
            (f"{quota_id}%",)
        )
        results = cursor.fetchall()

    conn.close()
    return results


def lookup_series(quota_id: str, province: str = "北京2024") -> list:
    """查看某条定额所在的整个系列（同名不同档位的所有定额）"""
    db_path = get_quota_db_path(province)
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 先找到这条定额的名称（去掉最后的数字参数部分）
    cursor.execute("SELECT name FROM quotas WHERE quota_id = ?", (quota_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    # 提取名称的"家族前缀"（去掉最后的数字）
    name = row[0]
    # 找到名称中最后一个非数字非小数点的位置
    parts = name.rsplit(" ", 1)
    if len(parts) > 1:
        family_name = parts[0]
    else:
        family_name = name

    cursor.execute(
        "SELECT quota_id, name, unit FROM quotas WHERE name LIKE ? ORDER BY quota_id",
        (f"{family_name}%",)
    )
    results = cursor.fetchall()
    conn.close()
    return results


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
    parser.add_argument("--province", default="北京2024", help="省份（默认北京2024）")
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
