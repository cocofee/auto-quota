#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Quota vector index health checker.

Checks active province quota databases against their active Chroma index dirs.
Optionally probes the Chroma collection and rebuilds bad indices one province at a time.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config


def _list_active_provinces() -> list[str]:
    provinces_dir = config.DB_DIR / "provinces"
    if not provinces_dir.exists():
        return []
    provinces: list[str] = []
    for path in sorted(provinces_dir.iterdir(), key=lambda p: p.name):
        if not path.is_dir():
            continue
        if path.name == "test":
            continue
        quota_db_path = path / "quota.db"
        if quota_db_path.exists() and _looks_like_standard_quota_db(quota_db_path):
            provinces.append(path.name)
    return provinces


def _list_nonstandard_province_dirs() -> list[str]:
    provinces_dir = config.DB_DIR / "provinces"
    if not provinces_dir.exists():
        return []
    names: list[str] = []
    for path in sorted(provinces_dir.iterdir(), key=lambda p: p.name):
        if not path.is_dir():
            continue
        if path.name == "test":
            continue
        quota_db_path = path / "quota.db"
        if quota_db_path.exists() and not _looks_like_standard_quota_db(quota_db_path):
            names.append(path.name)
    return names


def _looks_like_standard_quota_db(quota_db_path: Path) -> bool:
    if not quota_db_path.exists() or quota_db_path.stat().st_size <= 0:
        return False
    conn = sqlite3.connect(str(quota_db_path))
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotas'"
        ).fetchone()
        return bool(row)
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _count_quota_rows(quota_db_path: Path) -> tuple[int, str]:
    conn = sqlite3.connect(str(quota_db_path))
    try:
        cur = conn.cursor()
        try:
            count = int(
                cur.execute(
                    "SELECT COUNT(*) FROM quotas WHERE search_text IS NOT NULL"
                ).fetchone()[0]
            )
            return count, ""
        except sqlite3.OperationalError as exc:
            return 0, str(exc)
    finally:
        conn.close()


def _count_header_bins(chroma_dir: Path) -> int:
    if not chroma_dir.exists():
        return 0
    return sum(1 for _ in chroma_dir.rglob("header.bin"))


def _safe_probe_collection(chroma_dir: Path) -> tuple[bool, int | None, str]:
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_dir))
        collection = client.get_collection("quotas")
        count = int(collection.count())
        return True, count, ""
    except Exception as exc:  # pragma: no cover - runtime probe
        return False, None, str(exc)


def _flush_chroma_client(chroma_dir: Path) -> None:
    from src.model_cache import ModelCache

    chroma_path = str(chroma_dir)
    client = ModelCache._chroma_clients.get(chroma_path)
    if client is None:
        return
    try:
        client.clear_system_cache()
    except Exception:
        pass
    try:
        del ModelCache._chroma_clients[chroma_path]
    except Exception:
        pass
    gc.collect()


def _rebuild_province_index(province: str, batch_size: int) -> tuple[bool, str]:
    try:
        from src.vector_engine import VectorEngine

        engine = VectorEngine(province=province)
        engine.build_index(batch_size=batch_size)
        _flush_chroma_client(engine.chroma_dir)
        return True, ""
    except Exception as exc:  # pragma: no cover - runtime rebuild
        return False, str(exc)


@dataclass
class ProvinceIndexHealth:
    province: str
    quota_db_path: str
    chroma_dir: str
    quota_db_size: int
    quota_rows: int
    quota_error: str
    has_quota_db: bool
    has_chroma_dir: bool
    has_sqlite: bool
    header_count: int
    status: str
    probe_ok: bool | None = None
    collection_count: int | None = None
    probe_error: str = ""
    rebuild_attempted: bool = False
    rebuild_ok: bool | None = None
    rebuild_error: str = ""


def _evaluate_status(
    *,
    quota_rows: int,
    quota_error: str,
    has_quota_db: bool,
    has_chroma_dir: bool,
    has_sqlite: bool,
    header_count: int,
    probe_ok: bool | None,
) -> str:
    if not has_quota_db:
        return "missing_quota_db"
    if quota_error:
        return "invalid_quota_schema"
    if quota_rows <= 0:
        return "empty_quota_db"
    if not has_chroma_dir:
        return "missing_chroma_dir"
    if not has_sqlite:
        return "missing_chroma_sqlite"
    if header_count <= 0:
        return "missing_header_bin"
    if probe_ok is False:
        return "probe_failed"
    return "ok"


