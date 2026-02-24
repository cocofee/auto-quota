# -*- coding: utf-8 -*-
"""
定额库搜索工具 — 轻量级查询函数

供审核纠正（review_correctors）和命令行查询（jarvis_lookup）共用，
避免重复实现相同的 SQL 逻辑。

注意：src/quota_db.py 的 QuotaDB 类也有搜索方法，但返回完整字典（供 Web 页面用）。
本模块的函数返回轻量元组 (quota_id, name, unit)，适合批量纠正场景。
"""

import os
import re

from db.sqlite import connect as _db_connect
from config import get_quota_db_path


def search_quota_db(keywords, dn=None, section=None, province=None, limit=10,
                    conn=None):
    """从定额库搜索匹配的定额

    参数:
        keywords: 关键词列表（取交集，每个关键词都必须出现在定额名中）
        dn: 公称直径，如果指定则按参数距离排序（精确匹配优先，向上取档次之）
        section: 章节前缀过滤（如 "C10-6"）
        province: 省份（决定使用哪个定额库）
        limit: 最大返回条数
        conn: 可选的共享数据库连接（传入则复用，不传则自己开关）
    返回: [(quota_id, name, unit), ...]
    """
    # 是否由外部管理连接生命周期
    own_conn = conn is None
    if own_conn:
        db_path = get_quota_db_path(province)
        if not os.path.exists(db_path):
            return []
        conn = _db_connect(db_path)

    cursor = conn.cursor()

    raw_keywords = []
    if keywords is None:
        raw_keywords = []
    elif isinstance(keywords, str):
        raw_keywords = [keywords]
    else:
        raw_keywords = list(keywords)

    normalized_keywords = []
    for kw in raw_keywords:
        if kw is None:
            continue
        text = str(kw).strip()
        if text:
            normalized_keywords.append(text)

    # 没有关键词也没有章节时，不执行全表查询，直接返回空
    if not normalized_keywords and not section:
        if own_conn:
            conn.close()
        return []

    conditions = []
    params = []
    for kw in normalized_keywords:
        conditions.append("name LIKE ?")
        params.append(f"%{kw}%")

    if section:
        conditions.append("quota_id LIKE ?")
        params.append(f"{section}%")

    where = " AND ".join(conditions)
    where_clause = f" WHERE {where}" if where else ""
    sql = f"SELECT quota_id, name, unit FROM quotas{where_clause} ORDER BY quota_id LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
    finally:
        if own_conn:
            conn.close()

    # 如果指定了 DN，优先返回参数匹配的
    if dn and results:
        def dn_distance(row):
            m = re.search(r'(\d+)\s*$', row[1])
            if m:
                quota_dn = int(m.group(1))
                if quota_dn == dn:
                    return 0
                elif quota_dn > dn:
                    return quota_dn - dn  # 向上取档
                else:
                    return 10000 + dn - quota_dn  # 向下惩罚大
            return 5000
        results.sort(key=dn_distance)

    return results


def search_by_id(quota_id, province=None, conn=None):
    """按定额编号精确查找

    参数:
        quota_id: 定额编号（如 "C10-6-5"）
        province: 省份
        conn: 可选的共享数据库连接
    返回: (quota_id, name, unit) 或 None
    """
    own_conn = conn is None
    if own_conn:
        db_path = get_quota_db_path(province)
        if not os.path.exists(db_path):
            return None
        conn = _db_connect(db_path)

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT quota_id, name, unit FROM quotas WHERE quota_id = ?",
                       (quota_id,))
        row = cursor.fetchone()
    finally:
        if own_conn:
            conn.close()
    return row


def search_by_id_prefix(quota_id, province=None, conn=None, limit=30):
    """按定额编号前缀查找（精确匹配优先，无则前缀匹配）

    参数:
        quota_id: 定额编号或前缀（如 "C10-6"）
        province: 省份
        conn: 可选的共享数据库连接
        limit: 最大返回条数
    返回: [(quota_id, name, unit), ...]
    """
    own_conn = conn is None
    if own_conn:
        db_path = get_quota_db_path(province)
        if not os.path.exists(db_path):
            return []
        conn = _db_connect(db_path)

    cursor = conn.cursor()
    try:
        # 先尝试精确匹配
        cursor.execute("SELECT quota_id, name, unit FROM quotas WHERE quota_id = ?",
                       (quota_id,))
        results = cursor.fetchall()

        # 没有精确匹配则用前缀
        if not results:
            cursor.execute(
                "SELECT quota_id, name, unit FROM quotas WHERE quota_id LIKE ? ORDER BY quota_id LIMIT ?",
                (f"{quota_id}%", limit)
            )
            results = cursor.fetchall()
    finally:
        if own_conn:
            conn.close()
    return results


def search_series(quota_id, province=None, conn=None):
    """查看某条定额所在的整个系列（同名不同档位的所有定额）

    参数:
        quota_id: 定额编号（如 "C10-6-5"）
        province: 省份
        conn: 可选的共享数据库连接
    返回: [(quota_id, name, unit), ...]
    """
    own_conn = conn is None
    if own_conn:
        db_path = get_quota_db_path(province)
        if not os.path.exists(db_path):
            return []
        conn = _db_connect(db_path)

    cursor = conn.cursor()
    try:
        # 先找到这条定额的名称
        cursor.execute("SELECT name FROM quotas WHERE quota_id = ?", (quota_id,))
        row = cursor.fetchone()
        if not row:
            return []

        # 提取名称的"家族前缀"（去掉最后的数字参数部分）
        name = row[0]
        parts = name.rsplit(" ", 1)
        family_name = parts[0] if len(parts) > 1 else name

        cursor.execute(
            "SELECT quota_id, name, unit FROM quotas WHERE name LIKE ? ORDER BY quota_id",
            (f"{family_name}%",)
        )
        results = cursor.fetchall()
    finally:
        if own_conn:
            conn.close()
    return results
