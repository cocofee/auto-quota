"""
重庆信息价PDF导入工具

数据来源：重庆市建设工程造价信息网(cqsgczjxx.org)
格式：造价信息期刊PDF，前40页政策文件，41页起为材料价格表
- 7列：序号/材料名称/规格型号/单位/含税价/除税价/备注
- 特殊问题：侧边栏水印文字("站总价造程工设建乡城和房住市")混入表格数据，需清洗

用法：
  python tools/import_chongqing_pdf.py --file xxx.pdf
  python tools/import_chongqing_pdf.py --file xxx.pdf --dry-run -v
"""
import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import argparse
import os
import re
import sqlite3

import pdfplumber

# 侧边栏水印文字（会被混入名称/规格列），需要清除
# 这些是PDF侧边栏竖排文字"重庆市住房和城乡建设工程造价总站"的单字
_SIDEBAR_WORDS = {
    "站", "总", "价", "造", "程", "工", "设", "建",
    "乡", "城", "和", "房", "住", "市", "重", "庆",
}

def _clean_sidebar(text):
    """清除侧边栏水印文字干扰
    水印以换行分隔的单字出现在单元格中，如 '房\\n住\\n市\\n热轧光圆钢筋'
    需要删除这些单字行，保留有意义的内容行
    """
    if not text:
        return ""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 纯侧边栏单字行（1-2个字且全是水印字符）→ 跳过
        if len(line) <= 2 and all(c in _SIDEBAR_WORDS for c in line):
            continue
        # 带括号的规格行如"（ ）"→保留
        cleaned.append(line)
    return ' '.join(cleaned).strip()

def _guess_period(filepath):
    """从文件路径提取期号"""
    text = os.path.basename(filepath)
    m = re.search(r'(\d{4})\s*年?\s*第?\s*(\d{1,2})\s*期', text)
    if m:
        year = m.group(1)
        issue = int(m.group(2))
        # 重庆造价信息大约每月一期，期号≈月份
        month = min(issue, 12)
        return f"{year}-{month:02d}"
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""

