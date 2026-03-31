import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl

from src.output_writer import OutputWriter


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api import results as results_api  # noqa: E402


def _build_source_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fire"
    ws.append(["序号", "编码", "名称", "项目特征", "单位", "工程量"])
    ws.append([1, "030109001001", "泵1", "", "台", 2])
    ws.append([2, "030109001002", "泵2", "", "台", 2])
    ws.append([3, "030109001003", "泵3", "", "台", 2])
    ws.append([4, "030404017001", "配电箱", "", "台", 17])
    ws.append([5, "030404032001", "端子箱", "", "台", 17])
    wb.save(path)


def test_build_rebuilt_results_from_source_file_recovers_source_row_for_subset(tmp_path):
    source_path = tmp_path / "input.xlsx"
    _build_source_workbook(source_path)

    task = SimpleNamespace(
        id="task-export-rebuild",
        file_path=str(source_path),
        output_path=None,
        sheet=None,
    )
    items = [
        SimpleNamespace(
            index=0,
            corrected_quotas=None,
            quotas=[{"quota_id": "4-4-16", "name": "端子箱安装 户内", "unit": "台"}],
            confidence=88,
            explanation="ok",
            match_source="search",
            bill_code="030404032001",
            bill_name="端子箱",
            bill_description="",
            bill_unit="台",
            bill_quantity=17,
            sheet_name="Fire",
            section="",
            specialty="C4",
        ),
    ]

    rebuilt = results_api._build_rebuilt_results_from_source_file(task, items)

    assert rebuilt is not None
    assert rebuilt[0]["bill_item"]["source_row"] == 6
    assert rebuilt[0]["bill_item"]["sheet_bill_seq"] == 5
    assert rebuilt[0]["bill_item"]["sheet_name"] == "Fire"


def test_output_writer_rebuilds_correctly_from_preexisting_wrong_output_template(tmp_path):
    source_path = tmp_path / "input.xlsx"
    bad_output_path = tmp_path / "bad_output.xlsx"
    fixed_output_path = tmp_path / "fixed_output.xlsx"
    _build_source_workbook(source_path)

    writer = OutputWriter()

    wrong_subset_results = [{
        "bill_item": {
            "sheet_name": "Fire",
            "sheet_bill_seq": 1,
            "code": "030404032001",
            "name": "端子箱",
            "unit": "台",
            "quantity": 17,
        },
        "quotas": [{
            "quota_id": "4-4-16",
            "name": "端子箱安装 户内",
            "unit": "台",
        }],
        "confidence": 88,
        "explanation": "subset old mapping",
        "alternatives": [],
    }]
    writer.write_results(wrong_subset_results, str(bad_output_path), original_file=str(source_path))

    bad_ws = openpyxl.load_workbook(bad_output_path)["Fire"]
    assert bad_ws.cell(row=3, column=2).value == "4-4-16"

    fixed_subset_results = [{
        "bill_item": {
            "sheet_name": "Fire",
            "sheet_bill_seq": 1,
            "source_row": 6,
            "code": "030404032001",
            "name": "端子箱",
            "unit": "台",
            "quantity": 17,
        },
        "quotas": [{
            "quota_id": "4-4-16",
            "name": "端子箱安装 户内",
            "unit": "台",
        }],
        "confidence": 95,
        "explanation": "source row rebuild",
        "alternatives": [],
    }]
    writer.write_results(fixed_subset_results, str(fixed_output_path), original_file=str(bad_output_path))

    fixed_ws = openpyxl.load_workbook(fixed_output_path)["Fire"]
    assert fixed_ws.cell(row=2, column=3).value == "泵1"
    assert fixed_ws.cell(row=3, column=3).value == "泵2"
    assert fixed_ws.cell(row=4, column=3).value == "泵3"
    assert fixed_ws.cell(row=5, column=3).value == "配电箱"
    assert fixed_ws.cell(row=6, column=3).value == "端子箱"
    assert fixed_ws.cell(row=7, column=2).value == "4-4-16"
    assert fixed_ws.cell(row=7, column=3).value == "端子箱安装 户内"
