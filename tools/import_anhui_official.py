#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Import official Anhui material price sources that are currently accessible online."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import fitz
import pandas as pd
import pdfplumber
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.import_price_pdf import import_to_db
from tools.import_wuhan_ocr import _group_into_rows, _ocr_page
from tools.pdf_profiles.base_profile import clean_price, guess_category

PROVINCE = "安徽"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = (30, 300)

TXT_SEQ = "序号"
TXT_NAME = "名称"
TXT_UNIT = "单位"
TXT_MEASURE = "计量"
TXT_CATEGORY_LP = "（"
TXT_MATERIAL_CODE = "材料编码"
TXT_MATERIAL_NAME = "材料名称"
TXT_SPEC = "规格型号"
TXT_FEATURE = "特征"
TXT_PRICE_EXCL = "除税价"
TXT_PRICE_INCL = "含税价"
TXT_NOTE_COL = "清单编制说明"
TXT_MATERIAL_MARKET = "材料市场信息价"
TXT_HUAIBEI_TITLE = "淮北工程造价"
TXT_LABOR_PRICE = "人工价格信息"
TXT_INDEX = "指标指数"
TXT_ECON_INDEX = "工程经济指标"
TXT_CREDIT = "企业信用等级"
TXT_REMARK = "备注"
TXT_NOTE = "注"
TXT_NOTE_FULL = "注："
TXT_EXPLAIN = "说明"

HUAINAN_SOURCES = [
    {"city": "淮南", "period": "2024-04", "url": "https://zjj.huainan.gov.cn/zjgl/551755905.html"},
    {"city": "淮南", "period": "2024-11", "url": "https://zjj.huainan.gov.cn/zjgl/551782733.html"},
    {"city": "淮南", "period": "2025-03", "url": "https://zjj.huainan.gov.cn/zjgl/551805494.html"},
    {"city": "淮南", "period": "2025-06", "url": "https://zjj.huainan.gov.cn/zjgl/551825860.html"},
    {"city": "淮南", "period": "2025-07", "url": "https://zjj.huainan.gov.cn/zjgl/551831841.html"},
    {"city": "淮南", "period": "2025-09", "url": "https://zjj.huainan.gov.cn/zjgl/551842045.html"},
    {"city": "淮南", "period": "2025-10", "url": "https://zjj.huainan.gov.cn/zjgl/551844428.html"},
    {"city": "淮南", "period": "2025-11", "url": "https://zjj.huainan.gov.cn/zjgl/551846599.html"},
    {"city": "淮南", "period": "2025-12", "url": "https://zjj.huainan.gov.cn/zjgl/551850311.html"},
    {"city": "淮南", "period": "2026-01", "url": "https://zjj.huainan.gov.cn/zjgl/551852750.html"},
    {"city": "淮南", "period": "2026-02", "url": "https://zjj.huainan.gov.cn/zjgl/551854543.html"},
]

HUANGSHAN_SOURCES = [
    {"city": "黄山", "period": "2025-04", "url": "https://zjj.huangshan.gov.cn/group1/M00/21/D8/wKiM92gkPceAUN3eAAk2YhOxKJA918.pdf"},
]

HUAIBEI_SOURCES = [
    {"city": "淮北", "period": "2024-02", "url": "https://hbzjj.huaibei.gov.cn/xwzx/tzgg/57748232.html"},
    {"city": "淮北", "period": "2025-04", "url": "https://hbzjj.huaibei.gov.cn/xwzx/tzgg/57927800.html"},
    {"city": "淮北", "period": "2025-09", "url": "https://hbzjj.huaibei.gov.cn/xwzx/tzgg/57996773.html"},
]

HUAIBEI_DEFAULT_COLS = {
    "seq": 0.06,
    "code": 0.19,
    "name": 0.35,
    "spec": 0.52,
    "note": 0.69,
    "unit": 0.80,
    "price_excl": 0.89,
    "price_incl": 0.96,
}


def _http_get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() in {"iso-8859-1", "latin-1"}:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp


