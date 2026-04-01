"""
Unified historical price reference store.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from db.sqlite import connect as db_connect
from db.sqlite import connect_init as db_connect_init

try:
    from src.text_normalizer import normalize_for_match as _normalize_for_match
except Exception:  # pragma: no cover
    _normalize_for_match = None

from src.utils import safe_json_list

from loguru import logger


class PriceReferenceDB:
    WRITE_RETRY_ATTEMPTS = 6
    WRITE_RETRY_BASE_DELAY = 0.25
    LOCAL_TZ = timezone(timedelta(hours=8))
    UNKNOWN_SPECIALTY = "_unknown_specialty"
    UNKNOWN_UNIT = "_unknown_unit"
    UNKNOWN_MATERIAL = "_unknown_material"
    CATEGORY_SAMPLE_LIMIT = 20
    ITEM_FACT_FIELDS = (
        ("unit_price", "equipment_unit_price"),
        ("install_price", "install_unit_price"),
        ("combined_unit_price", "equipment_combined_price"),
    )
    ITEM_PRICE_PRIORITY = {
        "equipment_combined_price": 4,
        "equipment_unit_price": 3,
        "material_unit_price": 2,
        "install_unit_price": 1,
        "composite_price": 3,
        "info_price": 2,
        "total_price": 0,
        "": 0,
    }
    MATERIAL_CATEGORY_KEYWORDS = [
        ("steel_pipe", ["钢管", "镀锌钢管", "焊接钢管", "无缝钢管", "steel pipe", "galvanized pipe"]),
        ("copper_pipe", ["铜管", "紫铜管", "copper pipe"]),
        ("plastic_pipe", ["ppr", "pe", "pvc", "hdpe", "塑料管", "plastic pipe"]),
        ("valve", ["阀门", "闸阀", "截止阀", "球阀", "蝶阀", "止回阀", "valve"]),
        ("insulation", ["保温", "橡塑", "岩棉", "玻璃棉", "insulation"]),
        ("fan_coil", ["风机盘管", "fcu", "fan coil"]),
        ("ahu", ["空调机组", "ahu", "组合式空调"]),
        ("chiller", ["冷水机组", "冷机", "离心机", "螺杆机", "chiller"]),
        ("pump", ["水泵", "循环泵", "加压泵", "消防泵", "pump"]),
        ("duct", ["风管", "镀锌风管", "铁皮风管", "复合风管", "duct"]),
        ("cable", ["电缆", "电线", "bv", "yjv", "wdzn", "cable"]),
        ("bridge", ["桥架", "线槽", "电缆桥架", "cable tray", "bridge"]),
        ("sprinkler", ["喷淋头", "喷头", "洒水喷头", "sprinkler"]),
        ("fire_hydrant", ["消火栓", "消防栓", "hydrant"]),
        ("fitting", ["管件", "弯头", "三通", "法兰", "fitting", "flange"]),
    ]

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else config.get_price_reference_db_path()
        self._init_db()

    def _init_db(self):
        conn = db_connect_init(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    synthetic_key TEXT UNIQUE,
                    file_id TEXT DEFAULT '',
                    document_type TEXT NOT NULL DEFAULT 'historical_quote_file',
                    project_name TEXT DEFAULT '',
                    project_stage TEXT DEFAULT '',
                    specialty TEXT DEFAULT '',
                    region TEXT DEFAULT '',
                    source_file_name TEXT DEFAULT '',
                    status TEXT DEFAULT 'created',
                    parse_summary TEXT DEFAULT '{}',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_quote_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    item_name_raw TEXT NOT NULL DEFAULT '',
                    item_name_normalized TEXT DEFAULT '',
                    brand TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    spec TEXT DEFAULT '',
                    unit TEXT DEFAULT '',
                    unit_price REAL,
                    install_price REAL,
                    combined_unit_price REAL,
                    specialty TEXT DEFAULT '',
                    system_name TEXT DEFAULT '',
                    region TEXT DEFAULT '',
                    source_date TEXT DEFAULT '',
                    project_name TEXT DEFAULT '',
                    remarks TEXT DEFAULT '',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (document_id) REFERENCES price_documents(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_boq_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    source_experience_id INTEGER UNIQUE,
                    seed_source TEXT DEFAULT '',
                    project_name TEXT DEFAULT '',
                    project_stage TEXT DEFAULT '',
                    specialty TEXT DEFAULT '',
                    system_name TEXT DEFAULT '',
                    subsystem_name TEXT DEFAULT '',
                    boq_code TEXT DEFAULT '',
                    boq_name_raw TEXT DEFAULT '',
                    boq_name_normalized TEXT DEFAULT '',
                    feature_text TEXT DEFAULT '',
                    work_content TEXT DEFAULT '',
                    item_features_structured TEXT DEFAULT '',
                    unit TEXT DEFAULT '',
                    quantity REAL,
                    composite_unit_price REAL,
                    quota_code TEXT DEFAULT '',
                    quota_name TEXT DEFAULT '',
                    quota_group TEXT DEFAULT '',
                    labor_cost REAL,
                    material_cost REAL,
                    machine_cost REAL,
                    management_fee REAL,
                    profit REAL,
                    measure_fee REAL,
                    other_fee REAL,
                    tax REAL,
                    currency TEXT DEFAULT 'CNY',
                    region TEXT DEFAULT '',
                    source_date TEXT DEFAULT '',
                    source_sheet TEXT DEFAULT '',
                    source_row_no INTEGER,
                    remarks TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    search_text TEXT DEFAULT '',
                    materials_json TEXT DEFAULT '[]',
                    bill_text TEXT DEFAULT '',
                    migration_flags TEXT DEFAULT '',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (document_id) REFERENCES price_documents(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_name ON historical_quote_items(item_name_raw, item_name_normalized)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_brand_model ON historical_quote_items(brand, model)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_name ON historical_boq_items(boq_name_raw, boq_name_normalized)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_quota_code ON historical_boq_items(quota_code)"
            )
            self._ensure_column(conn, "price_documents", "file_id", "TEXT DEFAULT ''")
            self._ensure_column(conn, "price_documents", "status", "TEXT DEFAULT 'created'")
            self._ensure_column(conn, "price_documents", "source_file_path", "TEXT DEFAULT ''")
            self._ensure_column(conn, "price_documents", "source_file_ext", "TEXT DEFAULT ''")
            self._ensure_column(conn, "price_documents", "parse_status", "TEXT DEFAULT 'created'")
            self._ensure_column(conn, "price_documents", "parse_version", "TEXT DEFAULT ''")
            self._ensure_column(conn, "price_documents", "parse_summary", "TEXT DEFAULT '{}'")
            self._ensure_price_columns(conn, "historical_quote_items")
            self._ensure_price_columns(conn, "historical_boq_items")
            conn.commit()
        finally:
            conn.close()

    def _ensure_price_columns(self, conn, table: str) -> None:
        self._ensure_column(conn, table, "normalized_name", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "materials_signature", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "materials_signature_first", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "price_type", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "price_value", "REAL DEFAULT NULL")
        self._ensure_column(conn, table, "price_outlier", "INTEGER DEFAULT 0")
        self._ensure_column(conn, table, "outlier_method", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "outlier_score", "REAL DEFAULT NULL")
        self._ensure_column(conn, table, "outlier_reason", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "price_date_iso", "TEXT DEFAULT NULL")
        self._ensure_column(conn, table, "date_parse_failed", "INTEGER DEFAULT 0")
        self._ensure_column(conn, table, "source_record_id", "INTEGER DEFAULT NULL")
        self._ensure_column(conn, table, "source_type", "TEXT DEFAULT ''")
        self._ensure_column(conn, table, "project_id", "TEXT DEFAULT NULL")

        if table == "historical_quote_items":
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_normalized ON historical_quote_items(normalized_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_bucket ON historical_quote_items(specialty, unit, materials_signature_first, price_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_outlier ON historical_quote_items(price_outlier)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_quote_items_materials_signature ON historical_quote_items(materials_signature)"
            )
        else:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_normalized ON historical_boq_items(normalized_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_bucket ON historical_boq_items(specialty, unit, materials_signature_first, price_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_outlier ON historical_boq_items(price_outlier)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_boq_items_materials_signature ON historical_boq_items(materials_signature)"
            )

    @staticmethod
    def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @staticmethod
    def _json_dump(value) -> str:
        return json.dumps(value or {}, ensure_ascii=False)

    @staticmethod
    def _safe_text(value) -> str:
        return str(value or "").strip()

    @staticmethod
    def _coerce_float(value) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_lock_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database schema is locked" in message

    def _write_with_retry(self, action):
        delay = self.WRITE_RETRY_BASE_DELAY
        last_error = None
        for attempt in range(1, self.WRITE_RETRY_ATTEMPTS + 1):
            conn = db_connect(self.db_path)
            try:
                result = action(conn)
                conn.commit()
                return result
            except Exception as exc:
                last_error = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
                if not self._is_lock_error(exc) or attempt >= self.WRITE_RETRY_ATTEMPTS:
                    raise
                time.sleep(delay)
                delay *= 2
            finally:
                conn.close()
        if last_error:
            raise last_error
        raise RuntimeError("write_with_retry exited without result")

    @classmethod
    def _normalize_name(cls, text: str) -> str:
        value = cls._safe_text(text)
        if not value:
            return ""
        if _normalize_for_match:
            try:
                return _normalize_for_match(value)
            except Exception:
                logger.debug("normalize_for_match failed, fallback used", exc_info=True)
        return re.sub(r"[^\u4e00-\u9fffa-z0-9]+", "", value.lower().replace("×", "x").replace("*", "x"))

    @classmethod
    def _material_category_from_text(cls, text: str) -> str:
        value = cls._safe_text(text).lower()
        if not value:
            return "other"
        for category, keywords in cls.MATERIAL_CATEGORY_KEYWORDS:
            for keyword in keywords:
                needle = keyword.lower()
                if re.fullmatch(r"[a-z0-9_. -]+", needle):
                    if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", value):
                        return category
                elif needle in value:
                    return category
        return "other"

    @classmethod
    def _compute_material_signature(cls, materials) -> str:
        items = safe_json_list(materials)
        if not items:
            raw_text = cls._safe_text(materials)
            if not raw_text:
                return ""
            category = cls._material_category_from_text(raw_text)
            return "" if category == "other" else category

        ranked: list[tuple[float, str]] = []
        for index, item in enumerate(items):
            if isinstance(item, dict):
                name = item.get("name") or item.get("material_name") or item.get("raw_name") or item.get("text") or ""
                amount = cls._coerce_float(item.get("amount"))
                if amount is None:
                    unit_price = cls._coerce_float(item.get("unit_price"))
                    qty = cls._coerce_float(item.get("qty") or item.get("quantity"))
                    if unit_price is not None and qty is not None:
                        amount = unit_price * qty
                score = amount if amount is not None else float(-(index + 1))
            else:
                name = str(item or "")
                score = float(-(index + 1))
            ranked.append((score, cls._material_category_from_text(name)))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        categories: list[str] = []
        seen: set[str] = set()
        for _, category in ranked:
            if category in seen:
                continue
            seen.add(category)
            categories.append(category)
            if len(categories) >= 3:
                break
        return "|".join(sorted(categories))

    @classmethod
    def _materials_signature_first(cls, materials_signature: str) -> str:
        value = cls._safe_text(materials_signature)
        if not value:
            return cls.UNKNOWN_MATERIAL
        return value.split("|", 1)[0] or cls.UNKNOWN_MATERIAL

    @classmethod
    def _standardize_price_date(cls, date_str: str) -> tuple[str | None, int]:
        raw = cls._safe_text(date_str)
        if not raw:
            return None, 0

        patterns = [
            (r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", True),
            (r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", True),
            (r"^(\d{4})[-/.](\d{1,2})$", False),
            (r"^(\d{4})年(\d{1,2})月$", False),
        ]
        for pattern, with_day in patterns:
            match = re.match(pattern, raw)
            if not match:
                continue
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3)) if with_day else 1
            try:
                dt = datetime(year, month, day, tzinfo=cls.LOCAL_TZ)
            except ValueError:
                return None, 1
            return dt.isoformat(), 0
        return None, 1

    @classmethod
    def _extract_date_token(cls, text: str) -> str:
        raw = cls._safe_text(text)
        if not raw:
            return ""

        patterns = [
            r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",
            r"(\d{4}年\d{1,2}月\d{1,2}日?)",
            r"(\d{4}[-/.]\d{1,2})",
            r"(\d{4}年\d{1,2}月)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if match:
                return cls._safe_text(match.group(1))
        return ""

    @classmethod
    def _infer_source_date_from_texts(cls, *texts: Any) -> str:
        for text in texts:
            token = cls._extract_date_token(str(text or ""))
            if token:
                return token
        return ""

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return float(ordered[0])
        position = (len(ordered) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)

    @classmethod
    def _detect_outliers_iqr(cls, prices: list[float]) -> list[tuple[bool, float | None, str | None]]:
        if len(prices) < 4:
            return [(False, None, None) for _ in prices]
        q1 = cls._percentile(prices, 0.25)
        q3 = cls._percentile(prices, 0.75)
        if q1 is None or q3 is None:
            return [(False, None, None) for _ in prices]
        iqr = q3 - q1
        if iqr <= 0:
            return [(False, None, None) for _ in prices]
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        results = []
        for price in prices:
            if price < lower:
                score = float((lower - price) / iqr)
                results.append((True, score, f"价格显著低于同类分布（{score:.2f}倍IQR外）"))
            elif price > upper:
                score = float((price - upper) / iqr)
                results.append((True, score, f"价格显著高于同类分布（{score:.2f}倍IQR外）"))
            else:
                results.append((False, None, None))
        return results

    @classmethod
    def _detect_outliers_magnitude(cls, prices: list[float]) -> list[tuple[bool, float | None, str | None]]:
        if len(prices) < 3:
            return [(False, None, None) for _ in prices]
        median_price = statistics.median(prices)
        if median_price <= 0:
            return [(False, None, None) for _ in prices]
        results = []
        for price in prices:
            if price > median_price * 10:
                score = float(price / median_price)
                results.append((True, score, f"价格高于同类中位数 {score:.2f} 倍"))
            elif price < median_price * 0.1:
                score = float(median_price / max(price, 1e-9))
                results.append((True, score, f"价格低于同类中位数 {score:.2f} 倍"))
            else:
                results.append((False, None, None))
        return results

    @classmethod
    def _price_type_priority(cls, price_type: str) -> int:
        return cls.ITEM_PRICE_PRIORITY.get(cls._safe_text(price_type), 0)

    @classmethod
    def _quote_row_price_value(cls, row: dict) -> float | None:
        explicit = cls._coerce_float(row.get("price_value"))
        if explicit is not None:
            return explicit
        for field in ("combined_unit_price", "unit_price", "install_price"):
            value = cls._coerce_float(row.get(field))
            if value is not None:
                return value
        return None

    @classmethod
    def _quote_row_price_type(cls, row: dict) -> str:
        value = cls._safe_text(row.get("price_type"))
        if value:
            return value
        if cls._coerce_float(row.get("combined_unit_price")) is not None:
            return "equipment_combined_price"
        if cls._coerce_float(row.get("unit_price")) is not None:
            return "equipment_unit_price"
        if cls._coerce_float(row.get("install_price")) is not None:
            return "install_unit_price"
        return ""

    @classmethod
    def _boq_row_price_value(cls, row: dict) -> float | None:
        explicit = cls._coerce_float(row.get("price_value"))
        if explicit is not None:
            return explicit
        return cls._coerce_float(row.get("composite_unit_price"))

    @classmethod
    def _boq_row_price_type(cls, row: dict) -> str:
        value = cls._safe_text(row.get("price_type"))
        if value:
            return value
        if cls._coerce_float(row.get("composite_unit_price")) is not None:
            return "composite_price"
        return ""

    @staticmethod
    def _build_synthetic_key(*, file_id: str, document_type: str, project_name: str, region: str, source_file_name: str) -> str:
        parts = [
            document_type or "unknown_type",
            file_id or "unknown_file",
            project_name or "unknown_project",
            region or "unknown_region",
            source_file_name or "unknown_source_file",
        ]
        return "||".join(parts)

    def create_document_from_file(
        self,
        *,
        file_id: str,
        document_type: str,
        project_name: str = "",
        project_stage: str = "",
        specialty: str = "",
        region: str = "",
        source_file_name: str = "",
        source_file_path: str = "",
        source_file_ext: str = "",
        status: str = "created",
        parse_status: str = "",
        parse_version: str = "",
    ) -> int:
        normalized_ext = (source_file_ext or Path(source_file_name or "").suffix or "").lower()
        parse_status = parse_status or status or "created"
        parse_version = parse_version or "priced_bill_parser_v1"

        def _action(conn):
            synthetic_key = self._build_synthetic_key(
                file_id=file_id,
                document_type=document_type,
                project_name=project_name,
                region=region,
                source_file_name=source_file_name,
            )
            existing = conn.execute(
                "SELECT id FROM price_documents WHERE (file_id=? AND document_type=?) OR synthetic_key=?",
                (file_id, document_type, synthetic_key),
            ).fetchone()
            if existing:
                document_id = int(existing[0])
                conn.execute(
                    """
                    UPDATE price_documents
                    SET project_name=COALESCE(NULLIF(?, ''), project_name),
                        project_stage=COALESCE(NULLIF(?, ''), project_stage),
                        specialty=COALESCE(NULLIF(?, ''), specialty),
                        region=COALESCE(NULLIF(?, ''), region),
                        source_file_name=COALESCE(NULLIF(?, ''), source_file_name),
                        source_file_path=COALESCE(NULLIF(?, ''), source_file_path),
                        source_file_ext=COALESCE(NULLIF(?, ''), source_file_ext),
                        status=COALESCE(NULLIF(?, ''), status),
                        parse_status=COALESCE(NULLIF(?, ''), parse_status),
                        parse_version=COALESCE(NULLIF(?, ''), parse_version),
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        project_name,
                        project_stage,
                        specialty,
                        region,
                        source_file_name,
                        source_file_path,
                        normalized_ext,
                        status,
                        parse_status,
                        parse_version,
                        time.time(),
                        document_id,
                    ),
                )
                return document_id
            cur = conn.execute(
                """
                INSERT INTO price_documents (
                    synthetic_key, file_id, document_type, project_name, project_stage, specialty,
                    region,
                    source_file_name, source_file_path, source_file_ext,
                    status, parse_status, parse_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    synthetic_key,
                    file_id,
                    document_type,
                    project_name,
                    project_stage,
                    specialty,
                    region,
                    source_file_name,
                    source_file_path,
                    normalized_ext,
                    status,
                    parse_status,
                    parse_version,
                    time.time(),
                    time.time(),
                ),
            )
            return int(cur.lastrowid)
        return self._write_with_retry(_action)

    def list_documents(
        self,
        *,
        document_type: str = "",
        page: int = 1,
        size: int = 20,
    ) -> dict:
        page = max(page, 1)
        size = max(1, min(size, 100))
        clauses = []
        params: list = []
        if document_type:
            clauses.append("document_type = ?")
            params.append(document_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * size
        conn = db_connect(self.db_path, row_factory=True)
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM price_documents {where}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, file_id, document_type, project_name, project_stage,
                       specialty, region, source_file_name, source_file_path, source_file_ext,
                       status, parse_status, parse_summary,
                       created_at, updated_at
                FROM price_documents
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, size, offset],
            ).fetchall()
        finally:
            conn.close()
        return {
            "items": [dict(row) for row in rows],
            "total": int(total),
            "page": page,
            "size": size,
        }

    def get_document(self, document_id: int) -> dict | None:
        conn = db_connect(self.db_path, row_factory=True)
        try:
            row = conn.execute(
                """
                SELECT id, file_id, document_type, project_name, project_stage,
                       specialty, region, source_file_name, source_file_path, source_file_ext,
                       status, parse_status, parse_summary,
                       created_at, updated_at
                FROM price_documents
                WHERE id=?
                """,
                (document_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def update_document_parse(
        self,
        document_id: int,
        *,
        status: str,
        parse_summary: dict,
    ) -> dict | None:
        def _action(conn):
            conn.execute(
                """
                UPDATE price_documents
                SET status=?,
                    parse_status=?,
                    parse_summary=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    status,
                    self._json_dump(parse_summary),
                    time.time(),
                    document_id,
                ),
            )
            return None
        self._write_with_retry(_action)
        return self.get_document(document_id)

    def replace_quote_items(self, document_id: int, items: list[dict]) -> int:
        def _action(conn):
            conn.execute("DELETE FROM historical_quote_items WHERE document_id=?", (document_id,))
            now = time.time()
            rows = []
            inserted = 0
            document_meta = conn.execute(
                "SELECT source_file_name, source_file_path, project_name FROM price_documents WHERE id=?",
                (document_id,),
            ).fetchone()
            for index, item in enumerate(items, start=1):
                raw_name = self._safe_text(item.get("item_name_raw") or item.get("raw_name") or item.get("name"))
                normalized_name = self._safe_text(item.get("normalized_name") or item.get("item_name_normalized"))
                normalized_name = normalized_name or self._normalize_name(raw_name)
                materials_signature = self._safe_text(item.get("materials_signature"))
                if not materials_signature:
                    materials_signature = self._compute_material_signature(item.get("materials") or item.get("materials_json"))
                materials_signature_first = self._materials_signature_first(materials_signature)
                source_date = self._safe_text(item.get("source_date")) or self._infer_source_date_from_texts(
                    item.get("source_file_name"),
                    item.get("source_file_path"),
                    document_meta[0] if document_meta else "",
                    document_meta[1] if document_meta else "",
                    item.get("project_name"),
                    document_meta[2] if document_meta else "",
                )
                price_date_iso, date_parse_failed = self._standardize_price_date(source_date)
                source_record_id = self._coerce_int(item.get("source_record_id")) or (document_id * 1_000_000 + index)

                for value_field, price_type in self.ITEM_FACT_FIELDS:
                    price_value = self._coerce_float(item.get(value_field))
                    if price_value is None:
                        continue
                    rows.append(
                        (
                            document_id,
                            raw_name,
                            self._safe_text(item.get("item_name_normalized")) or normalized_name,
                            self._safe_text(item.get("brand")),
                            self._safe_text(item.get("model")),
                            self._safe_text(item.get("spec")),
                            self._safe_text(item.get("unit")),
                            price_value if value_field == "unit_price" else None,
                            price_value if value_field == "install_price" else None,
                            price_value if value_field == "combined_unit_price" else None,
                            self._safe_text(item.get("specialty")),
                            self._safe_text(item.get("system_name")),
                            self._safe_text(item.get("region") or item.get("province")),
                            source_date,
                            self._safe_text(item.get("project_name")),
                            self._safe_text(item.get("remarks")),
                            normalized_name,
                            materials_signature,
                            materials_signature_first,
                            price_type,
                            price_value,
                            0,
                            None,
                            None,
                            None,
                            price_date_iso,
                            date_parse_failed,
                            source_record_id,
                            self._safe_text(item.get("source_type") or item.get("seed_source") or "parsed_file"),
                            self._safe_text(item.get("project_id")) or None,
                            now,
                            now,
                        )
                    )
                    inserted += 1
            if rows:
                conn.executemany(
                    """
                    INSERT INTO historical_quote_items (
                        document_id, item_name_raw, item_name_normalized, brand, model, spec, unit,
                        unit_price, install_price, combined_unit_price,
                        specialty, system_name, region, source_date, project_name, remarks,
                        normalized_name, materials_signature, materials_signature_first,
                        price_type, price_value, price_outlier, outlier_method, outlier_score, outlier_reason,
                        price_date_iso, date_parse_failed, source_record_id, source_type, project_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                self._apply_outlier_scan(conn, "historical_quote_items", document_id=document_id)
            return inserted

        return self._write_with_retry(_action)

    def replace_boq_items(self, document_id: int, items: list[dict]) -> int:
        def _action(conn):
            conn.execute("DELETE FROM historical_boq_items WHERE document_id=?", (document_id,))
            now = time.time()
            rows = []
            document_meta = conn.execute(
                "SELECT source_file_name, source_file_path, project_name FROM price_documents WHERE id=?",
                (document_id,),
            ).fetchone()
            for index, item in enumerate(items, start=1):
                raw_name = self._safe_text(item.get("boq_name_raw") or item.get("raw_name") or item.get("name"))
                normalized_name = self._safe_text(item.get("normalized_name") or item.get("boq_name_normalized"))
                normalized_name = normalized_name or self._normalize_name(raw_name)
                materials_signature = self._safe_text(item.get("materials_signature"))
                if not materials_signature:
                    materials_signature = self._compute_material_signature(item.get("materials_json") or item.get("materials"))
                materials_signature_first = self._materials_signature_first(materials_signature)
                source_date = self._safe_text(item.get("source_date")) or self._infer_source_date_from_texts(
                    item.get("source_file_name"),
                    item.get("source_file_path"),
                    document_meta[0] if document_meta else "",
                    document_meta[1] if document_meta else "",
                    item.get("project_name"),
                    document_meta[2] if document_meta else "",
                )
                price_date_iso, date_parse_failed = self._standardize_price_date(source_date)
                source_experience_id = (
                    self._coerce_int(item.get("source_record_id"))
                    or self._coerce_int(item.get("source_experience_id"))
                    or (document_id * 1_000_000 + index)
                )
                price_value = self._coerce_float(item.get("composite_unit_price"))
                rows.append(
                    (
                        document_id,
                        source_experience_id,
                        self._safe_text(item.get("seed_source") or item.get("source_type") or "parsed_file"),
                        self._safe_text(item.get("project_name")),
                        self._safe_text(item.get("project_stage")),
                        self._safe_text(item.get("specialty")),
                        self._safe_text(item.get("system_name")),
                        self._safe_text(item.get("subsystem_name")),
                        self._safe_text(item.get("boq_code")),
                        raw_name,
                        self._safe_text(item.get("boq_name_normalized")) or normalized_name,
                        self._safe_text(item.get("feature_text")),
                        self._safe_text(item.get("work_content")),
                        self._json_dump(item.get("item_features_structured"))
                        if isinstance(item.get("item_features_structured"), (dict, list))
                        else self._safe_text(item.get("item_features_structured")),
                        self._safe_text(item.get("unit")),
                        self._coerce_float(item.get("quantity")),
                        price_value,
                        self._safe_text(item.get("quota_code")),
                        self._safe_text(item.get("quota_name")),
                        self._safe_text(item.get("quota_group")),
                        self._coerce_float(item.get("labor_cost")),
                        self._coerce_float(item.get("material_cost")),
                        self._coerce_float(item.get("machine_cost")),
                        self._coerce_float(item.get("management_fee")),
                        self._coerce_float(item.get("profit")),
                        self._coerce_float(item.get("measure_fee")),
                        self._coerce_float(item.get("other_fee")),
                        self._coerce_float(item.get("tax")),
                        self._safe_text(item.get("currency") or "CNY"),
                        self._safe_text(item.get("region") or item.get("province")),
                        source_date,
                        self._safe_text(item.get("source_sheet")),
                        self._coerce_int(item.get("source_row_no")),
                        self._safe_text(item.get("remarks")),
                        self._json_dump(item.get("tags") or []),
                        self._safe_text(item.get("search_text")),
                        self._json_dump(item.get("materials_json") or []),
                        self._safe_text(item.get("bill_text")),
                        self._safe_text(item.get("migration_flags")),
                        normalized_name,
                        materials_signature,
                        materials_signature_first,
                        "composite_price" if price_value is not None else "",
                        price_value,
                        0,
                        None,
                        None,
                        None,
                        price_date_iso,
                        date_parse_failed,
                        source_experience_id,
                        self._safe_text(item.get("source_type") or item.get("seed_source") or "parsed_file"),
                        self._safe_text(item.get("project_id")) or None,
                        now,
                        now,
                    )
                )
            conn.executemany(
                """
                INSERT INTO historical_boq_items (
                    document_id, source_experience_id, seed_source,
                    project_name, project_stage, specialty,
                    system_name, subsystem_name,
                    boq_code, boq_name_raw, boq_name_normalized,
                    feature_text, work_content, item_features_structured, unit, quantity, composite_unit_price,
                    quota_code, quota_name, quota_group,
                    labor_cost, material_cost, machine_cost,
                    management_fee, profit, measure_fee, other_fee, tax,
                    currency, region, source_date, source_sheet, source_row_no,
                    remarks, tags, search_text, materials_json, bill_text, migration_flags,
                    normalized_name, materials_signature, materials_signature_first,
                    price_type, price_value, price_outlier, outlier_method, outlier_score, outlier_reason,
                    price_date_iso, date_parse_failed, source_record_id, source_type, project_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._apply_outlier_scan(conn, "historical_boq_items", document_id=document_id)
            return len(items)
        return self._write_with_retry(_action)

    def search_item_prices(
        self,
        *,
        query: str,
        specialty: str = "",
        brand: str = "",
        model: str = "",
        region: str = "",
        page: int = 1,
        size: int = 20,
    ) -> dict:
        page = max(page, 1)
        size = max(1, min(size, 100))
        query = self._safe_text(query)
        normalized_query = self._normalize_name(query)

        clauses = []
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            clauses.append(
                "(item_name_raw LIKE ? OR item_name_normalized LIKE ? OR normalized_name = ? OR brand LIKE ? OR model LIKE ?)"
            )
            params.extend([like, like, normalized_query, like, like])
        if specialty:
            clauses.append("specialty = ?")
            params.append(specialty)
        if brand:
            clauses.append("brand LIKE ?")
            params.append(f"%{brand}%")
        if model:
            clauses.append("model LIKE ?")
            params.append(f"%{model}%")
        if region:
            clauses.append("region LIKE ?")
            params.append(f"%{region}%")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * size

        conn = db_connect(self.db_path, row_factory=True)
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM historical_quote_items {where}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, item_name_raw, item_name_normalized, normalized_name, brand, model, spec, unit,
                       unit_price, install_price, combined_unit_price,
                       specialty, system_name, region, source_date, price_date_iso, date_parse_failed,
                       project_name, remarks,
                       materials_signature, materials_signature_first,
                       price_type, price_value, source_record_id,
                       price_outlier, outlier_method, outlier_score, outlier_reason
                FROM historical_quote_items
                {where}
                ORDER BY
                    CASE WHEN price_value IS NOT NULL THEN 0 ELSE 1 END,
                    price_outlier ASC,
                    COALESCE(price_date_iso, '') DESC,
                    updated_at DESC,
                    id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, size, offset],
            ).fetchall()
        finally:
            conn.close()

        return {
            "items": [self._serialize_item_search_row(dict(row)) for row in rows],
            "total": int(total),
            "page": page,
            "size": size,
        }

    def get_item_price_reference(
        self,
        *,
        query: str,
        specialty: str = "",
        brand: str = "",
        model: str = "",
        region: str = "",
        top_k: int = 20,
    ) -> dict:
        result = self.search_item_prices(
            query=query,
            specialty=specialty,
            brand=brand,
            model=model,
            region=region,
            page=1,
            size=max(top_k * 5, 100),
        )
        prices = [
            self._coerce_float(row.get("combined_unit_price")) or self._coerce_float(row.get("unit_price"))
            for row in result["items"]
            if (self._coerce_float(row.get("combined_unit_price")) or self._coerce_float(row.get("unit_price"))) is not None
            and not bool(row.get("price_outlier"))
        ]
        summary = {
            "min_unit_price": None,
            "max_unit_price": None,
            "median_unit_price": None,
            "sample_count": len(prices),
        }
        if prices:
            summary.update(
                {
                    "min_unit_price": float(min(prices)),
                    "max_unit_price": float(max(prices)),
                    "median_unit_price": float(statistics.median(prices)),
                }
            )
        return {
            "query": query,
            "reference_type": "item_price",
            "summary": summary,
            "samples": result["items"][:top_k],
            "layered_result": self._build_layered_result(
                table="historical_quote_items",
                rows=result["items"],
                query=query,
                specialty=specialty,
                brand=brand,
                model=model,
            ),
        }

    def search_composite_prices(
        self,
        *,
        query: str,
        specialty: str = "",
        quota_code: str = "",
        region: str = "",
        page: int = 1,
        size: int = 20,
    ) -> dict:
        page = max(page, 1)
        size = max(1, min(size, 100))
        query = self._safe_text(query)
        normalized_query = self._normalize_name(query)
        clauses = []
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            clauses.append(
                "(boq_name_raw LIKE ? OR boq_name_normalized LIKE ? OR normalized_name = ? OR feature_text LIKE ? OR bill_text LIKE ?)"
            )
            params.extend([like, like, normalized_query, like, like])
        if specialty:
            clauses.append("specialty = ?")
            params.append(specialty)
        if quota_code:
            clauses.append("quota_code LIKE ?")
            params.append(f"%{quota_code}%")
        if region:
            clauses.append("region LIKE ?")
            params.append(f"%{region}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (page - 1) * size
        conn = db_connect(self.db_path, row_factory=True)
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM historical_boq_items {where}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, boq_code, boq_name_raw, boq_name_normalized, normalized_name, feature_text,
                       unit, quantity, composite_unit_price, quota_code, quota_name,
                       specialty, region, source_date, price_date_iso, date_parse_failed,
                       project_name, remarks,
                       materials_signature, materials_signature_first,
                       price_type, price_value, source_record_id,
                       price_outlier, outlier_method, outlier_score, outlier_reason
                FROM historical_boq_items
                {where}
                ORDER BY
                    CASE WHEN price_value IS NOT NULL THEN 0 ELSE 1 END,
                    price_outlier ASC,
                    COALESCE(price_date_iso, '') DESC,
                    updated_at DESC,
                    id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, size, offset],
            ).fetchall()
        finally:
            conn.close()
        return {
            "items": [self._serialize_boq_search_row(dict(row)) for row in rows],
            "total": int(total),
            "page": page,
            "size": size,
        }

    def get_composite_price_reference(
        self,
        *,
        query: str,
        specialty: str = "",
        quota_code: str = "",
        region: str = "",
        top_k: int = 20,
    ) -> dict:
        result = self.search_composite_prices(
            query=query,
            specialty=specialty,
            quota_code=quota_code,
            region=region,
            page=1,
            size=max(top_k * 5, 100),
        )
        prices = [
            self._coerce_float(row.get("composite_unit_price"))
            for row in result["items"]
            if self._coerce_float(row.get("composite_unit_price")) is not None and not bool(row.get("price_outlier"))
        ]
        summary = {
            "min_composite_unit_price": None,
            "max_composite_unit_price": None,
            "median_composite_unit_price": None,
            "sample_count": len(prices),
        }
        if prices:
            summary.update(
                {
                    "min_composite_unit_price": float(min(prices)),
                    "max_composite_unit_price": float(max(prices)),
                    "median_composite_unit_price": float(statistics.median(prices)),
                }
            )
        return {
            "query": query,
            "reference_type": "composite_price",
            "summary": summary,
            "samples": result["items"][:top_k],
            "layered_result": self._build_layered_result(
                table="historical_boq_items",
                rows=result["items"],
                query=query,
                specialty=specialty,
                brand="",
                model="",
            ),
        }

    def backfill_quote_item_enhancements(self, *, document_id: int | None = None, batch_size: int = 2000) -> dict:
        return self._backfill_table_enhancements(
            table="historical_quote_items",
            raw_name_column="item_name_raw",
            legacy_normalized_column="item_name_normalized",
            materials_column=None,
            document_id=document_id,
            batch_size=batch_size,
        )

    def backfill_boq_item_enhancements(self, *, document_id: int | None = None, batch_size: int = 2000) -> dict:
        return self._backfill_table_enhancements(
            table="historical_boq_items",
            raw_name_column="boq_name_raw",
            legacy_normalized_column="boq_name_normalized",
            materials_column="materials_json",
            document_id=document_id,
            batch_size=batch_size,
        )

    def run_outlier_scan(self, *, table: str = "", document_id: int | None = None) -> dict:
        tables = [table] if table else ["historical_quote_items", "historical_boq_items"]

        def _action(conn):
            result = {}
            for target in tables:
                result[target] = self._apply_outlier_scan(conn, target, document_id=document_id)
            return result

        return self._write_with_retry(_action)

    def _backfill_table_enhancements(
        self,
        *,
        table: str,
        raw_name_column: str,
        legacy_normalized_column: str,
        materials_column: str | None,
        document_id: int | None,
        batch_size: int,
    ) -> dict:
        where = []
        params: list[Any] = []
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        select_columns = [
            "id",
            "document_id",
            raw_name_column,
            legacy_normalized_column,
            "normalized_name",
            "materials_signature",
            "materials_signature_first",
            "source_date",
            "price_date_iso",
            "date_parse_failed",
            "price_type",
            "price_value",
            "source_record_id",
            "source_type",
        ]
        if materials_column:
            select_columns.append(materials_column)
        if table == "historical_quote_items":
            select_columns.extend(["unit_price", "install_price", "combined_unit_price"])
        else:
            select_columns.extend(["composite_unit_price", "source_experience_id"])

        conn = db_connect(self.db_path, row_factory=True)
        try:
            rows = conn.execute(
                f"""
                SELECT {', '.join(f't.{column}' for column in select_columns)},
                       d.source_file_name AS _doc_source_file_name,
                       d.source_file_path AS _doc_source_file_path,
                       d.project_name AS _doc_project_name
                FROM {table} t
                LEFT JOIN price_documents d ON d.id = t.document_id
                {where_sql.replace('document_id', 't.document_id') if where_sql else ''}
                ORDER BY t.id ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

        processed = 0
        updated = 0
        chunk_index = 0
        if not rows:
            return {"table": table, "processed": processed, "updated": updated}

        for start in range(0, len(rows), max(1, batch_size)):
            chunk = rows[start:start + max(1, batch_size)]
            chunk_index += 1

            def _action(conn):
                nonlocal processed, updated
                for raw_row in chunk:
                    row = dict(raw_row)
                    updates = {}
                    raw_name = self._safe_text(row.get(raw_name_column))
                    normalized_name = self._safe_text(row.get("normalized_name")) or self._safe_text(row.get(legacy_normalized_column))
                    normalized_name = normalized_name or self._normalize_name(raw_name)
                    if normalized_name != self._safe_text(row.get("normalized_name")):
                        updates["normalized_name"] = normalized_name
                    if normalized_name != self._safe_text(row.get(legacy_normalized_column)):
                        updates[legacy_normalized_column] = normalized_name

                    materials_signature = self._safe_text(row.get("materials_signature"))
                    if not materials_signature and materials_column:
                        materials_signature = self._compute_material_signature(row.get(materials_column))
                        updates["materials_signature"] = materials_signature
                    materials_first = self._materials_signature_first(materials_signature)
                    if materials_first != self._safe_text(row.get("materials_signature_first")):
                        updates["materials_signature_first"] = materials_first

                    source_date = self._safe_text(row.get("source_date")) or self._infer_source_date_from_texts(
                        row.get("_doc_source_file_name"),
                        row.get("_doc_source_file_path"),
                        row.get("_doc_project_name"),
                    )
                    if source_date != self._safe_text(row.get("source_date")):
                        updates["source_date"] = source_date
                    price_date_iso, date_parse_failed = self._standardize_price_date(source_date)
                    if price_date_iso != row.get("price_date_iso"):
                        updates["price_date_iso"] = price_date_iso
                    if int(date_parse_failed) != int(row.get("date_parse_failed") or 0):
                        updates["date_parse_failed"] = date_parse_failed

                    if table == "historical_quote_items":
                        price_type = self._quote_row_price_type(row)
                        price_value = self._quote_row_price_value(row)
                        source_record_id = self._coerce_int(row.get("source_record_id")) or self._coerce_int(row.get("id"))
                    else:
                        price_type = self._boq_row_price_type(row)
                        price_value = self._boq_row_price_value(row)
                        source_record_id = self._coerce_int(row.get("source_record_id")) or self._coerce_int(row.get("source_experience_id")) or self._coerce_int(row.get("id"))
                    if price_type != self._safe_text(row.get("price_type")):
                        updates["price_type"] = price_type
                    if price_value != self._coerce_float(row.get("price_value")):
                        updates["price_value"] = price_value
                    if source_record_id != self._coerce_int(row.get("source_record_id")):
                        updates["source_record_id"] = source_record_id
                    if not self._safe_text(row.get("source_type")):
                        updates["source_type"] = "migrated_backfill"

                    if updates:
                        assignments = ", ".join(f"{column}=?" for column in updates)
                        conn.execute(
                            f"UPDATE {table} SET {assignments} WHERE id=?",
                            [*updates.values(), row["id"]],
                        )
                        updated += 1
                    processed += 1
                return None

            self._write_with_retry(_action)
            logger.info(
                "price backfill progress: table={} chunk={} processed={} updated={}",
                table,
                chunk_index,
                processed,
                updated,
            )

        self.run_outlier_scan(table=table, document_id=document_id)

        return {"table": table, "processed": processed, "updated": updated}

    def _apply_outlier_scan(self, conn, table: str, document_id: int | None = None) -> dict:
        where = ["price_value IS NOT NULL"]
        params: list[Any] = []
        if document_id is not None:
            where.append("document_id = ?")
            params.append(document_id)
        rows = conn.execute(
            f"""
            SELECT id, specialty, unit, materials_signature_first, price_type, price_value
            FROM {table}
            WHERE {' AND '.join(where)}
            ORDER BY specialty, unit, materials_signature_first, price_type, id
            """,
            params,
        ).fetchall()

        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            record = {
                "id": row[0],
                "specialty": row[1],
                "unit": row[2],
                "materials_signature_first": row[3],
                "price_type": row[4],
                "price_value": row[5],
            }
            key = (
                self._safe_text(record.get("specialty")) or self.UNKNOWN_SPECIALTY,
                self._safe_text(record.get("unit")) or self.UNKNOWN_UNIT,
                self._safe_text(record.get("materials_signature_first")) or self.UNKNOWN_MATERIAL,
                self._safe_text(record.get("price_type")),
            )
            grouped.setdefault(key, []).append(record)

        updated_rows = 0
        for bucket_rows in grouped.values():
            prices = [self._coerce_float(item.get("price_value")) or 0.0 for item in bucket_rows]
            iqr_flags = self._detect_outliers_iqr(prices)
            magnitude_flags = self._detect_outliers_magnitude(prices)
            for index, row in enumerate(bucket_rows):
                price = self._coerce_float(row.get("price_value")) or 0.0
                is_outlier = False
                method = None
                score = None
                reason = None
                if price <= 0:
                    is_outlier = True
                    method = "non_positive"
                    reason = "价格非正"
                else:
                    for flagged, raw_score, raw_reason, raw_method in (
                        (*iqr_flags[index], "iqr"),
                        (*magnitude_flags[index], "magnitude"),
                    ):
                        if flagged:
                            is_outlier = True
                            method = raw_method
                            score = raw_score
                            reason = raw_reason
                            break
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET price_outlier=?, outlier_method=?, outlier_score=?, outlier_reason=?
                    WHERE id=?
                    """,
                    (1 if is_outlier else 0, method, score, reason, row["id"]),
                )
                updated_rows += 1
        return {"updated_rows": updated_rows, "bucket_count": len(grouped)}

    def _serialize_item_search_row(self, row: dict) -> dict:
        row["price_type"] = self._quote_row_price_type(row)
        row["price_value"] = self._quote_row_price_value(row)
        row["price_outlier"] = bool(row.get("price_outlier"))
        return row

    def _serialize_boq_search_row(self, row: dict) -> dict:
        row["price_type"] = self._boq_row_price_type(row)
        row["price_value"] = self._boq_row_price_value(row)
        row["price_outlier"] = bool(row.get("price_outlier"))
        return row

    def _build_layered_result(
        self,
        *,
        table: str,
        rows: list[dict],
        query: str,
        specialty: str,
        brand: str,
        model: str,
    ) -> dict:
        prepared = [self._prepare_reference_row(table, row) for row in rows]
        prepared = [row for row in prepared if row.get("price_value") is not None]

        normalized_query = self._normalize_name(query)
        if normalized_query:
            exact_name_rows = [row for row in prepared if row["normalized_name"] == normalized_query]
            if exact_name_rows:
                prepared = exact_name_rows
        if specialty:
            specialty_rows = [row for row in prepared if row["specialty"] == specialty]
            if specialty_rows:
                prepared = specialty_rows

        total_sample_count = len(prepared)
        valid_sample_count = sum(1 for row in prepared if not row["price_outlier"])
        outlier_count = sum(1 for row in prepared if row["price_outlier"])

        brand_key = self._safe_text(brand).lower()
        model_key = self._safe_text(model).lower()
        exact_match = None
        brand_match = None
        category_match = self._select_best_bucket(prepared)

        if brand_key and model_key:
            exact_match = self._select_best_bucket(
                [
                    row for row in prepared
                    if self._safe_text(row.get("brand")).lower() == brand_key
                    and self._safe_text(row.get("model")).lower() == model_key
                ]
            )
        if brand_key:
            brand_match = self._select_best_bucket(
                [row for row in prepared if self._safe_text(row.get("brand")).lower() == brand_key]
            )

        recommended_price = None
        recommended_source = None
        for source_name, bucket, minimum_count in (
            ("exact_match", exact_match, 3),
            ("brand_match", brand_match, 3),
            ("category_match", category_match, 5),
        ):
            if bucket and not self._is_unknown_bucket(bucket) and int(bucket.get("sample_count") or 0) >= minimum_count:
                recommended_price = bucket.get("median_price")
                recommended_source = source_name
                break

        return {
            "exact_match": exact_match,
            "brand_match": brand_match,
            "category_match": category_match,
            "recommended_price": recommended_price,
            "recommended_source": recommended_source,
            "total_sample_count": total_sample_count,
            "valid_sample_count": valid_sample_count,
            "outlier_count": outlier_count,
        }

    def _prepare_reference_row(self, table: str, row: dict) -> dict:
        record = dict(row)
        if table == "historical_quote_items":
            raw_name = self._safe_text(record.get("item_name_raw"))
            price_type = self._quote_row_price_type(record)
            price_value = self._quote_row_price_value(record)
        else:
            raw_name = self._safe_text(record.get("boq_name_raw"))
            price_type = self._boq_row_price_type(record)
            price_value = self._boq_row_price_value(record)
        normalized_name = self._safe_text(record.get("normalized_name")) or self._normalize_name(raw_name)
        specialty = self._safe_text(record.get("specialty")) or self.UNKNOWN_SPECIALTY
        unit = self._safe_text(record.get("unit")) or self.UNKNOWN_UNIT
        materials_signature = self._safe_text(record.get("materials_signature"))
        materials_first = self._safe_text(record.get("materials_signature_first")) or self._materials_signature_first(materials_signature)
        record.update(
            {
                "raw_name": raw_name,
                "normalized_name": normalized_name,
                "price_type": price_type,
                "price_value": price_value,
                "specialty": specialty,
                "unit": unit,
                "materials_signature": materials_signature,
                "materials_signature_first": materials_first,
                "bucket_key": "|".join((specialty, unit, materials_first, self._safe_text(price_type))),
                "price_outlier": bool(record.get("price_outlier")),
            }
        )
        return record

    def _select_best_bucket(self, rows: list[dict]) -> dict | None:
        if not rows:
            return None
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["bucket_key"], []).append(row)
        ranked = sorted(
            grouped.values(),
            key=lambda bucket_rows: (
                self._bucket_valid_count(bucket_rows),
                self._price_type_priority(bucket_rows[0].get("price_type")),
                self._latest_bucket_sort_key(bucket_rows),
            ),
            reverse=True,
        )
        for bucket_rows in ranked:
            bucket = self._compute_bucket_stats(bucket_rows)
            if bucket:
                return bucket
        return None

    def _bucket_valid_count(self, rows: list[dict]) -> int:
        return sum(1 for row in rows if not row.get("price_outlier") and row.get("price_value") is not None)

    def _latest_bucket_sort_key(self, rows: list[dict]) -> str:
        dates = [self._safe_text(row.get("price_date_iso")) for row in rows if self._safe_text(row.get("price_date_iso"))]
        return max(dates) if dates else ""

    def _is_unknown_bucket(self, bucket: dict) -> bool:
        key = self._safe_text(bucket.get("bucket_key"))
        return any(flag in key for flag in (self.UNKNOWN_SPECIALTY, self.UNKNOWN_UNIT, self.UNKNOWN_MATERIAL))

    def _compute_bucket_stats(self, rows: list[dict]) -> dict | None:
        if not rows:
            return None
        valid_rows = [row for row in rows if not row.get("price_outlier") and row.get("price_value") is not None]
        prices = [float(row["price_value"]) for row in valid_rows]
        sorted_samples = sorted(
            rows,
            key=lambda row: (self._safe_text(row.get("price_date_iso")), row.get("id") or 0),
            reverse=True,
        )[:self.CATEGORY_SAMPLE_LIMIT]

        latest_row = None
        dated_valid_rows = [row for row in valid_rows if self._safe_text(row.get("price_date_iso"))]
        if dated_valid_rows:
            latest_row = max(dated_valid_rows, key=lambda row: self._safe_text(row.get("price_date_iso")))
        elif valid_rows:
            latest_row = valid_rows[0]

        return {
            "sample_count": len(prices),
            "median_price": float(statistics.median(prices)) if prices else None,
            "mean_price": float(statistics.mean(prices)) if prices else None,
            "min_price": float(min(prices)) if prices else None,
            "max_price": float(max(prices)) if prices else None,
            "p25_price": self._percentile(prices, 0.25) if prices else None,
            "p75_price": self._percentile(prices, 0.75) if prices else None,
            "latest_price": float(latest_row["price_value"]) if latest_row else None,
            "latest_date": latest_row.get("price_date_iso") if latest_row else None,
            "price_type": self._safe_text(rows[0].get("price_type")) or None,
            "bucket_key": self._safe_text(rows[0].get("bucket_key")) or None,
            "samples": [self._to_layered_sample(row) for row in sorted_samples],
        }

    def _to_layered_sample(self, row: dict) -> dict:
        return {
            "id": int(row.get("id") or 0),
            "raw_name": self._safe_text(row.get("raw_name")),
            "normalized_name": self._safe_text(row.get("normalized_name")),
            "specialty": self._safe_text(row.get("specialty")),
            "unit": self._safe_text(row.get("unit")),
            "brand": self._safe_text(row.get("brand")),
            "model": self._safe_text(row.get("model")),
            "spec": self._safe_text(row.get("spec")),
            "price_type": self._safe_text(row.get("price_type")),
            "price_value": self._coerce_float(row.get("price_value")),
            "materials_signature": self._safe_text(row.get("materials_signature")),
            "materials_signature_first": self._safe_text(row.get("materials_signature_first")),
            "region": self._safe_text(row.get("region")),
            "source_date": self._safe_text(row.get("source_date")),
            "price_date_iso": row.get("price_date_iso"),
            "date_parse_failed": int(row.get("date_parse_failed") or 0),
            "project_name": self._safe_text(row.get("project_name")),
            "quota_code": self._safe_text(row.get("quota_code")),
            "quota_name": self._safe_text(row.get("quota_name")),
            "source_record_id": self._coerce_int(row.get("source_record_id")),
            "price_outlier": bool(row.get("price_outlier")),
            "outlier_method": row.get("outlier_method"),
            "outlier_score": self._coerce_float(row.get("outlier_score")),
            "outlier_reason": row.get("outlier_reason"),
            "remarks": self._safe_text(row.get("remarks")),
        }
