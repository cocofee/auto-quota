import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = ROOT / "web" / "backend"
if str(WEB_BACKEND) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND))

from app.api import experience as experience_api  # noqa: E402


def _make_db(path: Path, table: str, rows: int) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
        conn.executemany(
            f'INSERT INTO "{table}" (value) VALUES (?)',
            [(f"row-{idx}",) for idx in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()


def test_get_historical_source_stats_counts_existing_sqlite_sources(tmp_path, monkeypatch):
    bill_db = tmp_path / "bill_library.db"
    material_db = tmp_path / "material.db"

    _make_db(bill_db, "bill_items", 12)
    conn = sqlite3.connect(str(bill_db))
    try:
        conn.execute('CREATE TABLE "bill_descriptions" (id INTEGER PRIMARY KEY, value TEXT)')
        conn.executemany(
            'INSERT INTO "bill_descriptions" (value) VALUES (?)',
            [("desc-1",), ("desc-2",), ("desc-3",)],
        )
        conn.commit()
    finally:
        conn.close()

    _make_db(material_db, "price_fact", 7)
    conn = sqlite3.connect(str(material_db))
    try:
        conn.execute('CREATE TABLE "material_master" (id INTEGER PRIMARY KEY, value TEXT)')
        conn.executemany(
            'INSERT INTO "material_master" (value) VALUES (?)',
            [("mat-1",), ("mat-2",)],
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(experience_api, "_BILL_LIBRARY_DB_PATH", bill_db)
    monkeypatch.setattr(experience_api, "_MATERIAL_DB_PATH", material_db)

    stats, total = experience_api._get_historical_source_stats()

    assert stats["bill_items"]["count"] == 12
    assert stats["bill_descriptions"]["count"] == 3
    assert stats["price_facts"]["count"] == 7
    assert stats["material_master"]["count"] == 2
    assert total == 24
