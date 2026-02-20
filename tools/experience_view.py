# -*- coding: utf-8 -*-
"""
经验库查看工具 - 统计、搜索、浏览经验库记录

用法：
    python tools/experience_view.py stats                    # 查看统计
    python tools/experience_view.py search "镀锌钢管"         # 搜索记录
    python tools/experience_view.py list                     # 浏览最近记录
    python tools/experience_view.py list --page 2            # 第2页
    python tools/experience_view.py list --province "北京2024" # 按省份过滤
"""

import sys
import os
import json
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def cmd_stats(args):
    """显示经验库统计信息"""
    from src.experience_db import ExperienceDB
    db = ExperienceDB()
    s = db.get_stats()

    print("=" * 50)
    print("经验库统计")
    print("=" * 50)
    print(f"  总记录数:   {s['total']}")
    print(f"  权威层:     {s['authority']}  （用户确认/修正）")
    print(f"  候选层:     {s['candidate']}  （自动匹配/导入）")
    print(f"  平均置信度: {s['avg_confidence']}")
    print()

    # 按来源
    by_source = s.get("by_source", {})
    if by_source:
        print("按来源:")
        source_labels = {
            "user_correction": "用户修正",
            "user_confirmed": "用户确认",
            "project_import": "项目导入",
            "auto_match": "自动匹配",
        }
        for src, cnt in by_source.items():
            label = source_labels.get(src, src)
            print(f"  {label}: {cnt}条")
        print()

    # 按省份
    by_province = s.get("by_province", {})
    if by_province:
        print("按省份:")
        for prov, cnt in by_province.items():
            print(f"  {prov}: {cnt}条")


def cmd_search(args):
    """搜索经验库记录"""
    keyword = args.keyword
    if not keyword:
        print("错误：请输入搜索关键词")
        return

    from src.experience_db import ExperienceDB
    db = ExperienceDB()

    province = _resolve_province(args.province) if args.province else None
    records = db.find_experience(keyword, province=province, limit=args.limit or 20)

    if not records:
        print(f"未找到包含「{keyword}」的记录")
        return

    print(f"找到 {len(records)} 条记录（关键词: {keyword}）")
    print()
    _print_records(records)


def cmd_list(args):
    """分页浏览经验库记录"""
    page = max(1, args.page or 1)
    page_size = 20
    offset = (page - 1) * page_size

    province = _resolve_province(args.province) if args.province else None

    # 直接查 SQLite（ExperienceDB 没有分页浏览接口）
    db_path = config.get_experience_db_path()
    if not db_path.exists():
        print("经验库为空（数据库文件不存在）")
        return

    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # 总数
        if province:
            total = conn.execute(
                "SELECT COUNT(*) FROM experiences WHERE province = ?", (province,)
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]

        if total == 0:
            print("经验库为空")
            return

        total_pages = (total + page_size - 1) // page_size
        if page > total_pages:
            print(f"只有 {total_pages} 页，请输入 1-{total_pages}")
            return

        # 查询当前页
        if province:
            rows = conn.execute("""
                SELECT id, bill_name, quota_ids, quota_names, confidence,
                       confirm_count, source, layer, province, updated_at
                FROM experiences
                WHERE province = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """, (province, page_size, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, bill_name, quota_ids, quota_names, confidence,
                       confirm_count, source, layer, province, updated_at
                FROM experiences
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """, (page_size, offset)).fetchall()
    finally:
        conn.close()

    # 显示
    prov_info = f"  省份: {province}" if province else ""
    print(f"经验库记录  第{page}/{total_pages}页  共{total}条{prov_info}")
    print()
    _print_records([dict(r) for r in rows])
    print()
    if page < total_pages:
        next_cmd = f"python tools/experience_view.py list --page {page + 1}"
        if province:
            next_cmd += f' --province "{args.province}"'
        print(f"下一页: {next_cmd}")


def _resolve_province(name):
    """解析省份名称（模糊匹配）"""
    try:
        return config.resolve_province(name, interactive=False)
    except Exception:
        return name


def _print_records(records):
    """格式化打印经验记录列表"""
    for r in records:
        rid = r.get("id", "?")
        name = r.get("bill_name", "") or "(无名称)"
        confidence = r.get("confidence", 0)
        confirm_count = r.get("confirm_count", 0)
        layer = r.get("layer", "?")
        source = r.get("source", "?")

        # 解析定额编号列表
        quota_ids_raw = r.get("quota_ids", "[]")
        if isinstance(quota_ids_raw, str):
            try:
                quota_ids = json.loads(quota_ids_raw)
            except Exception:
                quota_ids = []
        else:
            quota_ids = quota_ids_raw or []

        quota_names_raw = r.get("quota_names", "[]")
        if isinstance(quota_names_raw, str):
            try:
                quota_names = json.loads(quota_names_raw)
            except Exception:
                quota_names = []
        else:
            quota_names = quota_names_raw or []

        # 更新时间
        updated = r.get("updated_at")
        if updated:
            try:
                time_str = datetime.fromtimestamp(float(updated)).strftime("%m-%d %H:%M")
            except Exception:
                time_str = "?"
        else:
            time_str = "?"

        # 置信度标记
        if confidence >= 85:
            conf_mark = "★★★"
        elif confidence >= 60:
            conf_mark = "★★"
        else:
            conf_mark = "★"

        # 层级标记
        layer_mark = "权威" if layer == "authority" else "候选"

        print(f"[{rid}] {name}")
        # 显示定额（编号+名称并排）
        for i, qid in enumerate(quota_ids):
            qname = quota_names[i] if i < len(quota_names) else ""
            print(f"  定额: {qid} {qname}")
        print(f"  置信:{confidence}{conf_mark}  确认:{confirm_count}次  "
              f"层级:{layer_mark}  来源:{source}  更新:{time_str}")
        print(f"{'─' * 50}")


def main():
    parser = argparse.ArgumentParser(
        description="经验库查看工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/experience_view.py stats
  python tools/experience_view.py search "镀锌钢管DN25"
  python tools/experience_view.py list
  python tools/experience_view.py list --page 2 --province "北京2024"
""",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # stats 子命令
    sub.add_parser("stats", help="查看统计信息")

    # search 子命令
    p_search = sub.add_parser("search", help="搜索经验记录")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("--province", help="按省份过滤")
    p_search.add_argument("--limit", type=int, default=20, help="最多返回条数（默认20）")

    # list 子命令
    p_list = sub.add_parser("list", help="分页浏览记录")
    p_list.add_argument("--page", type=int, default=1, help="页码（默认第1页）")
    p_list.add_argument("--province", help="按省份过滤")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "list":
        cmd_list(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