def _normalize_cell(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    if text.lower() == "nan":
        return ""
    return text


def _download_to_tempfile(url: str, suffix: str = '.bin', attempts: int = 3) -> Path:
    last_error = None
    for _ in range(attempts):
        tmp_path: Path | None = None
        try:
            with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True) as resp:
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tmp.write(chunk)
                return tmp_path
        except Exception as exc:
            last_error = exc
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
    if last_error is not None:
        raise last_error
    raise RuntimeError(f'Failed to download {url}')


def _is_header_row(cells: list[str]) -> bool:
    joined = " ".join(cells)
    return (TXT_SEQ in joined and TXT_NAME in joined and TXT_UNIT in joined)


def _is_category_row(cells: list[str]) -> str:
    values = [c for c in cells if c]
    if not values:
        return ""
    if values[0].startswith(TXT_CATEGORY_LP):
        return values[0]
    if len(set(values)) == 1 and re.search(r"[\u4e00-\u9fff]", values[0]):
        return values[0]
    return ""


def _seq_like(text: str) -> bool:
    return bool(re.fullmatch(r"\d+", text.strip()))


def parse_huainan_html(url: str, city: str) -> list[dict]:
    html = _http_get(url).text
    tables = pd.read_html(StringIO(html))
    if not tables:
        return []
    rows = tables[0].values.tolist()

    current_section = ""
    records: list[dict] = []
    for row in rows:
        cells = [_normalize_cell(x) for x in row[:8]]
        if not any(cells):
            continue
        if _is_header_row(cells):
            continue
        category = _is_category_row(cells)
        if category:
            current_section = category
            continue
        if not _seq_like(cells[0]):
            continue

        name = cells[2]
        spec = cells[3]
        unit = cells[4]
        price = clean_price(cells[6])
        if not name or price <= 0:
            continue
        records.append({
            "name": name,
            "spec": spec,
            "unit": unit,
            "price": price,
            "tax_included": True,
            "city": city,
            "category": guess_category(name, spec),
            "subcategory": current_section,
        })
    return records


def parse_huangshan_pdf(url: str, city: str) -> list[dict]:
    tmp_path = _download_to_tempfile(url, suffix=' .pdf'.strip())

    doc = fitz.open(tmp_path)
    records: list[dict] = []
    current_section = ""
    try:
        for page in doc:
            for table in page.find_tables().tables:
                for row in table.extract():
                    cells = [_normalize_cell(x) for x in row[:7]]
                    if not any(cells):
                        continue
                    if _is_header_row(cells):
                        continue
                    category = _is_category_row(cells)
                    if category:
                        current_section = category
                        continue
                    if len(cells) < 7 or not _seq_like(cells[0]):
                        continue
                    name = cells[2]
                    spec = cells[3]
                    unit = cells[4]
                    price = clean_price(cells[6])
                    if not name or price <= 0:
                        continue
                    records.append({
                        "name": name,
                        "spec": spec,
                        "unit": unit,
                        "price": price,
                        "tax_included": True,
                        "city": city,
                        "category": guess_category(name, spec),
                        "subcategory": current_section,
                    })
    finally:
        doc.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return records


def _extract_first_attachment_url(article_url: str) -> str:
    html = _http_get(article_url).text
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html):
        href = match.group(1)
        href_lower = href.lower()
        if href_lower.endswith(".pdf") or ("/group" in href_lower and ".pdf" in href_lower):
            return href if href.startswith("http") else urljoin(article_url, href)
    raise ValueError(f"No PDF attachment found in article: {article_url}")


def _fill_missing_huaibei_cols(detected: dict[str, float]) -> dict[str, float]:
    merged = dict(HUAIBEI_DEFAULT_COLS)
    merged.update(detected)
    return merged


