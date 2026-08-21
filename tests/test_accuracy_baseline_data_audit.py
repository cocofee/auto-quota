import json
import sqlite3
import sys

from eval.accuracy_baseline.data_audit import (
    export_oss_diagnostic_cases,
    export_primary_cases,
    resolve_oss_project,
)


def _create_experience_db(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE experiences (
                id INTEGER PRIMARY KEY,
                bill_text TEXT NOT NULL,
                bill_name TEXT,
                bill_code TEXT,
                bill_unit TEXT,
                quota_ids TEXT NOT NULL,
                quota_names TEXT,
                source TEXT,
                confidence INTEGER,
                province TEXT,
                project_name TEXT,
                layer TEXT,
                specialty TEXT,
                disputed INTEGER DEFAULT 0
            )
            """
        )
        rows = [
            (
                1,
                "DN50 threaded",
                "Valve",
                "0310",
                "set",
                '["借Q-1 换", "Q-1*2", "Q-2"]',
                '["Quota 1", "Quota 2"]',
                "user_correction",
                95,
                "demo-province",
                "project-a",
                "authority",
                "C10",
                0,
            ),
            (2, "candidate", "Valve", "", "", '["Q-3"]', "[]", "user_correction", 80, "demo-province", "project-b", "candidate", "", 0),
            (3, "disputed", "Valve", "", "", '["Q-4"]', "[]", "user_correction", 80, "demo-province", "project-c", "authority", "", 1),
            (4, "no province", "Valve", "", "", '["Q-5"]', "[]", "user_correction", 80, "", "project-d", "authority", "", 0),
            (5, "no oracle", "Valve", "", "", "[]", "[]", "user_correction", 80, "demo-province", "project-e", "authority", "", 0),
            (6, "oss", "Valve", "", "", '["Q-6"]', "[]", "oss_import", 80, "demo-province", "project-f", "candidate", "", 0),
        ]
        conn.executemany(
            """
            INSERT INTO experiences (
                id, bill_text, bill_name, bill_code, bill_unit, quota_ids,
                quota_names, source, confidence, province, project_name,
                layer, specialty, disputed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_export_primary_cases_uses_only_human_authority_rows(tmp_path):
    db_path = tmp_path / "experience.db"
    output_path = tmp_path / "primary.jsonl"
    _create_experience_db(db_path)

    report = export_primary_cases(db_path, output_path)

    exported = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(exported) == 1
    assert exported[0] == {
        "bill_code": "0310",
        "bill_name": "Valve",
        "bill_text": "DN50 threaded",
        "confidence": 95,
        "oracle_quota_ids": ["Q-1", "Q-2"],
        "oracle_semantics": "all",
        "oracle_quota_names": ["Quota 1", "Quota 2"],
        "project_name": "project-a",
        "province": "demo-province",
        "sample_id": exported[0]["sample_id"],
        "source": "user_correction",
        "source_family": "human_user_correction",
        "specialty": "C10",
        "unit": "set",
    }
    assert exported[0]["sample_id"].startswith("human-")
    assert report.source_rows == 5
    assert report.accepted_rows == 1
    assert report.rejection_counts == {
        "disputed": 1,
        "missing_oracle": 1,
        "missing_province": 1,
        "not_authority": 1,
    }
    assert report.province_counts == {"demo-province": 1}
    assert report.project_counts == {"project-a": 1}
    assert len(report.content_sha256) == 64


def test_export_primary_cases_is_deterministic(tmp_path):
    db_path = tmp_path / "experience.db"
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    _create_experience_db(db_path)

    first = export_primary_cases(db_path, first_path)
    second = export_primary_cases(db_path, second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first.content_sha256 == second.content_sha256


def test_build_accuracy_datasets_cli_writes_manifest(tmp_path, monkeypatch, capsys):
    from tools.build_accuracy_datasets import main

    db_path = tmp_path / "experience.db"
    output_path = tmp_path / "primary.jsonl"
    summary_path = tmp_path / "manifest.json"
    _create_experience_db(db_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_accuracy_datasets.py",
            "--experience-db",
            str(db_path),
            "--primary-output",
            str(output_path),
            "--summary-output",
            str(summary_path),
        ],
    )

    assert main() == 0

    manifest = json.loads(summary_path.read_text(encoding="utf-8"))
    assert manifest["primary"]["accepted_rows"] == 1
    assert manifest["primary"]["rejection_counts"]["not_authority"] == 1
    assert manifest["primary"]["content_sha256"]
    assert json.loads(capsys.readouterr().out) == manifest


def test_resolve_oss_project_uses_province_canonical_xml(tmp_path):
    xml_root = tmp_path / "oss_samples"
    canonical = xml_root / "by_province" / "FJ" / "sample-id.XML"
    duplicate = xml_root / "fj_other" / "sample-id.XML"
    canonical.parent.mkdir(parents=True)
    duplicate.parent.mkdir(parents=True)
    canonical.write_text("<GCZJWJ />", encoding="utf-8")
    duplicate.write_text("<GCZJWJ />", encoding="utf-8")

    provenance = resolve_oss_project(
        "oss_20260528_1847_sample-id.XML",
        "福建省通用安装工程预算定额(2017)",
        xml_root,
    )

    assert provenance.project_id == "sample-id"
    assert provenance.original_file_name == "sample-id.XML"
    assert provenance.source_path == canonical.resolve()
    assert provenance.province_code == "FJ"
    assert provenance.xml_format == "gczjwj"
    assert provenance.source_family == "oss_xml/FJ/gczjwj"


def test_export_oss_diagnostic_cases_splits_only_by_project(tmp_path):
    db_path = tmp_path / "experience.db"
    xml_root = tmp_path / "oss_samples"
    output_dir = tmp_path / "oss_output"
    _create_experience_db(db_path)
    fixtures = [
        ("FJ", "project-a.XML", "<GCZJWJ />"),
        ("ZJ", "project-b.XML", "<浙江计价成果 />"),
    ]
    for province_code, name, content in fixtures:
        path = xml_root / "by_province" / province_code / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO experiences (
                id, bill_text, bill_name, bill_code, bill_unit, quota_ids,
                quota_names, source, confidence, province, project_name,
                layer, specialty, disputed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (7, "DN50", "Pipe", "A", "m", '["Q-7"]', '["Quota 7"]', "oss_import", 80, "福建省通用安装工程预算定额(2017)", "oss_20260528_1847_project-a.XML", "candidate", "C10", 0),
                (8, "DN80", "Pipe", "B", "m", '["Q-8"]', '["Quota 8"]', "oss_import", 80, "福建省通用安装工程预算定额(2017)", "oss_20260529_0844_project-a.XML", "candidate", "C10", 0),
                (9, "Device", "Device", "C", "set", '["Q-9"]', '["Quota 9"]', "oss_import", 80, "浙江省通用安装工程预算定额(2018)", "oss_20260529_0844_project-b.XML", "candidate", "C5", 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    report = export_oss_diagnostic_cases(db_path, xml_root, output_dir, "seed-v1")

    rows = []
    for split, path in report.output_paths.items():
        rows.extend(
            {**json.loads(line), "file_split": split}
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    accepted = [row for row in rows if row["project_id"] in {"project-a", "project-b"}]
    assert len(accepted) == 3
    assert len({row["file_split"] for row in accepted if row["project_id"] == "project-a"}) == 1
    assert all(row["split"] == row["file_split"] for row in accepted)
    assert all(row["source"].endswith(".XML") for row in accepted)
    assert all(row["source_family"].startswith("oss_xml/") for row in accepted)
    assert report.source_rows == 4
    assert report.accepted_rows == 3
    assert report.rejection_counts == {"missing_provenance": 1}
    assert report.project_overlap_count == 0


def test_build_accuracy_datasets_cli_writes_oss_manifest(tmp_path, monkeypatch):
    from tools.build_accuracy_datasets import main

    db_path = tmp_path / "experience.db"
    xml_root = tmp_path / "oss_samples"
    output_dir = tmp_path / "oss_output"
    summary_path = tmp_path / "manifest.json"
    _create_experience_db(db_path)
    xml_path = xml_root / "by_province" / "FJ" / "project-a.XML"
    xml_path.parent.mkdir(parents=True)
    xml_path.write_text("<GCZJWJ />", encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO experiences (
                id, bill_text, bill_name, bill_code, bill_unit, quota_ids,
                quota_names, source, confidence, province, project_name,
                layer, specialty, disputed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (7, "DN50", "Pipe", "A", "m", '["Q-7"]', '["Quota 7"]', "oss_import", 80, "福建省通用安装工程预算定额(2017)", "oss_20260528_1847_project-a.XML", "candidate", "C10", 0),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_accuracy_datasets.py",
            "--experience-db",
            str(db_path),
            "--oss-xml-root",
            str(xml_root),
            "--oss-output-dir",
            str(output_dir),
            "--summary-output",
            str(summary_path),
        ],
    )

    assert main() == 0

    manifest = json.loads(summary_path.read_text(encoding="utf-8"))
    assert manifest["oss_diagnostic"]["accepted_rows"] == 1
    assert manifest["oss_diagnostic"]["project_overlap_count"] == 0
    assert set(manifest["oss_diagnostic"]["output_paths"]) == {"train", "dev", "eval"}
