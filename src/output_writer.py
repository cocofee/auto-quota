"""
结果输出模块
功能：
1. 读取原始清单Excel，保留完整结构（表头、分部、小节标题）
2. 在每条清单行下面插入匹配到的子目行（定额行）
3. 跳过原始文件中已有的定额行（用我们的匹配结果替换）
4. 格式匹配广联达标准，可直接导入

广联达识别规则：
  - A列有序号(数字) → 清单行
  - A列为空，B列有C开头编号 → 子目行（定额行）
  - A列和B列都为空，C列有文字 → 分节行
"""

import re
import sys
import shutil
import os
import uuid
import tempfile
from pathlib import Path
from datetime import datetime

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from loguru import logger

import config


# 颜色定义
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
YELLOW_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
RED_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
GRAY_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
LIGHT_BLUE_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")

# 字体（和广联达一致：宋体9号，全表统一）
HEADER_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
BILL_FONT = Font(name="宋体", size=9)
# 定额行字体：宋体9号，和广联达导出格式一致
GLD_FONT = Font(name="宋体", size=9)

# 边框
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)

# 标准列宽（参考广联达格式，配合 wrap_text 使用）
STANDARD_COL_WIDTHS = {
    "A": 5,     # 序号
    "B": 13,    # 项目编码/定额编号
    "C": 20,    # 项目名称
    "D": 30,    # 项目特征描述/定额名称
    "E": 6,     # 计量单位
    "F": 10,    # 工程量
    "G": 10,    # 综合单价
    "H": 10,    # 合价
    "I": 10,    # 暂估价
}


# ================================================================
# 单位换算（清单单位 ≠ 定额单位时，自动转换工程量）
# ================================================================

# 单位换算系数表：(清单单位, 定额单位) → 乘以系数
UNIT_CONVERSIONS = {}


def _build_unit_conversions():
    """构建双向单位换算表"""
    # (单位A, 单位B, A→B的系数)
    pairs = [
        ("t", "kg", 1000),           # 吨 → 千克
        ("kg", "t", 0.001),          # 千克 → 吨
        ("km", "m", 1000),           # 千米 → 米
        ("m", "km", 0.001),          # 米 → 千米
        ("100m", "m", 100),          # 百米 → 米
        ("m", "100m", 0.01),         # 米 → 百米
        ("m²", "100m²", 0.01),       # 平方米 → 百平方米
        ("100m²", "m²", 100),        # 百平方米 → 平方米
        ("m³", "10m³", 0.1),         # 立方米 → 10立方米
        ("10m³", "m³", 10),          # 10立方米 → 立方米
        ("m³", "100m³", 0.01),       # 立方米 → 百立方米
        ("100m³", "m³", 100),        # 百立方米 → 立方米
    ]
    for u1, u2, factor in pairs:
        UNIT_CONVERSIONS[(u1, u2)] = factor


_build_unit_conversions()


def _safe_confidence(value, default: int = 0) -> int:
    """把置信度安全转换为0-100整数。"""
    try:
        conf = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, conf))


def _ensure_list(value) -> list:
    """把任意输入收敛为list，避免脏类型触发导出异常。"""
    return value if isinstance(value, list) else []


def _safe_write_cell(ws, row: int, column: int, value=None):
    """安全写入单元格值，遇到合并单元格时跳过。

    当目标格是MergedCell（合并区域中非左上主格）时，openpyxl禁止写入
    并抛 AttributeError。本函数检测到MergedCell后静默跳过，避免整次输出崩溃。

    返回:
        cell对象（可继续设置样式），或 None（合并单元格，已跳过）
    """
    cell = ws.cell(row=row, column=column)
    if isinstance(cell, MergedCell):
        return None
    if value is not None:
        cell.value = value
    return cell


def _is_bill_serial(a_val) -> bool:
    """判断A列是否为清单序号（兼容int/float/字符串）。"""
    if a_val is None:
        return False
    if isinstance(a_val, int):
        return a_val > 0
    if isinstance(a_val, float):
        return a_val > 0 and a_val.is_integer()
    text = str(a_val).strip()
    if not text:
        return False
    if text.isdigit():
        return True
    return bool(re.fullmatch(r"\d+\.0+", text))


def _is_quota_code(code: str) -> bool:
    """判断是否是定额编号格式（支持 X-XXX / D00003 / 带'换'后缀）。"""
    if not isinstance(code, str):
        return False
    c = code.strip()
    if not c:
        return False
    core = c[:-1] if c.endswith("换") else c
    return bool(re.match(r'^[A-Za-z]?\d{1,2}-\d+', core)) or bool(re.match(r'^[A-Za-z]{1,2}\d{4,}$', core))


