# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
上海信息价API导入工具

上海住建局提供免登录JSON API，无需PDF解析，直接调API抓取材料价格数据。
数据源：https://ciac.zjw.sh.gov.cn/JGBGCZJInterWeb/
API特点：免登录、无Token、返回含税+除税双价格

用法：
    # 查看所有可用期号（170期，2011年11月~至今）
    python tools/import_shanghai_api.py --list-periods

    # 试运行（只看数据，不写库）
    python tools/import_shanghai_api.py --period-id 180 --dry-run

    # 导入指定期号
    python tools/import_shanghai_api.py --period-id 180

    # 导入最近N期
    python tools/import_shanghai_api.py --recent 12

    # 导入全部170期（约80万条，需要几十分钟）
    python tools/import_shanghai_api.py --all

    # 只导入2025年的数据
    python tools/import_shanghai_api.py --year 2025
"""

import argparse
import re
import time
import json
from pathlib import Path
from datetime import datetime

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles.base_profile import guess_category  # 统一分类函数（唯一定义在base_profile）

# ======== API配置 ========

BASE_URL = "https://ciac.zjw.sh.gov.cn/JGBGCZJInterWeb"
HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
PAGE_SIZE = 500  # 每页500条，API最大支持值
REQUEST_DELAY = 0.3  # 每次请求间隔（秒），别把人家服务器打挂


# ======== API调用函数 ========

def fetch_period_list() -> list:
    """
    获取所有期号列表

    返回：[{"id": 180, "bt": "2026年2月信息价", ...}, ...]
    按时间倒序（最新的在前）
    """
    url = f"{BASE_URL}/GljView/GetJgcjList"
    r = requests.post(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("Result"):
        raise RuntimeError(f"获取期号列表失败: {data.get('Message')}")
    return data["Data"]


def parse_period_title(title: str) -> dict:
    """
    解析期号标题，提取年月

    '2026年2月信息价' → {'year': 2026, 'month': 2}
    """
    m = re.match(r'(\d{4})年(\d{1,2})月', title)
    if m:
        return {"year": int(m.group(1)), "month": int(m.group(2))}
    return {"year": 0, "month": 0}


def fetch_period_materials(period_id: int) -> list:
    """
    获取指定期号的全部材料数据

    分页请求，每页500条，自动翻页直到取完。
    返回：原始API记录列表
    """
    all_items = []
    page = 1

    while True:
        url = f"{BASE_URL}/GljView/GetShjgFwList"
        r = requests.post(url, data={
            "jgcjid": period_id,
            "zyid": "",
            "gljfl": "",
            "PageSize": PAGE_SIZE,
            "PageIndex": page,
        }, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        if not data.get("Result"):
            if page == 1:
                print(f"  ⚠ 期号{period_id}无数据: {data.get('Message')}")
            break

        items = data.get("Data", [])
        if not items:
            break

        all_items.extend(items)

        # 算总页数
        total = data.get("TotalPage", 0)
        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

        if page >= total_pages:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_items


# ======== 数据清洗 ========

def clean_api_record(item: dict, period_info: dict) -> dict:
    """
    把API原始记录转成标准导入格式

    API字段对照：
    - mc: 名称
    - tz: 规格型号
    - dw: 单位
    - xxj: 含税信息价
    - bhsxxj: 除税信息价
    - bm: 编码
    - lb: 类别(glj=工料机)
    """
    name = (item.get("mc") or "").strip()
    spec = (item.get("tz") or "").strip()
    unit = (item.get("dw") or "").strip()
    price_incl = item.get("xxj") or 0
    price_excl = item.get("bhsxxj") or 0
    code = (item.get("bm") or "").strip()

    # 名称为空或是垃圾数据，跳过
    if not name or is_junk_material(name):
        return None

    # 没有任何价格，跳过
    if not price_incl and not price_excl:
        return None

    # 计算实际税率（上海API同时返回含税和除税价，可以反算真实税率）
    if price_incl and price_excl and price_excl > 0:
        tax_rate = round((price_incl / price_excl) - 1, 4)
        # 异常税率修正（正常应该在0~0.20之间）
        if tax_rate < 0 or tax_rate > 0.25:
            tax_rate = 0.13  # 兜底用13%
    else:
        tax_rate = 0.13

    # 单位标准化
    unit = normalize_unit(unit)
    spec = normalize_spec(spec)

    # 猜测分类
    category = guess_category(name, spec)

    # 构建期间日期（上海按月发布）
    year = period_info.get("year", 0)
    month = period_info.get("month", 0)
    if year and month:
        period_start = f"{year}-{month:02d}-01"
        # 月末
        if month == 12:
            period_end = f"{year}-12-31"
        else:
            from calendar import monthrange
            _, last_day = monthrange(year, month)
            period_end = f"{year}-{month:02d}-{last_day}"
        price_date = period_start
    else:
        period_start = ""
        period_end = ""
        price_date = ""

    return {
        "name": name,
        "spec": spec,
        "unit": unit,
        "category": category,
        "price_incl_tax": float(price_incl),
        "price_excl_tax": float(price_excl),
        "tax_rate": tax_rate,
        "code": code,
        "period_start": period_start,
        "period_end": period_end,
        "price_date": price_date,
    }


# ======== 写库 ========

def import_period(db, period_id: int, period_title: str,
                  period_info: dict, dry_run: bool = False) -> dict:
    """
    导入一期上海信息价数据

    返回：{"total": 抓取条数, "imported": 写库条数, "skipped": 跳过, "errors": 错误}
    """
    # 抓数据
    raw_items = fetch_period_materials(period_id)
    if not raw_items:
        return {"total": 0, "imported": 0, "skipped": 0, "errors": 0}

    # 清洗
    records = []
    skipped = 0
    for item in raw_items:
        rec = clean_api_record(item, period_info)
        if rec:
            records.append(rec)
        else:
            skipped += 1

    if dry_run:
        # 只打印不写库
        print(f"  抓取 {len(raw_items)} 条，清洗后 {len(records)} 条（跳过{skipped}条垃圾数据）")
        # 打印前几条示例
        for i, rec in enumerate(records[:5]):
            print(f"    [{i+1}] {rec['name']} | {rec['spec']} | {rec['unit']} | "
                  f"含税{rec['price_incl_tax']} | 除税{rec['price_excl_tax']} | "
                  f"税率{rec['tax_rate']:.2%} | {rec['category']}")
        if len(records) > 5:
            print(f"    ... 共{len(records)}条")
        return {"total": len(raw_items), "imported": 0, "skipped": skipped, "errors": 0}

    # 创建导入批次
    source_doc = f"上海_{period_title}"
    batch_id = create_import_batch(
        db, source_file=source_doc,
        source_type="official_info",
        parser_template="shanghai_api",
        notes=f"上海住建局JSON API导入，{period_title}"
    )

    # 批量写库（单连接+单事务，比逐条add_material/add_price快几十倍）
    imported = 0
    errors = 0
    conn = db._conn()
    try:
        # 预加载已有材料ID缓存（name+spec+unit → id）
        material_cache = {}
        for row in conn.execute("SELECT id, name, spec, unit FROM material_master"):
            key = (row["name"], row["spec"], row["unit"])
            material_cache[key] = row["id"]

        for rec in records:
            try:
                name = rec["name"].strip()
                spec = rec["spec"].strip()
                unit = rec["unit"].strip()
                key = (name, spec, unit)

                # 查缓存获取material_id
                material_id = material_cache.get(key)
                if not material_id:
                    # 新材料，插入
                    search_text = f"{name} {spec} {rec.get('category', '')}".strip()
                    cursor = conn.execute(
                        """INSERT INTO material_master
                           (name, spec, unit, category, search_text)
                           VALUES (?, ?, ?, ?, ?)""",
                        (name, spec, unit, rec.get("category", ""), search_text)
                    )
                    material_id = cursor.lastrowid
                    material_cache[key] = material_id

                # 去重检查：同材料+同价格+同来源文件
                existing = conn.execute(
                    """SELECT 1 FROM price_fact
                       WHERE material_id=? AND price_incl_tax=? AND source_doc=?
                       LIMIT 1""",
                    (material_id, rec["price_incl_tax"], source_doc)
                ).fetchone()
                if existing:
                    continue

                # 计算除税价
                tax_rate = rec["tax_rate"]
                price_excl = round(rec["price_incl_tax"] / (1 + tax_rate), 2) if tax_rate > 0 else rec["price_incl_tax"]

                # 插入价格记录
                conn.execute(
                    """INSERT INTO price_fact
                       (material_id, price_incl_tax, price_excl_tax, tax_rate, unit,
                        source_type, authority_level, province, city,
                        period_start, period_end, price_date, source_doc,
                        batch_id, usable_for_quote)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (material_id, rec["price_incl_tax"], price_excl, tax_rate, unit,
                     "official_info", "official", "上海", "上海",
                     rec["period_start"], rec["period_end"], rec["price_date"],
                     source_doc, batch_id, 1)
                )
                imported += 1
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"    写库失败: {rec['name']} {rec['spec']} - {e}")

        conn.commit()
    finally:
        conn.close()

    update_batch_count(db, batch_id, imported)

    return {
        "total": len(raw_items),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


# ======== 主流程 ========

def main():
    parser = argparse.ArgumentParser(description="上海信息价API导入工具")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-periods", action="store_true",
                       help="列出所有可用期号")
    group.add_argument("--period-id", type=int,
                       help="导入指定期号ID")
    group.add_argument("--recent", type=int,
                       help="导入最近N期")
    group.add_argument("--year", type=int,
                       help="导入指定年份的全部数据")
    group.add_argument("--all", action="store_true",
                       help="导入全部170期")
    parser.add_argument("--dry-run", action="store_true",
                        help="试运行，只看数据不写库")
    args = parser.parse_args()

    # 获取期号列表
    print("正在获取期号列表...")
    periods = fetch_period_list()
    print(f"共 {len(periods)} 期（{periods[-1]['bt']} ~ {periods[0]['bt']}）")

    if args.list_periods:
        print(f"\n{'ID':<6} {'标题':<20} {'状态'}")
        print("-" * 45)
        for p in periods:
            info = parse_period_title(p["bt"])
            status = p.get("ztName", "")
            print(f"{p['id']:<6} {p['bt']:<20} {status}")
        return

    # 确定要导入哪些期号
    targets = []
    if args.period_id:
        # 单期导入
        matched = [p for p in periods if p["id"] == args.period_id]
        if not matched:
            print(f"期号ID {args.period_id} 不存在")
            return
        targets = matched

    elif args.recent:
        # 最近N期
        targets = periods[:args.recent]

    elif args.year:
        # 指定年份
        for p in periods:
            info = parse_period_title(p["bt"])
            if info["year"] == args.year:
                targets.append(p)
        if not targets:
            print(f"没有找到{args.year}年的数据")
            return

    elif args.all:
        targets = periods

    # 按时间正序导入（先导旧的，后导新的）
    targets.sort(key=lambda p: p["id"])

    print(f"\n准备导入 {len(targets)} 期数据")
    if not args.dry_run:
        db = MaterialDB()
        print(f"数据库: {db.db_path}")

    # 逐期导入
    grand_total = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
    for i, p in enumerate(targets):
        period_title = p["bt"]
        period_info = parse_period_title(period_title)
        period_id = p["id"]

        prefix = f"[{i+1}/{len(targets)}]"
        print(f"\n{prefix} {period_title} (id={period_id})")

        try:
            if args.dry_run:
                result = import_period(None, period_id, period_title,
                                       period_info, dry_run=True)
            else:
                result = import_period(db, period_id, period_title,
                                       period_info, dry_run=False)

            grand_total["total"] += result["total"]
            grand_total["imported"] += result["imported"]
            grand_total["skipped"] += result["skipped"]
            grand_total["errors"] += result["errors"]

            if not args.dry_run:
                print(f"  导入 {result['imported']} 条（跳过{result['skipped']}，错误{result['errors']}）")

            # 请求间隔，避免被限流
            if i < len(targets) - 1:
                time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"  导入失败: {e}")
            grand_total["errors"] += 1

    # 汇总
    print(f"\n{'='*50}")
    print(f"导入完成！")
    print(f"  总抓取: {grand_total['total']} 条")
    print(f"  写入库: {grand_total['imported']} 条")
    print(f"  跳过:   {grand_total['skipped']} 条")
    print(f"  错误:   {grand_total['errors']} 条")

    if not args.dry_run and grand_total["imported"] > 0:
        stats = db.stats()
        print(f"\n数据库当前状态:")
        print(f"  总材料: {stats.get('total_materials', '?')} 种")
        print(f"  总价格: {stats.get('total_prices', '?')} 条")


if __name__ == "__main__":
    main()
