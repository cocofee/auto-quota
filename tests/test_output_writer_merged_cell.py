"""测试 output_writer 在遇到合并单元格时不会崩溃。

背景：
  openpyxl 的 MergedCell（合并区域中非左上主格）禁止写入 value 属性，
  写入会抛 AttributeError: 'MergedCell' object attribute 'value' is read-only。
  修复后所有写入点通过 _safe_write_cell() 检测并跳过 MergedCell。

复现证据：
  历史 merged-cell 崩溃问题记录（已归档/清理）。
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell
import pytest

from src.output_writer import OutputWriter, _safe_write_cell


# ========== _safe_write_cell 单元测试 ==========

class TestSafeWriteCell:
    """测试安全写入函数本身的行为"""

    def test_normal_cell_write(self):
        """普通单元格：正常写入值并返回cell对象"""
        wb = openpyxl.Workbook()
        ws = wb.active
        cell = _safe_write_cell(ws, 1, 1, "测试值")
        assert cell is not None
        assert cell.value == "测试值"

    def test_normal_cell_no_value(self):
        """不传value时只获取cell，不改变值"""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.cell(row=1, column=1, value="原始值")
        cell = _safe_write_cell(ws, 1, 1)  # 不传value
        assert cell is not None
        assert cell.value == "原始值"

    def test_merged_cell_returns_none(self):
        """合并单元格：返回None，不崩溃"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 合并J1:J3（模拟原始清单中J列有合并）
        ws.merge_cells("A1:A3")
        # A2和A3现在是MergedCell
        cell_a2 = ws.cell(row=2, column=1)
        assert isinstance(cell_a2, MergedCell), "前置条件：A2应为MergedCell"

        # 安全写入应返回None，不抛异常
        result = _safe_write_cell(ws, 2, 1, "这个值不该写入")
        assert result is None

    def test_merged_cell_master_still_writable(self):
        """合并区域的主格（左上角）仍可正常写入"""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.merge_cells("A1:A3")
        # A1是主格，应该可写
        cell = _safe_write_cell(ws, 1, 1, "主格写入")
        assert cell is not None
        assert cell.value == "主格写入"


# ========== 保结构回写模式下的合并单元格场景 ==========

class TestWriteBillExtraInfoWithMergedCells:
    """测试 _write_bill_extra_info 在J-O列存在合并单元格时不崩溃"""

    def _create_ws_with_merged_extra_cols(self):
        """创建一个模拟的worksheet，J-O列部分有合并"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 写入表头行（第1行）
        headers = ["序号", "编码", "名称", "特征", "单位", "工程量",
                    "", "", "", "推荐度", "说明", "备选1", "备选2", "备选3", "主材"]
        for col, h in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=h)

        # 第2行：正常清单行（不合并）
        ws.cell(row=2, column=1, value=1)
        ws.cell(row=2, column=3, value="DN25镀锌钢管")

        # 第3行：清单行，但J列(col10)和K列(col11)被合并了
        ws.cell(row=3, column=1, value=2)
        ws.cell(row=3, column=3, value="DN32镀锌钢管")
        # 模拟J3:J4合并（跨行合并）
        ws.merge_cells(start_row=3, start_column=10, end_row=4, end_column=10)
        # 模拟L3:N3合并（跨列合并，覆盖备选定额区域）
        ws.merge_cells(start_row=3, start_column=12, end_row=3, end_column=14)

        return wb, ws

    def test_write_extra_info_no_merge_normal(self):
        """正常行（无合并）：写入不报错"""
        wb, ws = self._create_ws_with_merged_extra_cols()
        writer = OutputWriter.__new__(OutputWriter)  # 不走__init__
        result = {
            "confidence": 85,
            "quotas": [{"quota_id": "C10-1-1", "name": "管道安装"}],
            "explanation": "匹配成功",
            "alternatives": [
                {"quota_id": "C10-1-2", "name": "备选1"},
                {"quota_id": "C10-1-3", "name": "备选2"},
            ],
            "materials": [{"name": "镀锌钢管", "spec": "DN25"}],
        }
        # 第2行无合并，应正常写入
        writer._write_bill_extra_info(ws, 2, result)

        # 验证J列写入了值
        assert ws.cell(row=2, column=10).value is not None

    def test_write_extra_info_with_merged_cells_no_crash(self):
        """合并行：不崩溃、不抛异常（核心回归测试）"""
        wb, ws = self._create_ws_with_merged_extra_cols()
        writer = OutputWriter.__new__(OutputWriter)
        result = {
            "confidence": 90,
            "quotas": [{"quota_id": "C10-2-1", "name": "管道安装"}],
            "explanation": "匹配成功",
            "alternatives": [
                {"quota_id": "C10-2-2", "name": "备选A"},
                {"quota_id": "C10-2-3", "name": "备选B"},
                {"quota_id": "C10-2-4", "name": "备选C"},
            ],
            "materials": [],
        }
        # 第4行的J列是MergedCell（J3:J4合并的下半部分）
        # 这里曾经会抛 AttributeError: 'MergedCell' object attribute 'value' is read-only
        writer._write_bill_extra_info(ws, 4, result)
        # 能走到这里就说明没崩溃


class TestWriteAlternativeCellsWithMergedCells:
    """测试 _write_alternative_cells 在备选列有合并时不崩溃"""

    def test_alternatives_on_merged_cols_no_crash(self):
        """L/M/N列跨列合并时，写入备选定额不崩溃"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 合并L2:N2（跨列合并覆盖备选区域）
        ws.merge_cells(start_row=2, start_column=12, end_row=2, end_column=14)

        alternatives = [
            {"quota_id": "C4-1-1", "name": "定额A"},
            {"quota_id": "C4-1-2", "name": "定额B"},
        ]
        # M2和N2是MergedCell，曾经会崩溃
        OutputWriter._write_alternative_cells(ws, 2, start_col=12, alternatives=alternatives)
        # L2是合并主格，可以写入
        assert ws.cell(row=2, column=12).value is not None


