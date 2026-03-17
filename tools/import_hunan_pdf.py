# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
湖南信息价导入工具

湖南省住建厅发布两类PDF：
1. 材料价格行情资讯（双月刊）：15页，91种材料，14市州+全省综合价
2. 材料价格行情表（半月刊）：1页，6种基础材料，14市州

数据特点：
- 价格为不含税预算价（含原价+运杂费+采保费+运输损耗费）
- 需反算含税价（×1.13）
- 14市州：长沙/株洲/湘潭/岳阳/永州/益阳/怀化/张家界/常德/湘西/衡阳/娄底/郴州/邵阳
- 材料有标准编码（如RVYPTNP3810087DKG0）

用法：
    # 试运行（行情资讯PDF）
    python tools/import_hunan_pdf.py --file "data/pdf_info_price/hunan/xxx.pdf" \
      --period "2025-09" --dry-run

    # 正式导入
    python tools/import_hunan_pdf.py --file "data/pdf_info_price/hunan/xxx.pdf" \
      --period "2025-09"

    # 批量导入目录下所有PDF
    python tools/import_hunan_pdf.py --dir "data/pdf_info_price/hunan/" --dry-run

    # 只导入全省综合价（跳过各市州明细）
    python tools/import_hunan_pdf.py --file "xxx.pdf" --period "2025-09" --province-only

依赖：
    pip install pdfplumber
