#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定额数据库全面质量检查脚本
检查 db/provinces/ 下所有省份的 quota.db，输出完整质量报告。

检查项目：
1. 日期格式编码 — quota_id 中包含 "00:00:00" 等日期时间格式
2. 空编码 — quota_id 为空/None/纯空白
3. 空名称 — name 为空/None/纯空白
4. 重复编码 — 同一库内有重复 quota_id
5. 异常字符 — 编码中包含中文、特殊符号等不合理字符
6. 编码格式一致性 — 同一库内编码前缀模式是否统一
7. 数据量 — 每个库有多少条记录，是否有空库
8. 索引状态 — 是否有 BM25 索引和向量索引文件
"""

import sqlite3
import os
import re
import sys
import io
import json
from collections import Counter, defaultdict

# 强制 stdout 使用 UTF-8 编码（解决 Windows 终端中文乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# === 配置 ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVINCES_DIR = os.path.join(BASE_DIR, "db", "provinces")

# === 工具函数 ===

def has_datetime_pattern(text):
    """检查文本是否包含日期时间格式（已知问题：编码中混入了 '00:00:00' 等）"""
    patterns = [
        r'\d{4}-\d{2}-\d{2}',           # 2024-01-01
        r'\d{2}:\d{2}:\d{2}',           # 00:00:00
        r'\d{4}/\d{2}/\d{2}',           # 2024/01/01
        r'\d{4}\.\d{2}\.\d{2}',         # 2024.01.01
        r'T\d{2}:\d{2}',               # T00:00 (ISO格式片段)
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False

def has_abnormal_chars(quota_id):
    """检查编码是否包含异常字符（中文、特殊符号等）
    合理的编码字符：字母、数字、横杠-、下划线_、点.、斜杠/
    """
    # 中文字符
    if re.search(r'[\u4e00-\u9fff]', quota_id):
        return "包含中文"
    # 常见不合理字符
    if re.search(r'[#@$%^&*()+=\[\]{}|\\<>~`!！？，。；：""''【】]', quota_id):
        return "包含特殊符号"
    # 空格（编码中不应有空格）
    if ' ' in quota_id.strip():
        return "包含空格"
    # 制表符/换行
    if '\t' in quota_id or '\n' in quota_id or '\r' in quota_id:
        return "包含制表符/换行"
    return None

def extract_prefix(quota_id):
    """提取编码前缀模式，用于判断格式一致性
    例如 'C1-1-1' → 'C<数字>', 'A-1-1' → 'A', '1-1-1' → '<纯数字>'
    """
    quota_id = quota_id.strip()
    # 匹配开头的字母+数字部分作为前缀
    m = re.match(r'^([A-Za-z]+\d*)', quota_id)
    if m:
        prefix = m.group(1)
        # 标准化：把具体数字替换为通配
        return re.sub(r'\d+', 'N', prefix)
    # 纯数字开头
    m = re.match(r'^(\d+)', quota_id)
    if m:
        return "<数字开头>"
    return "<其他>"


def check_province(province_name, province_dir):
    """检查单个省份的 quota.db，返回问题列表"""
    result = {
        "province": province_name,
        "db_exists": False,
        "has_quotas_table": False,
        "total_rows": 0,
        "issues": [],
        "index_status": {
            "bm25_json": False,
            "bm25_pkl": False,
            "vector_index": False,
        },
        "prefix_distribution": {},
    }

    db_path = os.path.join(province_dir, "quota.db")
    if not os.path.exists(db_path):
        result["issues"].append(("严重", "数据库文件不存在"))
        return result
    result["db_exists"] = True

    # 检查索引文件
    for f in os.listdir(province_dir):
        if f == "bm25_index.json":
            result["index_status"]["bm25_json"] = True
        elif f == "bm25_index.pkl":
            result["index_status"]["bm25_pkl"] = True
        elif "vector" in f.lower() or "chroma" in f.lower() or f.endswith(".faiss"):
            result["index_status"]["vector_index"] = True

    # 打开数据库
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
    except Exception as e:
        result["issues"].append(("严重", f"无法打开数据库: {e}"))
        return result

    # 检查是否有 quotas 表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cur.fetchall()]
    if "quotas" not in tables:
        result["issues"].append(("严重", f"没有 quotas 表，现有表: {tables}"))
        conn.close()
        return result
    result["has_quotas_table"] = True

    # 读取所有记录
    cur.execute("SELECT id, quota_id, name FROM quotas")
    rows = cur.fetchall()
    result["total_rows"] = len(rows)

    if len(rows) == 0:
        result["issues"].append(("警告", "数据库为空（0条记录）"))
        conn.close()
        return result

    # === 逐条检查 ===
    datetime_issues = []   # 日期格式编码
    empty_id_issues = []   # 空编码
    empty_name_issues = [] # 空名称
    abnormal_issues = []   # 异常字符
    all_quota_ids = []     # 用于检查重复
    prefix_counter = Counter()  # 编码前缀统计

    for row_id, quota_id, name in rows:
        # 1. 空编码检查
        if quota_id is None or str(quota_id).strip() == "":
            empty_id_issues.append(row_id)
            continue

        quota_id_str = str(quota_id).strip()
        all_quota_ids.append(quota_id_str)

        # 2. 日期格式编码
        if has_datetime_pattern(quota_id_str):
            datetime_issues.append((row_id, quota_id_str))

        # 3. 空名称
        if name is None or str(name).strip() == "":
            empty_name_issues.append((row_id, quota_id_str))

        # 4. 异常字符
        abnormal = has_abnormal_chars(quota_id_str)
        if abnormal:
            abnormal_issues.append((row_id, quota_id_str, abnormal))

        # 5. 编码前缀
        prefix = extract_prefix(quota_id_str)
        prefix_counter[prefix] += 1

    # 6. 重复编码
    id_counter = Counter(all_quota_ids)
    duplicates = {k: v for k, v in id_counter.items() if v > 1}

    # === 汇总问题 ===
    if datetime_issues:
        samples = datetime_issues[:5]
        sample_text = ", ".join([f"id={s[0]}:'{s[1]}'" for s in samples])
        more = f" ...等共{len(datetime_issues)}条" if len(datetime_issues) > 5 else ""
        result["issues"].append(("严重", f"日期格式编码 {len(datetime_issues)}条: {sample_text}{more}"))

    if empty_id_issues:
        sample_text = ", ".join([str(x) for x in empty_id_issues[:10]])
        result["issues"].append(("严重", f"空编码 {len(empty_id_issues)}条, 行ID: {sample_text}"))

    if empty_name_issues:
        samples = empty_name_issues[:5]
        sample_text = ", ".join([f"'{s[1]}'" for s in samples])
        more = f" ...等共{len(empty_name_issues)}条" if len(empty_name_issues) > 5 else ""
        result["issues"].append(("警告", f"空名称 {len(empty_name_issues)}条: {sample_text}{more}"))

    if duplicates:
        dup_samples = sorted(duplicates.items(), key=lambda x: -x[1])[:10]
        sample_text = ", ".join([f"'{k}'×{v}" for k, v in dup_samples])
        more = f" ...等共{len(duplicates)}个重复编码" if len(duplicates) > 10 else ""
        result["issues"].append(("警告", f"重复编码 {len(duplicates)}个: {sample_text}{more}"))

    if abnormal_issues:
        samples = abnormal_issues[:5]
        sample_text = ", ".join([f"'{s[1]}'({s[2]})" for s in samples])
        more = f" ...等共{len(abnormal_issues)}条" if len(abnormal_issues) > 5 else ""
        result["issues"].append(("警告", f"异常字符 {len(abnormal_issues)}条: {sample_text}{more}"))

    result["prefix_distribution"] = dict(prefix_counter.most_common(20))

    # 编码格式一致性判断：如果前缀种类超过合理范围，标记
    if len(prefix_counter) > 15:
        result["issues"].append(("提示", f"编码前缀种类较多({len(prefix_counter)}种)，格式可能不统一"))

    conn.close()
    return result


def main():
    print("=" * 80)
    print("  定额数据库全面质量检查报告")
    print("=" * 80)
    print(f"\n数据库目录: {PROVINCES_DIR}")

    if not os.path.exists(PROVINCES_DIR):
        print("错误: 目录不存在!")
        return

    # 遍历所有省份
    provinces = sorted([
        d for d in os.listdir(PROVINCES_DIR)
        if os.path.isdir(os.path.join(PROVINCES_DIR, d))
    ])
    print(f"共发现 {len(provinces)} 个省份/定额库目录\n")

    all_results = []
    # 汇总统计
    total_records = 0
    total_issues_count = {"严重": 0, "警告": 0, "提示": 0}
    empty_dbs = []         # 空库列表
    no_index_dbs = []      # 无索引列表
    datetime_dbs = []      # 有日期编码问题的库
    duplicate_dbs = []     # 有重复编码的库

    for province in provinces:
        province_dir = os.path.join(PROVINCES_DIR, province)
        result = check_province(province, province_dir)
        all_results.append(result)
        total_records += result["total_rows"]

        for level, msg in result["issues"]:
            total_issues_count[level] += 1
            if "日期格式" in msg:
                datetime_dbs.append(province)
            if "重复编码" in msg:
                duplicate_dbs.append(province)

        if result["total_rows"] == 0 and result["db_exists"]:
            empty_dbs.append(province)

        if result["db_exists"] and result["has_quotas_table"] and result["total_rows"] > 0:
            if not result["index_status"]["bm25_json"]:
                no_index_dbs.append(province)

    # === 打印汇总报告 ===
    print("=" * 80)
    print("  一、整体概况")
    print("=" * 80)
    print(f"  省份/定额库数量: {len(provinces)}")
    print(f"  总记录数: {total_records:,}")
    print(f"  有数据的库: {sum(1 for r in all_results if r['total_rows'] > 0)}")
    print(f"  空库/无表: {sum(1 for r in all_results if r['total_rows'] == 0)}")
    print(f"  问题统计: 严重={total_issues_count['严重']}, 警告={total_issues_count['警告']}, 提示={total_issues_count['提示']}")

    # === 二、数据量排行 ===
    print("\n" + "=" * 80)
    print("  二、各库数据量排行")
    print("=" * 80)
    sorted_by_count = sorted(all_results, key=lambda x: -x["total_rows"])
    for i, r in enumerate(sorted_by_count):
        idx_icon = "有" if r["index_status"]["bm25_json"] else "无"
        status = ""
        if r["total_rows"] == 0:
            status = " [空库]"
        elif not r["has_quotas_table"]:
            status = " [无quotas表]"
        print(f"  {i+1:3d}. {r['province']}")
        print(f"       记录数: {r['total_rows']:>8,}  BM25索引: {idx_icon}{status}")

    # === 三、问题详情 ===
    print("\n" + "=" * 80)
    print("  三、问题详情（按省份）")
    print("=" * 80)

    has_any_issue = False
    for r in all_results:
        if r["issues"]:
            has_any_issue = True
            print(f"\n  [{r['province']}] (记录数: {r['total_rows']:,})")
            for level, msg in r["issues"]:
                marker = {"严重": "!!!", "警告": " ! ", "提示": " i "}[level]
                print(f"    [{marker}] [{level}] {msg}")

    if not has_any_issue:
        print("  无问题，所有库均通过检查。")

    # === 四、日期格式编码专项 ===
    if datetime_dbs:
        print("\n" + "=" * 80)
        print("  四、日期格式编码专项（已知问题）")
        print("=" * 80)
        print(f"  共 {len(datetime_dbs)} 个库存在此问题:")
        for db_name in datetime_dbs:
            print(f"    - {db_name}")
    else:
        print("\n" + "=" * 80)
        print("  四、日期格式编码专项 — 无此问题")
        print("=" * 80)

    # === 五、重复编码专项 ===
    if duplicate_dbs:
        print("\n" + "=" * 80)
        print("  五、重复编码专项")
        print("=" * 80)
        print(f"  共 {len(duplicate_dbs)} 个库存在重复编码:")
        for db_name in duplicate_dbs:
            print(f"    - {db_name}")
    else:
        print("\n" + "=" * 80)
        print("  五、重复编码专项 — 无此问题")
        print("=" * 80)

    # === 六、索引缺失 ===
    print("\n" + "=" * 80)
    print("  六、索引状态检查")
    print("=" * 80)
    if no_index_dbs:
        print(f"  以下 {len(no_index_dbs)} 个有数据的库缺少 BM25 索引:")
        for db_name in no_index_dbs:
            print(f"    - {db_name}")
    else:
        print("  所有有数据的库均有 BM25 索引。")

    # 向量索引统计
    has_vector = sum(1 for r in all_results if r["index_status"]["vector_index"])
    print(f"\n  向量索引情况: {has_vector}/{len(provinces)} 个库有向量索引文件")

    # === 七、编码格式分布 ===
    print("\n" + "=" * 80)
    print("  七、编码前缀格式分布（各库 top5 前缀）")
    print("=" * 80)
    for r in all_results:
        if r["total_rows"] > 0 and r["prefix_distribution"]:
            prefixes = list(r["prefix_distribution"].items())[:5]
            prefix_str = ", ".join([f"{k}({v}条)" for k, v in prefixes])
            print(f"  {r['province']}")
            print(f"    前缀: {prefix_str}")

    # === 八、空库清单 ===
    if empty_dbs:
        print("\n" + "=" * 80)
        print("  八、空库/无数据清单")
        print("=" * 80)
        for db_name in empty_dbs:
            print(f"    - {db_name}")

    print("\n" + "=" * 80)
    print("  检查完毕")
    print("=" * 80)


if __name__ == "__main__":
    main()
