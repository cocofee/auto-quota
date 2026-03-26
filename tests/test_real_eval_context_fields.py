from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from tools.export_real_eval_set import fetch_real_eval_records
from tools.run_real_eval import _bill_item_from_record


def test_fetch_real_eval_records_keeps_optional_context_columns():
    temp_root = Path("output/_tmp_real_eval_tools_optional")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        db_path = temp_root / "experience.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bill_text TEXT,
                    bill_name TEXT,
                    bill_code TEXT,
                    quota_ids TEXT,
                    quota_names TEXT,
                    source TEXT,
                    confidence INTEGER,
                    confirm_count INTEGER,
                    province TEXT,
                    project_name TEXT,
                    layer TEXT,
                    specialty TEXT,
                    section TEXT,
                    sheet_name TEXT,
                    context_prior TEXT,
                    source_file_name TEXT,
                    source_file_stem TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO experiences (
                    bill_text, bill_name, bill_code, quota_ids, quota_names,
                    source, confidence, confirm_count, province, project_name,
                    layer, specialty, section, sheet_name, context_prior,
                    source_file_name, source_file_stem
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "普通PVC排水管 DN50",
                    "塑料管",
                    "031001006009",
                    json.dumps(["C10-1-1"], ensure_ascii=False),
                    json.dumps(["室内塑料排水管"], ensure_ascii=False),
                    "project_import",
                    98,
                    2,
                    "广东",
                    "项目A",
                    "authority",
                    "C10",
                    "重力排水系统",
                    "08A分部分项工程量清单与计价表",
                    json.dumps({"system_hint": "给排水"}, ensure_ascii=False),
                    "[广东]4-2单元-给排水工程_wx_zip.xlsx",
                    "4-2单元-给排水工程",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        records = fetch_real_eval_records(db_path, sources=["project_import"])

        assert records[0]["section"] == "重力排水系统"
        assert records[0]["sheet_name"] == "08A分部分项工程量清单与计价表"
        assert records[0]["context_prior"]["system_hint"] == "给排水"
        assert records[0]["source_file_name"] == "[广东]4-2单元-给排水工程_wx_zip.xlsx"
        assert records[0]["source_file_stem"] == "4-2单元-给排水工程"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_bill_item_from_record_preserves_optional_context_fields():
    item = _bill_item_from_record(
        {
            "bill_name": "塑料管",
            "bill_text": "普通PVC排水管 DN50",
            "specialty": "C10",
            "project_name": "4-2单元-给排水工程",
            "section": "重力排水系统",
            "sheet_name": "08A分部分项工程量清单与计价表",
            "source_file_name": "[广东]4-2单元-给排水工程_wx_zip.xlsx",
            "source_file_stem": "4-2单元-给排水工程",
            "context_prior": {"system_hint": "给排水"},
        },
        seq=3,
    )

    assert item["project_name"] == "4-2单元-给排水工程"
    assert item["bill_name"] == "塑料管"
    assert item["section"] == "重力排水系统"
    assert item["sheet_name"] == "08A分部分项工程量清单与计价表"
    assert item["source_file_name"] == "[广东]4-2单元-给排水工程_wx_zip.xlsx"
    assert item["source_file_stem"] == "4-2单元-给排水工程"
    assert item["context_prior"]["system_hint"] == "给排水"
    assert item["context_prior"]["project_name"] == "4-2单元-给排水工程"
    assert item["context_prior"]["bill_name"] == "塑料管"
