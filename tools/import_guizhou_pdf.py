# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
贵州信息价导入工具

贵州省建设工程造价管理协会发布月度信息价PDF（文字版，非扫描件），
格式规范：6列表格（序号|材料名称|规格|单位|除税价|备注），
覆盖9个城市（贵阳/遵义/六盘水/安顺/毕节/铜仁/黔东南/黔南/黔西南）。

用法：
    # 试运行
    python tools/import_guizhou_pdf.py --file "data/pdf_info_price/guizhou/贵州2025年第12期.pdf" \
      --period "2025-12" --dry-run

    # 正式导入
    python tools/import_guizhou_pdf.py --file "data/pdf_info_price/guizhou/贵州2025年第12期.pdf" \
      --period "2025-12"

    # 批量导入目录下所有PDF
    python tools/import_guizhou_pdf.py --dir "data/pdf_info_price/guizhou/" --dry-run

特点：
    - 只有除税价（需反算含税价，税率13%）
    - 全角数字（如"３２６１．２５"），需转半角
    - 9个城市各有独立表格
    - 分类标题行嵌入序号列（如"０１ 黑色及有色金属"）

依赖：
    pip install pdfplumber
"""

import argparse
import re
import unicodedata
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


# ======== 全角→半角转换 ========

def _fullwidth_to_halfwidth(s: str) -> str:
    """
    全角字符转半角

    贵州PDF用全角数字（如"３２６１．２５"），需要转成"3261.25"才能解析价格。
    只转数字、字母和常用标点，不转中文。
    """
    if not s:
        return s
    result = []
    for ch in s:
        code = ord(ch)
        # 全角空格 → 半角空格
        if code == 0x3000:
            result.append(' ')
        # 全角可打印字符范围（！到～）→ 半角
        elif 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    return ''.join(result)


# ======== 城市识别 ========

# 城市名称映射（从页面标题中提取）
_CITY_PATTERNS = [
    ('贵阳', '贵阳'),
    ('遵义', '遵义'),
    ('六盘水', '六盘水'),
    ('安顺', '安顺'),
    ('毕节', '毕节'),
    ('铜仁', '铜仁'),
    ('黔东南', '凯里'),
    ('黔南', '都匀'),
    ('黔西南', '兴义'),
]


def _detect_city(text: str) -> str:
    """
    从页面文本中检测当前城市

    城市标题格式如"2025年 12月份安顺市区主要建筑安装材料市场综合参考价"，
    可能出现在页面任意位置（上一城市结束+下一城市开始的过渡页）。
    如果同一页出现多个城市关键词，取最后一个（即最新开始的城市）。
    """
    found_city = ""
    found_pos = -1
    for keyword, city in _CITY_PATTERNS:
        # 搜索整个页面文本（不只是前300字符）
        pos = text.rfind(keyword)
        if pos >= 0 and pos > found_pos:
            found_city = city
            found_pos = pos
    return found_city


# ======== 表格解析 ========

def _is_category_row(row: list) -> bool:
    """判断是否为分类标题行（如"０１ 黑色及有色金属"）"""
    if not row:
        return False
    first = _fullwidth_to_halfwidth(str(row[0] or '')).strip()
    # 分类标题特征：数字+空格+中文，且后面的列全空
    if re.match(r'^\d{1,2}\s+[\u4e00-\u9fff]', first):
        # 后面的列应该全空
        non_empty = sum(1 for c in row[1:] if c and str(c).strip())
        return non_empty == 0
    return False


def _parse_table_page(page, current_city: str) -> tuple:
    """
    解析一页PDF的表格数据

    返回：(records列表, 检测到的城市)
    """
    text = page.extract_text() or ''

    # 检测城市
    city = _detect_city(text)
    if not city:
        city = current_city

    # 跳过非数据页（公告/说明/目录等）
    if not text.strip():
        return [], city
    # 跳过目录页
    if '目  录' in text[:100] or '目录' in text[:20]:
        return [], city
    # 跳过纯文字页（没有表格特征）
    if '除税价格' not in text and '序号' not in text[:200]:
        return [], city

    # 提取表格
    tables = page.find_tables()
    if not tables:
        return [], city

    records = []

    for table in tables:
        data = table.extract()
        if not data or len(data) < 2:
            continue

        # 检查表头格式
        header = data[0]
        if not header:
            continue

        header_text = ' '.join(_fullwidth_to_halfwidth(str(h or '')) for h in header)

        # 必须包含"材料名称"或"苗木名称"和"除税价格"
        if '名称' not in header_text or '价格' not in header_text:
            continue

        # 确定列位置
        name_col = None
        spec_col = None
        unit_col = None
        price_col = None
        note_col = None

        for ci, h in enumerate(header):
            ht = _fullwidth_to_halfwidth(str(h or '')).strip()
            if '名称' in ht and name_col is None:
                name_col = ci
            elif '规格' in ht and spec_col is None:
                spec_col = ci
            elif '单位' in ht and unit_col is None:
                unit_col = ci
            elif '价格' in ht and price_col is None:
                price_col = ci
            elif '备' in ht and note_col is None:
                note_col = ci

        if name_col is None or price_col is None:
            continue

        # 解析数据行
        for row in data[1:]:
            if len(row) <= max(name_col, price_col):
                continue

            # 跳过分类标题行
            if _is_category_row(row):
                continue

            # 提取字段（全角转半角）
            name = _fullwidth_to_halfwidth(str(row[name_col] or '')).strip()

            # 规格可能跨2列（表格有时把规格拆成2列）
            spec = ""
            if spec_col is not None:
                spec = _fullwidth_to_halfwidth(str(row[spec_col] or '')).strip()
                # 如果规格列后面紧跟的列不是单位列，可能是规格的第二部分
                if spec_col + 1 < len(row) and spec_col + 1 != unit_col:
                    spec2 = _fullwidth_to_halfwidth(str(row[spec_col + 1] or '')).strip()
                    if spec2 and spec2 not in ('t', 'm', '吨', '个', '套'):
                        spec = (spec + ' ' + spec2).strip()

            unit = ""
            if unit_col is not None:
                unit = _fullwidth_to_halfwidth(str(row[unit_col] or '')).strip()

            price_str = _fullwidth_to_halfwidth(str(row[price_col] or '')).strip()
            note = ""
            if note_col is not None and note_col < len(row):
                note = _fullwidth_to_halfwidth(str(row[note_col] or '')).strip()

            # 跳过空名称
            if not name:
                continue

            # 跳过序号不是数字的行（可能是分类标题的残留）
            seq = _fullwidth_to_halfwidth(str(row[0] or '')).strip()
            if not re.match(r'^\d+$', seq):
                continue

            # 解析价格
            price = clean_price(price_str)
            if price <= 0:
                continue

            # 贵州只有除税价，反算含税价（建材13%税率）
            tax_rate = 0.13
            price_incl = round(price * (1 + tax_rate), 2)

            records.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "price": price_incl,
                "category": guess_category(name),
                "tax_included": True,
                "city": city,
                "price_excl_tax": price,
                "tax_rate": tax_rate,
            })

    return records, city


# ======== 整个PDF提取 ========

def extract_guizhou_pdf(filepath: str, verbose: bool = False) -> list:
    """
    从贵州信息价PDF中提取所有材料价格记录

    返回：标准记录列表
    """
    pdf = pdfplumber.open(filepath)
    all_records = []
    current_city = ""
    total_pages = len(pdf.pages)

    if verbose:
        print(f"  PDF共{total_pages}页")

    for i, page in enumerate(pdf.pages):
        records, current_city = _parse_table_page(page, current_city)
        if records:
            all_records.extend(records)
            if verbose:
                print(f"  第{i+1}页({current_city}): {len(records)}条")

    pdf.close()
    return all_records


# ======== 导入数据库 ========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """复用现有的导入函数"""
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


# ======== 从文件名猜期次 ========

def _guess_period_from_filename(filename: str) -> str:
    """
    从文件名猜测期次

    例如：贵州2025年第12期.pdf → 2025-12
          guizhou_202512.pdf → 2025-12
    """
    # 中文格式：贵州2025年第12期
    m = re.search(r'(\d{4})年第(\d{1,2})期', filename)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # 数字格式：guizhou_202512
    m = re.search(r'(\d{4})(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


# ======== 主入口 ========

def main():
    parser = argparse.ArgumentParser(
        description="贵州信息价导入工具 — 从PDF提取材料价格导入主材库"
    )
    parser.add_argument("--file", "-f", help="单个PDF文件路径")
    parser.add_argument("--dir", "-d", help="批量导入：目录下所有PDF")
    parser.add_argument("--period", help="信息价期次（如 2025-12），批量模式下自动从文件名猜")
    parser.add_argument("--dry-run", action="store_true", help="试运行：只看结果，不写库")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")

    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("请指定 --file 或 --dir")

    # 收集要处理的文件
    files = []
    if args.file:
        fp = Path(args.file)
        if not fp.exists():
            print(f"错误：文件不存在 '{fp}'")
            return
        period = args.period or _guess_period_from_filename(fp.name)
        if not period:
            parser.error("无法从文件名猜出期次，请用 --period 指定（如 2025-12）")
        files.append((fp, period))

    if args.dir:
        dir_path = Path(args.dir)
        if not dir_path.is_dir():
            print(f"错误：目录不存在 '{dir_path}'")
            return
        for fp in sorted(dir_path.glob("*.pdf")):
            period = _guess_period_from_filename(fp.name)
            if period:
                files.append((fp, period))
            else:
                print(f"  跳过（无法猜出期次）: {fp.name}")

    if not files:
        print("没有找到可处理的PDF文件")
        return

    print(f"共{len(files)}个文件待处理")
    print(f"省份: 贵州 | 覆盖9市州")
    print()

    total_imported = 0

    for fp, period in files:
        print(f"{'='*60}")
        print(f"文件: {fp.name} | 期次: {period}")
        print(f"{'='*60}")

        # 提取
        records = extract_guizhou_pdf(str(fp), verbose=args.verbose)
        print(f"\n共提取 {len(records)} 条记录")

        if not records:
            print("未提取到记录，跳过")
            continue

        # 按城市统计
        from collections import Counter
        city_counts = Counter(r['city'] for r in records)
        for city, cnt in city_counts.most_common():
            print(f"  {city or '未知'}: {cnt}条")

        # 导入
        stats = import_to_db(
            records,
            province="贵州",
            period=period,
            source_file=fp.name,
            dry_run=args.dry_run,
        )

        if not args.dry_run:
            print(f"\n导入完成: 成功{stats['imported']} "
                  f"跳过{stats['skipped']} "
                  f"过滤{stats['junk_filtered']} "
                  f"失败{stats['errors']}")
            total_imported += stats["imported"]

        print()

    # 最终汇总
    if not args.dry_run and len(files) > 1:
        print(f"\n{'='*60}")
        print(f"全部完成！共导入 {total_imported} 条")

        db = MaterialDB()
        s = db.stats()
        print(f"\n主材库当前统计:")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
