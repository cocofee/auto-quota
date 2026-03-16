"""
新疆信息价Excel导入工具

数据来源：新疆工程造价信息网 (www.xjzj.com)
格式：.xls 文件，每个地州每月一个文件
- 附件1：通用材料（钢筋/型钢/管材/电缆/阀门等），5列
- 附件2：地产材料（水泥/砂石/商砼），6列，按子城市分区

用法：
  # 第一步：批量下载xls文件
  python tools/import_xinjiang_xls.py --download --months 3   # 下载最近3个月
  python tools/import_xinjiang_xls.py --download --all         # 下载全部

  # 第二步：导入到主材库
  python tools/import_xinjiang_xls.py --import --dir data/pdf_info_price/xinjiang/
  python tools/import_xinjiang_xls.py --import --file data/pdf_info_price/xinjiang/aksu_202601.xls

  # 一步到位：下载+导入
  python tools/import_xinjiang_xls.py --download --import --months 3

  # 调试：只看不存
  python tools/import_xinjiang_xls.py --import --file xxx.xls --dry-run --verbose
"""
import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ── 地州映射 ──────────────────────────────────────────
# areaid → (短名, 完整地州名)
AREA_MAP = {
    1:  ("yili",      "伊犁"),
    2:  ("wulumuqi",  "乌鲁木齐"),
    3:  ("changji",   "昌吉"),
    4:  ("kelamayi",  "克拉玛依"),
    5:  ("shihezi",   "石河子"),
    7:  ("tacheng",   "塔城"),
    8:  ("aletai",    "阿勒泰"),
    9:  ("hami",      "哈密"),
    10: ("bazhou",    "巴州"),
    11: ("aksu",      "阿克苏"),
    12: ("kashi",     "喀什"),
    13: ("wujiaqu",   "五家渠"),
    14: ("bozhou",    "博州"),
    15: ("kezhou",    "克州"),
    16: ("hetian",    "和田"),
    17: ("tulufan",   "吐鲁番"),
}

# 用于从文件名/标题提取城市的关键词（长的排前面，避免"克州"匹配到"克拉玛依"）
_CITY_KEYWORDS = sorted([
    "乌鲁木齐", "伊犁", "昌吉", "克拉玛依", "石河子", "塔城",
    "阿勒泰", "哈密", "巴州", "阿克苏", "阿拉尔", "喀什",
    "五家渠", "博州", "博乐", "克州", "和田", "吐鲁番",
    "奎屯", "独山子", "沙湾", "乌苏", "图木舒克",
    "库尔勒", "铁门关", "可克达拉", "双河", "胡杨河", "新星",
    "北屯", "昆玉", "阿图什", "库车",
], key=len, reverse=True)

# API配置
API_BASE = "https://www.xjzj.com"
API_LIST = f"{API_BASE}/Home/GetPoliciesListBy"
API_DETAIL = f"{API_BASE}/Home/PoliciesDetail"
DOWNLOAD_DIR = "data/pdf_info_price/xinjiang"

# ── 下载功能 ────────────────────────────────────────────

def _api_post(url, params, timeout=15, retries=3):
    """调用xjzj.com的POST API"""
    data = urllib.parse.urlencode(params).encode()
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

def _api_get(url, timeout=15, retries=3):
    """GET请求"""
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise

def fetch_file_list(areaid, max_items=None):
    """获取某地州的信息价文件列表
    返回: [(id, name, xls_url), ...]
    """
    results = []
    page = 1
    pagesize = 50
    while True:
        data = _api_post(API_LIST, {
            "title": "", "content": "",
            "guid": "c8df326e-cf0e-494d-b643-bd45cdb32773",
            "areaid": areaid, "page": page, "pagesize": pagesize,
        })
        rows = data.get("Rows", [])
        if not rows:
            break
        for r in rows:
            results.append((r["ID"], r["Name"]))
            if max_items and len(results) >= max_items:
                return results
        if len(rows) < pagesize:
            break
        page += 1
    return results

def fetch_xls_url(detail_id):
    """从详情页提取xls下载链接"""
    html = _api_get(f"{API_DETAIL}/{detail_id}").decode("utf-8", errors="ignore")
    # 匹配 href="javascript:LookFile('/Upload/File/xxx/xxx.xls')"
    m = re.search(r"LookFile\('(/Upload/File/[^']+\.xls)'\)", html)
    if m:
        return API_BASE + m.group(1)
    # 也可能是直接链接
    m = re.search(r'href="(/Upload/File/[^"]+\.xls[^"]*)"', html)
    if m:
        return API_BASE + m.group(1)
    return None

