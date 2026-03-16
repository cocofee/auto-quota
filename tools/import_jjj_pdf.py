"""
天津+北京信息价PDF导入工具

数据来源：天津住建委京津冀工程造价信息共享平台
格式：RAR包内含4个PDF（天津材料/北京材料/天津人工/北京人工）

天津PDF：6列（序号/材料名称/规格型号/单位/中准价格/区间价格），51页
北京PDF：6列（代号/产品名称/规格型号/单位/含税价/除税价），113页
         北京的特殊之处：多条材料压缩在同一个表格行，用换行符分隔

用法：
  python tools/import_jjj_pdf.py --file "天津.pdf"              # 导入单个PDF
  python tools/import_jjj_pdf.py --dir data/pdf_info_price/tianjin/  # 导入目录下所有PDF
  python tools/import_jjj_pdf.py --file "天津.pdf" --dry-run -v     # 只看不存
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
import glob

import pdfplumber


# ── 天津PDF解析 ──────────────────────────────────────────

def parse_tianjin_pdf(filepath, verbose=False):
    """解析天津材料价格PDF
    格式：6列（序号/材料名称/规格型号/单位/中准价格/区间价格）
    """
    records = []
    period = _guess_period(filepath)

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 5:
                        continue
                    seq, name, spec, unit, price_mid = row[0], row[1], row[2], row[3], row[4]

                    # 跳过表头行
                    if not seq or "序号" in str(seq):
                        continue

                    # 解析价格
                    price = _parse_price(price_mid)
                    if price is None:
                        continue

                    name = str(name or "").strip()
                    spec = str(spec or "").strip()
                    unit = str(unit or "").strip()

                    if not name:
                        continue

                    # 区间价格取中准价（已经是中准价了）
                    # 反算除税价（天津信息价是含税价，税率13%）
                    price_excl = round(price / 1.13, 2)

                    records.append({
                        "name": name,
                        "spec": spec,
                        "unit": unit,
                        "price_incl_tax": round(price, 2),
                        "price_excl_tax": price_excl,
                        "city": "天津",
                        "province": "天津",
                        "period": period,
                    })

    if verbose:
        print(f"  天津: {len(records)}条")
    return records


# ── 北京PDF解析 ──────────────────────────────────────────

def parse_beijing_pdf(filepath, verbose=False):
    """解析北京材料价格PDF
    格式：6列（代号/产品名称/规格型号/单位/含税价/除税价）
    特殊：多条材料压缩在同一行，用换行符分隔
    """
    records = []
    period = _guess_period(filepath)

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 6:
                        continue

                    # 跳过表头
                    header_text = str(row[0] or "") + str(row[1] or "")
                    if "序号" in header_text or "代 号" in header_text or "产品名称" in header_text:
                        continue
                    # 跳过税率说明表
                    if "税 目" in header_text or "税率" in header_text:
                        continue

                    # 北京格式：多行压缩在一个单元格里，用\n分隔
                    codes = str(row[0] or "").split("\n")
                    names = str(row[1] or "").split("\n")
                    specs = str(row[2] or "").split("\n")
                    units = str(row[3] or "").split("\n")
                    prices_incl = str(row[4] or "").split("\n")
                    prices_excl = str(row[5] or "").split("\n")

                    # 取最长列表的长度作为实际行数
                    max_len = max(len(names), len(prices_incl))

                    for i in range(max_len):
                        name = names[i].strip() if i < len(names) else ""
                        spec = specs[i].strip() if i < len(specs) else ""
                        unit = units[i].strip() if i < len(units) else ""
                        p_incl = _parse_price(prices_incl[i]) if i < len(prices_incl) else None
                        p_excl = _parse_price(prices_excl[i]) if i < len(prices_excl) else None

                        if not name or (p_incl is None and p_excl is None):
                            continue

                        # 如果只有含税价，反算除税价
                        if p_incl and not p_excl:
                            p_excl = round(p_incl / 1.13, 2)
                        elif p_excl and not p_incl:
                            p_incl = round(p_excl * 1.13, 2)

                        records.append({
                            "name": name,
                            "spec": spec,
                            "unit": unit,
                            "price_incl_tax": round(p_incl, 2) if p_incl else None,
                            "price_excl_tax": round(p_excl, 2) if p_excl else None,
                            "city": "北京",
                            "province": "北京",
                            "period": period,
                        })

    if verbose:
        print(f"  北京: {len(records)}条")
    return records


# ── 通用工具函数 ──────────────────────────────────────────

def _guess_period(filepath):
    """从文件路径提取期号 YYYY-MM"""
    text = os.path.basename(filepath) + " " + os.path.basename(os.path.dirname(filepath))
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
    # 去掉千分位逗号
    s = s.replace(",", "")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None

def detect_city(filepath):
    """从文件名判断是天津还是北京"""
    basename = os.path.basename(filepath).lower()
    if "天津" in basename or "tianjin" in basename:
        return "天津"
    elif "北京" in basename or "beijing" in basename:
        return "北京"
    return ""


# ── 导入数据库 ──────────────────────────────────────────

def import_to_db(records, source_file, province, dry_run=False, verbose=False):
    """将记录导入主材库"""
    if not records:
        return 0

    if dry_run:
        print(f"  [DRY RUN] 将导入 {len(records)} 条 ({province})")
        if verbose:
            for r in records[:8]:
                print(f"    {r['name']} {r['spec']} | {r['unit']} | "
                      f"含税={r['price_incl_tax']} 除税={r['price_excl_tax']}")
            if len(records) > 8:
                print(f"    ... 还有 {len(records)-8} 条")
        return len(records)

    db_path = os.path.join(os.path.dirname(__file__), "..", "db", "common", "material.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 创建batch
    cur.execute("""
        INSERT INTO import_batch (source_type, source_file, province, record_count, status, created_at)
        VALUES (?, ?, ?, ?, 'importing', datetime('now'))
    """, ("jjj_pdf", source_file, province, len(records)))
    batch_id = cur.lastrowid

    inserted = 0
    for r in records:
        # 查找或创建material_master
        cur.execute("SELECT id FROM material_master WHERE name = ? AND spec = ?",
                    (r['name'], r['spec']))
        row = cur.fetchone()
        if row:
            mat_id = row[0]
        else:
            cur.execute("""
                INSERT INTO material_master (name, spec, unit, category)
                VALUES (?, ?, ?, '')
            """, (r['name'], r['spec'], r['unit']))
            mat_id = cur.lastrowid

        full_name = f"{r['name']} {r['spec']}".strip()
        cur.execute("""
            INSERT INTO price_fact (material_id, batch_id, province, city,
                                    period_start, period_end, price_date,
                                    price_incl_tax, price_excl_tax, unit,
                                    source_type, source_doc)
            VALUES (?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    'info_price', ?)
        """, (mat_id, batch_id, r['province'], r['city'],
              r['period'], r['period'], r['period'],
              r['price_incl_tax'], r['price_excl_tax'], r['unit'],
              full_name))
        inserted += 1

    cur.execute("UPDATE import_batch SET record_count = ?, status = 'done' WHERE id = ?",
                (inserted, batch_id))
    conn.commit()
    conn.close()
    return inserted


# ── 主函数 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="天津+北京（京津冀）信息价PDF导入工具")
    parser.add_argument("--file", type=str, help="指定单个PDF文件")
    parser.add_argument("--dir", type=str, help="扫描目录下所有PDF")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.print_help()
        return

    # 收集PDF文件
    if args.file:
        files = [args.file]
    else:
        files = sorted(glob.glob(os.path.join(args.dir, "**/*.pdf"), recursive=True))
        files = [f for f in files if "材料" in os.path.basename(f) or "material" in os.path.basename(f).lower()]

    if not files:
        print("未找到材料价格PDF文件")
        return

    total = 0
    for fpath in files:
        basename = os.path.basename(fpath)
        city = detect_city(fpath)
        print(f"\n解析: {basename} ({city or '未知城市'})")

        if city == "天津":
            records = parse_tianjin_pdf(fpath, verbose=args.verbose)
            province = "天津"
        elif city == "北京":
            records = parse_beijing_pdf(fpath, verbose=args.verbose)
            province = "北京"
        else:
            # 尝试两种都解析看哪个有数据
            print(f"  无法判断城市，跳过")
            continue

        if not records:
            print(f"  无有效数据")
            continue

        print(f"  {len(records)}条 | 期号: {records[0]['period'] if records else '未知'}")
        n = import_to_db(records, basename, province, dry_run=args.dry_run, verbose=args.verbose)
        total += n

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}导入完成: {total:,}条")


if __name__ == "__main__":
    main()