def convert_quantity(bill_qty, bill_unit: str, quota_unit: str):
    """
    单位换算：当清单单位和定额单位不同时，转换工程量

    例如：清单 5t → 定额 kg → 返回 5000
    大多数情况单位相同，直接返回原值。
    """
    if bill_qty is None:
        return 0

    # 归一化工程量为数值，避免字符串数量在换算时触发类型错误
    qty = bill_qty
    if isinstance(qty, str):
        q = qty.strip().replace(",", "")
        if q == "":
            return 0
        try:
            qty = float(q)
        except ValueError:
            return bill_qty
    elif not isinstance(qty, (int, float)):
        try:
            qty = float(qty)
        except Exception:
            return bill_qty

    if not bill_unit or not quota_unit:
        return qty

    # 标准化单位文本（统一小写，处理特殊Unicode字符）
    bu = bill_unit.strip().lower().replace("㎡", "m²").replace("㎥", "m³")
    qu = quota_unit.strip().lower().replace("㎡", "m²").replace("㎥", "m³")

    if bu == qu:
        return qty

    factor = UNIT_CONVERSIONS.get((bu, qu))
    if factor:
        converted = round(qty * factor, 4)
        logger.debug(f"单位换算: {qty}{bill_unit} → {converted}{quota_unit} (×{factor})")
        return converted

    # 没有找到换算关系，原样返回（大多数情况单位相同）
    return qty


# ================================================================
# 置信度显示
# ================================================================

def confidence_to_stars(confidence: int, has_quotas: bool) -> str:
    """
    把置信度百分比转换为星级推荐展示

    规则：
    - ≥85% → ★★★推荐(95%)  绿色，基本可信
    - 60-84% → ★★参考(72%)  黄色，建议人工确认
    - <60% → ★待审(45%)     红色，需要人工处理
    - 无匹配 → —

    参数:
        confidence: 置信度百分比（0-100）
        has_quotas: 是否有匹配到定额

    返回:
        星级文字（如 "★★★推荐(85%)"）
    """
    if not has_quotas:
        return "—"
    if confidence >= config.CONFIDENCE_GREEN:
        return f"★★★推荐({confidence}%)"
    elif confidence >= config.CONFIDENCE_YELLOW:
        return f"★★参考({confidence}%)"
    else:
        return f"★待审({confidence}%)"


