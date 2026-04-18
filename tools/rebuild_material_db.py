#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Rebuild material.db from bundled repo assets.

This script recreates `db/common/material.db` from the raw files committed in
the repository. It initializes an empty database, then sequentially invokes the
existing province-specific importers that have local source data available.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "common" / "material.db"


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _month_period_from_name(name: str) -> str | None:
    m = re.search(r"(20\d{2})年\s*(\d{1,2})月", name)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(20\d{2})_(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"(20\d{2})(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _quarter_period_from_name(name: str) -> str | None:
    m = re.search(r"(20\d{2})年\s*Q([1-4])", name, flags=re.IGNORECASE)
    if not m:
        return None
    year = int(m.group(1))
    quarter = int(m.group(2))
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return f"{year}-{start_month:02d}~{year}-{end_month:02d}"


def _supported_pdf_period(path: Path) -> str | None:
    quarter = _quarter_period_from_name(path.name)
    if quarter:
        return quarter
    return _month_period_from_name(path.name)


@dataclass(frozen=True)
class CommandSpec:
    label: str
    args: list[str]


def _run_command(spec: CommandSpec, dry_run: bool) -> bool:
    cmd = [sys.executable, *spec.args]
    _print(f"[RUN] {spec.label}")
    if dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode == 0:
        _print(f"[ OK ] {spec.label}")
        return True
    _print(f"[FAIL] {spec.label} (exit={result.returncode})")
    return False


def _init_empty_db(reset: bool) -> None:
    if reset and DB_PATH.exists():
        backup_path = DB_PATH.with_suffix(".db.bak")
        if DB_PATH.stat().st_size > 0:
            shutil.copy2(DB_PATH, backup_path)
            _print(f"Backed up existing DB to: {backup_path}")
        DB_PATH.unlink()
    from src.material_db import MaterialDB
    MaterialDB(str(DB_PATH))
    _print(f"Initialized DB: {DB_PATH}")


def _build_supported_pdf_commands() -> list[CommandSpec]:
    configs = [
        ("guangzhou", "广东", PROJECT_ROOT / "data" / "pdf_info_price" / "guangzhou"),
        ("hainan", "海南", PROJECT_ROOT / "data" / "pdf_info_price" / "hainan"),
        ("shaanxi", "陕西", PROJECT_ROOT / "data" / "pdf_info_price" / "shaanxi"),
        ("jiangxi", "江西", PROJECT_ROOT / "data" / "pdf_info_price" / "jiangxi"),
        ("zhengzhou", "河南", PROJECT_ROOT / "data" / "pdf_info_price" / "zhengzhou"),
    ]
    commands: list[CommandSpec] = []
    for profile, province, folder in configs:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.pdf")):
            if profile == "zhengzhou" and "造价指标" in path.name:
                continue
            period = _supported_pdf_period(path)
            if not period:
                continue
            commands.append(
                CommandSpec(
                    label=f"{profile}:{path.name}",
                    args=[
                        "tools/import_price_pdf.py",
                        "--file",
                        str(path),
                        "--profile",
                        profile,
                        "--province",
                        province,
                        "--period",
                        period,
                    ],
                )
            )
    return commands


def _build_extra_commands(include_wuhan: bool, include_anhui_official: bool = False) -> list[CommandSpec]:
    commands: list[CommandSpec] = []

    anhui_importer = PROJECT_ROOT / 'tools' / 'import_anhui_official.py'
    if include_anhui_official and anhui_importer.exists():
        commands.append(CommandSpec('anhui:official', ['tools/import_anhui_official.py']))

    fujian_file = PROJECT_ROOT / "data" / "pdf_info_price" / "fujian" / "fujian_2024_materials.xlsx"
    if fujian_file.exists():
        commands.append(CommandSpec("fujian:file", ["tools/import_fujian_excel.py", "--file", str(fujian_file)]))

    qinghai_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "qinghai"
    if qinghai_dir.exists():
        commands.append(CommandSpec("qinghai:dir", ["tools/import_qinghai_pdf.py", "--dir", str(qinghai_dir)]))

    hunan_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "hunan"
    if hunan_dir.exists():
        commands.append(CommandSpec("hunan:dir", ["tools/import_hunan_pdf.py", "--dir", str(hunan_dir)]))

    xinjiang_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "xinjiang"
    if xinjiang_dir.exists():
        commands.append(CommandSpec("xinjiang:dir", ["tools/import_xinjiang_xls.py", "--import", "--dir", str(xinjiang_dir)]))

    guizhou_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "guizhou"
    if guizhou_dir.exists():
        commands.append(CommandSpec("guizhou:dir", ["tools/import_guizhou_pdf.py", "--dir", str(guizhou_dir)]))

    nanning_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "nanning"
    if nanning_dir.exists() and list(nanning_dir.glob("*_search_text.js")):
        commands.append(CommandSpec("nanning:dir", ["tools/import_nanning_js.py", "--dir", str(nanning_dir)]))

    tianjin_root = PROJECT_ROOT / "data" / "pdf_info_price" / "tianjin"
    if tianjin_root.exists():
        for path in sorted(tianjin_root.rglob("*.pdf")):
            if "市场价格" not in path.name:
                continue
            if "人工" in path.name and "材料" not in path.name:
                continue
            commands.append(CommandSpec(f"jjj:{path.name}", ["tools/import_jjj_pdf.py", "--file", str(path)]))

    if include_wuhan:
        wuhan_dir = PROJECT_ROOT / "data" / "pdf_info_price" / "wuhan"
        if wuhan_dir.exists():
            commands.append(CommandSpec("wuhan:dir", ["tools/import_wuhan_ocr.py", "--dir", str(wuhan_dir)]))

    return commands


def _db_summary() -> dict[str, object]:
    conn = sqlite3.connect(DB_PATH)
    try:
        total_materials = conn.execute("SELECT COUNT(*) FROM material_master").fetchone()[0]
        total_prices = conn.execute("SELECT COUNT(*) FROM price_fact").fetchone()[0]
        province_rows = conn.execute(
            """
            SELECT province, COUNT(*)
            FROM price_fact
            WHERE province IS NOT NULL AND province <> ''
            GROUP BY province
            ORDER BY COUNT(*) DESC, province ASC
            """
        ).fetchall()
        city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM price_fact WHERE city IS NOT NULL AND city <> ''"
        ).fetchone()[0]
        period_count = conn.execute(
            "SELECT COUNT(DISTINCT period_end) FROM price_fact WHERE period_end IS NOT NULL AND period_end <> ''"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "total_materials": total_materials,
        "total_prices": total_prices,
        "province_rows": province_rows,
        "city_count": city_count,
        "period_count": period_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild db/common/material.db from repo assets")
    parser.add_argument("--keep-existing", action="store_true", help="Append into the current DB instead of resetting it")
    parser.add_argument("--dry-run", action="store_true", help="Validate the rebuild plan without writing DB data")
    parser.add_argument(
        "--include-anhui-official",
        action="store_true",
        help="Also run the online Anhui official importer; off by default to keep rebuild offline/reproducible",
    )
    parser.add_argument("--include-wuhan", action="store_true", help="Also try the OCR-based Wuhan importer")
    args = parser.parse_args()

    reset_db = not args.keep_existing and not args.dry_run
    if reset_db:
        _print("Resetting material DB before rebuild")
    elif args.dry_run:
        _print("Dry-run mode: DB will not be modified")
    else:
        _print("Appending into existing DB")

    if not args.dry_run:
        _init_empty_db(reset=reset_db)

    commands = _build_supported_pdf_commands()
    commands.extend(
        _build_extra_commands(
            include_wuhan=args.include_wuhan,
            include_anhui_official=args.include_anhui_official,
        )
    )
    if not commands:
        _print("No rebuild sources were found")
        return 1

    _print(f"Planned import commands: {len(commands)}")
    ok_count = 0
    fail_count = 0
    for spec in commands:
        ok = _run_command(spec, dry_run=args.dry_run)
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    _print()
    _print(f"Import commands finished: ok={ok_count}, failed={fail_count}")
    if args.dry_run:
        return 0 if ok_count > 0 else 1

    summary = _db_summary()
    _print(f"DB file: {DB_PATH} ({DB_PATH.stat().st_size:,} bytes)")
    _print(f"material_master rows: {summary['total_materials']:,}")
    _print(f"price_fact rows: {summary['total_prices']:,}")
    _print(f"distinct cities: {summary['city_count']:,}")
    _print(f"distinct periods: {summary['period_count']:,}")
    _print("top provinces:")
    for province, count in summary["province_rows"][:15]:
        _print(f"  {province}: {count:,}")
    return 0 if summary["total_prices"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
