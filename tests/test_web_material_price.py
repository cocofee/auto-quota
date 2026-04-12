# -*- coding: utf-8 -*-

from pathlib import Path
import sys

import openpyxl


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api import material_price as material_price_api  # noqa: E402


def test_parse_sheet_exposes_material_name_and_spec_columns(tmp_path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "主材表"
    ws.append(["材料编码", "材料名称", "规格型号", "单位", "数量", "单价"])
    ws.append(["26010101", "镀锌钢管", "DN25", "m", 12, 18.5])

    result = material_price_api._parse_sheet(ws)

    assert len(result["materials"]) == 1
    material = result["materials"][0]
    assert material["name"] == "镀锌钢管"
    assert material["name_col"] == 2
    assert material["spec_col"] == 3
    assert material["price_col"] == 6


def test_write_material_updates_writes_name_spec_and_price(tmp_path: Path):
    file_path = tmp_path / "reviewed.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "主材表"
    ws.append(["材料编码", "材料名称", "规格型号", "单位", "数量", "单价"])
    ws.append(["26010101", "原主材名", "DN25", "m", 12, None])
    wb.save(file_path)
    wb.close()

    written = material_price_api._do_write_material_updates(
        str(file_path),
        [
            {
                "row": 2,
                "sheet": "主材表",
                "name_col": 2,
                "spec_col": 3,
                "final_spec": "DN32",
                "final_name": "镀锌钢管",
                "price_col": 6,
                "final_price": 18.5,
            }
        ],
    )

    assert written == 1

    wb2 = openpyxl.load_workbook(file_path, data_only=True)
    ws2 = wb2["主材表"]
    assert ws2.cell(row=2, column=2).value == "镀锌钢管"
    assert ws2.cell(row=2, column=3).value == "DN32"
    assert ws2.cell(row=2, column=6).value == 18.5
    wb2.close()


def test_z_prefix_rows_are_treated_as_material_targets():
    assert material_price_api._classify_row("031001007001", "复合管", "1") == "bill"
    assert material_price_api._classify_row("A10-1-366", "给排水管道", "") == "quota"
    assert material_price_api._classify_row("Z1728A01B01BY", "复合管", "") == "material"
    assert material_price_api._classify_row("Z1811A07B01BF", "给水室内钢塑复合管螺纹管件", "") == "material"


def test_parse_sheet_prefills_z_material_name_and_spec_from_bill_desc():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "安装工程"
    ws.append(["序号", "项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "综合单价"])
    ws.append(["1", "031001007001", "复合管", "1.安装部位:室内\n2.介质:给水（冷水）\n3.材质、规格:衬塑钢管 DN100", "m", 14.03, None])
    ws.append(["", "A10-1-366", "给排水管道 室内 钢塑复合管（螺纹连接） 公称直径（mm以内）100", "", "10m", 1.403, None])
    ws.append(["", "Z1728A01B01BY", "复合管", "", "m", 10.02, None])
    ws.append(["", "Z1811A07B01BF", "给水室内钢塑复合管螺纹管件", "", "个", 4.15, None])

    result = material_price_api._parse_sheet(ws)

    materials = [m for m in result["materials"] if m["code"].startswith("Z")]
    assert len(materials) == 2
    assert materials[0]["suggested_name"] == "衬塑钢管"
    assert materials[0]["suggested_spec"] == "DN100"
    assert materials[1]["name"] == "给水室内钢塑复合管螺纹管件"
    assert materials[1]["suggested_name"] == ""
    assert materials[1]["suggested_spec"] == "DN100"


def test_extract_material_from_desc_supports_separate_material_and_spec_fields():
    info = material_price_api._extract_material_from_desc(
        "1.安装部位:室内\n2.介质:给水（冷水）\n3.材质:PPR管\n4.规格:De25 S5"
    )
    assert info["name"] == "PPR管"
    assert info["spec"] == "De25 S5"


def test_valve_rows_use_bill_type_for_generic_valve_names():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "螺纹阀门",
        "减压器组成安装",
        "1.类型:可调式减压阀\n2.材质:黄铜\n3.规格:DN32\n4.连接形式:螺纹连接",
    )
    assert suggested_name == "黄铜可调式减压阀"
    assert suggested_spec == "DN32"


def test_non_valve_accessories_keep_name_but_inherit_spec():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "螺纹Y型过滤器",
        "减压器组成安装",
        "1.类型:可调式减压阀\n2.材质:黄铜\n3.规格:DN32\n4.连接形式:螺纹连接",
    )
    assert suggested_name == ""
    assert suggested_spec == "DN32"


def test_non_generic_rows_do_not_get_renamed_from_bill_context():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "法兰压盖",
        "镀锌钢管",
        "1.规格:DN100\n2.连接形式:法兰连接",
    )
    assert suggested_name == ""
    assert suggested_spec == "DN100"


def test_generic_pipe_prefers_specific_bill_name_and_spec():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "钢管",
        "镀锌钢管",
        "1.安装部位:室外\n2.介质:雨水\n3.规格、压力等级:内外壁镀锌钢管DN100\n4.连接形式:沟槽连接",
    )
    assert suggested_name == "镀锌钢管"
    assert suggested_spec == "DN100"


def test_generic_filter_uses_connection_type_and_spec():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "法兰阀门",
        "法兰阀门",
        "1.类型:过滤器\n2.规格、压力等级:DN100\n3.连接形式:法兰连接",
    )
    assert suggested_name == "法兰过滤器"
    assert suggested_spec == "DN100"


def test_upvc_pipe_rows_keep_material_family_and_spec():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "塑料排水管",
        "套管",
        "1.介质:排水\n2.材质、规格:U-PVC De110\n3.连接形式:承插式胶粘剂粘接",
    )
    assert suggested_name == "U-PVC塑料排水管"
    assert suggested_spec == "De110"


def test_device_rows_merge_material_name_and_device_name():
    suggested_name, suggested_spec = material_price_api._suggest_material_from_bill_context(
        "不锈钢",
        "地漏",
        "1.名称:地漏\n2.材质:不锈钢\n3.型号、规格:DN50",
    )
    assert suggested_name == "不锈钢地漏"
    assert suggested_spec == "DN50"


def test_write_material_updates_merges_spec_into_name_when_no_spec_column(tmp_path: Path):
    file_path = tmp_path / "reviewed-no-spec.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["编码", "名称", "单价"])
    ws.append(["M001", "原主材", None])
    wb.save(file_path)
    wb.close()

    written = material_price_api._do_write_material_updates(
        str(file_path),
        [
            {
                "row": 2,
                "sheet": "Sheet1",
                "name_col": 2,
                "final_name": "镀锌钢管",
                "spec_col": None,
                "final_spec": "DN32",
                "price_col": 3,
                "final_price": 18.5,
            }
        ],
    )

    assert written == 1

    wb2 = openpyxl.load_workbook(file_path, data_only=True)
    ws2 = wb2["Sheet1"]
    assert ws2.cell(row=2, column=2).value == "镀锌钢管 DN32"
    assert ws2.cell(row=2, column=3).value == 18.5
    wb2.close()
