# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
青海信息价导入工具

青海省住建厅发布《青海建设工程市场价格信息》双月刊PDF（100-260MB），
按厂家分页列出材料报价，品类极其丰富（涵盖安装材料）。

表格格式多样，主要有5种：
A: 序号 | 材料名称 | 规格 | 单位 | 市场价（元）
B: 序号 | 材料名称 | 规格 | 单位 | 除税价 | 含税价
C: 序号 | 材料名称 | 规格 | 单位 | 备注 | 除税价 | 含税价 | 税率
D: 3列并排（产品名称|规格|含税价 ×3），用于管材等
E: 矩阵格式（电缆型号×截面，多列价格）

数据特点：
- 大部分含税价13%
- 按厂家分页，每家有联系人/电话/地址（不导入）
- 材料名称可能跨行（首行有名称，后续行只有规格变化）

用法：
    python tools/import_qinghai_pdf.py --file "data/pdf_info_price/qinghai/xxx.pdf" \
      --period "2025-11" --dry-run

    python tools/import_qinghai_pdf.py --dir "data/pdf_info_price/qinghai/" --dry-run

依赖：
    pip install pdfplumber
"""

import argparse
import re
from pathlib import Path

import pdfplumber

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles.base_profile import guess_category, clean_price


# ======== 表格格式检测 ========

def _detect_table_format(header: list) -> dict:
    """
    从表头行检测表格格式，返回列映射

    返回dict: {name_col, spec_col, unit_col, price_col, price_excl_col, ...}
    如果无法识别返回None
    """
    if not header:
        return None

    h_text = [str(c or '').strip().replace('\n', '') for c in header]
    h_joined = ' '.join(h_text)

    result = {}

    # 格式E：电缆矩阵（序号|型号规格|多列含税价）
    price_cols_count = sum(1 for h in h_text if '含税价' in h or '市场价' in h)
    if price_cols_count >= 3 and ('型号' in h_joined or '规格' in h_joined):
        # 找spec列（型号规格列）
        for i, h in enumerate(h_text):
            if '型号' in h or '规格' in h:
                result['spec_col'] = i
                break
        result['type'] = 'matrix'
        result['price_cols'] = []
        for i, h in enumerate(h_text):
            if '含税价' in h or '市场价' in h:
                result['price_cols'].append(i)
        return result

    # 格式D：3列并排（产品名称|规格|含税价 重复3次）
    name_count = sum(1 for h in h_text if '名称' in h or '产品' in h)
    if name_count >= 2:
        result['type'] = 'multi_col'
        result['groups'] = []
        i = 0
        while i < len(h_text):
            if '名称' in h_text[i] or '产品' in h_text[i]:
                group = {'name_col': i}
                # 找后面的规格和价格列
                for j in range(i + 1, min(i + 4, len(h_text))):
                    if '规格' in h_text[j] or '型号' in h_text[j]:
                        group['spec_col'] = j
                    elif '价' in h_text[j]:
                        group['price_col'] = j
                if 'price_col' in group:
                    result['groups'].append(group)
            i += 1
        if result['groups']:
            return result
        return None

    # 标准格式（A/B/C）：逐列检测
    for i, h in enumerate(h_text):
        if ('名称' in h or '产品' in h) and 'name_col' not in result:
            result['name_col'] = i
        elif ('规格' in h or '型号' in h) and 'spec_col' not in result:
            result['spec_col'] = i
        elif '单位' in h and 'unit_col' not in result:
            result['unit_col'] = i
        elif '除税' in h and 'price_excl_col' not in result:
            result['price_excl_col'] = i
        elif ('含税' in h or '市场价' in h or h == '单价/元') and 'price_col' not in result:
            result['price_col'] = i
        elif '备注' in h and 'note_col' not in result:
            result['note_col'] = i
        elif '税率' in h and 'tax_rate_col' not in result:
            result['tax_rate_col'] = i
        elif '用途' in h and 'usage_col' not in result:
            result['usage_col'] = i

    # 至少要有名称和价格
    if 'name_col' not in result:
        return None
    if 'price_col' not in result and 'price_excl_col' not in result:
        return None

    result['type'] = 'standard'
    return result


# ======== 解析标准格式表格 ========

def _parse_standard_table(table_data: list, fmt: dict, category_hint: str) -> list:
    """解析标准格式表格（A/B/C类型）"""
    records = []
    current_name = ""

    for row in table_data:
        if not row:
            continue

        cells = [str(c or '').strip() for c in row]

        # 提取名称
        name_col = fmt['name_col']
        name = cells[name_col] if name_col < len(cells) else ''

        # 名称跨行处理（空名称继承上一行）
        if name:
            current_name = name
        elif current_name:
            name = current_name
        else:
            continue

        # 提取规格
        spec = ''
        if 'spec_col' in fmt and fmt['spec_col'] < len(cells):
            spec = cells[fmt['spec_col']]

        # 提取单位
        unit = ''
        if 'unit_col' in fmt and fmt['unit_col'] < len(cells):
            unit = cells[fmt['unit_col']]

        # 提取价格（优先含税价）
        price_incl = 0
        price_excl = 0

        if 'price_col' in fmt and fmt['price_col'] < len(cells):
            price_incl = clean_price(cells[fmt['price_col']])
        if 'price_excl_col' in fmt and fmt['price_excl_col'] < len(cells):
            price_excl = clean_price(cells[fmt['price_excl_col']])

        # 没含税有除税→算含税
        if price_incl <= 0 and price_excl > 0:
            price_incl = round(price_excl * 1.13, 2)
        # 有含税没除税→算除税
        if price_excl <= 0 and price_incl > 0:
            price_excl = round(price_incl / 1.13, 2)

        if price_incl <= 0:
            continue

        # 跳过表头行（名称就是"材料名称"之类）
        if re.match(r'^(序号|材料|产品|名称)', name):
            continue

        # 跳过垃圾
        if is_junk_material(name):
            continue

        records.append({
            "name": name,
            "spec": normalize_spec(spec),
            "unit": normalize_unit(unit),
            "price": price_incl,
            "price_excl_tax": price_excl,
            "tax_included": True,
            "tax_rate": 0.13,
            "city": "西宁",  # 青海主要是西宁市场价
            "category": guess_category(name) or category_hint,
        })

    return records


# ======== 解析多列并排格式 ========

def _parse_multi_col_table(table_data: list, fmt: dict, category_hint: str) -> list:
    """解析3列并排格式（D类型）"""
    records = []

    for group in fmt['groups']:
        name_col = group['name_col']
        spec_col = group.get('spec_col', name_col + 1)
        price_col = group.get('price_col', spec_col + 1)
        current_name = ""

        for row in table_data:
            if not row:
                continue
            cells = [str(c or '').strip() for c in row]

            name = cells[name_col] if name_col < len(cells) else ''
            spec = cells[spec_col] if spec_col < len(cells) else ''
            price_str = cells[price_col] if price_col < len(cells) else ''

            # 跨行继承名称
            if name and not re.match(r'^(产品|名称|序号)', name):
                # 清理名称中的换行
                current_name = name.replace('\n', ' ').strip()
            elif not name and current_name:
                name = current_name
            else:
                continue

            price = clean_price(price_str)
            if price <= 0:
                continue

            # 从表头推断单位
            unit = ''
            # 尝试从列头提取单位 (如 "含税价(元/m)")
            header_text = str(fmt.get('_header', [None] * (price_col + 1))[price_col] or '')
            unit_match = re.search(r'元/(\w+)', header_text)
            if unit_match:
                unit = unit_match.group(1)

            if is_junk_material(name):
                continue

            records.append({
                "name": name,
                "spec": normalize_spec(spec),
                "unit": normalize_unit(unit),
                "price": price,
                "price_excl_tax": round(price / 1.13, 2),
                "tax_included": True,
                "tax_rate": 0.13,
                "city": "西宁",
                "category": guess_category(name) or category_hint,
            })

    return records


# ======== 解析电缆矩阵格式 ========

def _parse_matrix_table(table_data: list, fmt: dict, category_hint: str) -> list:
    """解析电缆矩阵格式（E类型）"""
    records = []

    # 第2行通常是电缆型号名（YJV, ZC-YJV等）
    if len(table_data) < 3:
        return records

    type_row = table_data[0]  # 可能是表头行
    # 找第2行的类型标识
    model_row = None
    for row in table_data[:3]:
        cells = [str(c or '').strip() for c in row]
        # 找有YJV等电缆型号的行
        if any(re.match(r'^[A-Z]', c) for c in cells if c):
            model_row = cells
            break

    if not model_row:
        return records

    spec_col = fmt.get('spec_col', 1)
    price_cols = fmt.get('price_cols', [])

    for row in table_data[1:]:
        cells = [str(c or '').strip() for c in row]

        # 跳过表头/型号行
        spec = cells[spec_col] if spec_col < len(cells) else ''
        if not spec or not re.search(r'\d', spec):
            continue

        for pcol in price_cols:
            if pcol >= len(cells):
                continue

            price = clean_price(cells[pcol])
            if price <= 0:
                continue

            # 电缆型号从model_row获取
            cable_model = model_row[pcol] if pcol < len(model_row) else ''
            if not cable_model or cable_model in ('含税价', '市场价'):
                continue

            name = f"电力电缆 {cable_model}"

            records.append({
                "name": name,
                "spec": normalize_spec(spec),
                "unit": "m",
                "price": price,
                "price_excl_tax": round(price / 1.13, 2),
                "tax_included": True,
                "tax_rate": 0.13,
                "city": "西宁",
                "category": "电缆",
            })

    return records


# ======== 检测当前大类 ========

# 大类关键词（按PDF目录顺序）
_CATEGORY_KEYWORDS = [
    ('绿色建材', '建材'), ('装配式', '建材'), ('水泥', '水泥砂石'),
    ('砖', '水泥砂石'), ('砂石', '水泥砂石'), ('混凝土', '水泥砂石'),
    ('木材', '木材'), ('竹材', '木材'),
    ('玻璃', '玻璃'), ('陶瓷', '陶瓷'), ('面砖', '陶瓷'),
    ('石材', '石材'), ('地板', '石材'),
    ('装饰', '装饰'), ('涂料', '涂料'), ('油漆', '涂料'),
    ('保温', '保温'), ('隔热', '保温'),
    ('塑材', '管材'), ('管材', '管材'), ('管件', '管材'), ('管道', '管材'),
    ('阀门', '阀门'), ('水暖', '水暖'), ('暖通', '暖通'),
    ('消防', '消防'),
    ('灯具', '灯具'), ('电源', '灯具'),
    ('电线', '电缆'), ('电缆', '电缆'), ('光纤', '电缆'),
    ('开关', '电气'), ('插座', '电气'), ('配电', '电气'),
    ('钢材', '钢材'), ('钢筋', '钢材'),
]


def _detect_category(text: str) -> str:
    """从页面文本中检测材料大类"""
    for keyword, category in _CATEGORY_KEYWORDS:
        if keyword in text[:200]:
            return category
    return ''


# ======== 主提取函数 ========

def extract_qinghai_pdf(filepath: str, verbose: bool = False) -> list:
    """从青海信息价PDF中提取所有材料价格记录"""
    pdf = pdfplumber.open(filepath)
    total_pages = len(pdf.pages)

    if verbose:
        print(f"  PDF共{total_pages}页")

    all_records = []
    current_category = ''
    skip_pages = set()  # 封面/目录等非数据页

    for i in range(total_pages):
        page = pdf.pages[i]
        text = (page.extract_text() or '')

        # 跳过封面/目录（前8页通常是封面和目录）
        if i < 8 and ('目录' in text[:50] or len(text) < 50):
            continue

        # 检测大类
        cat = _detect_category(text)
        if cat:
            current_category = cat

        # 提取表格
        tables = page.extract_tables()
        if not tables:
            continue

        page_records = []
        for table in tables:
            if not table or len(table) < 2:
                continue

            # 检测表格格式
            header = table[0]
            fmt = _detect_table_format(header)
            if not fmt:
                # 尝试第2行作为表头（第1行可能是标题）
                if len(table) >= 3:
                    fmt = _detect_table_format(table[1])
                    if fmt:
                        table = table[1:]  # 跳过标题行
                if not fmt:
                    continue

            # 保存原始表头供multi_col使用
            fmt['_header'] = table[0]

            # 按格式解析
            if fmt['type'] == 'standard':
                recs = _parse_standard_table(table[1:], fmt, current_category)
            elif fmt['type'] == 'multi_col':
                recs = _parse_multi_col_table(table[1:], fmt, current_category)
            elif fmt['type'] == 'matrix':
                recs = _parse_matrix_table(table, fmt, current_category)
            else:
                continue

            page_records.extend(recs)

        if page_records:
            all_records.extend(page_records)
            if verbose and len(all_records) % 500 < len(page_records):
                print(f"  已处理{i+1}/{total_pages}页, 累计{len(all_records)}条")

    pdf.close()

    if verbose:
        print(f"  总计提取: {len(all_records)}条")
        # 按category统计
        cat_counts = {}
        for r in all_records:
            c = r.get('category', '') or '未分类'
            cat_counts[c] = cat_counts.get(c, 0) + 1
        for c, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
            print(f"    {c}: {n}条")

    return all_records


# ======== 从文件名猜期次 ========

def _guess_period_from_filename(filename: str) -> str:
    """从文件名猜测期次"""
    name = str(Path(filename).stem)
    # 匹配 "2025年第11-12期" 或 "2025年第1-2期"
    m = re.search(r'(\d{4})年第(\d{1,2})-(\d{1,2})期', name)
    if m:
        year = m.group(1)
        month_start = m.group(2)
        return f"{year}-{int(month_start):02d}"
    # 匹配 "2024年第1期"
    m = re.search(r'(\d{4})年第(\d{1,2})期', name)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return "unknown"


# ======== 导入数据库 ========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """导入记录到主材库"""
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


# ======== 主函数 ========

def main():
    parser = argparse.ArgumentParser(description='青海信息价PDF导入工具')
    parser.add_argument('--file', help='单个PDF文件路径')
    parser.add_argument('--dir', help='批量导入目录')
    parser.add_argument('--period', help='期次（如2025-11）')
    parser.add_argument('--dry-run', action='store_true', help='试运行，不写库')
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
        print(f"处理: {filepath.name} ({filepath.stat().st_size / 1024 / 1024:.0f}MB)")

        records = extract_qinghai_pdf(
            str(filepath),
            verbose=True,
        )

        if not records:
            print(f"  未提取到数据，跳过")
            continue

        period = args.period or _guess_period_from_filename(str(filepath))
        print(f"  期次: {period}")
        print(f"  总记录: {len(records)}条")

        # 打印前5条示例
        for r in records[:5]:
            print(f"    {r['name']} | {r['spec']} | {r['unit']} | "
                  f"含税{r['price']} | {r['category']}")

        if args.dry_run:
            print(f"  [试运行] 不写库")
        else:
            result = import_to_db(
                records, '青海', period, str(filepath), dry_run=False
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
