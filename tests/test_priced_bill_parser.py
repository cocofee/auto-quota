from pathlib import Path

import openpyxl
import sqlite3

from src.priced_bill_parser import parse_priced_bill_document
from src.price_reference_db import PriceReferenceDB


def _build_sample_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "安装清单"
    ws.append(["序号", "项目编码", "项目名称", "项目特征", "", "计量单位", "工程量", "综合单价", "合价"])
    ws.append([1, "030404017001", "配电箱", "名称:AL1; 安装方式:暗装", "", "台", 2, 580.5, 1161.0])
    ws.append(["", "30402076", "成套配电箱安装 悬挂嵌入式 半周长1.0m", "", "", "台", 2, "", ""])
    ws.append(["", "CL0001", "铜接线端子", "", "", "个", "", 12.3, ""])
    wb.save(path)


def test_parse_priced_bill_excel_extracts_prices(tmp_path: Path):
    file_path = tmp_path / "priced_bill.xlsx"
    _build_sample_workbook(file_path)

    parsed = parse_priced_bill_document(file_path)

    assert parsed.file_type == "excel"
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.boq_name_raw == "配电箱"
    assert item.composite_unit_price == 580.5
    assert item.quantity == 2
    assert len(item.quotas) == 1
    assert item.quotas[0].code == "30402076"
    assert len(item.materials) == 1
    assert item.materials[0].price == 12.3


def test_price_reference_db_returns_numeric_composite_reference(tmp_path: Path):
    db_path = tmp_path / "price_reference.db"
    db = PriceReferenceDB(db_path=db_path)
    document_id = db.create_document_from_file(
        file_id="fi_test",
        document_type="priced_bill_file",
        project_name="测试项目",
        source_file_name="priced_bill.xlsx",
        status="created",
    )
    db.replace_boq_items(
        document_id,
        [
            {
                "project_name": "测试项目",
                "boq_code": "030404017001",
                "boq_name_raw": "配电箱",
                "boq_name_normalized": "配电箱",
                "feature_text": "名称:AL1",
                "unit": "台",
                "quantity": 2,
                "composite_unit_price": 580.5,
                "quota_code": "30402076",
                "quota_name": "成套配电箱安装 悬挂嵌入式 半周长1.0m",
                "materials_json": [{"name": "铜接线端子", "price": 12.3}],
                "bill_text": "配电箱 名称:AL1",
                "search_text": "配电箱 名称:AL1 30402076",
            }
        ],
    )

    ref = db.get_composite_price_reference(query="配电箱")

    assert ref["summary"]["sample_count"] == 1
    assert ref["summary"]["median_composite_unit_price"] == 580.5
    assert ref["samples"][0]["quota_code"] == "30402076"


def test_price_reference_db_infers_source_date_from_document_filename(tmp_path: Path):
    db_path = tmp_path / "price_reference.db"
    db = PriceReferenceDB(db_path=db_path)
    document_id = db.create_document_from_file(
        file_id="fi_date",
        document_type="priced_bill_file",
        project_name="测试项目",
        source_file_name="北京海燕药业项目[2024-4-13 11：20]_招标控制价文件.xml",
        status="created",
    )
    db.replace_boq_items(
        document_id,
        [
            {
                "project_name": "测试项目",
                "boq_name_raw": "配电箱",
                "unit": "台",
                "composite_unit_price": 580.5,
                "quota_code": "30402076",
                "quota_name": "成套配电箱安装",
            }
        ],
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "select source_date, price_date_iso, date_parse_failed from historical_boq_items where document_id=?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "2024-4-13"
    assert row[1] == "2024-04-13T00:00:00+08:00"
    assert row[2] == 0


def test_backfill_boq_item_enhancements_fills_source_date_from_document_filename(tmp_path: Path):
    db_path = tmp_path / "price_reference.db"
    db = PriceReferenceDB(db_path=db_path)
    document_id = db.create_document_from_file(
        file_id="fi_legacy_date",
        document_type="priced_bill_file",
        project_name="历史项目",
        source_file_name="sample_legacy_2024年3月.xml",
        status="created",
    )

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            insert into historical_boq_items (
                document_id, source_experience_id, seed_source, project_name, boq_name_raw,
                boq_name_normalized, unit, composite_unit_price, quota_code, quota_name,
                normalized_name, materials_signature_first, price_type, price_value,
                price_outlier, date_parse_failed
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            """,
            (
                document_id,
                1001,
                "legacy_seed",
                "历史项目",
                "给水管道",
                "给水管道",
                "m",
                88.0,
                "Q-1",
                "管道安装",
                "给水管道",
                "_unknown_material",
                "composite_price",
                88.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = db.backfill_boq_item_enhancements(document_id=document_id, batch_size=100)
    assert result["processed"] == 1
    assert result["updated"] == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "select source_date, price_date_iso, date_parse_failed from historical_boq_items where document_id=?",
            (document_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == "2024年3月"
    assert row[1] == "2024-03-01T00:00:00+08:00"
    assert row[2] == 0
