# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
PDF信息价导入工具

把各省PDF格式的官方信息价导入主材库(material.db)。
支持多省份配置，每个省份有独立的解析逻辑。

用法：
    # 试运行（只看提取结果，不写库）
    python tools/import_price_pdf.py --file "data/宁夏2025年第6期工程造价.pdf" \
      --profile ningxia --province 宁夏 --period "2025-11~2025-12" --dry-run

    # 正式导入
    python tools/import_price_pdf.py --file "data/宁夏2025年第6期工程造价.pdf" \
      --profile ningxia --province 宁夏 --period "2025-11~2025-12"

    # 导入安装材料（不含税价格）
    python tools/import_price_pdf.py --file "data/宁夏2025年安装工程材料价格信息.pdf" \
      --profile ningxia_install --province 宁夏 --period "2025-01~2025-12"

    # 查看支持的省份
    python tools/import_price_pdf.py --list-profiles
"""

import argparse
from pathlib import Path

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # pymupdf

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles import get_profile, list_profiles


def extract_tables_from_pdf(filepath: str, profile, verbose: bool = False) -> list:
    """
    从PDF中提取所有价格记录

    流程：
    1. 逐页扫描PDF
    2. 用profile识别页面类型（建筑/市政/安装/跳过）
    3. 提取表格并解析为标准记录

    返回：标准记录列表
    """
    doc = fitz.open(filepath)
    all_records = []

    # 获取页码范围（如果profile提供了固定范围）
    page_ranges = profile.get_page_ranges()

    # 统计信息
    page_stats = {"building": 0, "municipal": 0, "install": 0, "skip": 0}
    error_pages = []  # 记录解析出错的页码

    try:
        for page_idx in range(len(doc)):
            page_num = page_idx + 1  # 1-indexed（和PDF页码一致）
            page = doc[page_idx]

            # 判断页面类型
            if page_ranges:
                # 用固定页码范围
                page_type = "skip"
                for ptype, (start, end) in page_ranges.items():
                    if start <= page_num <= end:
                        page_type = ptype
                        break
            else:
                # 自动识别
                page_text = page.get_text()
                page_type = profile.classify_page(page_num, page_text)

            page_stats[page_type] = page_stats.get(page_type, 0) + 1

            if page_type == "skip":
                continue

            # 提取表格（按页容错，一页出错不影响其他页）
            try:
                tables = page.find_tables()
                if not tables.tables:
                    if verbose:
                        print(f"  第{page_num}页({page_type}): 无表格")
                    continue

                page_records = []
                for table in tables.tables:
                    # 转为原始数据（list of list）
                    raw_data = table.extract()  # 包含表头
                    if len(raw_data) < 2:
                        continue

                    # 第一行当表头，其余当数据
                    headers = raw_data[0]
                    data_rows = raw_data[1:]

                    # 如果第一行数据看起来也像表头（跨页续表时表头会重复）
                    if data_rows and profile.is_header_row(data_rows[0]):
                        headers = data_rows[0]
                        data_rows = data_rows[1:]

                    # 过滤无效行
                    valid_rows = [r for r in data_rows if profile.is_data_row(r)]

                    # 调用profile解析
                    records = profile.parse_table(valid_rows, headers, page_type, page_num)
                    page_records.extend(records)

                if verbose:
                    print(f"  第{page_num}页({page_type}): {len(page_records)}条记录")

                all_records.extend(page_records)

            except Exception as e:
                error_pages.append(page_num)
                print(f"  警告: 第{page_num}页解析出错，已跳过 - {e}")
    finally:
        doc.close()

    if verbose:
        print(f"\n页面统计: 建筑{page_stats.get('building',0)}页 "
              f"市政{page_stats.get('municipal',0)}页 "
              f"安装{page_stats.get('install',0)}页 "
              f"跳过{page_stats.get('skip',0)}页")
    if error_pages:
        print(f"  解析出错的页: {error_pages}")

    return all_records


def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """
    把提取的记录写入主材库

    参数：
    - records: extract_tables_from_pdf返回的记录列表
    - province: 省份名称（如"宁夏"）
    - period: 信息价期次（如"2025-11~2025-12"）
    - source_file: 源PDF文件名
    - dry_run: True时只打印不写库

    返回：{"imported": 数, "skipped": 数, "errors": 数}
    """
    stats = {"imported": 0, "skipped": 0, "errors": 0, "junk_filtered": 0}

    # 解析期次为开始/结束日期
    period_start, period_end = _parse_period(period)

    if dry_run:
        print(f"\n=== 试运行模式（不写库）===")
        print(f"省份: {province} | 期次: {period} ({period_start} ~ {period_end})")
        print(f"源文件: {source_file}")
        print(f"总记录数: {len(records)}")
        print(f"\n前20条预览：")
        for i, rec in enumerate(records[:20]):
            tax_mark = "含税" if rec.get("tax_included", True) else "不含税"
            city = rec.get("city", "")
            city_str = f" [{city}]" if city else ""
            print(f"  {i+1}. {rec['name']} | {rec.get('spec','')} | "
                  f"{rec.get('unit','')} | ¥{rec['price']:.2f}({tax_mark})"
                  f"{city_str} | {rec.get('category','')}")

        # 统计各类别数量
        categories = {}
        cities = set()
        for rec in records:
            cat = rec.get("category", "") or "未分类"
            categories[cat] = categories.get(cat, 0) + 1
            if rec.get("city"):
                cities.add(rec["city"])

        print(f"\n分类统计:")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}条")

        if cities:
            print(f"\n涉及城市: {', '.join(sorted(cities))}")

        return stats

    # 正式导入
    db = MaterialDB()

    # 创建导入批次
    batch_id = create_import_batch(
        db,
        source_file=source_file,
        source_type="official_info",
        parser_template="pdf_import",
        notes=f"PDF信息价导入 | 省份:{province} | 期次:{period}"
    )

    for rec in records:
        try:
            name = str(rec.get("name", "")).strip()
            if not name:
                stats["skipped"] += 1
                continue

            # 垃圾数据过滤
            if is_junk_material(name):
                stats["junk_filtered"] += 1
                continue

            spec = normalize_spec(str(rec.get("spec", "")))
            unit = normalize_unit(str(rec.get("unit", "")))
            price = float(rec.get("price", 0))
            city = rec.get("city", "")
            tax_included = rec.get("tax_included", True)

            # 写入材料主数据
            material_id = db.add_material(
                name=name,
                spec=spec,
                unit=unit,
                category=rec.get("category", ""),
                subcategory=rec.get("subcategory", ""),
            )

            # 写入价格记录
            if price > 0:
                # 含税/不含税处理
                if tax_included:
                    # 含税价格：直接作为price_incl_tax
                    price_incl_tax = price
                    tax_rate = 0.13
                else:
                    # 不含税价格：需要算出含税价（不含税 × 1.13 = 含税）
                    tax_rate = 0.13
                    price_incl_tax = round(price * (1 + tax_rate), 2)

                db.add_price(
                    material_id=material_id,
                    price_incl_tax=price_incl_tax,
                    source_type="official_info",
                    province=province,
                    city=city,
                    tax_rate=tax_rate,
                    period_start=period_start,
                    period_end=period_end,
                    source_doc=source_file,
                    batch_id=batch_id,
                    authority_level="official",
                    usable_for_quote=1,
                    unit=unit,
                    dedup=True,
                )

            stats["imported"] += 1
        except Exception as e:
            print(f"  导入失败: {rec.get('name', '?')} {rec.get('spec', '')} - {e}")
            stats["errors"] += 1

    # 更新批次计数
    update_batch_count(db, batch_id, stats["imported"])

    return stats


def _parse_period(period: str) -> tuple:
    """
    解析期次字符串为开始/结束日期

    例如：
    "2025-11~2025-12" → ("2025-11-01", "2025-12-31")
    "2025-01~2025-12" → ("2025-01-01", "2025-12-31")
    "2025-06"         → ("2025-06-01", "2025-06-30")
    """
    import calendar

    if "~" in period:
        parts = period.split("~")
        start_str = parts[0].strip()
        end_str = parts[1].strip()
    elif "-" in period and period.count("-") == 1:
        start_str = period.strip()
        end_str = period.strip()
    else:
        return (period, period)

    # 解析开始日期
    try:
        year, month = start_str.split("-")
        period_start = f"{year}-{month.zfill(2)}-01"
    except ValueError:
        period_start = start_str

    # 解析结束日期
    try:
        year, month = end_str.split("-")
        year_int, month_int = int(year), int(month)
        last_day = calendar.monthrange(year_int, month_int)[1]
        period_end = f"{year}-{month.zfill(2)}-{last_day}"
    except ValueError:
        period_end = end_str

    return (period_start, period_end)


def main():
    parser = argparse.ArgumentParser(
        description="PDF信息价导入工具 — 把各省官方PDF信息价导入主材库"
    )

    parser.add_argument("--file", "-f", help="PDF文件路径")
    parser.add_argument("--profile", "-p", help="省份解析配置名称（如 ningxia）")
    parser.add_argument("--province", help="省份名称（如 宁夏），写入数据库的province字段")
    parser.add_argument("--period", help="信息价期次（如 2025-11~2025-12）")
    parser.add_argument("--dry-run", action="store_true", help="试运行：只看提取结果，不写库")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细处理信息")
    parser.add_argument("--list-profiles", action="store_true", help="列出所有可用的省份配置")

    args = parser.parse_args()

    # 列出配置
    if args.list_profiles:
        profiles = list_profiles()
        print("可用的省份配置：")
        for p in profiles:
            print(f"  {p['name']:20s} {p['description']}")
        return

    # 检查必要参数
    if not args.file:
        parser.error("请指定PDF文件路径（--file）")
    if not args.profile:
        parser.error("请指定省份配置（--profile），用 --list-profiles 查看可用配置")
    if not args.province:
        parser.error("请指定省份名称（--province）")
    if not args.period:
        parser.error("请指定信息价期次（--period），如 2025-11~2025-12")

    # 获取配置
    profile = get_profile(args.profile)
    if not profile:
        print(f"错误：未找到配置 '{args.profile}'")
        print("可用配置：")
        for p in list_profiles():
            print(f"  {p['name']:20s} {p['description']}")
        return

    # 检查文件
    filepath = Path(args.file)
    if not filepath.exists():
        print(f"错误：文件不存在 '{filepath}'")
        return

    # 提取记录
    source_file = filepath.name
    print(f"开始处理: {source_file}")
    print(f"配置: {profile.name} ({profile.description})")
    print(f"省份: {args.province} | 期次: {args.period}")
    print()

    records = extract_tables_from_pdf(
        str(filepath), profile, verbose=args.verbose
    )

    print(f"\n共提取 {len(records)} 条记录")

    if not records:
        print("未提取到任何记录，请检查PDF文件和配置是否匹配。")
        return

    # 导入或试运行
    stats = import_to_db(
        records,
        province=args.province,
        period=args.period,
        source_file=source_file,
        dry_run=args.dry_run
    )

    # 打印结果
    if not args.dry_run:
        print(f"\n导入完成:")
        print(f"  成功导入: {stats['imported']}条")
        print(f"  跳过(空名称): {stats['skipped']}条")
        print(f"  过滤(垃圾数据): {stats['junk_filtered']}条")
        print(f"  失败: {stats['errors']}条")

        # 打印主材库最新统计
        db = MaterialDB()
        s = db.stats()
        print(f"\n主材库当前统计:")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