def safe_excel_text(value):
    """防止Excel公式注入：文本以 = + - @ 开头时前置单引号。"""
    if value is None:
        return value
    if not isinstance(value, str):
        return value
    text = value.replace("\x00", "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class OutputWriter:
    """匹配结果Excel输出，保留原始清单结构"""

    @staticmethod
    def _convert_xls_for_output(xls_path: str, output_xlsx_path: str):
        """将 .xls 文件转换为 .xlsx 格式（用于输出保留结构）"""
        import xlrd
        xls_wb = xlrd.open_workbook(str(xls_path))
        xlsx_wb = openpyxl.Workbook()
        xlsx_wb.remove(xlsx_wb.active)
        for sheet_idx in range(xls_wb.nsheets):
            xls_sheet = xls_wb.sheet_by_index(sheet_idx)
            xlsx_sheet = xlsx_wb.create_sheet(title=xls_sheet.name)
            for row_idx in range(xls_sheet.nrows):
                for col_idx in range(xls_sheet.ncols):
                    cell = xls_sheet.cell(row_idx, col_idx)
                    value = cell.value
                    if cell.ctype == 3:  # 日期类型
                        try:
                            value = xlrd.xldate_as_datetime(value, xls_wb.datemode)
                        except Exception:
                            pass
                    if value is not None and value != "":
                        xlsx_sheet.cell(row=row_idx + 1, column=col_idx + 1, value=value)
        xlsx_wb.save(str(output_xlsx_path))
        xls_wb.release_resources()

    @staticmethod
    def _save_workbook_atomic(wb, output_path: str):
        """原子写入Excel，避免中断时留下半成品文件。"""
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".xlsx",
                prefix=f"{out_path.stem}_tmp_",
                dir=str(out_path.parent),
                delete=False,
            ) as tf:
                tmp_path = tf.name
            wb.save(tmp_path)
            os.replace(tmp_path, out_path)
        finally:
            if tmp_path and Path(tmp_path).exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _confidence_fill(confidence: float):
        if confidence >= config.CONFIDENCE_GREEN:
            return GREEN_FILL
        if confidence >= config.CONFIDENCE_YELLOW:
            return YELLOW_FILL
        return RED_FILL

    @staticmethod
    def _write_alternative_cells(ws, row_idx: int, start_col: int, alternatives):
        for alt_idx, alt in enumerate(_ensure_list(alternatives)[:3]):
            alt_col = start_col + alt_idx
            alt_text = safe_excel_text(f"{alt.get('quota_id', '')} {alt.get('name', '')}")
            cell_alt = _safe_write_cell(ws, row_idx, alt_col, alt_text)
            if cell_alt:
                cell_alt.font = BILL_FONT  # 和正文统一字体
                cell_alt.border = THIN_BORDER

    @staticmethod
    def _brief_explanation(explanation: str) -> str:
        return safe_excel_text(explanation[:80] if explanation else "")

    @staticmethod
    def _check_review_needed(confidence: int, quotas: list,
                             match_source: str) -> bool:
        """判断该条清单是否需要人工复核

        标记条件（任一命中即标记）：
        - 无匹配结果（定额为空）
        - 置信度低于85%（★★参考 或 ★待审）
        - 降级结果（agent_fallback / agent_error）
        - 无经验库命中的纯搜索结果（首次出现的清单写法）
        """
        if not quotas:
            return True
        if confidence < config.CONFIDENCE_GREEN:
            return True
        if match_source in ("agent_fallback", "agent_error"):
            return True
        return False

    @staticmethod
    def _brief_materials(result: dict, max_items: int = 4) -> str:
        """把结果中的主材列表压缩成可读短文本。"""
        materials = result.get("materials")
        if not isinstance(materials, list) or not materials:
            return ""

        parts = []
        for mat in materials:
            if not isinstance(mat, dict):
                continue
            name = str(mat.get("name", "")).strip()
            if not name:
                continue
            unit = str(mat.get("unit", "")).strip()
            price = mat.get("price", None)
            if price is None:
                text = f"{name}({unit})" if unit else name
            else:
                try:
                    p = f"{float(price):g}"
                except Exception:
                    p = str(price)
                text = f"{name}({p}元/{unit})" if unit else f"{name}({p}元)"
            parts.append(text)
            if len(parts) >= max_items:
                break
        if not parts:
            return ""
        more = len(materials) - len(parts)
        suffix = f" 等{len(materials)}项" if more > 0 else ""
        return safe_excel_text("; ".join(parts) + suffix)

    @staticmethod
    def _write_no_match_row(ws, row_idx: int, no_reason: str, max_col: int):
        _safe_write_cell(ws, row_idx, 3, safe_excel_text(f"未匹配: {no_reason}"))
        for col in range(1, max_col + 1):
            cell = ws.cell(row=row_idx, column=col)
            if isinstance(cell, MergedCell):
                continue
            cell.font = Font(name="宋体", size=9, color="FF0000")
            cell.fill = RED_FILL
            cell.border = THIN_BORDER

    @staticmethod
    def _apply_row_style(ws, row_idx: int, start_col: int, end_col: int, wrap_cols: set[int]):
        for col_idx in range(start_col, end_col + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            if isinstance(cell, MergedCell):
                continue
            if cell.font == Font():
                cell.font = BILL_FONT
            cell.border = THIN_BORDER
            cell.alignment = Alignment(
                vertical="center",
                wrap_text=(col_idx in wrap_cols),
            )

    @staticmethod
    def _set_header_cell(ws, row_idx: int, col_idx: int, value, fill):
        cell = _safe_write_cell(ws, row_idx, col_idx, value)
        if cell is None:
            return None  # 合并单元格，跳过
        cell.font = HEADER_FONT
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
        return cell

    def write_results(self, results: list[dict], output_path: str = None,
                      original_file: str = None) -> str:
        """
        将匹配结果写入Excel

        策略：
        - 有原始文件 → 保留原始结构（复制原文件，在每个Sheet的清单行下插入定额行）
        - 无原始文件 → 新建工作簿（兜底模式）

        关键约束：
        - 原始Excel有几个Sheet输出就有几个Sheet，不能删减
        - 清单行的顺序不能改变（改变顺序 = 废标）
        - 每个Sheet独立处理，不跨Sheet合并
        """
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(
                config.OUTPUT_DIR / f"匹配结果_{timestamp}_{uuid.uuid4().hex[:6]}.xlsx"
            )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if original_file and Path(original_file).exists():
            # 主模式：保留原始文件的完整结构
            return self._write_preserve_structure(results, output_path, original_file)
        else:
            # 兜底模式：新建工作簿
            return self._write_new_workbook(results, output_path)

    # ================================================================
    # 主模式：保留原始文件完整结构
    # ================================================================

    def _write_preserve_structure(self, results, output_path, original_file):
        """
        保留原始Excel完整结构，在每个Sheet的清单行下方插入定额行

        逻辑：
        1. 复制原始文件到输出路径（保留所有Sheet、格式、合并单元格）
        2. 按sheet_name分组匹配结果
        3. 对每个有匹配结果的Sheet，在清单行下方插入定额行
        4. 没有匹配结果的Sheet原样保留不动
        5. 追加"待审核"和"统计汇总"Sheet
        """
        # 按sheet_name分组结果（保持组内顺序）
        results_by_sheet = {}
        for r in results:
            sheet = r.get("bill_item", {}).get("sheet_name", "")
            if sheet:
                results_by_sheet.setdefault(sheet, []).append(r)

        # 复制原始文件（保留所有格式和结构）
        # .xls 文件需要先转换为 .xlsx（openpyxl 不支持旧格式）
        if Path(original_file).suffix.lower() == ".xls":
            self._convert_xls_for_output(original_file, output_path)
        else:
            shutil.copy2(original_file, output_path)

        # 打开副本进行修改
        wb = openpyxl.load_workbook(output_path)
        try:
            # 逐个处理有匹配结果的Sheet
            processed_sheets = 0
            for sheet_name in wb.sheetnames:
                if sheet_name not in results_by_sheet:
                    continue  # 不是清单Sheet，原样保留
                ws = wb[sheet_name]
                sheet_results = results_by_sheet[sheet_name]
                self._process_bill_sheet(ws, sheet_results)
                processed_sheets += 1

            # 追加辅助Sheet（追加到最后，不影响原有Sheet顺序）
            ws_review = wb.create_sheet("待审核")
            self._write_review_sheet(ws_review, results)

            ws_stats = wb.create_sheet("统计汇总")
            self._write_stats_sheet(ws_stats, results)

            self._save_workbook_atomic(wb, output_path)
        finally:
            wb.close()
        logger.info(
            f"匹配结果已保存（保留原始{processed_sheets}个清单Sheet，"
            f"共{len(wb.sheetnames)}个Sheet）: {output_path}")
        return output_path

    def _process_bill_sheet(self, ws, results: list[dict]):
        """
        处理单个Sheet：在清单行下方插入定额行

        处理步骤：
        1. 找到表头行
        2. 删除已有的定额行（如果有的话）
        3. 重新扫描找到所有清单行
        4. 从下往上插入定额行（避免行号偏移）
        5. 添加推荐度/备选列标题
        """
        # 找表头行
        header_row = self._find_header_row_in_ws(ws)

        # 第1步：删除已有定额行（如果原文件中有旧的定额行）
        self._remove_existing_quota_rows(ws, header_row)

        # 第2步：扫描所有清单行（A列是纯数字序号的行）
        bill_rows = []
        for row_idx in range(header_row + 1, ws.max_row + 1):
            a_val = ws.cell(row=row_idx, column=1).value
            if _is_bill_serial(a_val):
                bill_rows.append(row_idx)

        # 第3步：构建“清单行 -> 结果”映射
        # 优先使用 sheet_bill_seq 精准回写（支持 filter-code/limit 等子集输出）
        row_result_pairs = []
        used_seq = set()
        can_use_seq_map = True
        for result in results:
            bill_item = result.get("bill_item", {})
            seq = bill_item.get("sheet_bill_seq")
            if not isinstance(seq, int):
                can_use_seq_map = False
                break
            if seq <= 0 or seq > len(bill_rows) or seq in used_seq:
                can_use_seq_map = False
                break
            used_seq.add(seq)
            row_result_pairs.append((bill_rows[seq - 1], result))

        # 兼容旧结果格式：无序号映射时退回 bill_code 匹配 → 顺序匹配
        if not can_use_seq_map:
            # 回退方案A：按 bill_code（12位清单编码）精准匹配
            code_pairs = []
            if results:
                # 构建 Excel 中 {B列编码: row_idx} 映射
                row_code_map = {}
                for row_idx in bill_rows:
                    b_val = ws.cell(row=row_idx, column=2).value
                    if b_val:
                        b_str = str(b_val).strip()
                        if b_str not in row_code_map:  # 同编码取第一个行
                            row_code_map[b_str] = row_idx

                used_rows = set()
                for result in results:
                    bill_code = result.get("bill_item", {}).get("code", "")
                    if bill_code and bill_code in row_code_map:
                        target_row = row_code_map[bill_code]
                        if target_row not in used_rows:
                            code_pairs.append((target_row, result))
                            used_rows.add(target_row)

            if code_pairs:
                row_result_pairs = code_pairs
                unmatched_count = len(results) - len(code_pairs)
                if unmatched_count > 0:
                    logger.warning(
                        f"Sheet [{ws.title}]: bill_code匹配模式下 "
                        f"{unmatched_count}/{len(results)}条结果无法映射到Excel行"
                        f"（bill_code在Excel中找不到对应行），这些结果将不会回写")
                else:
                    logger.info(
                        f"Sheet [{ws.title}]: sheet_bill_seq不可用，"
                        f"改用bill_code精准匹配 ({len(code_pairs)}/{len(bill_rows)})")
            else:
                # 回退方案B：顺序匹配（最后兜底）
                if len(bill_rows) != len(results):
                    logger.warning(
                        f"Sheet [{ws.title}]: 清单行数({len(bill_rows)}) != "
                        f"结果数({len(results)}), 且缺少可用定位信息，按顺序匹配")
                num_to_process = min(len(bill_rows), len(results))
                row_result_pairs = [
                    (bill_rows[i], results[i]) for i in range(num_to_process)
                ]
        elif len(bill_rows) != len(results):
            logger.info(
                f"Sheet [{ws.title}]: 结果为清单子集，按sheet_bill_seq精准回写 "
                f"({len(results)}/{len(bill_rows)})")

        # 第3.5步：保存原始清单行高度
        # insert_rows 不会移动 row_dimensions 的键，所以需要手动保存/恢复
        # 用 A 列序号作 key，插入后根据序号找到新行号再恢复
        original_bill_heights = {}
        for row_idx in bill_rows:
            h = ws.row_dimensions[row_idx].height
            a_val = ws.cell(row=row_idx, column=1).value
            if h and a_val is not None:
                original_bill_heights[str(a_val).strip()] = h

        # 第4步：从下往上插入定额行（避免插行导致行号偏移）
        for row_idx, result in sorted(row_result_pairs, key=lambda x: x[0], reverse=True):
            quotas = _ensure_list(result.get("quotas", []))

            # 在清单行的J-O列写入推荐度、备选和主材
            self._write_bill_extra_info(ws, row_idx, result)

            # 要插入的行数（至少1行用于未匹配提示）
            num_insert = max(len(quotas), 1)

            # 插入空行（在清单行的下一行位置）
            ws.insert_rows(row_idx + 1, amount=num_insert)

            # 写入定额数据
            bill_unit = result.get("bill_item", {}).get("unit", "")
            bill_qty = result.get("bill_item", {}).get("quantity")

            if quotas:
                for q_idx, quota in enumerate(quotas):
                    q_row = row_idx + 1 + q_idx
                    self._write_single_quota_row(
                        ws, q_row, quota, bill_unit, bill_qty)
            else:
                # 未匹配提示行
                q_row = row_idx + 1
                no_reason = result.get("no_match_reason", "未找到匹配定额")
                self._write_no_match_row(ws, q_row, no_reason, 9)

        # 第4.5步：恢复清单行原始行高（insert_rows 不移动 row_dimensions 键）
        for row_idx in range(header_row + 1, ws.max_row + 1):
            a_val = ws.cell(row=row_idx, column=1).value
            if _is_bill_serial(a_val):
                saved_h = original_bill_heights.get(str(a_val).strip())
                if saved_h:
                    ws.row_dimensions[row_idx].height = saved_h

        # 第5步：在表头行添加J-O列标题
        self._add_extra_headers(ws, header_row)

        # 第6步：统一格式化所有行（固定列宽 + 字体 + 边框 + 换行）
        self._apply_post_format(ws, header_row)

        logger.info(f"Sheet [{ws.title}]: 处理 {len(row_result_pairs)} 条清单项")

    def _find_header_row_in_ws(self, ws) -> int:
        """在worksheet中找到表头行（包含'项目编码''项目名称'等关键词的行）"""
        bill_keywords = ["项目编码", "项目名称", "计量单位", "工程量"]

        for row_idx in range(1, min(ws.max_row + 1, 21)):
            row_text = ""
            for col_idx in range(1, min(ws.max_column + 1, 20)):
                val = ws.cell(row=row_idx, column=col_idx).value
                if val is not None:
                    row_text += str(val).strip().replace("\n", "") + " "
            matched = sum(1 for kw in bill_keywords if kw in row_text)
            if matched >= 2:
                return row_idx

        return 1  # 默认第1行

    def _remove_existing_quota_rows(self, ws, header_row: int):
        """删除已有的定额行（从下往上删，避免行号偏移）"""
        existing_quota_rows = []

        for row_idx in range(header_row + 1, ws.max_row + 1):
            a_val = ws.cell(row=row_idx, column=1).value
            b_val = ws.cell(row=row_idx, column=2).value

            # 已有定额行：A列为空，B列是定额编号格式（如C4-4-31、5-325）
            if (a_val is None or str(a_val).strip() == "") and b_val:
                b_str = str(b_val).strip()
                if _is_quota_code(b_str):
                    existing_quota_rows.append(row_idx)

        # 从下往上删除
        for row_idx in reversed(existing_quota_rows):
            ws.delete_rows(row_idx, 1)

        if existing_quota_rows:
            logger.info(f"Sheet [{ws.title}]: 删除 {len(existing_quota_rows)} 条已有定额行")

    def _write_bill_extra_info(self, ws, row_idx: int, result: dict):
        """在清单行的J-O列写入推荐度、匹配说明、备选定额和主材"""
        confidence = _safe_confidence(result.get("confidence", 0), default=0)
        quotas = _ensure_list(result.get("quotas", []))
        explanation = result.get("explanation", "")
        match_source = result.get("match_source", "")

        # 判断是否需要复核（任一条件命中即标记）
        review_needed = self._check_review_needed(confidence, quotas, match_source)

        # 置信度颜色
        conf_fill = self._confidence_fill(confidence)

        # J列：星级推荐（安全写入，跳过合并单元格）
        conf_text = confidence_to_stars(confidence, bool(quotas))
        cell_j = _safe_write_cell(ws, row_idx, 10, conf_text)
        if cell_j:
            cell_j.font = BILL_FONT
            cell_j.border = THIN_BORDER
            if quotas:
                cell_j.fill = conf_fill

        # K列：匹配说明（L4请教/同类待定/需复核 前缀标记）
        brief = self._brief_explanation(explanation)
        if result.get("l4_representative"):
            # L4代表项：用户只需改这条，同类自动学习
            group_label = result.get("l4_group_label", "")
            group_size = result.get("l4_group_size", 0)
            brief = f"[请教] {group_label}类{group_size}条不确定，请修正此条（同类自动学习）"
        elif result.get("l4_follower"):
            brief = f"[同类待定] {brief}" if brief else "[同类待定]"
        elif review_needed:
            brief = f"[需复核] {brief}" if brief else "[需复核]"
        cell_k = _safe_write_cell(ws, row_idx, 11, brief)
        if cell_k:
            cell_k.font = BILL_FONT
            cell_k.border = THIN_BORDER

        # L/M/N列：备选定额
        self._write_alternative_cells(
            ws, row_idx, start_col=12, alternatives=result.get("alternatives", [])
        )

        # O列：主材
        material_text = self._brief_materials(result)
        cell_o = _safe_write_cell(ws, row_idx, 15, material_text)
        if cell_o:
            cell_o.font = BILL_FONT
            cell_o.border = THIN_BORDER
            cell_o.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    def _write_quota_rows(self, ws, current_row: int, result: dict,
                          bill_unit: str, bill_qty, max_col: int) -> int:
        """写入一条清单对应的所有定额行（兜底新建模式用）

        参数:
            ws: worksheet对象
            current_row: 当前写入的行号
            result: 匹配结果字典
            bill_unit: 清单单位
            bill_qty: 清单工程量
            max_col: 最大列数（用于格式化）

        返回:
            写入后的下一个可用行号
        """
        quotas = _ensure_list(result.get("quotas", []))

        if quotas:
            for quota in quotas:
                self._write_single_quota_row(
                    ws, current_row, quota, bill_unit, bill_qty)
                current_row += 1
        else:
            # 未匹配提示行
            no_reason = result.get("no_match_reason", "未找到匹配定额")
            self._write_no_match_row(ws, current_row, no_reason, max_col)
            current_row += 1

        return current_row

    def _write_single_quota_row(self, ws, q_row: int, quota: dict,
                                bill_unit: str, bill_qty):
        """写入一行定额数据（广联达标准格式：宋体9号、thin边框、无背景、不合并）"""
        # A列留空（广联达靠这个区分清单行和子目行）

        # B列：定额编号（居中）
        cell_b = ws.cell(
            row=q_row, column=2, value=safe_excel_text(quota.get("quota_id", ""))
        )
        cell_b.font = GLD_FONT
        cell_b.alignment = Alignment(horizontal="center", vertical="center",
                                     wrap_text=True)

        # C列：定额名称（左对齐，自动换行）
        cell_c = ws.cell(
            row=q_row, column=3, value=safe_excel_text(quota.get("name", ""))
        )
        cell_c.font = GLD_FONT
        cell_c.alignment = Alignment(horizontal="left", vertical="center",
                                     wrap_text=True)

        # E列：单位（居中）
        quota_unit = quota.get("unit", "") or bill_unit
        cell_e = ws.cell(row=q_row, column=5, value=quota_unit)
        cell_e.font = GLD_FONT
        cell_e.alignment = Alignment(horizontal="center", vertical="center",
                                     wrap_text=True)

        # F列：工程量（右对齐，自动单位换算）
        converted_qty = convert_quantity(bill_qty, bill_unit, quota_unit)
        cell_f = ws.cell(row=q_row, column=6, value=converted_qty)
        cell_f.font = GLD_FONT
        cell_f.alignment = Alignment(horizontal="right", vertical="center",
                                     wrap_text=True)

        # 所有列统一 thin 边框 + 宋体9号（和清单行一致）
        for col in range(1, 10):  # A-I列
            cell = ws.cell(row=q_row, column=col)
            cell.border = THIN_BORDER
            if cell.font == Font() or cell.font is None:
                cell.font = GLD_FONT
            if cell.alignment is None or cell.alignment == Alignment():
                cell.alignment = Alignment(vertical="center", wrap_text=True)

    def _add_extra_headers(self, ws, header_row: int):
        """在表头行添加J-O列标题"""
        extra_headers = {
            10: ("推荐度", HEADER_FILL),
            11: ("匹配说明", HEADER_FILL),
            12: ("备选1", LIGHT_BLUE_FILL),
            13: ("备选2", LIGHT_BLUE_FILL),
            14: ("备选3", LIGHT_BLUE_FILL),
            15: ("主材", LIGHT_BLUE_FILL),
        }
        for col, (title, fill) in extra_headers.items():
            self._set_header_cell(ws, header_row, col, title, fill)

        # 设置额外列宽
        ws.column_dimensions["J"].width = 14
        ws.column_dimensions["K"].width = 40
        ws.column_dimensions["L"].width = 30
        ws.column_dimensions["M"].width = 30
        ws.column_dimensions["N"].width = 30
        ws.column_dimensions["O"].width = 36

    def _apply_post_format(self, ws, header_row: int):
        """
        格式化清单行和定额行（不动分部/合计等原始行，保留其原始格式）

        解决的问题：
        - openpyxl 加载后清单行可能丢失字体/边框/对齐
        - insert_rows 新插入的定额行没有格式
        - 统一设 wrap_text=True 让文字自动换行
        - 设固定列宽让表格宽度一致
        """
        # 1. 设固定列宽（A-I列）
        for col, width in STANDARD_COL_WIDTHS.items():
            ws.column_dimensions[col].width = width

        # 2. 只格式化清单行和定额行，跳过分部/合计等原始行
        for row_idx in range(header_row + 1, ws.max_row + 1):
            a_val = ws.cell(row=row_idx, column=1).value
            b_val = ws.cell(row=row_idx, column=2).value
            is_bill = _is_bill_serial(a_val)
            is_quota = (
                (a_val is None or str(a_val).strip() == "")
                and b_val
                and _is_quota_code(str(b_val).strip())
            )

            if not is_bill and not is_quota:
                continue  # 分部/合计/空行等，保留原始格式不动

            for col_idx in range(1, 16):  # A-O 列（1-15）
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell, MergedCell):
                    continue  # 合并单元格跳过，避免写样式崩溃

                # 边框：统一设 thin 边框
                cell.border = THIN_BORDER

                # 字体：如果丢失或变成默认 Calibri 就覆盖为宋体9号
                if not cell.font or cell.font.name in (None, "Calibri"):
                    cell.font = GLD_FONT

                # 对齐 + wrap_text：根据列确定对齐方式
                h_align = "center"
                if col_idx in (3, 4, 11, 12, 13, 14, 15):  # C/D/K/L/M/N/O：左对齐
                    h_align = "left"
                elif col_idx in (6, 7, 8):  # F/G/H列（数量/单价/合价）：右对齐
                    h_align = "right"
                cell.alignment = Alignment(
                    horizontal=h_align, vertical="center", wrap_text=True
                )

    # ================================================================
    # 兜底模式：新建工作簿（无原始文件时使用）
    # ================================================================

    def _write_new_workbook(self, results, output_path):
        """无原始文件时，新建工作簿输出"""
        wb = openpyxl.Workbook()
        try:
            self._write_detail_sheet(wb.active, results)
            wb.active.title = "匹配结果明细"

            ws_review = wb.create_sheet("待审核")
            self._write_review_sheet(ws_review, results)

            ws_stats = wb.create_sheet("统计汇总")
            self._write_stats_sheet(ws_stats, results)

            self._save_workbook_atomic(wb, output_path)
        finally:
            wb.close()
        logger.info(f"匹配结果已保存（新建模式）: {output_path}")
        return output_path

    def _write_detail_sheet(self, ws, results: list[dict]):
        """纯新建模式：不依赖原始文件，直接生成标准格式"""
        col_widths = {"A": 6, "B": 16, "C": 50, "D": 40, "E": 8,
                      "F": 12, "G": 12, "H": 12, "I": 12, "J": 14, "K": 20,
                      "L": 18, "M": 18, "N": 18, "O": 20}
        for col, width in col_widths.items():
            ws.column_dimensions[col].width = width

        # 行1表头
        headers = {1: "序号", 2: "项目编码", 3: "项目名称", 4: "项目特征描述",
                   5: "计量单位", 6: "工程量", 7: "金额（元）", 10: "推荐度",
                   11: "匹配说明", 12: "备选1", 13: "备选2", 14: "备选3", 15: "主材"}
        for col_idx, header in headers.items():
            fill = LIGHT_BLUE_FILL if col_idx >= 12 else HEADER_FILL
            self._set_header_cell(ws, 1, col_idx, header, fill)
        for col_idx in [8, 9]:
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = HEADER_FILL
            cell.border = THIN_BORDER
        ws.merge_cells("G1:I1")

        # 行2子表头
        for col_idx, header in {7: "综合单价", 8: "合价", 9: "其中：暂估价"}.items():
            self._set_header_cell(ws, 2, col_idx, header, HEADER_FILL)
        for col_idx in list(range(1, 7)) + [10, 11]:
            cell = ws.cell(row=2, column=col_idx)
            cell.fill = HEADER_FILL
            cell.border = THIN_BORDER

        # 数据行
        current_row = 3
        for idx, result in enumerate(results, start=1):
            bill = result.get("bill_item", {})
            confidence = _safe_confidence(result.get("confidence", 0), default=0)
            quotas = _ensure_list(result.get("quotas", []))
            explanation = result.get("explanation", "")
            bill_quantity = bill.get("quantity", "")
            bill_unit = bill.get("unit", "")

            conf_fill = self._confidence_fill(confidence)

            # 清单行
            ws.cell(row=current_row, column=1, value=idx)
            ws.cell(row=current_row, column=2, value=safe_excel_text(bill.get("code", "")))
            ws.cell(row=current_row, column=3, value=safe_excel_text(bill.get("name", "")))
            ws.cell(row=current_row, column=4, value=safe_excel_text(bill.get("description", "")))
            ws.cell(row=current_row, column=5, value=bill_unit)
            ws.cell(row=current_row, column=6, value=bill_quantity)
            # J列：星级推荐
            conf_text = confidence_to_stars(confidence, bool(quotas))
            ws.cell(row=current_row, column=10, value=conf_text)
            ws.cell(
                row=current_row,
                column=11,
                value=self._brief_explanation(explanation)
            )

            # L/M/N列：备选定额
            self._write_alternative_cells(
                ws, current_row, start_col=12, alternatives=result.get("alternatives", [])
            )
            ws.cell(row=current_row, column=15, value=self._brief_materials(result))

            self._apply_row_style(ws, current_row, 1, 15, {4, 15})
            if quotas:
                ws.cell(row=current_row, column=10).fill = conf_fill
            current_row += 1

            # 子目行
            current_row = self._write_quota_rows(
                ws, current_row, result, bill_unit, bill_quantity, 9)

        ws.freeze_panes = "A3"

    def _write_review_sheet(self, ws, results: list[dict]):
        """
        写入"待审核"Sheet：只列出黄色和红色的清单项

        方便用户快速定位需要修改的条目，不需要翻看整个匹配结果。
        包含：序号、清单名称、当前定额、推荐度、问题说明、备选1/2/3
        """
        # 列宽（加了项目特征列，整体后移一列）
        col_widths = {"A": 8, "B": 30, "C": 40, "D": 20, "E": 30, "F": 14,
                      "G": 24, "H": 18, "I": 18, "J": 18, "K": 20}
        for col, width in col_widths.items():
            ws.column_dimensions[col].width = width

        # 表头
        headers = ["清单序号", "清单名称", "项目特征", "当前定额编号", "当前定额名称",
                   "推荐度", "问题说明", "备选1", "备选2", "备选3", "主材"]
        for col_idx, header in enumerate(headers, start=1):
            self._set_header_cell(ws, 1, col_idx, header, HEADER_FILL)

        # 筛选出置信度 < CONFIDENCE_GREEN（85%）的条目
        current_row = 2
        review_count = 0
        follower_count = 0  # L4从属项计数（不逐条显示，末尾汇总）

        for idx, result in enumerate(results, start=1):
            confidence = _safe_confidence(result.get("confidence", 0), default=0)
            quotas = _ensure_list(result.get("quotas", []))
            match_source = result.get("match_source", "")
            review_needed = self._check_review_needed(confidence, quotas, match_source)

            # 统一与主表逻辑：无匹配 / 低置信度 / 降级来源 都进入待审核
            if not review_needed:
                continue

            # L4从属项不在待审核Sheet逐条显示（用户只看代表项即可）
            # 但会在末尾汇总提示，避免从属项被完全遗漏
            if result.get("l4_follower"):
                follower_count += 1
                continue

            bill = result.get("bill_item", {})
            explanation = result.get("explanation", "")
            alternatives = _ensure_list(result.get("alternatives", []))

            # 当前匹配的定额
            main_quota_id = ""
            main_quota_name = ""
            if quotas:
                main_quota_id = quotas[0].get("quota_id", "")
                main_quota_name = quotas[0].get("name", "")

            # 推荐度颜色
            conf_fill = self._confidence_fill(confidence)

            # 写入数据（加了项目特征列，整体后移一列）
            ws.cell(row=current_row, column=1, value=idx)
            ws.cell(row=current_row, column=2, value=safe_excel_text(bill.get("name", "")))
            ws.cell(row=current_row, column=3, value=safe_excel_text(bill.get("description", "")))
            ws.cell(row=current_row, column=4, value=safe_excel_text(main_quota_id))
            ws.cell(row=current_row, column=5, value=safe_excel_text(main_quota_name))

            conf_text = confidence_to_stars(confidence, bool(quotas))
            cell_conf = ws.cell(row=current_row, column=6, value=conf_text)
            cell_conf.fill = conf_fill

            # 问题说明（代表项加 [请教] 前缀）
            brief_text = self._brief_explanation(explanation)
            if result.get("l4_representative"):
                group_label = result.get("l4_group_label", "")
                group_size = result.get("l4_group_size", 0)
                brief_text = f"[请教] {group_label}类{group_size}条，改此条同类自动学习"
            ws.cell(
                row=current_row,
                column=7,
                value=brief_text
            )

            # 备选定额
            self._write_alternative_cells(
                ws, current_row, start_col=8, alternatives=alternatives
            )
            ws.cell(row=current_row, column=11, value=self._brief_materials(result))

            # 格式（项目特征、定额名称、问题说明、主材列自动换行）
            self._apply_row_style(ws, current_row, 1, 11, {2, 3, 5, 7, 11})

            current_row += 1
            review_count += 1

        # L4从属项汇总提示（不逐条列出，但告知用户数量）
        if follower_count > 0:
            current_row += 1  # 空一行
            summary_text = (
                f"另有 {follower_count} 条同类从属项未逐条列出，"
                f"修正上方[请教]代表项后，下次同类自动生效"
            )
            cell = ws.cell(row=current_row, column=1, value=summary_text)
            cell.font = Font(name="微软雅黑", size=10, italic=True, color="808080")
            ws.merge_cells(
                start_row=current_row, start_column=1,
                end_row=current_row, end_column=11)

        # 如果没有待审核项，写个提示
        if review_count == 0:
            cell = ws.cell(row=2, column=1, value="全部匹配结果均为高置信度，无需审核")
            cell.font = Font(name="微软雅黑", size=11, color="006100")
            ws.merge_cells("A2:K2")

        ws.freeze_panes = "A2"
        logger.info(f"待审核Sheet: {review_count} 条需要人工审核")

    def _write_stats_sheet(self, ws, results: list[dict]):
        """写入统计汇总表"""
        total = len(results)
        matched = sum(1 for r in results if r.get("quotas"))
        high_conf = sum(1 for r in results
                        if _safe_confidence(r.get("confidence", 0), default=0) >= config.CONFIDENCE_GREEN)
        mid_conf = sum(1 for r in results
                       if config.CONFIDENCE_YELLOW
                       <= _safe_confidence(r.get("confidence", 0), default=0) < config.CONFIDENCE_GREEN)
        low_conf = sum(1 for r in results
                       if 0 < _safe_confidence(r.get("confidence", 0), default=0) < config.CONFIDENCE_YELLOW)
        no_match = total - matched

        stats = [
            ["统计项", "数量", "占比"],
            ["清单总数", total, "100%"],
            ["已匹配", matched, f"{matched * 100 // max(total, 1)}%"],
            ["高置信度（绿色）", high_conf,
             f"{high_conf * 100 // max(total, 1)}%"],
            ["中置信度（黄色）", mid_conf,
             f"{mid_conf * 100 // max(total, 1)}%"],
            ["低置信度（红色）", low_conf,
             f"{low_conf * 100 // max(total, 1)}%"],
            ["未匹配", no_match, f"{no_match * 100 // max(total, 1)}%"],
        ]

        for row_idx, row_data in enumerate(stats, start=1):
            for col_idx, value in enumerate(row_data, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = THIN_BORDER
                if row_idx == 1:
                    cell.font = HEADER_FONT
                    cell.fill = HEADER_FILL
                else:
                    cell.font = BILL_FONT

        ws.column_dimensions["A"].width = 20
        ws.column_dimensions["B"].width = 10
        ws.column_dimensions["C"].width = 10

