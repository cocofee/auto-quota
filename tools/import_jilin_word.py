# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
吉林信息价导入工具

吉林省住建厅按季度发布造价信息，各地市各自提交Word文件（doc/docx/wps/rtf），
格式不完全统一但核心结构相似：表格包含序号、名称、规格、单位、除税价、含税价、税率。

用法：
    # 试运行（只看结果，不写库）
    python tools/import_jilin_word.py --dir "data/pdf_info_price/jilin/2025_Q4/2025第四季度" \
      --period "2025-Q4" --dry-run

    # 正式导入
    python tools/import_jilin_word.py --dir "data/pdf_info_price/jilin/2025_Q4/2025第四季度" \
      --period "2025-Q4"

    # 单个文件
    python tools/import_jilin_word.py --file "吉林市xxx.docx" --period "2025-Q4" --city "吉林"

特点：
    - 季度发布（不是月刊）
    - 各地市格式有差异（列数/表头/合并单元格）
    - 含税+除税双价格，税率13%为主
    - 人工/机械/苗木部分自动跳过，只取材料价格
    - 支持docx格式，doc/wps/rtf需先转换为docx

依赖：
    pip install python-docx
"""

import argparse
import os
import re
import subprocess
from pathlib import Path

from docx import Document

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
)
from tools.pdf_profiles.base_profile import guess_category, clean_price


# ======== 城市名提取 ========

# 吉林省地级市列表（从文件名中提取）
_CITY_KEYWORDS = [
    '长春', '吉林', '四平', '辽源', '通化', '白山', '松原', '白城',
    '延吉', '延边', '梅河口', '公主岭', '长白山', '珲春',
    '柳河', '辉南', '集安', '磐石', '桦甸', '舒兰', '蛟河',
    '龙井', '图们', '敦化', '汪清', '安图', '和龙',
]


def _guess_city(filename: str, table_title: str = "") -> str:
    """
    从文件名或表格标题猜测城市

    优先匹配长名称（如"长白山"优先于"白山"），
    忽略"吉林省"前缀（避免把所有文件都识别成吉林市）。
    """
    for text in [filename, table_title]:
        if not text:
            continue
        # 去掉"吉林省"前缀避免误匹配
        cleaned = text.replace('吉林省', '')
        # 按城市名长度降序匹配（长名称优先）
        for city in sorted(_CITY_KEYWORDS, key=len, reverse=True):
            if city in cleaned:
                return city
    return ""


# ======== 跳过的分类（人工、机械、苗木不导入） ========

_SKIP_CATEGORIES = [
    '人工', '工种', '机械', '租赁', '苗木', '花卉', '草坪', '草皮',
]


def _is_skip_category(text: str) -> bool:
    """判断是否为应跳过的分类（人工/机械/苗木）"""
    return any(kw in text for kw in _SKIP_CATEGORIES)


# ======== 表格解析 ========

def _find_header_row(table) -> tuple:
    """
    在表格中找到表头行，返回 (行索引, 列映射字典)

    列映射: {'name': 列号, 'spec': 列号, 'unit': 列号,
              'price_excl': 列号, 'price_incl': 列号, 'tax_rate': 列号}
    """
    for ri, row in enumerate(table.rows):
        cells = [c.text.strip() for c in row.cells]
        # 去重（合并单元格导致同一内容重复）
        # 表头特征：包含"序号"和"名称"
        has_seq = any('序号' in c for c in cells)
        has_name = any('名称' in c or '工种' in c for c in cells)
        if not (has_seq and has_name):
            continue

        # 找到表头行，定位各列
        col_map = {}
        seen_cols = set()  # 跟踪已使用的列号（处理合并单元格）

        for ci, cell_text in enumerate(cells):
            if ci in seen_cols:
                continue
            seen_cols.add(ci)
            t = cell_text.replace('\n', '').strip()

            if '序号' in t and 'seq' not in col_map:
                col_map['seq'] = ci
            elif ('名称' in t or '工种' in t) and 'name' not in col_map:
                col_map['name'] = ci
            elif '规格' in t and 'spec' not in col_map:
                col_map['spec'] = ci
            elif '单位' in t and 'unit' not in col_map:
                col_map['unit'] = ci
            elif '除税' in t and 'price_excl' not in col_map:
                col_map['price_excl'] = ci
            elif '含税' in t and 'price_incl' not in col_map:
                col_map['price_incl'] = ci
            elif '税率' in t and 'tax_rate' not in col_map:
                col_map['tax_rate'] = ci

        # 至少要有名称和一个价格列
        if 'name' in col_map and ('price_excl' in col_map or 'price_incl' in col_map):
            return ri, col_map

    return -1, {}


def _parse_price(text: str) -> float:
    """
    解析价格文本，处理各种特殊格式

    '3406.84' → 3406.84
    '160-200' → 180.0（取中间值）
    '3016.2' → 3016.2
    '' → 0
    """
    s = text.strip()
    if not s:
        return 0

    # 范围格式（如"160-200"），取中间值
    m = re.match(r'^(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)$', s)
    if m:
        low = float(m.group(1))
        high = float(m.group(2))
        return round((low + high) / 2, 2)

    return clean_price(s)


def _parse_tax_rate(text: str) -> float:
    """解析税率文本"""
    s = text.strip().replace('%', '')
    if not s:
        return 0.13  # 默认13%
    try:
        rate = float(s)
        if rate > 1:
            rate = rate / 100  # "13" → 0.13
        return rate
    except ValueError:
        return 0.13


def _is_category_row(cells: list) -> str:
    """
    判断是否为分类标题行，返回分类名或空字符串

    分类标题特征：合并单元格（所有cell内容相同）且包含中文
    或者序号列是"01"/"一、"之类的格式
    """
    # 所有单元格内容相同（合并单元格）
    texts = [c.strip() for c in cells if c.strip()]
    if texts and all(t == texts[0] for t in texts):
        return texts[0]

    # 序号列是分类编号（如"01"、"一、"）
    first = cells[0].strip() if cells else ""
    if re.match(r'^[一二三四五六七八九十]+[、.]', first):
        return first
    if re.match(r'^0\d$', first) and len(cells) > 1:
        # "01 黑色及有色金属" 格式
        second = cells[1].strip() if len(cells) > 1 else ""
        if re.search(r'[\u4e00-\u9fff]', second) and not re.match(r'^\d', second):
            return second

    return ""


def extract_from_table(table, city: str = "", verbose: bool = False) -> list:
    """
    从一个Word表格中提取材料价格记录

    返回：标准记录列表
    """
    header_ri, col_map = _find_header_row(table)
    if header_ri < 0:
        if verbose:
            print(f"    未找到有效表头，跳过")
        return []

    if verbose:
        print(f"    表头在行{header_ri}，列映射: {col_map}")

    records = []
    in_skip_section = False  # 是否在应跳过的分类中

    for ri in range(header_ri + 1, len(table.rows)):
        row = table.rows[ri]
        cells = [c.text.strip() for c in row.cells]

        # 检查分类标题行
        cat = _is_category_row(cells)
        if cat:
            in_skip_section = _is_skip_category(cat)
            if verbose and in_skip_section:
                print(f"    跳过分类: {cat[:30]}")
            continue

        # 在跳过分类中，不提取数据
        if in_skip_section:
            continue

        # 提取各字段
        seq = cells[col_map.get('seq', 0)] if 'seq' in col_map else ""
        name = cells[col_map['name']] if 'name' in col_map else ""
        spec = cells[col_map.get('spec', -1)] if 'spec' in col_map and col_map['spec'] < len(cells) else ""
        unit = cells[col_map.get('unit', -1)] if 'unit' in col_map and col_map['unit'] < len(cells) else ""

        # 价格
        price_excl = 0
        price_incl = 0
        tax_rate = 0.13

        if 'price_excl' in col_map and col_map['price_excl'] < len(cells):
            price_excl = _parse_price(cells[col_map['price_excl']])
        if 'price_incl' in col_map and col_map['price_incl'] < len(cells):
            price_incl = _parse_price(cells[col_map['price_incl']])
        if 'tax_rate' in col_map and col_map['tax_rate'] < len(cells):
            tax_rate = _parse_tax_rate(cells[col_map['tax_rate']])

        # 跳过空行
        if not name:
            continue
        # 跳过没有任何价格的行
        if price_excl <= 0 and price_incl <= 0:
            continue
        # 跳过序号不是数字的行（可能是残留的分类标题）
        if seq and not re.match(r'^\d+$', seq):
            continue

        # 补全价格（有一个就能算另一个）
        if price_incl <= 0 and price_excl > 0:
            price_incl = round(price_excl * (1 + tax_rate), 2)
        elif price_excl <= 0 and price_incl > 0:
            price_excl = round(price_incl / (1 + tax_rate), 2)

        # 跳过人工相关（可能分类检测漏了）
        if unit in ('工日', '工时', '台班'):
            continue

        # 跳过价格异常
        if price_incl > 100000:
            continue

        records.append({
            "name": name,
            "spec": normalize_spec(spec),
            "unit": normalize_unit(unit),
            "price": price_incl,
            "price_excl_tax": price_excl,
            "tax_rate": tax_rate,
            "category": guess_category(name),
            "tax_included": True,
            "city": city,
        })

    return records


# ======== 整个文件提取 ========

def extract_jilin_docx(filepath: str, city: str = "", verbose: bool = False) -> list:
    """
    从吉林Word文件(docx)中提取所有材料价格记录

    自动从文件名猜城市，遍历所有表格提取数据。
    """
    fname = os.path.basename(filepath)
    if not city:
        city = _guess_city(fname)

    if verbose:
        print(f"  文件: {fname} | 城市: {city or '未知'}")

    doc = Document(filepath)
    all_records = []

    for ti, table in enumerate(doc.tables):
        # 从表格标题行猜城市（通化文件里有多个县的表格）
        if len(table.rows) > 0:
            first_cells = [c.text.strip() for c in table.rows[0].cells]
            title = first_cells[0] if first_cells else ""
            table_city = _guess_city("", title) or city

        if verbose:
            print(f"  表{ti}: {len(table.rows)}行")

        records = extract_from_table(table, city=table_city, verbose=verbose)
        if records:
            all_records.extend(records)
            if verbose:
                print(f"    提取{len(records)}条")

    return all_records


# ======== doc/wps/rtf → docx 转换 ========

def _convert_to_docx(filepath: str, verbose: bool = False) -> str:
    """
    用LibreOffice将doc/wps/rtf转换为docx

    返回转换后的docx文件路径，失败返回空字符串
    """
    fp = Path(filepath)
    out_dir = fp.parent

    # 尝试找LibreOffice
    lo_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice",  # PATH中
    ]

    lo_bin = None
    for p in lo_paths:
        if os.path.isfile(p) or p == "soffice":
            lo_bin = p
            break

    if not lo_bin:
        print("  错误：未找到LibreOffice，无法转换doc/wps文件")
        print("  请安装LibreOffice: https://www.libreoffice.org/download/")
        return ""

    if verbose:
        print(f"  转换: {fp.name} → docx")

    try:
        result = subprocess.run(
            [lo_bin, "--headless", "--convert-to", "docx",
             "--outdir", str(out_dir), str(fp)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"  转换失败: {result.stderr[:200]}")
            return ""
    except FileNotFoundError:
        print("  错误：LibreOffice命令不可用")
        return ""
    except subprocess.TimeoutExpired:
        print("  转换超时(60s)")
        return ""

    # 转换后的文件名
    docx_path = out_dir / (fp.stem + ".docx")
    if docx_path.exists():
        return str(docx_path)

    print(f"  转换后文件未找到: {docx_path}")
    return ""


# ======== 导入数据库 ========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """复用现有的导入函数"""
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


# ======== 期次格式化 ========

def _normalize_period(period: str) -> str:
    """
    标准化期次格式

    '2025-Q4' → '2025-12'（季度取末月）
    '2025-Q3' → '2025-09'
    '2025-12' → '2025-12'（已标准化）
    """
    m = re.match(r'(\d{4})-Q(\d)', period, re.I)
    if m:
        year = m.group(1)
        q = int(m.group(2))
        month = q * 3  # Q1→3, Q2→6, Q3→9, Q4→12
        return f"{year}-{month:02d}"
    return period


# ======== 主入口 ========

def main():
    parser = argparse.ArgumentParser(
        description="吉林信息价导入工具 — 从Word文件提取材料价格导入主材库"
    )
    parser.add_argument("--file", "-f", help="单个Word文件路径")
    parser.add_argument("--dir", "-d", help="批量导入：目录下所有Word文件")
    parser.add_argument("--period", required=True,
                        help="信息价期次（如 2025-Q4 或 2025-12）")
    parser.add_argument("--city", help="指定城市（单文件模式下使用）")
    parser.add_argument("--dry-run", action="store_true", help="试运行：只看结果，不写库")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--no-convert", action="store_true",
                        help="不转换doc/wps/rtf（只处理docx）")

    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("请指定 --file 或 --dir")

    period = _normalize_period(args.period)

    # 收集要处理的文件
    files = []
    if args.file:
        fp = Path(args.file)
        if not fp.exists():
            print(f"错误：文件不存在 '{fp}'")
            return
        files.append(str(fp))

    if args.dir:
        dir_path = Path(args.dir)
        if not dir_path.is_dir():
            print(f"错误：目录不存在 '{dir_path}'")
            return
        for ext in ('*.docx', '*.doc', '*.wps', '*.rtf'):
            files.extend(str(f) for f in sorted(dir_path.glob(ext)))

    if not files:
        print("没有找到可处理的Word文件")
        return

    # 按格式分类
    docx_files = [f for f in files if f.endswith('.docx')]
    other_files = [f for f in files if not f.endswith('.docx')]

    print(f"共{len(files)}个文件待处理 (docx:{len(docx_files)}, 其他:{len(other_files)})")
    print(f"省份: 吉林 | 期次: {period}")
    print()

    # 转换非docx文件
    if other_files and not args.no_convert:
        print(f"需要转换{len(other_files)}个非docx文件...")
        for fp in other_files:
            converted = _convert_to_docx(fp, verbose=args.verbose)
            if converted and converted not in docx_files:
                docx_files.append(converted)
        print()

    if not docx_files:
        print("没有可处理的docx文件")
        return

    total_imported = 0
    all_records = []

    for fp in sorted(docx_files):
        fname = os.path.basename(fp)
        city = args.city or ""
        print(f"{'='*50}")
        print(f"文件: {fname}")

        try:
            records = extract_jilin_docx(fp, city=city, verbose=args.verbose)
        except Exception as e:
            print(f"  解析失败: {e}")
            continue

        print(f"提取 {len(records)} 条记录")

        if not records:
            print("未提取到记录，跳过")
            print()
            continue

        # 统计城市
        from collections import Counter
        city_counts = Counter(r['city'] for r in records)
        for c, cnt in city_counts.most_common():
            print(f"  {c or '未知'}: {cnt}条")

        all_records.extend(records)
        print()

    if not all_records:
        print("总计：无有效记录")
        return

    print(f"{'='*50}")
    print(f"总计提取 {len(all_records)} 条记录")

    # 按城市统计
    from collections import Counter
    city_counts = Counter(r['city'] for r in all_records)
    for c, cnt in city_counts.most_common():
        print(f"  {c or '未知'}: {cnt}条")

    # 导入
    stats = import_to_db(
        all_records,
        province="吉林",
        period=period,
        source_file=f"jilin_{period}.docx",
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        print(f"\n导入完成: 成功{stats['imported']} "
              f"跳过{stats['skipped']} "
              f"过滤{stats['junk_filtered']} "
              f"失败{stats['errors']}")
        total_imported = stats["imported"]

        db = MaterialDB()
        s = db.stats()
        print(f"\n主材库当前统计:")
        for k, v in s.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