def _parse_price(val):
    """解析价格"""
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    s = str(val or "").strip()
    if not s:
        return None
    s = s.replace(",", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None

def parse_chongqing_pdf(filepath, verbose=False):
    """解析重庆造价信息期刊PDF"""
    records = []
    period = _guess_period(filepath)

    # 尝试从PDF内容获取期号
    with pdfplumber.open(filepath) as pdf:
        # 从封面提取期号
        if not period and len(pdf.pages) > 2:
            cover_text = pdf.pages[2].extract_text() or ""
            m = re.search(r'(\d{4})\s*年\s*第\s*(\d{1,2})\s*期', cover_text)
            if m:
                year = m.group(1)
                issue = int(m.group(2))
                month = min(issue, 12)
                period = f"{year}-{month:02d}"

        # 当前材料分类名
        current_category = ""
        # 当前材料名（跨行继承）
        current_name = ""

        # 从第40页开始扫描材料价格表
        start_page = 0
        for i in range(len(pdf.pages)):
            text = (pdf.pages[i].extract_text() or "")[:200]
            if "材料信息价" in text or "材料名称" in text:
                start_page = i
                break

        if start_page == 0:
            start_page = 40  # 默认从第41页

        for page_idx in range(start_page, len(pdf.pages)):
            tables = pdf.pages[page_idx].extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 6:
                        continue

                    seq = str(row[0] or "").strip()
                    name_raw = str(row[1] or "").strip()
                    spec_raw = str(row[2] or "").strip()
                    unit = str(row[3] or "").strip()
                    price_incl_str = str(row[4] or "").strip()
                    price_excl_str = str(row[5] or "").strip()

                    # 跳过表头
                    if "序号" in seq or "材料名称" in name_raw:
                        continue

                    # 清除侧边栏水印
                    name_clean = _clean_sidebar(name_raw)
                    spec_clean = _clean_sidebar(spec_raw)
                    unit = _clean_sidebar(unit)

                    # 检测分类行（如"一、黑色及有色金属"、"四、墙砖..."）
                    cat_match = re.match(r'^[一二三四五六七八九十]+、(.+)', name_clean)
                    if cat_match and not _parse_price(price_incl_str):
                        current_category = cat_match.group(1).strip()
                        if verbose:
                            print(f"  分类: {current_category}")
                        continue

                    # 解析价格
                    price_incl = _parse_price(price_incl_str)
                    price_excl = _parse_price(price_excl_str)

                    if price_incl is None and price_excl is None:
                        continue

                    # 处理名称（如果当前行名称为空，继承上一行的名称）
                    if name_clean:
                        current_name = name_clean
                    name = current_name

                    if not name:
                        continue

                    # 处理规格中的换行（多行规格合并）
                    spec = spec_clean.replace('\n', ' ').strip()

                    # 清理单位中的换行
                    unit = unit.replace('\n', '').strip()

                    records.append({
                        "name": name,
                        "spec": spec,
                        "unit": unit,
                        "price_incl_tax": round(price_incl, 2) if price_incl else None,
                        "price_excl_tax": round(price_excl, 2) if price_excl else None,
                        "city": "重庆",
                        "province": "重庆",
                        "period": period,
                        "category": current_category,
                    })

    return records


def import_to_db(records, source_file, dry_run=False, verbose=False):
    """导入到主材库"""
    if not records:
        return 0

    if dry_run:
        print(f"  [DRY RUN] 将导入 {len(records)} 条")
        if verbose:
            for r in records[:10]:
                print(f"    {r['name']} {r['spec']} | {r['unit']} | "
                      f"含税={r['price_incl_tax']} 除税={r['price_excl_tax']}")
            if len(records) > 10:
                print(f"    ... 还有 {len(records)-10} 条")
        return len(records)

    db_path = os.path.join(os.path.dirname(__file__), "..", "db", "common", "material.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO import_batch (source_type, source_file, province, record_count, status, created_at)
        VALUES (?, ?, '重庆', ?, 'importing', datetime('now'))
    """, ("chongqing_pdf", source_file, len(records)))
    batch_id = cur.lastrowid

    inserted = 0
    for r in records:
        cur.execute("SELECT id FROM material_master WHERE name = ? AND spec = ?",
                    (r['name'], r['spec']))
        row = cur.fetchone()
        if row:
            mat_id = row[0]
        else:
            cur.execute("INSERT INTO material_master (name, spec, unit, category) VALUES (?, ?, ?, ?)",
                        (r['name'], r['spec'], r['unit'], r.get('category', '')))
            mat_id = cur.lastrowid

        full_name = f"{r['name']} {r['spec']}".strip()
        cur.execute("""
            INSERT INTO price_fact (material_id, batch_id, province, city,
                                    period_start, period_end, price_date,
                                    price_incl_tax, price_excl_tax, unit,
                                    source_type, source_doc)
            VALUES (?, ?, '重庆', '重庆', ?, ?, ?, ?, ?, ?, 'info_price', ?)
        """, (mat_id, batch_id, r['period'], r['period'], r['period'],
              r['price_incl_tax'], r['price_excl_tax'], r['unit'], full_name))
        inserted += 1

    cur.execute("UPDATE import_batch SET record_count = ?, status = 'done' WHERE id = ?",
                (inserted, batch_id))
    conn.commit()
    conn.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="重庆信息价PDF导入工具")
    parser.add_argument("--file", type=str, required=True, help="PDF文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    print(f"解析: {os.path.basename(args.file)}")
    records = parse_chongqing_pdf(args.file, verbose=args.verbose)
    if not records:
        print("  无有效数据")
        return

    # 统计
    cats = set(r['category'] for r in records if r['category'])
    print(f"  {len(records)}条 | 期号: {records[0]['period']} | 分类: {len(cats)}个")

    n = import_to_db(records, os.path.basename(args.file),
                     dry_run=args.dry_run, verbose=args.verbose)
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}导入完成: {n:,}条")


if __name__ == "__main__":
    main()