def _detect_huaibei_col_centers(rows: list[list[dict]], page_width: float) -> dict[str, float]:
    centers: dict[str, float] = {}
    for row in rows[:8]:
        for block in row:
            text = block["text"].replace(" ", "")
            rel_x = block["x"] / page_width
            if text == TXT_SEQ:
                centers["seq"] = rel_x
            elif TXT_MATERIAL_CODE in text:
                centers["code"] = rel_x
            elif TXT_MATERIAL_NAME in text:
                centers["name"] = rel_x
            elif TXT_SPEC in text or TXT_FEATURE in text:
                centers["spec"] = rel_x
            elif text in {TXT_MEASURE, TXT_UNIT}:
                centers["unit"] = rel_x
            elif TXT_PRICE_EXCL in text:
                centers["price_excl"] = rel_x
            elif TXT_PRICE_INCL in text:
                centers["price_incl"] = rel_x
            elif TXT_NOTE_COL in text:
                centers["note"] = rel_x
    return _fill_missing_huaibei_cols(centers)


def _assign_huaibei_col(block: dict, col_centers: dict[str, float], page_width: float) -> str:
    rel_x = block["x"] / page_width
    return min(col_centers.items(), key=lambda item: abs(rel_x - item[1]))[0]


def _looks_like_huaibei_material_page(texts: list[str]) -> bool:
    head = " ".join(texts[:80])
    return TXT_MATERIAL_MARKET in head and TXT_MATERIAL_NAME in head and (TXT_PRICE_INCL in head or TXT_PRICE_EXCL in head)


def _is_huaibei_section_row(joined: str) -> bool:
    compact = joined.replace(" ", "")
    if not compact:
        return False
    if compact.startswith(TXT_REMARK) or compact.startswith(TXT_NOTE):
        return False
    for token in [TXT_HUAIBEI_TITLE, TXT_MATERIAL_MARKET, TXT_LABOR_PRICE, TXT_INDEX, TXT_ECON_INDEX, TXT_CREDIT]:
        if token in compact:
            return False
    return bool(re.search(r"[\u4e00-\u9fff]", compact) and not re.search(r"\d+\.\d{2}", compact))


def _parse_huaibei_row(row_blocks: list[dict], page_width: float, col_centers: dict[str, float]) -> dict | None:
    if not row_blocks:
        return None

    joined = " ".join(block["text"] for block in row_blocks).strip()
    compact = joined.replace(" ", "")
    if not compact:
        return None

    header_tokens = [TXT_SEQ, TXT_MATERIAL_CODE, TXT_MATERIAL_NAME, TXT_SPEC, TXT_PRICE_INCL, TXT_PRICE_EXCL, TXT_NOTE_COL]
    skip_tokens = [TXT_HUAIBEI_TITLE, TXT_MATERIAL_MARKET, TXT_LABOR_PRICE, TXT_INDEX, TXT_ECON_INDEX]
    if any(token in compact for token in skip_tokens):
        return None
    if sum(token in compact for token in header_tokens) >= 2:
        return None
    if compact in {TXT_UNIT, TXT_MEASURE, "（元）"}:
        return None
    if compact.startswith(TXT_REMARK) or compact.startswith(TXT_NOTE_FULL) or compact.startswith(TXT_EXPLAIN):
        return None

    cols = {key: [] for key in HUAIBEI_DEFAULT_COLS}
    for block in row_blocks:
        col_name = _assign_huaibei_col(block, col_centers, page_width)
        cols[col_name].append(block["text"])

    seq = " ".join(cols["seq"]).strip()
    code = " ".join(cols["code"]).strip()
    name = " ".join(cols["name"]).strip()
    spec = " ".join(cols["spec"]).strip()
    note = " ".join(cols["note"]).strip()
    unit = " ".join(cols["unit"]).strip()
    price_excl = clean_price(" ".join(cols["price_excl"]).strip())
    price_incl = clean_price(" ".join(cols["price_incl"]).strip())

    if not code and re.fullmatch(r"[0-9A-Z]{8,}", seq.replace(" ", "")):
        code, seq = seq, ""

    if price_incl <= 0 and price_excl > 0:
        price_incl = round(price_excl * 1.13, 2)

    valid_code = bool(re.fullmatch(r"[0-9A-Z]{8,}", code.replace(" ", "")))

    if price_incl <= 0:
        if _is_huaibei_section_row(joined) and len(row_blocks) <= 4:
            return {"_section": joined}
        return None
    if not name:
        return None
    if not valid_code and not seq:
        return None

    spec_parts = [part for part in [spec, note] if part and part not in {"-", "—"}]
    full_spec = " ".join(spec_parts).strip()
    return {
        "name": name,
        "spec": full_spec,
        "unit": unit,
        "price": price_incl,
    }


