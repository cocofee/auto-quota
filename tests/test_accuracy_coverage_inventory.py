import json
import sqlite3

from eval.accuracy_baseline.inventory import (
    audit_bill_library,
    audit_jsonl_group,
    audit_national_index,
    audit_oss_root,
    build_coverage_inventory,
)


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_jsonl_inventory_reports_provenance_gaps_and_cross_split_overlap(tmp_path):
    dev = tmp_path / "dev.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    _write_jsonl(
        dev,
        [
            {
                "sample_id": "1",
                "province": "安徽",
                "bill_name": "给水管",
                "expected_ids": ["Q-1"],
                "source_family": "oss/a",
                "project_id": "project-a",
                "source_file": "aggregate.jsonl",
            }
        ],
    )
    _write_jsonl(
        heldout,
        [
            {
                "sample_id": "2",
                "province": "安徽",
                "bill_name": "给水管",
                "expected_ids": ["Q-2"],
                "source_family": "oss/a",
                "project_id": "project-a",
                "source_file": "aggregate.jsonl",
            }
        ],
    )

    report = audit_jsonl_group(
        {"dev": dev, "heldout": heldout},
        evidence_role="oss_diagnostic_only",
        file_names_are_splits=True,
        aggregate_sources=("aggregate.jsonl",),
    )

    assert report["rows"] == 2
    assert report["labeled_rows"] == 2
    assert report["system_baseline_eligible"] is False
    assert report["missing"]["split"] == 2
    assert report["split_integrity"]["declared_split_used_count"] == 2
    overlaps = report["split_integrity"]["cross_split_overlap"]
    assert overlaps["query"]["count"] == 1
    assert overlaps["province_query"]["count"] == 1
    assert overlaps["source"]["count"] == 0
    assert report["split_integrity"]["ignored_aggregate_sources"] == [
        "aggregate.jsonl"
    ]
    assert overlaps["source_family"]["count"] == 1
    assert overlaps["project_id"]["count"] == 1
    assert overlaps["province"]["count"] == 1


def test_jsonl_inventory_reports_global_and_province_scoped_query_fingerprints(
    tmp_path,
):
    dev = tmp_path / "dev.jsonl"
    heldout = tmp_path / "heldout.jsonl"
    _write_jsonl(
        dev,
        [
            {
                "sample_id": "1",
                "province": "AH",
                "bill_name": "Valve",
                "bill_text": "DN50",
                "expected_ids": ["Q-1"],
            }
        ],
    )
    _write_jsonl(
        heldout,
        [
            {
                "sample_id": "2",
                "province": "ZJ",
                "bill_name": " valve ",
                "bill_text": " dn50 ",
                "expected_ids": ["Q-2"],
            }
        ],
    )

    report = audit_jsonl_group(
        {"dev": dev, "heldout": heldout},
        evidence_role="diagnostic",
        file_names_are_splits=True,
    )

    overlaps = report["split_integrity"]["cross_split_overlap"]
    assert overlaps["query"]["count"] == 1
    assert overlaps["province_query"]["count"] == 0
    assert report["duplicates"]["query_count"] == 1
    assert report["duplicates"]["province_query_count"] == 0


def test_sqlite_sampling_frames_are_not_misclassified_as_gold(tmp_path):
    national_index = tmp_path / "national.sqlite"
    connection = sqlite3.connect(national_index)
    connection.execute(
        """
        CREATE TABLE national_quotas (
            province TEXT,
            quota_id TEXT,
            name TEXT,
            specialty TEXT,
            family TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO national_quotas VALUES (?, ?, ?, ?, ?)",
        [
            ("安徽安装", "Q-1", "给水管", "C10", "pipe"),
            ("浙江安装", "Q-2", "电缆", "C4", "cable"),
        ],
    )
    connection.commit()
    connection.close()

    bill_library = tmp_path / "bill.db"
    connection = sqlite3.connect(bill_library)
    connection.execute(
        """
        CREATE TABLE files (
            file_path TEXT,
            province TEXT,
            specialty TEXT,
            bill_count INTEGER
        )
        """
    )
    connection.execute("CREATE TABLE bill_items (id INTEGER)")
    connection.executemany(
        "INSERT INTO files VALUES (?, ?, ?, ?)",
        [
            ("a.xlsx", "安徽", "给排水", 2),
            ("b.xlsx", "", "电气", 1),
        ],
    )
    connection.executemany("INSERT INTO bill_items VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    connection.close()

    quota_report = audit_national_index(national_index)
    bill_report = audit_bill_library(bill_library)

    assert quota_report["rows"] == 2
    assert quota_report["quota_book_count"] == 2
    assert quota_report["eligible_as_gold"] is False
    assert bill_report["bill_items"] == 3
    assert bill_report["missing_province_files"] == 1
    assert bill_report["eligible_as_gold"] is False


def test_oss_inventory_and_contract_draft_remain_non_baseline(tmp_path):
    province_a = tmp_path / "by_province" / "AH"
    province_b = tmp_path / "by_province" / "ZJ"
    province_a.mkdir(parents=True)
    province_b.mkdir(parents=True)
    (province_a / "a.XML").write_text("<xml />", encoding="utf-8")
    (province_b / "b.xml").write_text("<xml />", encoding="utf-8")

    oss_report = audit_oss_root(tmp_path)
    inventory = build_coverage_inventory(oss_root=tmp_path)

    assert oss_report["province_directory_count"] == 2
    assert oss_report["xml_files"] == 2
    assert oss_report["eligible_as_independent_gold"] is False
    assert inventory["system_baseline_eligible"] is False
    assert inventory["headline_policy"]["combined_score_allowed"] is False
    assert inventory["coverage_contract_draft"]["cli_compatible"] is False
    assert all(
        value is None
        for value in inventory["coverage_contract_draft"]["requirements"].values()
    )