def inspect_province(province: str, *, probe_chroma: bool) -> ProvinceIndexHealth:
    quota_db_path = config.get_quota_db_path(province)
    chroma_dir = config.get_chroma_quota_dir(province)
    has_quota_db = quota_db_path.exists()
    quota_db_size = quota_db_path.stat().st_size if has_quota_db else 0
    quota_rows, quota_error = _count_quota_rows(quota_db_path) if has_quota_db else (0, "")
    has_chroma_dir = chroma_dir.exists()
    has_sqlite = (chroma_dir / "chroma.sqlite3").exists()
    header_count = _count_header_bins(chroma_dir)

    probe_ok: bool | None = None
    collection_count: int | None = None
    probe_error = ""
    if probe_chroma and has_chroma_dir and has_sqlite:
        probe_ok, collection_count, probe_error = _safe_probe_collection(chroma_dir)

    status = _evaluate_status(
        quota_rows=quota_rows,
        quota_error=quota_error,
        has_quota_db=has_quota_db,
        has_chroma_dir=has_chroma_dir,
        has_sqlite=has_sqlite,
        header_count=header_count,
        probe_ok=probe_ok,
    )
    return ProvinceIndexHealth(
        province=province,
        quota_db_path=str(quota_db_path),
        chroma_dir=str(chroma_dir),
        quota_db_size=quota_db_size,
        quota_rows=quota_rows,
        quota_error=quota_error,
        has_quota_db=has_quota_db,
        has_chroma_dir=has_chroma_dir,
        has_sqlite=has_sqlite,
        header_count=header_count,
        status=status,
        probe_ok=probe_ok,
        collection_count=collection_count,
        probe_error=probe_error,
    )


def _render_table(rows: Iterable[ProvinceIndexHealth], only_bad: bool) -> str:
    lines = []
    header = (
        f"{'province':<30} {'status':<22} {'rows':>8} "
        f"{'sqlite':<6} {'header':>6} {'probe':<6} chroma_dir"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        if only_bad and row.status == "ok":
            continue
        probe_text = "-"
        if row.probe_ok is True:
            probe_text = "ok"
        elif row.probe_ok is False:
            probe_text = "fail"
        lines.append(
            f"{row.province:<30} {row.status:<22} {row.quota_rows:>8} "
            f"{str(row.has_sqlite):<6} {row.header_count:>6} {probe_text:<6} {row.chroma_dir}"
        )
    return "\n".join(lines)


def _write_report(payload: dict) -> Path:
    report_dir = PROJECT_ROOT / "output" / "health_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"vector_index_health_{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check active quota vector index health.")
    parser.add_argument("--province", action="append", help="Only inspect specified province(s).")
    parser.add_argument(
        "--include-nonstandard",
        action="store_true",
        help="Include placeholder/non-standard province dirs in inspection.",
    )
    parser.add_argument("--only-bad", action="store_true", help="Print only bad provinces.")
    parser.add_argument("--probe-chroma", action="store_true", help="Probe Chroma collection count.")
    parser.add_argument(
        "--rebuild-bad",
        action="store_true",
        help="Rebuild bad province indices in place, one by one.",
    )
    parser.add_argument("--batch-size", type=int, default=512, help="Batch size for rebuild.")
    args = parser.parse_args()

    default_provinces = _list_active_provinces()
    nonstandard_dirs = _list_nonstandard_province_dirs()
    provinces = args.province or (
        default_provinces + nonstandard_dirs if args.include_nonstandard else default_provinces
    )
    results: list[ProvinceIndexHealth] = []

    print(f"VECTOR_MODEL_KEY={config.VECTOR_MODEL_KEY}")
    print(f"active_provinces={len(default_provinces)}")
    print(f"nonstandard_dirs={len(nonstandard_dirs)}")
    print(f"inspected_provinces={len(provinces)}")

    for province in provinces:
        row = inspect_province(province, probe_chroma=args.probe_chroma)
        if args.rebuild_bad and row.status != "ok":
            row.rebuild_attempted = True
            ok, err = _rebuild_province_index(province, batch_size=args.batch_size)
            row.rebuild_ok = ok
            row.rebuild_error = err
            row = inspect_province(province, probe_chroma=args.probe_chroma)
            row.rebuild_attempted = True
            row.rebuild_ok = ok
            row.rebuild_error = err
        results.append(row)

    bad_rows = [row for row in results if row.status != "ok"]
    status_counts: dict[str, int] = {}
    for row in results:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1

    print(_render_table(results, only_bad=args.only_bad))
    print("")
    print(f"bad={len(bad_rows)}/{len(results)}")
    print(f"status_counts={json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "vector_model_key": config.VECTOR_MODEL_KEY,
        "active_provinces": len(default_provinces),
        "nonstandard_dirs": nonstandard_dirs,
        "inspected_provinces": len(provinces),
        "bad_provinces": len(bad_rows),
        "status_counts": status_counts,
        "results": [asdict(row) for row in results],
    }
    report_path = _write_report(payload)
    print(f"report={report_path}")

    return 1 if bad_rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