"""

import argparse
import re
from pathlib import Path

import pdfplumber

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles.base_profile import guess_category, clean_price


# ======== 14市州 ========

HUNAN_CITIES = [
    '长沙', '株洲', '湘潭', '岳阳', '永州', '益阳', '怀化',
    '张家界', '常德', '湘西', '衡阳', '娄底', '郴州', '邵阳'
]


# ======== 分类行判断 ========

def _is_category_row(row: list) -> bool:
    """判断是否为分类标题行（如序号为空，编码为'01'，名称为'黑色及有色金属'）"""
    if not row or len(row) < 3:
        return False
    seq = str(row[0] or '').strip()
    code = str(row[1] or '').strip()
    name = str(row[2] or '').strip()
    # 分类行特征：序号为空，编码是2位数字，名称是中文
    if seq == '' and re.match(r'^\d{2}$', code) and name:
        return True
    return False


# ======== 解析全省综合价表（第3-5页，8列格式）========

def _parse_province_table(pdf, verbose: bool = False) -> list:
    """
    解析全省综合价表（行情资讯PDF的第3-5页）

    表格格式：序号 | 编码 | 名称 | 规格 | 单位 | 基期价 | 上期价 | 本期价
    返回标准记录列表
    """
    records = []

    for i in range(2, min(5, len(pdf.pages))):  # 第3-5页（索引2-4）
        page = pdf.pages[i]
        tables = page.extract_tables()
        if not tables:
            continue

        for table in tables:
            for row in table:
                if not row or len(row) < 8:
                    continue

                # 跳过表头行
                if str(row[0] or '').strip() == '序号':
                    continue

                # 跳过分类标题行
                if _is_category_row(row):
                    continue

                seq = str(row[0] or '').strip()
                # 序号必须是数字
                if not re.match(r'^\d+$', seq):
                    continue

                code = str(row[1] or '').strip()
                name = str(row[2] or '').strip()
                spec = str(row[3] or '').strip()
                unit = str(row[4] or '').strip()
                # 取本期价（最后一列，即第7列）
                price_str = str(row[7] or '').strip()

                if not name or not price_str:
                    continue

                price = clean_price(price_str)
                if price <= 0:
                    continue

                # 跳过垃圾数据
                if is_junk_material(name):
                    continue

                # 不含税价 → 含税价（建材13%）
                tax_rate = 0.13
                price_incl = round(price * (1 + tax_rate), 2)

                records.append({
                    "name": name,
                    "spec": normalize_spec(spec),
                    "unit": normalize_unit(unit),
                    "price": price_incl,
                    "price_excl_tax": price,
                    "tax_included": True,
                    "tax_rate": tax_rate,
                    "city": "湖南",  # 全省综合价
                    "category": guess_category(name),
                    "material_code": code,
                })

    if verbose:
        print(f"  全省综合价: {len(records)}条")
    return records


# ======== 解析各市州价格表（第1-2页，40列大表）========

def _parse_city_tables(pdf, period: str, verbose: bool = False) -> list:
    """
    解析各市州价格表（行情资讯PDF的第1-2页）

    表格格式：40列大表
    列头第1行：序号 | 编码 | 名称 | 规格 | 单位 | 长沙 | (合并) | 株洲 | (合并) | ...
    列头第2行：空 | 空 | 空 | 空 | 空 | 9月 | 10月 | 9月 | 10月 | ...

    每个城市占2列（2个月份），邵阳比较特殊占了多列（按半月分期）
    """
    records = []

    # 先确定列映射：哪些列对应哪个城市
    # 从第1页表格的表头行推断
    if len(pdf.pages) < 1:
        return records

    page0 = pdf.pages[0]
    tables = page0.extract_tables()
    if not tables:
        return records

    table = tables[0]
    if len(table) < 3:
        return records

    # 动态查找城市名行和月份行（不同年份格式不同）
    # 城市名行特征：某个单元格的值正好是"长沙"（不是嵌入在说明文字里）
    # 月份行特征：包含"X月"格式的单元格
    city_row = None
    month_row = None
    for ri, row in enumerate(table[:8]):
        cells = [str(c or '').strip() for c in row]
        # 城市名行：有一个单元格值恰好是"长沙"
        if city_row is None and '长沙' in cells:
            city_row = row
        # 月份行：紧跟城市名行之后，包含"X月"格式
        elif city_row is not None and month_row is None:
            row_text = ' '.join(cells)
            if re.search(r'\d+月', row_text):
                month_row = row

    if city_row is None or month_row is None:
        if verbose:
            print("  未找到城市名/月份表头行，跳过城市解析")
        return records

    # 构建城市-列映射
    # 格式：城市名在列5开始，每个城市占2列(None表示合并单元格)
    city_cols = []  # [(城市名, 列索引, 月份标签), ...]

    current_city = None
    for col_idx in range(5, len(city_row)):
        cell = str(city_row[col_idx] or '').strip()
        month_label = str(month_row[col_idx] or '').strip() if col_idx < len(month_row) else ''

        if cell:
            # 新城市名
            current_city = cell
        if current_city and month_label:
            city_cols.append((current_city, col_idx, month_label))

    if verbose:
        print(f"  检测到{len(city_cols)}个城市-月份列")

    # 从period推断有效月份（如"2025-09"→取9月和10月）
    # 但实际取所有有数据的列

    # 解析数据行（跨第1-2页）
    all_rows = []
    for page_idx in range(min(2, len(pdf.pages))):
        page = pdf.pages[page_idx]
        page_tables = page.extract_tables()
        if not page_tables:
            continue
        for t_row in page_tables[0]:
            all_rows.append(t_row)

    for row in all_rows:
        if not row or len(row) < 8:
            continue

        seq = str(row[0] or '').strip()
        # 跳过表头/说明行
        if not re.match(r'^\d+$', seq):
            continue

        code = str(row[1] or '').strip()
        name = str(row[2] or '').strip()
        spec = str(row[3] or '').strip()
        unit = str(row[4] or '').strip()

        if not name:
            continue
        if is_junk_material(name):
            continue

        # 遍历每个城市-月份列
        for city_name, col_idx, month_label in city_cols:
            if col_idx >= len(row):
                continue

            price_str = str(row[col_idx] or '').strip()
            if not price_str:
                continue

            price = clean_price(price_str)
            if price <= 0:
                continue

            # 规范化城市名（去掉可能的后缀）
            city = city_name
            for c in HUNAN_CITIES:
                if c in city_name:
                    city = c
                    break

            tax_rate = 0.13
            price_incl = round(price * (1 + tax_rate), 2)

            records.append({
                "name": name,
                "spec": normalize_spec(spec),
                "unit": normalize_unit(unit),
                "price": price_incl,
                "price_excl_tax": price,
                "tax_included": True,
                "tax_rate": tax_rate,
                "city": city,
                "category": guess_category(name),
                "material_code": code,
                "month_label": month_label,  # 保留月份标签方便核对
            })

    if verbose:
        # 按城市统计
        city_counts = {}
        for r in records:
            city_counts[r['city']] = city_counts.get(r['city'], 0) + 1
        for city, count in sorted(city_counts.items()):
            print(f"  {city}: {count}条")

    return records


# ======== 解析行情表PDF（半月刊，1页6种材料）========

def _parse_price_table(pdf, verbose: bool = False) -> list:
    """
    解析半月刊行情表PDF（1页，14市州×6种材料）

    表格格式：序号 | 市州 | 螺纹钢筋(价格/涨跌) | 水泥(价格/涨跌) | ...
    """
    records = []

    if not pdf.pages:
        return records

    tables = pdf.pages[0].extract_tables()
    if not tables:
        return records

    table = tables[0]
    if len(table) < 3:
        return records

    # 表头第1行：材料名称（含规格信息）
    header1 = table[0]
    # 表头第2行：价格/涨跌幅列标签
    header2 = table[1]

    # 解析材料信息（从第3列开始，每2列一种材料）
    materials = []
    col = 2
    while col < len(header1):
        cell = str(header1[col] or '').strip()
        if cell:
            # 从表头提取材料名和规格（用\n分隔）
            parts = cell.replace('\n', ' ').strip()
            # 尝试分离名称和规格
            name = parts
            spec = ''
            # 表头第2行的价格列告诉我们单位
            unit_cell = str(header2[col] or '').strip() if col < len(header2) else ''
            unit_match = re.search(r'元/(\w+)', unit_cell)
            unit = unit_match.group(1) if unit_match else ''

            materials.append({
                'name': name,
                'spec': spec,
                'unit': unit,
                'price_col': col,
            })
        col += 2  # 每种材料占2列（价格+涨跌）

    if verbose:
        print(f"  行情表检测到{len(materials)}种材料")

    # 解析数据行（第3行起）
    for row in table[2:]:
        if not row or len(row) < 3:
            continue

        city = str(row[1] or '').strip()
        if not city:
            continue

        for mat in materials:
            pcol = mat['price_col']
            if pcol >= len(row):
                continue
            price_str = str(row[pcol] or '').strip()
            if not price_str:
                continue

            # 清理价格（可能包含备注文字如"(HRB400E 25)"）
            price_clean = re.sub(r'\([^)]*\)', '', price_str).strip()
            # 可能有换行+备注
            price_clean = price_clean.split('\n')[0].strip()

            price = clean_price(price_clean)
            if price <= 0:
                continue

            # 行情表价格已含税（不含税信息不确定，暂存原值）
            records.append({
                "name": mat['name'],
                "spec": mat['spec'],
                "unit": normalize_unit(mat['unit']),
                "price": price,  # 含税预算价
                "price_excl_tax": round(price / 1.13, 2),
                "tax_included": True,
                "tax_rate": 0.13,
                "city": city,
                "category": guess_category(mat['name']),
            })

    if verbose:
        print(f"  行情表共{len(records)}条")
    return records


# ======== 自动判断PDF类型 ========

def _detect_pdf_type(pdf) -> str:
    """
    自动判断PDF类型

    返回：'zixun'=行情资讯（双月刊15页）, 'table'=行情表（半月刊1页）
    """
    if len(pdf.pages) >= 5:
        return 'zixun'
    # 1页的一定是行情表
    if len(pdf.pages) == 1:
        return 'table'
    # 看内容关键词
    text = (pdf.pages[0].extract_text() or '')[:500]
    if '行情资讯' in text or '全省综合价' in text:
        return 'zixun'
    return 'table'


# ======== 主提取函数 ========

def extract_hunan_pdf(filepath: str, province_only: bool = False,
                      verbose: bool = False) -> list:
    """
    从湖南信息价PDF中提取所有材料价格记录

    参数：
        filepath: PDF文件路径
        province_only: 只提取全省综合价（跳过各市州明细）
        verbose: 打印详细信息
    返回：标准记录列表
    """
    try:
        pdf = pdfplumber.open(filepath)
    except Exception as e:
        print(f"  PDF损坏，跳过: {e}")
        return []

    if verbose:
        print(f"  PDF共{len(pdf.pages)}页")

    pdf_type = _detect_pdf_type(pdf)

    if pdf_type == 'zixun':
        if verbose:
            print(f"  类型: 行情资讯（双月刊）")
        # 全省综合价（页3-5）
        records = _parse_province_table(pdf, verbose)
        # 各市州明细（页1-2）
        if not province_only:
            period = _guess_period_from_filename(filepath)
            city_records = _parse_city_tables(pdf, period, verbose)
            records.extend(city_records)
    else:
        if verbose:
            print(f"  类型: 行情表（半月刊）")
        records = _parse_price_table(pdf, verbose)

    pdf.close()
    return records


# ======== 从文件名猜期次 ========

def _guess_period_from_filename(filename: str) -> str:
    """
    从文件名猜测期次

    例如：2025年第五期_9-10月_xxx.pdf → 2025-09
          2025年全省第二十四期_xxx.pdf → 2025-12
    """
    name = str(Path(filename).stem)

    # 中文月份 → 数字
    month_map = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
        '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
    }

    # 尝试匹配"X-Y月"
    m = re.search(r'(\d{1,2})-(\d{1,2})月', name)
    if m:
        year_m = re.search(r'(\d{4})年', name)
        year = year_m.group(1) if year_m else '2025'
        return f"{year}-{int(m.group(1)):02d}"

    # 尝试匹配"第X期"（双月刊，期号×2-1=起始月份）
    m = re.search(r'第([一二三四五六七八九十]+)期', name)
    if m:
        cn_num = m.group(1)
        period_num = month_map.get(cn_num, 0)
        if period_num:
            month = (period_num - 1) * 2 + 1  # 第1期=1-2月，第2期=3-4月
            year_m = re.search(r'(\d{4})年', name)
            year = year_m.group(1) if year_m else '2025'
            return f"{year}-{month:02d}"

    # 尝试从半月刊期号推算
    m = re.search(r'第([一二三四五六七八九十百]+)期', name)
    if m:
        year_m = re.search(r'(\d{4})年', name)
        year = year_m.group(1) if year_m else '2025'
        return f"{year}-01"

    return "unknown"


# ======== 导入数据库 ========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """导入记录到主材库"""
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


# ======== 主函数 ========

def main():
    parser = argparse.ArgumentParser(description='湖南信息价PDF导入工具')
    parser.add_argument('--file', help='单个PDF文件路径')
    parser.add_argument('--dir', help='批量导入目录')
    parser.add_argument('--period', help='期次（如2025-09），不指定则从文件名推断')
    parser.add_argument('--dry-run', action='store_true', help='试运行，不写库')
    parser.add_argument('--province-only', action='store_true',
                        help='只导入全省综合价（跳过各市州明细）')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("请指定 --file 或 --dir")

    files = []
    if args.file:
        files.append(Path(args.file))
    elif args.dir:
        d = Path(args.dir)
        files = sorted(d.glob('*.pdf'))
        if not files:
            print(f"目录 {d} 下没有PDF文件")
            return

    total_records = 0
    total_files = 0

    for filepath in files:
        print(f"\n{'='*60}")
        print(f"处理: {filepath.name}")

        records = extract_hunan_pdf(
            str(filepath),
            province_only=args.province_only,
            verbose=args.verbose or True,  # 默认打印概要
        )

        if not records:
            print(f"  未提取到数据，跳过")
            continue

        # 确定期次
        period = args.period or _guess_period_from_filename(str(filepath))
        print(f"  期次: {period}")
        print(f"  总记录: {len(records)}条")

        # 按城市统计
        city_counts = {}
        for r in records:
            city_counts[r['city']] = city_counts.get(r['city'], 0) + 1
        for city in sorted(city_counts.keys()):
            print(f"    {city}: {city_counts[city]}条")

        if args.dry_run:
            print(f"  [试运行] 不写库")
            # 打印前5条示例
            for r in records[:5]:
                print(f"    {r['city']} | {r['name']} | {r['spec']} | "
                      f"{r['unit']} | 含税{r['price']} | 除税{r['price_excl_tax']}")
        else:
            result = import_to_db(
                records, '湖南', period, str(filepath), dry_run=False
            )
            print(f"  导入完成: 导入{result.get('imported', 0)}, "
                  f"跳过{result.get('skipped', 0)}, "
                  f"过滤{result.get('junk_filtered', 0)}")

        total_records += len(records)
        total_files += 1

    print(f"\n{'='*60}")
    print(f"汇总: {total_files}个文件, {total_records}条记录")
    if args.dry_run:
        print("（试运行模式，未写库）")


if __name__ == '__main__':
    main()
