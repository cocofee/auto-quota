import json
import sqlite3
import sys

import pytest

from eval.accuracy_baseline.reconstructed_assets import materialize_province_db


def _create_national_index(path):
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE national_quotas (
                province TEXT NOT NULL,
                quota_id TEXT NOT NULL,
                name TEXT NOT NULL,
                unit TEXT NOT NULL DEFAULT '',
                chapter TEXT NOT NULL DEFAULT '',
                specialty TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                material TEXT NOT NULL DEFAULT '',
                connection TEXT NOT NULL DEFAULT '',
                install_method TEXT NOT NULL DEFAULT '',
                dn REAL,
                cable_section REAL,
                cable_cores INTEGER,
                circuits INTEGER,
                concrete_grade INTEGER,
                thickness REAL,
                param_type TEXT NOT NULL DEFAULT '',
                cluster_key TEXT NOT NULL DEFAULT '',
                tokens TEXT NOT NULL DEFAULT '[]',
                normalized_text TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (province, quota_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO national_quotas (
                province, quota_id, name, unit, chapter, specialty, material,
                connection, dn, cable_section, circuits, normalized_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("demo-province", "A10-1-1", "Pipe DN50", "m", "chapter-a", "install", "steel", "threaded", 50, None, 1, "pipe dn50 threaded"),
                ("demo-province", "A10-1-2", "Pipe DN80", "m", "chapter-a", "install", "steel", "threaded", 80, None, 2, "pipe dn80 threaded"),
                ("other-province", "B1-1-1", "Other", "set", "chapter-b", "other", "", "", None, None, None, "other"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _write_primary(path, oracle_ids=("A10-1-1",)):
    path.write_text(
        json.dumps(
            {
                "sample_id": "human-1",
                "province": "demo-province",
                "bill_name": "Pipe",
                "bill_text": "DN50",
                "oracle_quota_ids": list(oracle_ids),
                "source_family": "human_user_correction",
                "project_name": "project-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_materialize_province_db_creates_compatible_schema_and_passes_gates(tmp_path):
    national_index = tmp_path / "national_index.sqlite"
    primary = tmp_path / "primary.jsonl"
    output_root = tmp_path / "output" / "reconstructed_assets"
    production_root = tmp_path / "repo" / "db" / "provinces"
    _create_national_index(national_index)
    _write_primary(primary, oracle_ids=("A10-1-1 换",))

    report = materialize_province_db(
        national_index=national_index,
        province="demo-province",
        output_root=output_root,
        primary_dataset=primary,
        production_provinces_dir=production_root,
    )

    assert report.gate_passed is True
    assert report.failed_gates == ()
    assert report.asset_mode == "reconstructed_from_national_index"
    assert report.source_row_count == 2
    assert report.target_row_count == 2
    assert report.oracle_count == 1
    assert report.missing_oracle_ids == ()
    assert report.database_path == output_root / "provinces" / "demo-province" / "quota.db"
    assert report.manifest_path.exists()
    with sqlite3.connect(report.database_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(quotas)")}
        row = conn.execute("SELECT * FROM quotas WHERE quota_id='A10-1-1'").fetchone()
        conn.row_factory = sqlite3.Row
        mapped = conn.execute("SELECT * FROM quotas WHERE quota_id='A10-1-1'").fetchone()
    assert {
        "id", "quota_id", "name", "unit", "work_type", "specialty", "chapter",
        "dn", "cable_section", "kva", "kv", "ampere", "weight_t", "material",
        "connection", "circuits", "shape", "perimeter", "large_side",
        "elevator_stops", "elevator_speed", "search_text", "book",
    } <= columns
    assert row is not None
    assert dict(mapped)["search_text"] == "pipe dn50 threaded"
    assert dict(mapped)["book"] == "A10"
    assert dict(mapped)["circuits"] == 1
    assert report.non_empty_rates["search_text"] == 1.0
    assert report.non_empty_rates["book"] == 1.0
    assert report.non_empty_rates["specialty"] == 1.0
    assert "kva" in report.unavailable_fields


def test_materialize_province_db_reports_missing_oracle_gate(tmp_path):
    national_index = tmp_path / "national_index.sqlite"
    primary = tmp_path / "primary.jsonl"
    _create_national_index(national_index)
    _write_primary(primary, oracle_ids=("A10-1-1", "MISSING"))

    report = materialize_province_db(
        national_index=national_index,
        province="demo-province",
        output_root=tmp_path / "output",
        primary_dataset=primary,
        production_provinces_dir=tmp_path / "repo" / "db" / "provinces",
    )

    assert report.gate_passed is False
    assert report.failed_gates == ("oracle_coverage",)
    assert report.missing_oracle_ids == ("MISSING",)


def test_materialize_province_db_refuses_production_destination(tmp_path):
    national_index = tmp_path / "national_index.sqlite"
    primary = tmp_path / "primary.jsonl"
    production_root = tmp_path / "repo" / "db" / "provinces"
    _create_national_index(national_index)
    _write_primary(primary)

    with pytest.raises(ValueError, match="production provinces"):
        materialize_province_db(
            national_index=national_index,
            province="demo-province",
            output_root=production_root,
            primary_dataset=primary,
            production_provinces_dir=production_root,
        )


def test_materialize_eval_province_db_cli_returns_gate_status(tmp_path, monkeypatch, capsys):
    from tools.materialize_eval_province_db import main

    national_index = tmp_path / "national_index.sqlite"
    primary = tmp_path / "primary.jsonl"
    output_root = tmp_path / "output"
    _create_national_index(national_index)
    _write_primary(primary)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_eval_province_db.py",
            "--national-index",
            str(national_index),
            "--province",
            "demo-province",
            "--output-root",
            str(output_root),
            "--primary",
            str(primary),
            "--production-provinces-dir",
            str(tmp_path / "repo" / "db" / "provinces"),
        ],
    )

    assert main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["gate_passed"] is True
    assert payload["asset_mode"] == "reconstructed_from_national_index"
    assert payload["database_path"].endswith("quota.db")