def parse_huaibei_pdf(article_url: str, city: str) -> list[dict]:
    attachment_url = _extract_first_attachment_url(article_url)
    tmp_path = _download_to_tempfile(attachment_url, suffix=' .pdf'.strip())

    records: list[dict] = []
    current_section = ""
    material_started = False
    empty_pages_after_material = 0

    try:
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                blocks = _ocr_page(page)
                if not blocks:
                    if material_started:
                        empty_pages_after_material += 1
                        if empty_pages_after_material >= 5:
                            break
                    continue

                texts = [block["text"] for block in blocks if block.get("text")]
                if not texts:
                    continue

                if _looks_like_huaibei_material_page(texts):
                    material_started = True
                    empty_pages_after_material = 0
                elif not material_started:
                    continue

                head_text = " ".join(texts[:60])
                if TXT_INDEX in head_text or TXT_ECON_INDEX in head_text:
                    break

                rows = _group_into_rows(blocks)
                page_width = float(page.width) * (300 / 72)
                col_centers = _detect_huaibei_col_centers(rows, page_width)

                page_records = 0
                for row in rows:
                    parsed = _parse_huaibei_row(row, page_width, col_centers)
                    if not parsed:
                        continue
                    if "_section" in parsed:
                        current_section = parsed["_section"]
                        continue

                    records.append({
                        "name": parsed["name"],
                        "spec": parsed["spec"],
                        "unit": parsed["unit"],
                        "price": parsed["price"],
                        "tax_included": True,
                        "city": city,
                        "category": guess_category(parsed["name"], parsed["spec"]),
                        "subcategory": current_section,
                    })
                    page_records += 1

                if page_records:
                    empty_pages_after_material = 0
                else:
                    empty_pages_after_material += 1
                    if empty_pages_after_material >= 5:
                        break
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return records


def import_sources(sources: list[dict], parser_name: str, dry_run: bool = False) -> dict:
    summary = {"sources": 0, "records": 0}
    for source in sources:
        url = source["url"]
        city = source["city"]
        period = source["period"]
        if parser_name == "huainan_html":
            records = parse_huainan_html(url, city)
        elif parser_name == "huangshan_pdf":
            records = parse_huangshan_pdf(url, city)
        elif parser_name == "huaibei_pdf_ocr":
            records = parse_huaibei_pdf(url, city)
        else:
            raise ValueError(parser_name)

        print(f"{city} {period}: extracted {len(records)} record(s) from {url}")
        if records:
            stats = import_to_db(records, province=PROVINCE, period=period, source_file=url, dry_run=dry_run)
            if not dry_run:
                print(stats)
        summary["sources"] += 1
        summary["records"] += len(records)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Import online official Anhui material price sources")
    parser.add_argument("--source", choices=["all", "huainan", "huangshan", "huaibei"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total_sources = 0
    total_records = 0

    if args.source in ("all", "huainan"):
        result = import_sources(HUAINAN_SOURCES, "huainan_html", dry_run=args.dry_run)
        total_sources += result["sources"]
        total_records += result["records"]

    if args.source in ("all", "huangshan"):
        result = import_sources(HUANGSHAN_SOURCES, "huangshan_pdf", dry_run=args.dry_run)
        total_sources += result["sources"]
        total_records += result["records"]

    if args.source in ("all", "huaibei"):
        result = import_sources(HUAIBEI_SOURCES, "huaibei_pdf_ocr", dry_run=args.dry_run)
        total_sources += result["sources"]
        total_records += result["records"]

    print(f"Done. sources={total_sources} records={total_records} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
