"""
F盘清单文件清理 — 区分标准清单、非标文件、广联达文件、垃圾文件

标准清单判断标准：
  Excel(.xlsx/.xls/.xlsm)中至少有一个Sheet包含12位清单编码（如031001008004）

分类规则：
  标准清单（有12位编码）→ 原地不动
  非标Excel（无12位编码）→ F:/jarvis/_非标文件/{专业}/
  广联达文件(.GBQ6/.GBQ7等) → F:/jarvis/_广联达文件/{专业}/
  垃圾文件（重复/非房建/非清单/太小/其他格式）→ F:/jarvis/_垃圾文件/

用法：
  python tools/clean_jarvis_files.py              # 扫描统计（不动文件）
  python tools/clean_jarvis_files.py --move        # 实际移动文件
"""

import os
import re
import sys
import shutil
from pathlib import Path
from collections import defaultdict

# 复用 scan_jarvis_files 的过滤逻辑（只导入过滤函数，不导入 SCAN_DIRS）
sys.path.insert(0, str(Path(__file__).parent))
from scan_jarvis_files import (
    JARVIS_DIR,
    is_non_building, is_non_bill, is_too_small,
)

# ============================================================
# 常量配置
# ============================================================

# 12位清单编码正则：0开头，12位数字
BILL_CODE_PATTERN = re.compile(r'^0[1-9]\d{10}$')

# Excel扩展名（含.xlsm）
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}

# 广联达及行业造价软件文件扩展名（有价值，单独保存）
GLD_EXTENSIONS = {
    # 广联达计价
    ".gbq6", ".gbq7", ".gbq9", ".gbq",
    ".gpb6", ".gpb7", ".gpb",
    ".zbqd", ".qdg4", ".qdg",
    ".gczjwj", ".gczj", ".gad", ".zjxm",
    ".ysq", ".ygl", ".yfjz",
    ".bj23", ".sxzb4", ".spw", ".bsj",
    # 其他造价软件
    ".e2d", ".13jz", ".pbq",
}

# 移动目标目录
NON_STANDARD_DIR = JARVIS_DIR / "_非标文件"
GLD_DIR = JARVIS_DIR / "_广联达文件"
JUNK_DIR = JARVIS_DIR / "_垃圾文件"


# ============================================================
# 工具函数
# ============================================================

def get_scan_dirs():
    """自动发现 F:/jarvis/ 下所有专业目录（排除下划线开头的管理目录）"""
    if not JARVIS_DIR.exists():
        return []
    return sorted([
        d.name for d in JARVIS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ])


def is_duplicate(filename):
    """判断是否是重复文件

    匹配：xxx(2).xlsx、xxx_wx(2).xlsx、xxx - 副本.xlsx 等
    比原版更全面，覆盖所有扩展名（不只是xlsx/xls）
    """
    # 文件名末尾有(数字)
    if re.search(r'\(\d+\)\.\w+$', filename):
        return True
    # Windows "副本" 标记
    if " - 副本" in filename:
        return True
    return False


def has_standard_bill_codes(filepath):
    """检查Excel是否包含标准12位清单编码

    遍历所有Sheet，检查每行前5列，找到至少1个12位编码就算标准清单。
    只检查前200行（避免大文件太慢）。
    .xls 用 xlrd，.xlsx/.xlsm 用 openpyxl。
    """
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".xls":
            return _check_codes_xls(filepath)
        else:
            return _check_codes_xlsx(filepath)
    except Exception:
        return False


def _check_codes_xlsx(filepath):
    """检查 .xlsx/.xlsm 文件中是否有12位清单编码"""
    import openpyxl
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            row_count = 0
            try:
                for row in ws.iter_rows(values_only=True):
                    row_count += 1
                    if row_count > 200:
                        break
                    for cell in row[:5]:
                        if cell is None:
                            continue
                        val = str(cell).strip()
                        if BILL_CODE_PATTERN.match(val):
                            return True
            except Exception:
                continue
    finally:
        wb.close()
    return False


def _check_codes_xls(filepath):
    """检查 .xls 文件中是否有12位清单编码"""
    import xlrd
    wb = xlrd.open_workbook(filepath, on_demand=True)
    try:
        for sheet_name in wb.sheet_names():
            ws = wb.sheet_by_name(sheet_name)
            for row_idx in range(min(200, ws.nrows)):
                for col_idx in range(min(5, ws.ncols)):
                    val = str(ws.cell_value(row_idx, col_idx)).strip()
                    if BILL_CODE_PATTERN.match(val):
                        return True
    finally:
        wb.release_resources()
    return False


# ============================================================
# 核心逻辑：扫描并分类
# ============================================================

