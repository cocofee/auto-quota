"""
批量匹配执行器

功能：读取 batch.db 中 scanned 状态的文件，批量跑纯搜索匹配，收集结果。

设计要点（按 Codex 5.3 审核建议）：
1. 复用现有 match_search_only() 接口，不改核心逻辑
2. 纯搜索模式（不用Agent/LLM），免费+快速
3. 按省份分组初始化搜索引擎（不同省用不同定额库）
4. 断点续跑：基于数据库状态，已matched的不重跑
5. 每个文件处理完立即更新数据库状态（原子性）
6. 列映射机制：让不同格式（work_list/equipment_list）都能跑

用法：
    python tools/batch_runner.py                           # 跑全部可处理文件
    python tools/batch_runner.py --format standard_bill    # 只跑标准格式
    python tools/batch_runner.py --province 广东           # 只跑某省
    python tools/batch_runner.py --specialty 消防          # 只跑某专业
    python tools/batch_runner.py --limit 100               # 只跑前100个文件
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.batch_scanner import DB_PATH, get_db, init_db, _upsert_file, ALGORITHM_VERSION
from src.bill_reader import BillReader
from src.match_engine import init_search_components, init_experience_db, match_search_only
import config

# ============================================================
# 列映射配置（让不同格式的Excel都能转成标准清单项）
# ============================================================

# 不同格式对应的列名变体（按优先级排列，第一个匹配到的就用）
COLUMN_MAPPINGS = {
    "standard_bill": {
        # 标准格式直接用 BillReader 读取，不需要额外映射
    },
    "work_list": {
        "name": ["工作量名称", "工作项目", "项目", "名称"],
        "description": ["主要内容及范围说明", "内容说明", "说明", "范围"],
        "unit": ["单位"],
        "quantity": ["工作量", "数量"],
    },
    "equipment_list": {
        "name": ["设备名称", "材料名称", "品名", "项目名称", "名称"],
        "description": ["规格型号", "规格", "型号", "参数", "技术参数"],
        "unit": ["单位"],
        "quantity": ["数量", "工程量"],
    },
}

# 结果输出目录
RESULTS_DIR = Path(__file__).resolve().parent.parent / "output" / "batch" / "results"


# ============================================================
# 文件读取（支持多种格式）
# ============================================================

def read_file_items(file_path: str, fmt: str, sheet_info: list) -> list[dict]:
    """读取文件中的清单项。

    参数:
        file_path: Excel文件路径
        fmt: 格式类型（standard_bill/work_list/equipment_list）
        sheet_info: Sheet信息列表（从scanner扫描结果中获取）

    返回:
        清单项列表，每项包含 name, description, unit, quantity 等字段
    """
    if fmt == "standard_bill":
        # 标准格式用 BillReader 直接读（它有完整的列识别逻辑）
        reader = BillReader()
        try:
            items = reader.read_excel(file_path)
            return items
        except Exception as e:
            raise ValueError(f"BillReader读取失败: {e}")

    # 非标准格式：用列映射手动读取
    import openpyxl

    mapping = COLUMN_MAPPINGS.get(fmt)
    if not mapping:
        raise ValueError(f"不支持的格式: {fmt}")

    # 找出有清单数据的Sheet
    bill_sheets = []
    if sheet_info:
        for si in sheet_info:
            if isinstance(si, dict) and si.get("has_bill_data"):
                bill_sheets.append(si["name"])

    fp = Path(file_path)
    # .xls 转换
    actual_path = file_path
    temp_path = None
    if fp.suffix.lower() == ".xls":
        from tools.batch_scanner import _convert_xls
        temp_path = _convert_xls(file_path)
        actual_path = temp_path

    try:
        wb = openpyxl.load_workbook(str(actual_path), read_only=True, data_only=True)
        all_items = []

        try:
            sheets_to_read = bill_sheets if bill_sheets else wb.sheetnames[:5]
            for sn in sheets_to_read:
                if sn not in wb.sheetnames:
                    continue
                ws = wb[sn]
                items = _read_sheet_with_mapping(ws, sn, mapping)
                all_items.extend(items)
        finally:
            wb.close()
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass

    return all_items


def _read_sheet_with_mapping(ws, sheet_name: str, mapping: dict) -> list[dict]:
    """用列映射读取单个Sheet的数据。

    逻辑：
    1. 扫描前20行找表头
    2. 根据mapping匹配列位置
    3. 从表头下方读取数据行
    """
    # 读所有行
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(row)

    if not rows:
        return []

    # 在前20行中找表头（匹配mapping中的列名）
    header_row_idx = None
    col_map = {}  # field_name → col_index

    for row_idx in range(min(20, len(rows))):
        row = rows[row_idx]
        cells = [str(c).strip() if c is not None else "" for c in row]

        # 尝试匹配每个字段
        temp_map = {}
        for field, aliases in mapping.items():
            for col_idx, cell_text in enumerate(cells):
                if not cell_text:
                    continue
                for alias in aliases:
                    if alias in cell_text:
                        temp_map[field] = col_idx
                        break
                if field in temp_map:
                    break

        # 至少匹配到 name 字段才算找到表头
        if "name" in temp_map:
            header_row_idx = row_idx
            col_map = temp_map
            break

    if header_row_idx is None:
        return []

    # 从表头下一行开始读数据
    items = []
    for row_idx in range(header_row_idx + 1, len(rows)):
        row = rows[row_idx]
        cells = [str(c).strip() if c is not None else "" for c in row]

        name = cells[col_map["name"]] if "name" in col_map and col_map["name"] < len(cells) else ""
        if not name or len(name) < 2:
            continue  # 跳过空行和太短的行

        desc = cells[col_map["description"]] if "description" in col_map and col_map["description"] < len(cells) else ""
        unit = cells[col_map["unit"]] if "unit" in col_map and col_map["unit"] < len(cells) else ""
        qty = cells[col_map["quantity"]] if "quantity" in col_map and col_map["quantity"] < len(cells) else ""

        # 组装成标准清单项格式（兼容 match_search_only 的输入）
        item = {
            "index": len(items) + 1,
            "code": "",
            "name": name,
            "description": desc,
            "unit": unit,
            "quantity": qty,
            "search_text": f"{name} {desc}".strip(),
            "params": {},
            "sheet_name": sheet_name,
            "section": "",
        }
        items.append(item)

    return items


# ============================================================
# 核心批量匹配逻辑
# ============================================================

def run_batch(format_filter: str = None, province_filter: str = None,
              specialty_filter: str = None, limit: int = None):
    """批量匹配主函数。

    流程：
    1. 从数据库读取 scanned 状态的文件
    2. 按省份分组（不同省用不同定额库）
    3. 逐文件匹配，结果存到 output/batch/results/{省份}/
    4. 更新数据库状态为 matched
    """
    init_db()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 第1步：查询可处理的文件
    conn = get_db()
    try:
        query = "SELECT * FROM file_registry WHERE status = 'scanned'"
        params = []

        if format_filter:
            query += " AND format = ?"
            params.append(format_filter)
        if province_filter:
            query += " AND province = ?"
            params.append(province_filter)
        if specialty_filter:
            query += " AND specialty = ?"
            params.append(specialty_filter)

        query += " ORDER BY province, specialty"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        files = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    if not files:
        print("没有可处理的文件（都已matched或无scanned状态的文件）。")
        return

    print(f"待处理文件: {len(files)} 个")

    # 第2步：按省份分组
    by_province = {}
    for f in files:
        prov = f["province"] or "未知省份"
        if prov not in by_province:
            by_province[prov] = []
        by_province[prov].append(f)

    print(f"涉及省份: {list(by_province.keys())}")

    # 第3步：逐省份处理
    total_success = 0
    total_errors = 0
    total_items = 0

    for prov, prov_files in by_province.items():
        print(f"\n{'='*50}")
        print(f"处理省份: {prov} ({len(prov_files)} 个文件)")
        print(f"{'='*50}")

        # 初始化该省的搜索引擎
        resolved_province = _resolve_province(prov)
        if not resolved_province:
            print(f"  ⚠ 省份「{prov}」没有对应的定额库，跳过")
            # 标记为error
            _mark_files_error(prov_files, f"省份「{prov}」没有定额库")
            total_errors += len(prov_files)
            continue

        try:
            searcher, validator = init_search_components(resolved_province)
            experience_db = init_experience_db(no_experience=False, province=resolved_province)
        except Exception as e:
            print(f"  ⚠ 搜索引擎初始化失败: {e}")
            _mark_files_error(prov_files, f"搜索引擎初始化失败: {e}")
            total_errors += len(prov_files)
            continue

        # 逐文件匹配
        for i, f in enumerate(prov_files):
            file_path = f["file_path"]
            file_name = f["file_name"]
            fmt = f["format"]
            sheet_info_str = f["sheet_info"]

            print(f"  [{i+1}/{len(prov_files)}] {file_name}...", end=" ", flush=True)
            start_time = time.time()

            try:
                # 解析sheet_info
                sheet_info = json.loads(sheet_info_str) if sheet_info_str else []

                # 读取清单项
                items = read_file_items(file_path, fmt, sheet_info)
                if not items:
                    print(f"无清单项，跳过")
                    _mark_file_matched(f, [], 0)
                    total_success += 1
                    continue

                # 执行匹配
                results = match_search_only(
                    bill_items=items,
                    searcher=searcher,
                    validator=validator,
                    experience_db=experience_db,
                    province=resolved_province,
                )

                elapsed = time.time() - start_time

                # 保存结果
                _save_results(f, results, elapsed)
                _mark_file_matched(f, results, elapsed)

                # 统计
                total_items += len(results)
                total_success += 1

                # 置信度统计
                confs = [r.get("confidence", 0) for r in results]
                avg_conf = sum(confs) / len(confs) if confs else 0
                high = sum(1 for c in confs if c >= 85)
                low = sum(1 for c in confs if c < 60)

                print(f"{len(items)}条 | {elapsed:.1f}s | 绿{high} 红{low} 均{avg_conf:.0f}%")

            except Exception as e:
                elapsed = time.time() - start_time
                print(f"错误: {e}")
                _mark_file_error(f, str(e))
                total_errors += 1

    # 汇总
    print(f"\n{'='*50}")
    print(f"批量匹配完成")
    print(f"  成功: {total_success} 文件 | 错误: {total_errors} 文件")
    print(f"  清单总条数: {total_items}")
    print(f"{'='*50}")


# ============================================================
# 省份解析（省份名 → 定额库代码）
# ============================================================

def _resolve_province(province_name: str) -> str:
    """把省份名转成定额库目录名（如 "广东" → "广东2018"）。

    查找 db/provinces/ 下是否有对应目录。
    """
    provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
    if not provinces_dir.exists():
        return None

    # 精确匹配
    for d in provinces_dir.iterdir():
        if d.is_dir() and d.name.startswith(province_name):
            return d.name

    return None


# ============================================================
# 结果保存和状态更新
# ============================================================

def _save_results(file_info, results: list, elapsed: float):
    """保存匹配结果到 JSON 文件。"""
    prov = file_info["province"] or "未知省份"
    prov_dir = RESULTS_DIR / prov
    prov_dir.mkdir(parents=True, exist_ok=True)

    # 文件名用原文件名（去掉扩展名）+ .json
    base_name = Path(file_info["file_name"]).stem
    result_path = prov_dir / f"{base_name}.json"

    # 简化结果（只保留关键字段，减少文件大小）
    simplified = []
    for r in results:
        simplified.append({
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "matched_quota_id": r.get("matched_quota_id", ""),
            "matched_quota_name": r.get("matched_quota_name", ""),
            "confidence": r.get("confidence", 0),
            "match_source": r.get("match_source", ""),
        })

    data = {
        "file_path": file_info["file_path"],
        "province": prov,
        "specialty": file_info["specialty"],
        "format": file_info["format"],
        "total_items": len(results),
        "elapsed_seconds": round(elapsed, 1),
        "match_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "algo_version": ALGORITHM_VERSION,
        "results": simplified,
    }

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mark_file_matched(file_info, results: list, elapsed: float):
    """更新数据库状态为 matched。"""
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            UPDATE file_registry
            SET status = 'matched',
                match_time = ?,
                algo_version = ?,
                updated_at = ?
            WHERE file_path = ?
        """, (now, ALGORITHM_VERSION, now, file_info["file_path"]))
        conn.commit()
    finally:
        conn.close()


def _mark_file_error(file_info, error_msg: str):
    """更新数据库状态为 error。"""
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            UPDATE file_registry
            SET status = 'error',
                error_msg = ?,
                updated_at = ?
            WHERE file_path = ?
        """, (error_msg, now, file_info["file_path"]))
        conn.commit()
    finally:
        conn.close()


def _mark_files_error(files: list, error_msg: str):
    """批量标记文件为 error。"""
    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for f in files:
            conn.execute("""
                UPDATE file_registry
                SET status = 'error',
                    error_msg = ?,
                    updated_at = ?
                WHERE file_path = ?
            """, (error_msg, now, f["file_path"]))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="批量匹配执行器")
    parser.add_argument("--format", choices=["standard_bill", "work_list", "equipment_list"],
                        help="只跑某种格式")
    parser.add_argument("--province", help="只跑某省（如 广东）")
    parser.add_argument("--specialty", help="只跑某专业（如 消防）")
    parser.add_argument("--limit", type=int, help="只跑前N个文件")

    args = parser.parse_args()

    run_batch(
        format_filter=args.format,
        province_filter=args.province,
        specialty_filter=args.specialty,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