class TestWriteNoMatchRowWithMergedCells:
    """测试 _write_no_match_row 在存在合并单元格时不崩溃"""

    def test_no_match_row_with_merged_cells_no_crash(self):
        """行内有合并单元格时，标记未匹配不崩溃"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 合并C2:C3
        ws.merge_cells(start_row=2, start_column=3, end_row=3, end_column=3)

        # 第3行的C列是MergedCell
        OutputWriter._write_no_match_row(ws, 3, "无匹配结果", max_col=5)
        # 不崩溃即通过


class TestApplyRowStyleWithMergedCells:
    """测试 _apply_row_style 在存在合并单元格时不崩溃"""

    def test_apply_style_with_merged_cells_no_crash(self):
        """行内有合并单元格时，应用样式不崩溃"""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.merge_cells("B2:D2")

        # B2:D2合并后，C2和D2是MergedCell
        OutputWriter._apply_row_style(ws, 2, start_col=1, end_col=5, wrap_cols={3, 4})
        # 不崩溃即通过


class TestSetHeaderCellWithMergedCells:
    """测试 _set_header_cell 在表头行有合并单元格时不崩溃（P1-1 二次修复）"""

    def test_set_header_on_normal_cell(self):
        """正常单元格：写入值并返回cell对象"""
        from openpyxl.styles import PatternFill
        wb = openpyxl.Workbook()
        ws = wb.active
        fill = PatternFill(start_color="4472C4", fill_type="solid")
        cell = OutputWriter._set_header_cell(ws, 1, 1, "表头", fill)
        assert cell is not None
        assert cell.value == "表头"

    def test_set_header_on_merged_cell_returns_none(self):
        """合并单元格：返回None，不崩溃"""
        from openpyxl.styles import PatternFill
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.merge_cells("J1:K1")  # 合并表头区域
        fill = PatternFill(start_color="4472C4", fill_type="solid")
        # K1 是 MergedCell，写入应静默跳过
        result = OutputWriter._set_header_cell(ws, 1, 11, "备选", fill)
        assert result is None  # 跳过，不崩溃

    def test_add_extra_headers_with_merged_header_row(self):
        """_add_extra_headers 在表头行有合并时不崩溃"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 模拟原始Excel的表头合并：J1:L1 合并
        ws.merge_cells("J1:L1")
        writer = OutputWriter.__new__(OutputWriter)
        # K1和L1是MergedCell，不应崩溃
        writer._add_extra_headers(ws, header_row=1)


class TestApplyPostFormatWithMergedCells:
    """测试 _apply_post_format 在清单行有合并单元格时不崩溃（P1-1 二次修复）"""

    def test_post_format_skips_merged_cells(self):
        """清单行中有合并单元格时，样式应用跳过MergedCell"""
        wb = openpyxl.Workbook()
        ws = wb.active
        # 第1行：表头
        ws.cell(row=1, column=1, value="序号")
        ws.cell(row=1, column=3, value="名称")
        # 第2行：清单行（序号列=1 → 被识别为清单行）
        ws.cell(row=2, column=1, value=1)
        ws.cell(row=2, column=3, value="DN25钢管")
        # 合并C2:D2（模拟原始Excel特征描述合并）
        ws.merge_cells("C2:D2")

        writer = OutputWriter.__new__(OutputWriter)
        # 不应崩溃（D2是MergedCell）
        writer._apply_post_format(ws, header_row=1)


class TestReviewSheetSelection:
    """测试待审核Sheet筛选逻辑与主表复核规则一致"""

    def test_review_sheet_includes_high_confidence_no_match_item(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        writer = OutputWriter()

        results = [
            {
                "bill_item": {"name": "DN25钢管", "description": "无匹配场景"},
                "confidence": 95,
                "quotas": [],
                "match_source": "agent",
                "explanation": "无候选",
                "alternatives": [],
            }
        ]

        writer._write_review_sheet(ws, results)

        assert ws.cell(row=2, column=1).value == 1
        assert ws.cell(row=2, column=2).value == "DN25钢管"
        assert ws.cell(row=2, column=6).value == "—"

    def test_review_sheet_includes_high_confidence_agent_fallback_item(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        writer = OutputWriter()

        results = [
            {
                "bill_item": {"name": "DN32钢管", "description": "降级来源场景"},
                "confidence": 92,
                "quotas": [{"quota_id": "C10-1-1", "name": "钢管安装"}],
                "match_source": "agent_fallback",
                "explanation": "降级结果",
                "alternatives": [],
            }
        ]

        writer._write_review_sheet(ws, results)

        assert ws.cell(row=2, column=1).value == 1
        assert ws.cell(row=2, column=4).value == "C10-1-1"
        assert ws.cell(row=2, column=6).value.startswith("★★★")
