import shutil
import uuid
from pathlib import Path

import openpyxl

from src.bill_reader import BillReader
from src.output_writer import convert_quantity


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"quantity-header-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def test_bill_reader_parses_quantity_from_engineering_count_header():
    tmp_dir = _new_tmp_dir()
    xlsx_path = tmp_dir / "quantity_header.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "通风空调工程量清单"

    ws.append(["序号", "项目名称", "项目特征描述", "单位", "工程数量（暂定）"])
    ws.append([1, "组合式全空气机组", "风量:42000m3/h", "台", 3])
    try:
        wb.save(xlsx_path)
        wb.close()

        items = BillReader().read_excel(str(xlsx_path))
        assert len(items) == 1
        assert items[0]["name"] == "组合式全空气机组"
        assert items[0]["unit"] == "台"
        assert items[0]["quantity"] == 3.0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_convert_quantity_keeps_missing_quantity_empty():
    assert convert_quantity(None, "台", "台") is None
    assert convert_quantity("", "台", "台") is None