def _guess_period(name):
    """从标题提取年月，如 '阿克苏地区2026年1月份' → '202601'"""
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', name)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}"
    return ""

def _guess_city_from_name(name):
    """从标题提取城市名"""
    for kw in _CITY_KEYWORDS:
        if kw in name:
            return kw
    return ""

def download_files(months=3, download_all=False, areas=None, verbose=False):
    """批量下载xls文件

    Args:
        months: 下载最近几个月（默认3）
        download_all: 下载全部历史
        areas: 指定地州areaid列表，None则全部
        verbose: 打印详细信息
    Returns:
        下载的文件路径列表
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    target_areas = areas or list(AREA_MAP.keys())
    downloaded = []
    skipped = 0
    errors = 0

    for areaid in target_areas:
        short_name, cn_name = AREA_MAP.get(areaid, (f"area{areaid}", f"地区{areaid}"))
        print(f"\n[{cn_name}] 获取文件列表...")

        try:
            max_items = None if download_all else months * 3  # 每月可能有多个子地区
            items = fetch_file_list(areaid, max_items=max_items)
        except Exception as e:
            print(f"  ✗ 获取列表失败: {e}")
            errors += 1
            continue

        print(f"  找到 {len(items)} 个文件")

        for detail_id, name in items:
            period = _guess_period(name)
            city = _guess_city_from_name(name)

            # 文件名：{短名}_{城市}_{年月}.xls
            city_pinyin = city or cn_name
            filename = f"{short_name}_{city_pinyin}_{period}.xls" if period else f"{short_name}_{detail_id}.xls"
            filepath = os.path.join(DOWNLOAD_DIR, filename)

            # 跳过已下载的
            if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
                if verbose:
                    print(f"  · 跳过（已存在）: {filename}")
                skipped += 1
                continue

            try:
                xls_url = fetch_xls_url(detail_id)
                if not xls_url:
                    if verbose:
                        print(f"  ✗ 未找到xls链接: {name}")
                    errors += 1
                    continue

                # 下载URL需要编码中文部分
                content = _api_get(urllib.parse.quote(xls_url, safe="/:@?=&#"), timeout=30)
                with open(filepath, "wb") as f:
                    f.write(content)

                print(f"  ✓ {filename} ({len(content)//1024}KB)")
                downloaded.append(filepath)
                time.sleep(0.3)  # 礼貌延迟

            except Exception as e:
                print(f"  ✗ 下载失败 [{name}]: {e}")
                errors += 1

    print(f"\n下载完成: 成功{len(downloaded)} 跳过{skipped} 失败{errors}")
    return downloaded


# ── 导入功能 ────────────────────────────────────────────

def _guess_city_from_file(filepath, sheet_title=""):
    """从文件名和sheet标题提取城市"""
    basename = os.path.basename(filepath)
    for text in [sheet_title, basename]:
        for kw in _CITY_KEYWORDS:
            if kw in text:
                return kw
    return ""

def _guess_period_from_file(filepath, sheet_title=""):
    """从文件名和sheet标题提取期号(YYYY-MM)"""
    for text in [sheet_title, os.path.basename(filepath)]:
        m = re.search(r'(\d{4})\s*年?\s*(\d{1,2})\s*月', text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        # 也匹配 202601 格式
        m = re.search(r'_(\d{4})(\d{2})\.', text)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return ""

def parse_xls(filepath, verbose=False):
    """解析一个新疆信息价xls文件

    Returns:
        list of dict: [{name, spec, unit, price_with_tax, price_without_tax, city, period}, ...]
    """
    try:
        import xlrd
    except ImportError:
        print("错误: 需要安装 xlrd 库。运行: pip install xlrd")
        sys.exit(1)

    records = []

    try:
        wb = xlrd.open_workbook(filepath)
    except Exception as e:
        print(f"  ✗ 打开文件失败 [{filepath}]: {e}")
        return records

    for sheet_name in wb.sheet_names():
        sheet = wb.sheet_by_name(sheet_name)
        if sheet.nrows < 3:
            continue

        # 从第2行（index=1）获取标题行，提取城市和期号
        title_text = " ".join(str(sheet.cell_value(r, 0)) for r in range(min(3, sheet.nrows)))
        file_city = _guess_city_from_file(filepath, title_text)
        file_period = _guess_period_from_file(filepath, title_text)

        # 找表头行（包含"序号"和"材料名称"的行）
        header_row = -1
        for r in range(min(10, sheet.nrows)):
            row_text = " ".join(str(sheet.cell_value(r, c)) for c in range(sheet.ncols))
            if "序号" in row_text and ("材料名称" in row_text or "名称" in row_text):
                header_row = r
                break

        if header_row < 0:
            if verbose:
                print(f"  · 跳过sheet [{sheet_name}]: 未找到表头")
            continue

        # 解析列映射
        headers = [str(sheet.cell_value(header_row, c)).strip() for c in range(sheet.ncols)]
        col_map = {}
        for i, h in enumerate(headers):
            if "序号" in h:
                col_map["seq"] = i
            elif "名称" in h and "规格" in h:
                col_map["name_spec"] = i  # 名称+规格在同一列
            elif "名称" in h:
                col_map["name"] = i
            elif "单位" in h:
                col_map["unit"] = i
            elif "含税" in h:
                col_map["price_tax"] = i
            elif "除税" in h or "不含税" in h:
                col_map["price_notax"] = i
            elif "运距" in h:
                col_map["distance"] = i

        # 当前子城市（附件2中按城市分区）
        current_city = file_city

        # 解析数据行
        for r in range(header_row + 1, sheet.nrows):
            # 读取序号列
            seq_val = sheet.cell_value(r, col_map.get("seq", 0)) if "seq" in col_map else ""

            # 读取名称列
            name_col = col_map.get("name_spec", col_map.get("name", 1))
            name_raw = str(sheet.cell_value(r, name_col)).strip()

            # 检测子城市行（附件2中"阿克苏市"这样的行）
            if name_raw and not seq_val and not str(sheet.cell_value(r, col_map.get("unit", 2))).strip():
                # 可能是城市分隔行
                for kw in _CITY_KEYWORDS:
                    if kw in name_raw:
                        current_city = kw
                        if verbose:
                            print(f"    切换城市: {current_city}")
                        break
                continue

            # 跳过空行、注释行
            if not name_raw or name_raw.startswith("注"):
                continue

            # 跳过无价格的行
            price_tax = _parse_price(sheet.cell_value(r, col_map["price_tax"])) if "price_tax" in col_map else None
            price_notax = _parse_price(sheet.cell_value(r, col_map["price_notax"])) if "price_notax" in col_map else None

            if price_tax is None and price_notax is None:
                continue

            # 拆分名称和规格（名称中可能包含规格）
            name, spec = _split_name_spec(name_raw)

            unit = str(sheet.cell_value(r, col_map.get("unit", 2))).strip() if "unit" in col_map else ""

            records.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "price_with_tax": round(price_tax, 2) if price_tax else None,
                "price_without_tax": round(price_notax, 2) if price_notax else None,
                "city": current_city,
                "period": file_period,
                "sheet": sheet_name,
            })

    return records

def _parse_price(val):
    """解析价格值，支持数字和字符串"""
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

def _split_name_spec(text):
    """拆分材料名称和规格
    例: '热轧带肋钢筋 HRB400E Φ8-Φ10' → ('热轧带肋钢筋', 'HRB400E Φ8-Φ10')
    例: '水泥42.5（R）' → ('水泥', '42.5（R）')
    例: 'PE给水管 SDR11 dn110' → ('PE给水管', 'SDR11 dn110')
    """
    text = text.strip()

    # 用正则找第一个规格特征的位置
    # 规格特征：Φ、DN、dn、SDR、HPB、HRB、数字开头的规格
    patterns = [
        r'\s+(HPB\d)',          # HPB300
        r'\s+(HRB\d)',          # HRB400E
        r'\s+(Φ|φ|ф)',          # Φ8
        r'\s+(DN|dn|De)\d',     # DN100
        r'\s+(SDR\d)',          # SDR11
        r'\s+(\d+\.?\d*×)',     # 12×4 (规格尺寸)
        r'\s+(\d+\.?\d*mm)',    # 25mm
        r'(?<=[\u4e00-\u9fff])(\d+\.?\d*[（(])', # 水泥42.5（R）
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            pos = m.start(1)
            # 如果规格前面有空格，从空格后开始
            while pos > 0 and text[pos-1] == ' ':
                pos -= 1
            name = text[:pos].strip()
            spec = text[pos:].strip()
            if name:
                return name, spec

    # 没有明显规格特征，用第一个空格分割
    parts = text.split(None, 1)
    if len(parts) == 2 and len(parts[0]) >= 2:
        # 检查第二部分是否像规格（含数字或字母）
        if re.search(r'[0-9A-Za-zΦφ×]', parts[1]):
            return parts[0], parts[1]

    return text, ""


def import_to_db(records, source_file, dry_run=False, verbose=False):
    """将解析出的记录导入主材库

    Args:
        records: parse_xls返回的记录列表
        source_file: 来源文件名（用于import_batch记录）
        dry_run: 只打印不写库
        verbose: 打印详细信息
    Returns:
        导入的记录数
    """
    if not records:
        return 0

    if dry_run:
        print(f"  [DRY RUN] 将导入 {len(records)} 条记录")
        if verbose:
            for r in records[:10]:
                print(f"    {r['city']} | {r['name']} {r['spec']} | {r['unit']} | "
                      f"含税={r['price_with_tax']} 除税={r['price_without_tax']}")
            if len(records) > 10:
                print(f"    ... 还有 {len(records)-10} 条")
        return len(records)

    # 导入数据库
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from src.material_db import MaterialDB

    db = MaterialDB()

    # 创建import_batch
    import sqlite3
    conn = sqlite3.connect(db.db_path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO import_batch (source_type, source_file, province, record_count, status, created_at)
        VALUES (?, ?, '新疆', ?, 'importing', datetime('now'))
    """, ("xinjiang_xls", source_file, len(records)))
    batch_id = cur.lastrowid

    # 批量插入price_fact
    inserted = 0
    for r in records:
        # 查找或创建material_master
        full_name = f"{r['name']} {r['spec']}".strip() if r['spec'] else r['name']

        cur.execute("SELECT id FROM material_master WHERE name = ? AND spec = ?", (r['name'], r['spec']))
        row = cur.fetchone()
        if row:
            mat_id = row[0]
        else:
            cur.execute("""
                INSERT INTO material_master (name, spec, unit, category)
                VALUES (?, ?, ?, '')
            """, (r['name'], r['spec'], r['unit']))
            mat_id = cur.lastrowid

        # 插入price_fact（列名对齐实际表结构）
        cur.execute("""
            INSERT INTO price_fact (material_id, batch_id, province, city,
                                    period_start, period_end, price_date,
                                    price_incl_tax, price_excl_tax, unit,
                                    source_type, source_doc)
            VALUES (?, ?, '新疆', ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    'info_price', ?)
        """, (mat_id, batch_id, r['city'],
              r['period'], r['period'], r['period'],
              r['price_with_tax'], r['price_without_tax'], r['unit'],
              full_name))
        inserted += 1

    # 更新batch状态
    cur.execute("UPDATE import_batch SET record_count = ?, status = 'done' WHERE id = ?",
                (inserted, batch_id))
    conn.commit()
    conn.close()

    return inserted


