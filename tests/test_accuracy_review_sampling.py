import csv
import hashlib
import json
import sqlite3

from eval.accuracy_baseline.review_sampling import (
    build_review_queue,
    write_review_queue,
)
from src.goal_search.national_index import create_schema, row_to_index_tuple

_NATIONAL_INDEX_INSERT = """
    INSERT INTO national_quotas (
        province, quota_id, name, unit, chapter, specialty, family, action,
        material, connection, install_method, dn, cable_section, cable_cores,
        circuits, concrete_grade, thickness, param_type, cluster_key, tokens,
        normalized_text
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_national_rows(connection, rows):
    create_schema(connection)
    connection.executemany(
        _NATIONAL_INDEX_INSERT,
        [row_to_index_tuple(row) for row in rows],
    )


def _build_bill_library(path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE files (
            file_path TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            specialty TEXT,
            province TEXT,
            bill_count INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE bill_items (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            sheet_name TEXT,
            section TEXT,
            bill_code TEXT NOT NULL,
            bill_name TEXT NOT NULL,
            description TEXT,
            unit TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
        [
            ("a.xlsx", "a.xlsx", "给排水", "安徽", 3),
            ("b.xlsx", "b.xlsx", "电气", "安徽", 2),
            ("c.xlsx", "c.xlsx", "给排水", "浙江", 2),
            ("missing.xlsx", "missing.xlsx", "给排水", "", 1),
        ],
    )
    connection.executemany(
        "INSERT INTO bill_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "a.xlsx", "S1", "A", "001", "给水管", "DN50", "m"),
            (2, "a.xlsx", "S1", "A", "002", "给水管", "DN65", "m"),
            (3, "a.xlsx", "S1", "A", "003", "阀门", "DN50", "个"),
            (4, "b.xlsx", "S2", "B", "004", "配电箱", "嵌入式", "台"),
            (5, "b.xlsx", "S2", "B", "005", "给水管", "DN50", "m"),
            (6, "c.xlsx", "S1", "A", "006", "给水管", "DN50", "m"),
            (7, "c.xlsx", "S1", "A", "007", "水泵", "立式", "台"),
            (8, "missing.xlsx", "S1", "A", "008", "套管", "DN50", "个"),
        ],
    )
    connection.commit()
    connection.close()


def _build_national_index(path):
    connection = sqlite3.connect(path)
    _insert_national_rows(
        connection,
        [
            {
                "province": "安徽省安装工程计价定额(2018)",
                "quota_id": "Q-A-1",
                "name": "给水管安装 DN50",
                "unit": "m",
            },
            {
                "province": "安徽省安装工程计价定额(2018)",
                "quota_id": "Q-A-2",
                "name": "阀门安装 DN50",
                "unit": "个",
            },
            {
                "province": "浙江省通用安装工程预算定额(2018)",
                "quota_id": "Q-Z-1",
                "name": "给水管安装 DN50",
                "unit": "m",
            },
        ],
    )
    connection.commit()
    connection.close()


def _mojibake(text):
    return text.encode("utf-8").decode("latin1")


def test_review_queue_is_deterministic_stratified_and_project_capped(tmp_path):
    bill_library = tmp_path / "bill_library.db"
    national_index = tmp_path / "national.sqlite"
    _build_bill_library(bill_library)
    _build_national_index(national_index)

    first_rows, first_manifest = build_review_queue(
        bill_library_path=bill_library,
        national_index_path=national_index,
        target_per_province=3,
        max_per_project=1,
        seed="test-seed",
    )
    second_rows, second_manifest = build_review_queue(
        bill_library_path=bill_library,
        national_index_path=national_index,
        target_per_province=3,
        max_per_project=1,
        seed="test-seed",
    )

    assert first_rows == second_rows
    assert first_manifest == second_manifest
    assert first_manifest["eligible_rows_before_deduplication"] == 7
    assert first_manifest["duplicate_rows_removed"] == 1
    assert first_manifest["selected_provinces"] == 2
    assert first_manifest["selected_rows"] == 3
    assert first_manifest["system_baseline_eligible"] is False
    assert first_manifest["review_required"] is True

    project_counts = {}
    for row in first_rows:
        key = (row["province"], row["project_id"])
        project_counts[key] = project_counts.get(key, 0) + 1
        assert row["oracle_quota_ids"] == []
        assert row["oracle_semantics"] == ""
        assert row["review_status"] == "pending"
        assert row["review_selection"] == ""
        assert row["candidate_quota_books"]
        assert row["suggested_source"] == "national_index_structured_search"
        assert row["suggested_version"] == "accuracy_review_suggestions.v1"
    assert max(project_counts.values()) == 1
    assert len(
        {row["province_query_fingerprint"] for row in first_rows}
    ) == len(first_rows)


def test_write_review_queue_creates_jsonl_csv_and_manifest(tmp_path):
    bill_library = tmp_path / "bill_library.db"
    national_index = tmp_path / "national.sqlite"
    _build_bill_library(bill_library)
    _build_national_index(national_index)
    rows, manifest = build_review_queue(
        bill_library_path=bill_library,
        national_index_path=national_index,
        target_per_province=1,
    )

    outputs = write_review_queue(
        rows=rows,
        manifest=manifest,
        output_dir=tmp_path / "review",
    )

    jsonl_rows = [
        json.loads(line)
        for line in outputs["jsonl"].read_text(encoding="utf-8").splitlines()
    ]
    with outputs["csv"].open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    written_manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))

    assert jsonl_rows == rows
    assert len(csv_rows) == len(rows)
    assert json.loads(csv_rows[0]["oracle_quota_ids"]) == []
    assert json.loads(csv_rows[0]["suggested_quota_ids"]) == rows[0][
        "suggested_quota_ids"
    ]
    assert written_manifest["selected_rows"] == len(rows)
    assert len(written_manifest["content_sha256"]) == 64
    assert written_manifest["content_sha256"] == hashlib.sha256(
        outputs["jsonl"].read_bytes()
    ).hexdigest()
    assert csv_rows[0]["source_family"] == "bill_library"
    assert csv_rows[0]["queue_content_sha256"] == written_manifest[
        "content_sha256"
    ]
    assert "review_selection" in written_manifest["review_contract"][
        "required_fields"
    ]
    assert csv_rows[0]["review_selection"] == ""


def test_review_queue_repairs_text_before_grouping_and_fingerprinting(tmp_path):
    bill_library = tmp_path / "bill_library.db"
    national_index = tmp_path / "national.sqlite"

    connection = sqlite3.connect(bill_library)
    connection.execute(
        "CREATE TABLE files (file_path TEXT PRIMARY KEY, file_name TEXT, "
        "specialty TEXT, province TEXT, bill_count INTEGER)"
    )
    connection.execute(
        "CREATE TABLE bill_items (id INTEGER PRIMARY KEY, file_path TEXT, "
        "sheet_name TEXT, section TEXT, bill_code TEXT, bill_name TEXT, "
        "description TEXT, unit TEXT)"
    )
    connection.execute(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
        (
            "shanghai.xlsx",
            _mojibake("上海项目.xlsx"),
            _mojibake("给排水"),
            _mojibake("上海"),
            1,
        ),
    )
    connection.execute(
        "INSERT INTO bill_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "shanghai.xlsx",
            _mojibake("安装工程"),
            _mojibake("给水系统"),
            "031001",
            _mojibake("给水管"),
            _mojibake("室内\nDN50"),
            "m",
        ),
    )
    connection.commit()
    connection.close()

    connection = sqlite3.connect(national_index)
    _insert_national_rows(
        connection,
        [
            {
                "province": _mojibake("上海市安装工程预算定额(2016)"),
                "quota_id": "Q-SH-1",
                "name": _mojibake("室内给水管安装 DN50"),
                "unit": "m",
            }
        ],
    )
    connection.commit()
    connection.close()

    rows, manifest = build_review_queue(
        bill_library_path=bill_library,
        national_index_path=national_index,
        target_per_province=1,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["province"] == "上海"
    assert row["specialty"] == "给排水"
    assert row["source_file_name"] == "上海项目.xlsx"
    assert row["sheet_name"] == "安装工程"
    assert row["section"] == "给水系统"
    assert row["bill_name"] == "给水管"
    assert row["description"] == "室内 DN50"
    assert row["bill_text"] == "给水管 室内 DN50"
    assert row["candidate_quota_books"] == [
        {"name": "上海市安装工程预算定额(2016)", "quota_rows": 1}
    ]
    assert row["query_fingerprint"] == "给水管 室内 dn50"
    assert manifest["provinces_without_candidate_quota_books"] == []
    assert manifest["text_repair"]["repaired_field_values"] == 8
    assert manifest["text_repair"]["fields"] == {
        "bill_name": 1,
        "candidate_quota_book": 1,
        "description": 1,
        "province": 1,
        "section": 1,
        "sheet_name": 1,
        "source_file_name": 1,
        "specialty": 1,
    }


def test_review_queue_generates_advisory_suggestions_without_prefilling_oracle(
    tmp_path,
):
    bill_library = tmp_path / "bill_library.db"
    national_index = tmp_path / "national.sqlite"

    connection = sqlite3.connect(bill_library)
    connection.execute(
        "CREATE TABLE files (file_path TEXT PRIMARY KEY, file_name TEXT, "
        "specialty TEXT, province TEXT, bill_count INTEGER)"
    )
    connection.execute(
        "CREATE TABLE bill_items (id INTEGER PRIMARY KEY, file_path TEXT, "
        "sheet_name TEXT, section TEXT, bill_code TEXT, bill_name TEXT, "
        "description TEXT, unit TEXT)"
    )
    connection.execute(
        "INSERT INTO files VALUES (?, ?, ?, ?, ?)",
        ("project.xlsx", "project.xlsx", "安装", "上海", 1),
    )
    connection.execute(
        "INSERT INTO bill_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "project.xlsx", "清单", "给水", "031001", "给水管", "DN50", "m"),
    )
    connection.commit()
    connection.close()

    connection = sqlite3.connect(national_index)
    _insert_national_rows(
        connection,
        [
            {
                "province": "上海市安装工程预算定额(2026)",
                "quota_id": "Q-PIPE-50",
                "name": "室内给水管安装 DN50",
                "unit": "m",
            },
            {
                "province": "上海市安装工程预算定额(2026)",
                "quota_id": "Q-PIPE-100",
                "name": "室内排水管安装 DN100",
                "unit": "m",
            },
        ],
    )
    connection.commit()
    connection.close()

    rows, manifest = build_review_queue(
        bill_library_path=bill_library,
        national_index_path=national_index,
        target_per_province=1,
        suggested_top_k=2,
    )

    assert rows[0]["suggested_quota_ids"][0] == "Q-PIPE-50"
    assert rows[0]["suggested_quota_names"][0] == "室内给水管安装 DN50"
    assert rows[0]["suggested_quota_books"][0] == "上海市安装工程预算定额(2026)"
    assert rows[0]["suggested_scores"][0] >= rows[0]["suggested_scores"][1]
    assert rows[0]["oracle_quota_ids"] == []
    assert rows[0]["oracle_quota_names"] == []
    assert rows[0]["oracle_semantics"] == ""
    assert rows[0]["review_selection"] == ""
    assert manifest["version"] == "accuracy_review_queue.v5"
    assert manifest["review_contract"]["allowed_review_selections"] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "reject",
    ]
    assert manifest["review_contract"]["oracle_generated_by_promotion"] is True
    assert manifest["suggestion_contract"]["advisory_only"] is True
    assert manifest["suggestion_contract"]["oracle_fields_prefilled"] is False
