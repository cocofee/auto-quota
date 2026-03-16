# -*- coding: utf-8 -*-
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
"""
武汉信息价OCR导入工具

武汉市建设工程综合价格信息PDF是扫描件（纯图片），
无法用pdfplumber/pymupdf直接提取文字，必须先OCR再解析。

用法：
    # 试运行（只看提取结果，不写库）
    python tools/import_wuhan_ocr.py --file "data/pdf_info_price/wuhan/wuhan_202602.pdf" \
      --period "2026-02" --dry-run

    # 正式导入
    python tools/import_wuhan_ocr.py --file "data/pdf_info_price/wuhan/wuhan_202602.pdf" \
      --period "2026-02"

    # 批量导入目录下所有PDF
    python tools/import_wuhan_ocr.py --dir "data/pdf_info_price/wuhan/" --dry-run

依赖：
    pip install rapidocr-onnxruntime pdfplumber Pillow
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pdfplumber
from PIL import Image

# 确保项目根目录在搜索路径中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.material_db import MaterialDB
from tools.import_price_excel import (
    normalize_unit, normalize_spec, is_junk_material,
    create_import_batch, update_batch_count
)
from tools.pdf_profiles.base_profile import guess_category, clean_price


# ======== OCR单例（避免每页都重新初始化）========
_ocr_instance = None

def _get_ocr():
    """获取OCR实例（延迟初始化，全局复用）"""
    global _ocr_instance
    if _ocr_instance is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_instance = RapidOCR()
    return _ocr_instance


# ======== 页面类型判断 ========

# 封面/通知/目录页的关键词（出现任一就跳过）
_SKIP_KEYWORDS = [
    "武汉市市政工程招标", "抄送", "印发",  # 通知页
    "附件：", "附件:",                      # 只有"附件："的封面页
    "目  录", "目录",                       # 目录页
]

# 表格数据页的关键词
_DATA_KEYWORDS = [
    "序号", "名称", "规格型号", "单位", "含税价", "除税价",
]


def _is_data_page(texts: list) -> bool:
    """判断OCR结果是否为数据页（有表格）"""
    all_text = " ".join(texts)

    # 有"序号"+"名称"或者有明显的数据行（数字序号+价格）
    if "序号" in all_text and "名称" in all_text:
        return True

    # 续表页：没有表头但有大量数字序号+价格
    num_count = sum(1 for t in texts if re.match(r'^\d{1,3}$', t.strip()))
    price_count = sum(1 for t in texts if re.match(r'^\d+\.\d{2}$', t.strip()))
    if num_count >= 5 and price_count >= 5:
        return True

    return False


def _is_skip_page(texts: list) -> bool:
    """判断是否为应跳过的页面（封面/通知/目录等）"""
    if len(texts) < 10:
        return True  # 文字太少，不是数据页

    all_text = " ".join(texts[:20])  # 只看前20个文本块
    for kw in _SKIP_KEYWORDS:
        if kw in all_text:
            # 特殊情况："附件："后面紧跟标题+表格的页面不跳过
            if kw in ("附件：", "附件:"):
                if any("含税价" in t or "除税价" in t for t in texts):
                    return False
            else:
                return True
    return False


# ======== OCR文本块 → 表格行 ========

def _ocr_page(page, dpi: int = 300) -> list:
    """
    OCR识别一页PDF，返回文本块列表

    每个文本块：{"text": str, "x": float, "y": float, "conf": float}
    """
    # PDF页面转图片
    img = page.to_image(resolution=dpi)
    img_pil = img.original.convert("RGB")
    img_array = np.array(img_pil)

    # OCR识别
    ocr = _get_ocr()
    result = ocr(img_array)

    if result is None or result[0] is None:
        return []

    blocks = []
    for item in result[0]:
        box, text, conf = item
        # box是4个角的坐标，取中心点
        y_mid = (box[0][1] + box[2][1]) / 2
        x_mid = (box[0][0] + box[2][0]) / 2
        x_left = box[0][0]  # 左边界（用于列对齐）
        x_right = box[2][0]  # 右边界
        blocks.append({
            "text": text.strip(),
            "x": x_mid,
            "x_left": x_left,
            "x_right": x_right,
            "y": y_mid,
            "conf": conf,
        })

    return blocks


def _group_into_rows(blocks: list, y_threshold: int = 15) -> list:
    """
    把OCR文本块按y坐标分组成行

    y坐标差小于y_threshold的归为同一行。
    返回：list of list，每行是按x排序的文本块列表。
    """
    if not blocks:
        return []

    # 按y坐标排序
    blocks.sort(key=lambda b: (b["y"], b["x"]))

    rows = []
    current_row = [blocks[0]]

    for b in blocks[1:]:
        if abs(b["y"] - current_row[0]["y"]) < y_threshold:
            current_row.append(b)
        else:
            current_row.sort(key=lambda b: b["x"])
            rows.append(current_row)
            current_row = [b]

    current_row.sort(key=lambda b: b["x"])
    rows.append(current_row)

    return rows


# ======== 表格行解析 ========

def _detect_col_boundaries(rows: list, page_width: float) -> dict:
    """
    从OCR结果中自动检测列边界

    通过找表头行（"序号"/"名称"/"单位"等关键词）的x坐标，
    确定各列的分界线。这样不管是建筑装饰表、安装表还是城建交通表，
    都能自适应。

    返回：{"seq": x, "name": x, "spec": x, "unit": x,
           "price_incl": x, "price_excl": x, "note": x}
           每个值是该列中心的相对x坐标（0~1）
    """
    # 在所有行中找表头关键词的x坐标
    header_x = {}
    for row in rows:
        for b in row:
            text = b["text"].strip()
            rel_x = b["x"] / page_width
            if text == "序号" or (text == "序" and rel_x < 0.15):
                header_x["seq"] = rel_x
            elif text == "名称":
                header_x["name"] = rel_x
            elif "规格" in text:
                header_x["spec"] = rel_x
            elif text in ("单位", "单"):
                header_x["unit"] = rel_x
            elif "含税价" in text:
                header_x["price_incl"] = rel_x
            elif "除税价" in text:
                header_x["price_excl"] = rel_x
            elif text == "备注" or text == "备 注":
                header_x["note"] = rel_x

    # 如果找到了足够的表头列，用它们来推算分界线
    if len(header_x) >= 4:
        return header_x

    # 兜底：返回空字典，使用默认分界
    return {}


def _assign_block_to_col(b: dict, col_centers: dict, page_width: float) -> str:
    """
    根据列中心坐标，把一个文本块归入最近的列

    col_centers: {"seq": 0.095, "name": 0.17, "spec": 0.38, ...}
    """
    rel_x = b["x"] / page_width

    # 计算到每个列中心的距离，取最近的
    best_col = "note"
    best_dist = 999
    for col_name, col_x in col_centers.items():
        dist = abs(rel_x - col_x)
        if dist < best_dist:
            best_dist = dist
            best_col = col_name

    return best_col


# 默认列中心位置（建筑装饰表，兜底用）
_DEFAULT_COL_CENTERS = {
    "seq": 0.095, "name": 0.173, "spec": 0.383,
    "unit": 0.649, "price_incl": 0.717,
    "price_excl": 0.789, "note": 0.868,
}


def _parse_table_row(row_blocks: list, page_width: float,
                     col_centers: dict = None) -> dict:
    """
    解析一行OCR文本块为结构化数据

    武汉表格固定7列：序号 | 名称 | 规格型号 | 单位 | 含税价 | 除税价 | 备注
    通过列中心坐标（从表头自动检测或默认值）判断每个文本块属于哪一列。

    返回：{"seq": str, "name": str, "spec": str, "unit": str,
           "price_incl": float, "price_excl": float, "note": str}
           或 None（非数据行）
    """
    if not row_blocks:
        return None

    if col_centers is None:
        col_centers = _DEFAULT_COL_CENTERS

    texts = [b["text"] for b in row_blocks]
    joined = " ".join(texts)

    # 跳过表头行
    if "序号" in joined and "名称" in joined:
        return None
    if "（元）" in joined or "(元)" in joined:
        return None
    # 跳过"序"和"号"被拆成两个块的表头行
    if ("序" in texts and "名称" in joined) or ("单位" in joined and "备注" in joined):
        return None

    # 跳过分类标题行（如"一、水泥"、"二、砂、石、灰"）
    if re.match(r'^[一二三四五六七八九十]+、', joined):
        return {"_section": joined}  # 记录分类标题

    # 跳过备注/说明行
    if "备注" in joined or "备  注" in joined:
        return None
    if joined.startswith("备注") or joined.startswith("注：") or joined.startswith("说明"):
        return None

    # 跳过页面标题行
    if "综合价格信息" in joined or "武汉市" in joined:
        return None

    # 按列中心坐标归类
    cols = {"seq": [], "name": [], "spec": [], "unit": [],
            "price_incl": [], "price_excl": [], "note": []}

    for b in row_blocks:
        col_name = _assign_block_to_col(b, col_centers, page_width)
        cols[col_name].append(b["text"])

    seq = " ".join(cols["seq"]).strip()
    name = " ".join(cols["name"]).strip()
    spec = " ".join(cols["spec"]).strip()
    unit = " ".join(cols["unit"]).strip()
    price_incl_str = " ".join(cols["price_incl"]).strip()
    price_excl_str = " ".join(cols["price_excl"]).strip()
    note = " ".join(cols["note"]).strip()

    # 验证：序号必须是数字（或"+"等OCR误识别）
    if not seq or not re.match(r'^[\d+]+$', seq.replace(" ", "")):
        # 有些行没有序号但有名称和价格（合并行），也尝试解析
        if not name or not price_incl_str:
            return None

    # 解析价格
    price_incl = clean_price(price_incl_str)
    price_excl = clean_price(price_excl_str)

    # 至少要有一个价格
    if price_incl <= 0 and price_excl <= 0:
        return None

    # 如果只有除税价没有含税价，用除税价反算
    if price_incl <= 0 and price_excl > 0:
        price_incl = round(price_excl * 1.13, 2)

    return {
        "seq": seq,
        "name": name,
        "spec": spec,
        "unit": unit,
        "price_incl": price_incl,
        "price_excl": price_excl,
        "note": note,
    }


# ======== 整页解析 ========

def _detect_table_type(texts: list) -> str:
    """
    检测当前页属于哪种子表

    武汉PDF分3种子表：
    - 建筑装饰工程材料
    - 安装工程材料（最有用——管材/电缆/阀门等）
    - 城建交通工程材料
    """
    all_text = " ".join(texts[:15])  # 只看前15个文本块（标题在顶部）
    if "安装工程" in all_text:
        return "安装"
    elif "城建交通" in all_text:
        return "城建交通"
    elif "建筑装饰" in all_text:
        return "建筑装饰"
    return ""  # 续表页（没有标题），沿用上一页的类型


# 用于跨页记忆（续表页没有标题和表头）
_last_table_type = ""
_last_col_centers = None


def _extract_page(page, page_num: int, verbose: bool = False) -> list:
    """
    OCR并解析一页PDF，返回记录列表

    返回：[{"name": str, "spec": str, "unit": str, "price": float,
            "category": str, "tax_included": True}, ...]
    """
    global _last_table_type, _last_col_centers

    blocks = _ocr_page(page)
    if not blocks:
        if verbose:
            print(f"  第{page_num}页: OCR无结果，跳过")
        return []

    texts = [b["text"] for b in blocks]

    # 判断是否跳过
    if _is_skip_page(texts):
        if verbose:
            print(f"  第{page_num}页: 非数据页，跳过")
        return []

    if not _is_data_page(texts):
        if verbose:
            print(f"  第{page_num}页: 非表格页，跳过")
        return []

    # 分行
    rows = _group_into_rows(blocks)

    # 获取页面宽度（用于列定位）
    page_width = float(page.width) * (300 / 72)  # 转成300dpi像素宽度

    # 检测子表类型（建筑装饰/安装/城建交通）
    table_type = _detect_table_type(texts)
    if table_type:
        _last_table_type = table_type
    else:
        table_type = _last_table_type  # 续表页沿用上一页

    # 自动检测列位置（从表头关键词）
    col_centers = _detect_col_boundaries(rows, page_width)
    if col_centers and len(col_centers) >= 4:
        _last_col_centers = col_centers
    else:
        col_centers = _last_col_centers or _DEFAULT_COL_CENTERS

    # 解析每行
    records = []
    current_section = ""  # 当前分类标题

    for row in rows:
        parsed = _parse_table_row(row, page_width, col_centers)
        if parsed is None:
            continue

        # 分类标题行
        if "_section" in parsed:
            current_section = parsed["_section"]
            continue

        name = parsed["name"]
        if not name:
            continue

        # 从分类标题猜大类，或从名称猜
        category = guess_category(name)
        if not category and current_section:
            category = guess_category(current_section)

        records.append({
            "name": name,
            "spec": parsed["spec"],
            "unit": parsed["unit"],
            "price": parsed["price_incl"],  # 含税价
            "category": category,
            "tax_included": True,
            "city": "武汉",
            "subcategory": table_type,  # 子表类型作为小类
        })

    if verbose:
        print(f"  第{page_num}页({table_type or '续表'}): "
              f"OCR识别{len(blocks)}块 → {len(rows)}行 → {len(records)}条记录")

    return records


# ======== 整个PDF提取 ========

def extract_wuhan_pdf(filepath: str, verbose: bool = False) -> list:
    """
    从武汉扫描件PDF中OCR提取所有信息价记录

    返回：标准记录列表
    """
    # 重置跨页记忆
    global _last_table_type, _last_col_centers
    _last_table_type = ""
    _last_col_centers = None

    pdf = pdfplumber.open(filepath)
    all_records = []
    total_pages = len(pdf.pages)

    print(f"PDF共{total_pages}页，开始OCR识别...")

    for i, page in enumerate(pdf.pages):
        page_num = i + 1

        # 进度显示
        if page_num % 5 == 0 or page_num == 1:
            print(f"  正在处理第{page_num}/{total_pages}页...")

        try:
            records = _extract_page(page, page_num, verbose=verbose)
            all_records.extend(records)
        except Exception as e:
            print(f"  警告: 第{page_num}页处理出错，已跳过 - {e}")

    pdf.close()
    return all_records


# ======== 导入数据库（复用import_price_pdf的逻辑）========

def import_to_db(records: list, province: str, period: str,
                 source_file: str, dry_run: bool = False) -> dict:
    """把OCR提取的记录写入主材库"""
    # 直接调用现有的导入函数
    from tools.import_price_pdf import import_to_db as _import_to_db
    return _import_to_db(records, province, period, source_file, dry_run)


# ======== 从文件名猜期次 ========

def _guess_period_from_filename(filename: str) -> str:
    """
    从文件名猜测期次

    例如：wuhan_202602.pdf → 2026-02
          wuhan_202501.pdf → 2025-01
    """
    m = re.search(r'(\d{4})(\d{2})', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


# ======== 主入口 ========

def main():
    parser = argparse.ArgumentParser(
        description="武汉信息价OCR导入工具 — 扫描件PDF用OCR识别后导入主材库"
    )
    parser.add_argument("--file", "-f", help="单个PDF文件路径")
    parser.add_argument("--dir", "-d", help="批量导入：目录下所有PDF")
    parser.add_argument("--period", help="信息价期次（如 2026-02），批量模式下自动从文件名猜")
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
            parser.error("无法从文件名猜出期次，请用 --period 指定（如 2026-02）")
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
    print(f"省份: 湖北 | 城市: 武汉")
    print()

    total_imported = 0

    for fp, period in files:
        print(f"{'='*60}")
        print(f"文件: {fp.name} | 期次: {period}")
        print(f"{'='*60}")

        # OCR提取
        records = extract_wuhan_pdf(str(fp), verbose=args.verbose)
        print(f"\n共提取 {len(records)} 条记录")

        if not records:
            print("未提取到记录，跳过")
            continue

        # 导入
        stats = import_to_db(
            records,
            province="湖北",
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