# ── 主函数 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="新疆信息价Excel导入工具")
    parser.add_argument("--download", action="store_true", help="下载xls文件")
    parser.add_argument("--import", dest="do_import", action="store_true", help="导入到主材库")
    parser.add_argument("--months", type=int, default=3, help="下载最近N个月（默认3）")
    parser.add_argument("--all", action="store_true", help="下载全部历史数据")
    parser.add_argument("--areas", type=str, help="指定地州areaid，逗号分隔（如 1,2,11）")
    parser.add_argument("--dir", type=str, default=DOWNLOAD_DIR, help="xls文件目录")
    parser.add_argument("--file", type=str, help="指定单个xls文件导入")
    parser.add_argument("--dry-run", action="store_true", help="只解析不入库")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")

    args = parser.parse_args()

    if not args.download and not args.do_import:
        parser.print_help()
        print("\n请指定 --download（下载）和/或 --import（导入）")
        return

    # 解析地州参数
    area_ids = None
    if args.areas:
        area_ids = [int(x.strip()) for x in args.areas.split(",")]

    downloaded_files = []

    # 第一步：下载
    if args.download:
        downloaded_files = download_files(
            months=args.months,
            download_all=args.all,
            areas=area_ids,
            verbose=args.verbose,
        )

    # 第二步：导入
    if args.do_import:
        if args.file:
            # 导入单个文件
            files = [args.file]
        elif downloaded_files:
            files = downloaded_files
        else:
            # 导入目录下所有xls
            import glob
            files = sorted(glob.glob(os.path.join(args.dir, "*.xls")))
            if not files:
                print(f"目录 {args.dir} 下没有xls文件")
                return

        total_imported = 0
        total_files = 0

        for fpath in files:
            basename = os.path.basename(fpath)
            print(f"\n解析: {basename}")

            records = parse_xls(fpath, verbose=args.verbose)
            if not records:
                print(f"  · 无有效数据")
                continue

            # 统计城市
            cities = set(r["city"] for r in records if r["city"])
            periods = set(r["period"] for r in records if r["period"])
            print(f"  {len(records)}条 | 城市: {', '.join(sorted(cities)) or '未知'} | 期号: {', '.join(sorted(periods)) or '未知'}")

            n = import_to_db(records, basename, dry_run=args.dry_run, verbose=args.verbose)
            total_imported += n
            total_files += 1

        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}导入完成: {total_files}个文件, {total_imported:,}条记录")


if __name__ == "__main__":
    main()
