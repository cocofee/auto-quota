"""
工程量清单读取模块
功能：
1. 读取清单Excel文件，支持多种格式
2. 自动识别列映射（项目编码、名称、特征描述、单位、工程量）
3. 提取清单项目列表，每个项目包含完整信息

支持的清单格式：
- 标准12位编码格式（广联达/斯维尔等通用）
- 流水号格式（部分小型项目）
- 小栗AI输出格式
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import openpyxl
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.text_parser import parser as text_parser


def _is_quota_code(code: str) -> bool:
    """判断是否是定额编号（支持 X-XXX / D00003 / AD0003 / 带'换'后缀）。"""
    if not isinstance(code, str):
        return False
    c = code.strip()
    if not c:
        return False
    core = c[:-1] if c.endswith("换") else c
    return bool(re.match(r'^[A-Za-z]?\d{1,2}-\d+', core)) or bool(re.match(r'^[A-Za-z]{1,2}\d{4,}$', core))


class BillReader:
    """工程量清单读取器"""

    # 标准列名匹配规则（用于自动识别列映射）
    COLUMN_PATTERNS = {
        "index": ["序号"],
        "code": ["项目编码", "编码", "清单编码"],
        "name": ["项目名称", "名称", "清单名称"],
        "description": ["项目特征", "特征描述", "项目特征描述", "特征"],
        "unit": ["计量单位", "单位", "计量\n单位"],
        "quantity": ["工程量"],
    }

    def read_excel(self, file_path: str, sheet_name: str = None) -> list[dict]:
        """
        读取清单Excel文件

        参数:
            file_path: Excel文件路径
            sheet_name: 指定只读取某个Sheet（为None时读取所有Sheet）

        返回:
            清单项目列表，每项包含:
            {
                index: 序号,
                code: 项目编码,
                name: 项目名称,
                description: 特征描述,
                unit: 计量单位,
                quantity: 工程量,
                search_text: 搜索文本（名称+特征合并清洗后的）,
                params: 提取的结构化参数,
                sheet_name: 所在Sheet名,
                section: 所属分部工程,
            }
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"清单文件不存在: {file_path}")

        logger.info(f"读取清单文件: {file_path}")

        # .xls 文件自动转换为临时 .xlsx（openpyxl 不支持旧版 .xls 格式）
        temp_xlsx_path = None
        actual_path = file_path

        if file_path.suffix.lower() == ".xls":
            try:
                temp_xlsx_path = self._convert_xls_to_xlsx(file_path)
                actual_path = Path(temp_xlsx_path)
                logger.info(f"  已将 .xls 转换为临时 .xlsx")
            except Exception as e:
                logger.error(f"  .xls 转换失败: {e}")
                raise ValueError(f"无法读取 .xls 文件: {file_path}。转换失败: {e}")

        wb = openpyxl.load_workbook(str(actual_path), read_only=True, data_only=True)
        all_items = []
        try:
            # 确定要读取的Sheet列表
            if sheet_name:
                sheets_to_read = [sheet_name]
            else:
                sheets_to_read = self._filter_bill_sheets(wb.sheetnames)

            for sn in sheets_to_read:
                if sn not in wb.sheetnames:
                    logger.warning(f"Sheet '{sn}' 不存在，跳过")
                    continue
                ws = wb[sn]
                items = self._read_sheet(ws, sn)
                if items:
                    all_items.extend(items)
                    logger.info(f"  Sheet '{sn}': 读取 {len(items)} 条清单项")
        finally:
            wb.close()
            # 清理 .xls 转换产生的临时文件
            if temp_xlsx_path:
                try:
                    Path(temp_xlsx_path).unlink(missing_ok=True)
                except Exception:
                    pass

        if not all_items:
            logger.warning("未读取到任何清单项目，请检查文件格式")

        logger.info(f"清单读取完成: 共 {len(all_items)} 条项目")

        return all_items

    def _convert_xls_to_xlsx(self, xls_path: Path) -> str:
        """
        将 .xls 文件转换为临时 .xlsx 文件

        用 xlrd 读取 .xls 数据，openpyxl 写入临时 .xlsx。
        只转数据，不转格式（后续只需要数据值）。

        返回: 临时 .xlsx 文件路径
        """
        import xlrd  # 延迟导入，仅 .xls 场景需要

        xls_wb = xlrd.open_workbook(str(xls_path))

        # 临时文件放在 output/temp 目录
        temp_dir = Path(__file__).parent.parent / "output" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(suffix=".xlsx", prefix="xls_convert_", dir=str(temp_dir))
        os.close(fd)

        try:
            xlsx_wb = openpyxl.Workbook()
            xlsx_wb.remove(xlsx_wb.active)  # 删除默认空Sheet

            for sheet_idx in range(xls_wb.nsheets):
                xls_sheet = xls_wb.sheet_by_index(sheet_idx)
                xlsx_sheet = xlsx_wb.create_sheet(title=xls_sheet.name)

                for row_idx in range(xls_sheet.nrows):
                    for col_idx in range(xls_sheet.ncols):
                        cell = xls_sheet.cell(row_idx, col_idx)
                        value = cell.value
                        # xlrd 日期类型需特殊处理（ctype=3）
                        if cell.ctype == 3:
                            try:
                                value = xlrd.xldate_as_datetime(value, xls_wb.datemode)
                            except Exception:
                                pass
                        if value is not None and value != "":
                            xlsx_sheet.cell(row=row_idx + 1, column=col_idx + 1, value=value)

            xlsx_wb.save(temp_path)
            logger.debug(f"  .xls → .xlsx 转换完成: {xls_wb.nsheets} 个Sheet")
        except Exception:
            Path(temp_path).unlink(missing_ok=True)
            raise
        finally:
            xls_wb.release_resources()

        return temp_path

    # 非清单Sheet的名称关键词（包含这些词的Sheet直接跳过）
    SKIP_SHEET_KEYWORDS = [
        "汇总", "报告", "成果", "统计", "封面", "目录", "说明",
        "合计", "总表", "分析", "对比", "审核", "签章",
    ]

    def _filter_bill_sheets(self, sheet_names: list) -> list:
        """
        过滤Sheet列表，只保留可能是清单的Sheet

        规则：
        1. 优先选名称含"分部分项"或"清单"的Sheet（最可靠的标志）
        2. 跳过名称含"汇总"/"报告"/"成果"/"统计"等的Sheet
        3. 剩余的都尝试读取（让_detect_columns再判断）
        """
        # 正向识别关键词（含这些词的Sheet大概率是真正的清单）
        BILL_SHEET_KEYWORDS = ["分部分项", "清单"]

        # 第1步：找名称含"分部分项"或"清单"的Sheet（排除同时含跳过关键词的）
        bill_sheets = []
        for sn in sheet_names:
            has_bill_kw = any(kw in sn for kw in BILL_SHEET_KEYWORDS)
            if not has_bill_kw:
                continue
            # 含正向关键词但同时含"汇总"/"报告"/"成果"/"统计"的不算
            has_skip = any(kw in sn for kw in self.SKIP_SHEET_KEYWORDS)
            if not has_skip:
                bill_sheets.append(sn)
        if bill_sheets:
            logger.info(f"  识别到清单Sheet: {bill_sheets}")
            return bill_sheets

        # 第2步：过滤掉明显不是清单的Sheet
        filtered = []
        for sn in sheet_names:
            skip = False
            for kw in self.SKIP_SHEET_KEYWORDS:
                if kw in sn:
                    logger.debug(f"  跳过非清单Sheet: '{sn}'（含'{kw}'）")
                    skip = True
                    break
            if not skip:
                filtered.append(sn)

        return filtered if filtered else sheet_names  # 全过滤了就兜底读全部

    def get_sheet_info(self, file_path: str) -> list[dict]:
        """
        获取Excel中所有Sheet的信息，并检测哪些是分部分项工程量表

        返回:
            Sheet信息列表，每项包含:
            {
                "name": Sheet名,
                "is_bill": 是否检测到分部分项表头（True/False）,
                "matched_headers": 匹配到的表头关键词数量,
            }
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        result = []
        try:
            for sn in wb.sheetnames:
                ws = wb[sn]
                # 读取前20行检测表头
                header_rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 20:
                        break
                    header_rows.append(row)

                # 检测是否有分部分项的标准表头
                col_map, _ = self._detect_columns(header_rows)
                # 至少要有"名称"列才算有效
                is_bill = "name" in col_map and len(col_map) >= 2
                matched_count = len(col_map)

                result.append({
                    "name": sn,
                    "is_bill": is_bill,
                    "matched_headers": matched_count,
                })
        finally:
            wb.close()
        return result

    def _read_sheet(self, ws, sheet_name: str) -> list[dict]:
        """
        读取单个Sheet的清单数据

        自动识别逻辑：
        1. 扫描前20行，找到包含"项目编码"或"项目名称"等关键词的表头行
        2. 确定各列的映射关系
        3. 从表头下一行开始读取数据
        """
        # 读取前20行用于检测表头
        header_rows = []
        all_rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            all_rows.append(row)
            if i < 20:
                header_rows.append(row)

        if not all_rows:
            return []

        # 自动检测表头行和列映射
        col_map, header_row_idx = self._detect_columns(header_rows)

        if not col_map:
            # 未检测到标准格式，尝试简单格式
            col_map, header_row_idx = self._try_simple_format(header_rows)

        if not col_map or "name" not in col_map:
            logger.debug(f"  Sheet '{sheet_name}': 未检测到清单格式，跳过")
            return []

        logger.debug(f"  列映射: {col_map}, 表头行: {header_row_idx}")

        # 从表头下一行开始读取数据
        items = []
        current_section = ""  # 当前分部工程名

        sheet_bill_seq = 0
        for i, row in enumerate(all_rows):
            if i <= header_row_idx:
                continue  # 跳过表头及之前的行

            item = self._parse_bill_row(
                row, col_map, sheet_name, current_section, source_row=i + 1
            )

            if item is None:
                # 检查是否是分部工程标题行（如"土方工程"、"给排水工程"）
                section = self._detect_section_header(row, col_map)
                if section:
                    current_section = section
                continue

            sheet_bill_seq += 1
            item["sheet_bill_seq"] = sheet_bill_seq
            items.append(item)

        return items

    def _detect_columns(self, header_rows: list) -> tuple[dict, int]:
        """
        自动检测列映射

        返回:
            (col_map, header_row_idx)
            col_map: {"name": 4, "code": 1, "unit": 17, ...} 列名→列索引
            header_row_idx: 表头行索引

        识别逻辑：
        - 表头单元格通常很短（<15个字符），不是长句子
        - 至少要同时匹配"名称"和另一个列（编码/单位/工程量）才算有效
        - 避免误把"工程名称：xxx项目"这样的标题行当成表头
        """
        for row_idx, row in enumerate(header_rows):
            if not row:
                continue

            col_map = {}
            # 检查这一行的每个单元格
            for col_idx, cell_value in enumerate(row):
                if cell_value is None:
                    continue
                cell_text = str(cell_value).strip().replace("\n", "")

                # 表头单元格通常很短，超过15个字符的不当作表头处理
                if len(cell_text) > 15:
                    continue

                # 尝试匹配已知列名
                for field, patterns in self.COLUMN_PATTERNS.items():
                    for pattern in patterns:
                        if pattern in cell_text:
                            col_map[field] = col_idx
                            break

            # 至少要有"项目名称"和另一个列（编码/单位/工程量之一）才算有效表头
            if "name" in col_map and len(col_map) >= 2:
                return col_map, row_idx

        return {}, -1

    def _try_simple_format(self, header_rows: list) -> tuple[dict, int]:
        """
        尝试识别简单格式（没有标准表头，直接就是数据行）

        简单格式特征：
        - 第一列是序号或编码
        - 第二列是名称
        - 可能有单位和工程量列
        """
        for row_idx, row in enumerate(header_rows):
            if not row or len(row) < 2:
                continue

            first_val = str(row[0] or "").strip()
            second_val = str(row[1] or "").strip()

            # 检查第一列是否像编码（数字开头或字母+数字）
            if re.match(r'^[A-Za-z]?\d', first_val) and len(second_val) > 2:
                # 看起来是简单数据格式
                col_map = {"code": 0, "name": 1}
                if len(row) > 2 and row[2]:
                    col_map["unit"] = 2
                if len(row) > 3 and row[3]:
                    try:
                        float(str(row[3]))
                        col_map["quantity"] = 3
                    except (ValueError, TypeError):
                        pass
                return col_map, row_idx - 1  # 没有表头行

        return {}, -1

    def _parse_bill_row(self, row, col_map: dict, sheet_name: str,
                        section: str, source_row: int = None) -> dict | None:
        """
        解析一行清单数据

        返回:
            清单项目字典，或None（无效行）
        """
        if not row:
            return None

        def get_val(field):
            if field in col_map:
                idx = col_map[field]
                if idx < len(row) and row[idx] is not None:
                    return str(row[idx]).strip()
            return ""

        name = get_val("name")
        code = get_val("code")
        description = get_val("description")
        unit = get_val("unit")
        quantity_str = get_val("quantity")

        # 名称为空则跳过
        if not name:
            return None

        # 过滤表头行、合计行、概况说明等
        skip_keywords = ["合计", "小计", "序号", "项目名称", "总计", "分部分项",
                         "措施项目", "工程概况", "工程名称", "总 说 明", "说明",
                         "人工小计", "材料小计", "机械小计"]
        if any(kw in name for kw in skip_keywords):
            return None

        # 过滤辅助信息行：如"长度(m)"、"数量(个)"、"重量(kg)"等
        # 这些是小栗AI清单中配管/灯具等主清单项下面的属性行，不是独立清单项
        if re.match(r'^(长度|数量|重量|面积|体积|周长|高度|宽度|厚度)\s*[\(（]', name):
            return None

        # 过滤过长的描述行（超过100字符的通常是工程概况等说明文字，不是清单项）
        if len(name) > 100:
            return None

        # 解析工程量
        quantity = None
        if quantity_str:
            try:
                q = quantity_str.replace(",", "").strip()
                quantity = float(q)
            except (ValueError, TypeError):
                pass

        # 硬过滤：没有编码、没有工程量、没有单位的行 → 不是清单项
        # 真正的清单项至少有以下之一：项目编码、工程量、计量单位
        # 分部/小节标题（如"给排水工程"、"管道安装"）这三项全为空
        has_code = bool(code and re.search(r'\d', code))  # 编码非空且含数字
        has_quantity = quantity is not None                 # 有工程量
        has_unit = bool(unit)                               # 有计量单位
        if not has_code and not has_quantity and not has_unit:
            return None  # 分部/小节标题行，不是清单项

        # 过滤定额行：编码为定额格式（如C4-4-31、5-325、D00003等），不是清单项
        # 广联达导出的预算文件中，清单行和定额行交替排列：
        #   清单行编码为12位数字（如030402011001），定额行编码为 X-XXX 格式（如C4-4-31）
        if _is_quota_code(code):
            return None  # 定额编号格式，跳过

        # 构建搜索文本（合并名称+特征描述，去除无用信息）
        search_text = text_parser.build_search_text(name, description)

        # 提取结构化参数
        params = text_parser.parse(f"{name} {description}")

        # 获取序号
        index_str = get_val("index")

        return {
            "index": index_str,
            "code": code,
            "name": name,
            "description": description,
            "unit": unit,
            "quantity": quantity,
            "search_text": search_text,
            "params": params,
            "sheet_name": sheet_name,
            "section": section,
            "source_row": source_row,
        }

    def _detect_section_header(self, row, col_map: dict) -> str | None:
        """
        检测是否是分部工程标题行

        特征：
        - 序号列为空
        - 编码列或名称列有文字
        - 文字不是数据行（不含编码格式的数字）
        """
        if not row:
            return None

        # 序号列应该为空
        if "index" in col_map:
            idx_val = row[col_map["index"]] if col_map["index"] < len(row) else None
            if idx_val is not None and str(idx_val).strip():
                return None  # 有序号，不是标题行

        # 检查编码列或名称列
        for field in ["code", "name"]:
            if field in col_map:
                idx = col_map[field]
                if idx < len(row) and row[idx]:
                    text = str(row[idx]).strip()
                    # 分部工程标题通常是纯中文（如"给排水工程"、"电气工程"）
                    if len(text) >= 2 and not re.match(r'^\d', text):
                        return text

        return None


# ================================================================
# 命令行入口：测试清单读取
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="读取清单Excel并预览前N条解析结果")
    parser.add_argument("input", help="清单Excel路径")
    parser.add_argument("--limit", type=int, default=20, help="预览条数，默认20")
    args = parser.parse_args()

    reader = BillReader()
    items = reader.read_excel(args.input)

    logger.info(f"\n前{min(len(items), args.limit)}条清单项:")
    for item in items[:args.limit]:
        section = (item.get("section") or "")[:10]
        logger.info(
            f"  [{item.get('index', '')}] {item.get('code', '')} | {item.get('name', '')[:30]} | "
            f"{item.get('unit', '')} | {item.get('quantity', '')} | 分部:{section}"
        )
        if item.get("description"):
            # 只打印第一行特征描述
            desc_line1 = item["description"].split("\n")[0][:50]
            logger.info(f"    特征: {desc_line1}")
        if item.get("params"):
            logger.info(f"    参数: {item['params']}")