def scan_and_classify():
    """扫描所有专业目录，将文件分类为：标准清单/非标/广联达/各类垃圾"""
    results = {
        "standard": [],          # 标准清单（有12位编码的Excel）
        "non_standard": [],      # 非标Excel（无12位编码）
        "guanglianda": [],       # 广联达文件（.GBQ6/.GBQ7等）
        "junk_duplicate": [],    # 重复文件
        "junk_non_building": [], # 非房建
        "junk_non_bill": [],     # 非清单（询价单/审核表/临时文件等）
        "junk_too_small": [],    # 太小（<5KB）
        "junk_other": [],        # 其他格式（非Excel非广联达）
        "errors": [],            # 打开失败
    }

    scan_dirs = get_scan_dirs()
    total_files = 0

    for specialty in scan_dirs:
        dir_path = JARVIS_DIR / specialty
        if not dir_path.exists():
            continue

        print(f"  扫描 {specialty}...", end="", flush=True)
        spec_standard = 0
        spec_total = 0

        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                filepath = os.path.join(root, filename)
                total_files += 1
                ext = Path(filename).suffix.lower()

                # ---- 第1层：按扩展名分流 ----

                # 广联达文件 → 单独保存
                if ext in GLD_EXTENSIONS:
                    results["guanglianda"].append(filepath)
                    continue

                # 非Excel也非广联达 → 其他垃圾
                if ext not in EXCEL_EXTENSIONS:
                    results["junk_other"].append(filepath)
                    continue

                # ---- 第2层：Excel基本过滤 ----

                # 临时文件
                if filename.startswith('~') or filename.startswith('.'):
                    results["junk_non_bill"].append(filepath)
                    continue

                # 重复文件（用改进的正则）
                if is_duplicate(filename):
                    results["junk_duplicate"].append(filepath)
                    continue

                # 非房建项目
                if is_non_building(filename):
                    results["junk_non_building"].append(filepath)
                    continue

                # 非清单文件（询价单/审核表等）
                if is_non_bill(filename):
                    results["junk_non_bill"].append(filepath)
                    continue

                # 太小（<5KB，可能是空文件）
                if is_too_small(filepath):
                    results["junk_too_small"].append(filepath)
                    continue

                # ---- 第3层：打开Excel检查12位编码 ----

                spec_total += 1
                try:
                    if has_standard_bill_codes(filepath):
                        results["standard"].append(filepath)
                        spec_standard += 1
                    else:
                        results["non_standard"].append(filepath)
                except Exception as e:
                    results["errors"].append((filepath, str(e)))

        print(f" 标准{spec_standard}/{spec_total}")

    return results, total_files, scan_dirs


# ============================================================
# 移动文件
# ============================================================

def move_files(results, dry_run=True):
    """移动非标、广联达和垃圾文件到对应目录"""

    # 移动非标文件（保留专业子目录结构）
    non_std_moved = 0
    for filepath in results["non_standard"]:
        fp = Path(filepath)
        try:
            rel = fp.relative_to(JARVIS_DIR)
        except ValueError:
            continue
        dest = NON_STANDARD_DIR / rel
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fp), str(dest))
        non_std_moved += 1

    # 移动广联达文件（保留专业子目录结构）
    gld_moved = 0
    for filepath in results["guanglianda"]:
        fp = Path(filepath)
        try:
            rel = fp.relative_to(JARVIS_DIR)
        except ValueError:
            continue
        dest = GLD_DIR / rel
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fp), str(dest))
        gld_moved += 1

    # 移动垃圾文件
    junk_categories = [
        "junk_duplicate", "junk_non_building",
        "junk_non_bill", "junk_too_small", "junk_other",
    ]
    junk_moved = 0
    for cat in junk_categories:
        for filepath in results[cat]:
            fp = Path(filepath)
            try:
                rel = fp.relative_to(JARVIS_DIR)
            except ValueError:
                continue
            dest = JUNK_DIR / rel
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fp), str(dest))
            junk_moved += 1

    return non_std_moved, gld_moved, junk_moved


# ============================================================
# 入口
# ============================================================

def main():
    do_move = "--move" in sys.argv

    print("=" * 60)
    print("F:/jarvis/ 文件清理（全目录版）")
    print("=" * 60)
    print()

    print("[1/2] 扫描并分类文件...")
    results, total_files, scan_dirs = scan_and_classify()

    standard = len(results["standard"])
    non_standard = len(results["non_standard"])
    guanglianda = len(results["guanglianda"])
    junk = sum(len(results[k]) for k in results if k.startswith("junk_"))
    errors = len(results["errors"])

    print()
    print("=" * 60)
    print(f"扫描完成 — 共 {len(scan_dirs)} 个目录，{total_files} 个文件")
    print(f"  标准清单（有12位编码）: {standard}")
    print(f"  非标Excel（无12位编码）: {non_standard}")
    print(f"  广联达文件: {guanglianda}")
    print(f"  垃圾文件: {junk}")
    print(f"    重复/副本: {len(results['junk_duplicate'])}")
    print(f"    非房建: {len(results['junk_non_building'])}")
    print(f"    非清单: {len(results['junk_non_bill'])}")
    print(f"    太小(<5KB): {len(results['junk_too_small'])}")
    print(f"    其他格式: {len(results['junk_other'])}")
    if errors:
        print(f"  打开失败: {errors}")

    # 列出部分非标文件名（让用户确认判断是否合理）
    if results["non_standard"]:
        print()
        print("非标文件示例（前20个）:")
        for fp in results["non_standard"][:20]:
            name = Path(fp).name
            try:
                size = os.path.getsize(fp) // 1024
            except OSError:
                size = 0
            print(f"  {name} ({size}KB)")

    if do_move:
        print()
        print("[2/2] 移动文件...")
        non_std_moved, gld_moved, junk_moved = move_files(results, dry_run=False)
        print(f"  非标Excel  -> _非标文件/    ({non_std_moved} 个)")
        print(f"  广联达文件 -> _广联达文件/  ({gld_moved} 个)")
        print(f"  垃圾文件   -> _垃圾文件/    ({junk_moved} 个)")
        print()
        print(f"清理完成！各专业目录下只剩 {standard} 个标准清单文件")
    else:
        print()
        print("这是预览模式，文件没有移动。")
        print("确认无误后运行：python tools/clean_jarvis_files.py --move")


if __name__ == "__main__":
    main()
