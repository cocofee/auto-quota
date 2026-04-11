# -*- coding: utf-8 -*-
"""
Knowledge staging layer.

Purpose:
1. Initialize and validate the unified staging SQLite database.
2. Provide a stable read/write API for P0 tables.
3. Keep OpenClaw writes constrained to the staging layer.

Current P0 scope:
- audit_errors
- promotion_queue
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

import config
from db.sqlite import connect as _db_connect, connect_init as _db_connect_init


def get_knowledge_staging_db_path() -> Path:
    """Return the configured knowledge staging db path."""
    return config.get_knowledge_staging_db_path()


def get_knowledge_staging_schema_path() -> Path:
    """Return the configured knowledge staging schema SQL path."""
    return config.get_knowledge_staging_schema_path()


class KnowledgeStaging:
    """Unified staging database wrapper for OpenClaw-authored knowledge."""

    _INIT_LOCK = threading.Lock()
    _INITIALIZED_PATHS: set[str] = set()
    COLUMN_SPECS = {
        "promotion_queue": {
            "review_status": "TEXT NOT NULL DEFAULT 'unreviewed'",
            "reviewer": "TEXT NOT NULL DEFAULT ''",
            "reviewed_at": "REAL",
            "review_comment": "TEXT NOT NULL DEFAULT ''",
            "rejection_reason": "TEXT NOT NULL DEFAULT ''",
            "promoted_target_ref": "TEXT NOT NULL DEFAULT ''",
            "promotion_trace": "TEXT NOT NULL DEFAULT ''",
        },
        "audit_errors": {
            "review_status": "TEXT NOT NULL DEFAULT 'unreviewed'",
            "reviewer": "TEXT NOT NULL DEFAULT ''",
            "reviewed_at": "REAL",
            "review_comment": "TEXT NOT NULL DEFAULT ''",
        },
    }

    REQUIRED_TABLES = {
        "schema_info",
        "drawing_extractions",
        "audit_errors",
        "pricing_case_summaries",
        "quick_notes_structured",
        "promotion_queue",
    }
    REQUIRED_VIEWS = {
        "v_pending_promotions",
        "v_active_audit_errors",
    }
    JSON_FIELDS = {
        "audit_errors": {"root_cause_tags"},
        "promotion_queue": {"candidate_payload"},
    }
    TABLE_FIELDS = {
        "audit_errors": {
            "source_id",
            "source_type",
            "source_table",
            "source_record_id",
            "created_at",
            "updated_at",
            "owner",
            "version",
            "evidence_ref",
            "status",
            "content_hash",
            "review_status",
            "reviewer",
            "reviewed_at",
            "review_comment",
            "is_deleted",
            "task_id",
            "result_id",
            "project_id",
            "province",
            "specialty",
            "bill_name",
            "bill_desc",
            "predicted_quota_code",
            "predicted_quota_name",
            "corrected_quota_code",
            "corrected_quota_name",
            "match_source",
            "error_type",
            "error_level",
            "root_cause",
            "root_cause_tags",
            "fix_suggestion",
            "decision_basis",
            "requires_manual_followup",
            "can_promote_rule",
            "can_promote_method",
        },
        "promotion_queue": {
            "source_id",
            "source_type",
            "source_table",
            "source_record_id",
            "created_at",
            "updated_at",
            "owner",
            "version",
            "evidence_ref",
            "status",
            "content_hash",
            "review_status",
            "reviewer",
            "reviewed_at",
            "review_comment",
            "is_deleted",
            "candidate_type",
            "target_layer",
            "candidate_title",
            "candidate_summary",
            "candidate_payload",
            "priority",
            "approval_required",
            "promoted_at",
            "promoted_target_id",
            "promoted_target_ref",
            "target_version",
            "promotion_trace",
            "rejection_reason",
        },
    }

    def __init__(self, db_path: str | Path | None = None,
                 schema_path: str | Path | None = None,
                 auto_init: bool = True):
        self.db_path = Path(db_path) if db_path else get_knowledge_staging_db_path()
        self.schema_path = Path(schema_path) if schema_path else get_knowledge_staging_schema_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if auto_init:
            self.ensure_initialized()

    def _path_key(self) -> str:
        try:
            return str(self.db_path.resolve())
        except Exception:
            return str(self.db_path)

    def _schema_ready(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            conn = _db_connect(self.db_path, row_factory=True)
            try:
                tables = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                views = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='view'"
                    ).fetchall()
                }
            finally:
                conn.close()
        except Exception:
            return False
        return self.REQUIRED_TABLES.issubset(tables) and self.REQUIRED_VIEWS.issubset(views)

    def ensure_initialized(self) -> None:
        """Initialize the staging db only when the schema is missing."""
        path_key = self._path_key()
        if path_key in self._INITIALIZED_PATHS and self.db_path.exists():
            self._upgrade_schema_if_needed()
            return

        with self._INIT_LOCK:
            if path_key in self._INITIALIZED_PATHS and self.db_path.exists():
                self._upgrade_schema_if_needed()
                return
            if self._schema_ready():
                self._upgrade_schema_if_needed()
                self._INITIALIZED_PATHS.add(path_key)
                return
            self.init_db(force=True)
            self._INITIALIZED_PATHS.add(path_key)

    def init_db(self, *, force: bool = False) -> None:
        """Initialize or upgrade the staging database from the schema SQL."""
        if not self.schema_path.exists():
            raise FileNotFoundError(f"knowledge staging schema not found: {self.schema_path}")
        if not force and self._schema_ready():
            self._INITIALIZED_PATHS.add(self._path_key())
            return

        sql = self.schema_path.read_text(encoding="utf-8")
        conn = _db_connect_init(self.db_path)
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()
        self._upgrade_schema_if_needed()
        self._INITIALIZED_PATHS.add(self._path_key())
        logger.debug(f"knowledge staging initialized: {self.db_path}")

    def _upgrade_schema_if_needed(self) -> None:
        """Backfill newly added columns for older staging databases."""
        if not self.db_path.exists():
            return

        conn = _db_connect_init(self.db_path)
        try:
            changed = False
            for table, columns in self.COLUMN_SPECS.items():
                existing = {
                    str(row[1])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, spec in columns.items():
                    if column in existing:
                        continue
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
                    changed = True
                    logger.info(f"knowledge staging upgraded: added {table}.{column}")
            if changed:
                conn.commit()
        finally:
            conn.close()

    def _connect(self, row_factory: bool = False) -> sqlite3.Connection:
        return _db_connect(self.db_path, row_factory=row_factory)

    @staticmethod
    def _stable_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _encode_json_fields(cls, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        json_fields = cls.JSON_FIELDS.get(table, set())
        result = dict(payload)
        for field in json_fields:
            value = result.get(field)
            if value is None:
                continue
            if isinstance(value, str):
                continue
            result[field] = cls._stable_json(value)
        return result

    @classmethod
    def _decode_json_fields(cls, table: str, row: dict[str, Any]) -> dict[str, Any]:
        json_fields = cls.JSON_FIELDS.get(table, set())
        result = dict(row)
        for field in json_fields:
            raw = result.get(field)
            if not isinstance(raw, str) or not raw:
                continue
            try:
                result[field] = json.loads(raw)
            except Exception:
                pass
        return result

    @staticmethod
    def _compute_content_hash(payload: dict[str, Any]) -> str:
        filtered = {
            k: v for k, v in payload.items()
            if k not in {"created_at", "updated_at", "reviewed_at", "promoted_at"}
        }
        return hashlib.md5(
            json.dumps(filtered, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _prepare_insert_payload(cls, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        if table not in cls.TABLE_FIELDS:
            raise ValueError(f"unsupported staging table: {table}")

        now = time.time()
        allowed = cls.TABLE_FIELDS[table]
        data = {k: v for k, v in payload.items() if k in allowed}
        data.setdefault("source_id", "")
        data.setdefault("source_type", "")
        data.setdefault("source_table", "")
        data.setdefault("source_record_id", "")
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("owner", "")
        data.setdefault("version", 1)
        data.setdefault("evidence_ref", "")
        data.setdefault("status", "draft")
        data.setdefault("review_status", "unreviewed")
        data.setdefault("reviewer", "")
        data.setdefault("review_comment", "")
        data.setdefault("is_deleted", 0)
        if not data.get("content_hash"):
            data["content_hash"] = cls._compute_content_hash(data)
        return cls._encode_json_fields(table, data)

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        """Execute a write statement in its own transaction."""
        conn = self._connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query_one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        """Execute a query and return one row as dict."""
        conn = self._connect(row_factory=True)
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def query_all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        """Execute a query and return rows as dict list."""
        conn = self._connect(row_factory=True)
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _normalize_path(db_path: str | Path) -> Path:
        return Path(db_path)

    def _external_scalar(self, db_path: str | Path, sql: str, params: tuple | list = ()) -> int:
        path = self._normalize_path(db_path)
        if not path.exists():
            return 0
        conn = _db_connect(path, row_factory=True)
        try:
            row = conn.execute(sql, params).fetchone()
            if not row:
                return 0
            if isinstance(row, sqlite3.Row):
                value = next(iter(dict(row).values()), 0)
            else:
                value = row[0]
            return int(value or 0)
        except Exception:
            return 0
        finally:
            conn.close()

    def _external_rows(self, db_path: str | Path, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        path = self._normalize_path(db_path)
        if not path.exists():
            return []
        conn = _db_connect(path, row_factory=True)
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _count_by(self, table: str, field: str) -> dict[str, int]:
        rows = self.query_all(
            f"""
            SELECT COALESCE(NULLIF(TRIM({field}), ''), '(empty)') AS bucket, COUNT(*) AS total
            FROM {table}
            WHERE is_deleted = 0
            GROUP BY bucket
            ORDER BY total DESC, bucket ASC
            """
        )
        return {str(row["bucket"]): int(row["total"]) for row in rows}

    def _promotion_breakdown(self, field: str) -> list[dict[str, Any]]:
        rows = self.query_all(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM({field}), ''), '(empty)') AS bucket,
                COALESCE(NULLIF(TRIM(status), ''), '(empty)') AS status,
                COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
            GROUP BY bucket, status
            ORDER BY bucket ASC, status ASC
            """
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = str(row["bucket"])
            status = str(row["status"])
            total = int(row["total"])
            item = grouped.setdefault(bucket, {
                "bucket": bucket,
                "draft": 0,
                "reviewing": 0,
                "approved": 0,
                "rejected": 0,
                "promoted": 0,
                "rolled_back": 0,
                "total": 0,
            })
            if status in {"draft", "reviewing", "approved", "rejected", "promoted", "rolled_back"}:
                item[status] = total
            item["total"] += total

        results: list[dict[str, Any]] = []
        for item in grouped.values():
            approved_total = int(item["approved"]) + int(item["promoted"])
            rejected_total = int(item["rejected"])
            reviewed_total = approved_total + rejected_total
            result = dict(item)
            result["reviewed_total"] = reviewed_total
            result["approved_total"] = approved_total
            result["rejected_total"] = rejected_total
            result["approval_rate"] = self._safe_rate(approved_total, reviewed_total)
            result["rejection_rate"] = self._safe_rate(rejected_total, reviewed_total)
            result["execution_rate"] = self._safe_rate(int(item["promoted"]), approved_total)
            results.append(result)
        results.sort(key=lambda item: (-int(item["total"]), str(item["bucket"])))
        return results

    def _rejection_reason_breakdown(self, field: str, *, reason_limit: int = 3) -> list[dict[str, Any]]:
        rows = self.query_all(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM({field}), ''), '(empty)') AS bucket,
                COALESCE(NULLIF(TRIM(rejection_reason), ''), '(empty)') AS rejection_reason,
                COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status = 'rejected'
            GROUP BY bucket, rejection_reason
            ORDER BY bucket ASC, total DESC, rejection_reason ASC
            """
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = str(row["bucket"])
            item = grouped.setdefault(bucket, {
                "bucket": bucket,
                "rejected_total": 0,
                "top_reasons": [],
            })
            total = int(row["total"])
            item["rejected_total"] += total
            if len(item["top_reasons"]) < max(1, reason_limit):
                item["top_reasons"].append({
                    "reason": str(row["rejection_reason"]),
                    "count": total,
                })
        results = list(grouped.values())
        results.sort(key=lambda item: (-int(item["rejected_total"]), str(item["bucket"])))
        return results

    @staticmethod
    def _safe_rate(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 1)

    def _recent_activity(self, days: int = 7) -> list[dict[str, Any]]:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=max(1, days) - 1)
        date_keys = [
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range((end_date - start_date).days + 1)
        ]
        buckets = {
            date_key: {
                "date": date_key,
                "audit_created": 0,
                "promotion_created": 0,
                "promotion_reviewed": 0,
                "promotion_promoted": 0,
            }
            for date_key in date_keys
        }

        def merge_counts(rows: list[dict[str, Any]], key: str) -> None:
            for row in rows:
                bucket = str(row.get("bucket_date") or "")
                if bucket in buckets:
                    buckets[bucket][key] = int(row.get("total") or 0)

        audit_created_rows = self.query_all(
            """
            SELECT date(datetime(created_at, 'unixepoch', 'localtime')) AS bucket_date, COUNT(*) AS total
            FROM audit_errors
            WHERE is_deleted = 0
              AND created_at IS NOT NULL
              AND created_at > 0
            GROUP BY bucket_date
            """
        )
        promotion_created_rows = self.query_all(
            """
            SELECT date(datetime(created_at, 'unixepoch', 'localtime')) AS bucket_date, COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND created_at IS NOT NULL
              AND created_at > 0
            GROUP BY bucket_date
            """
        )
        promotion_reviewed_rows = self.query_all(
            """
            SELECT date(datetime(reviewed_at, 'unixepoch', 'localtime')) AS bucket_date, COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND reviewed_at IS NOT NULL
              AND reviewed_at > 0
              AND status IN ('approved', 'rejected', 'promoted')
            GROUP BY bucket_date
            """
        )
        promotion_promoted_rows = self.query_all(
            """
            SELECT date(datetime(promoted_at, 'unixepoch', 'localtime')) AS bucket_date, COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND promoted_at IS NOT NULL
              AND promoted_at > 0
              AND status = 'promoted'
            GROUP BY bucket_date
            """
        )

        merge_counts(audit_created_rows, "audit_created")
        merge_counts(promotion_created_rows, "promotion_created")
        merge_counts(promotion_reviewed_rows, "promotion_reviewed")
        merge_counts(promotion_promoted_rows, "promotion_promoted")
        return [buckets[date_key] for date_key in date_keys]

    def get_schema_version(self) -> str:
        row = self.query_one("SELECT value FROM schema_info WHERE key = 'schema_version'")
        return str(row["value"]) if row else ""

    def health_check(self) -> dict[str, Any]:
        """Return staging db health information."""
        tables = {
            row["name"] for row in self.query_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        views = {
            row["name"] for row in self.query_all(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        missing_tables = sorted(self.REQUIRED_TABLES - tables)
        missing_views = sorted(self.REQUIRED_VIEWS - views)
        return {
            "db_path": str(self.db_path),
            "schema_path": str(self.schema_path),
            "schema_version": self.get_schema_version(),
            "ok": not missing_tables and not missing_views,
            "missing_tables": missing_tables,
            "missing_views": missing_views,
        }

    def get_dashboard_stats(self) -> dict[str, Any]:
        """Return minimal staging dashboard metrics for the admin workbench."""
        audit_total_row = self.query_one(
            "SELECT COUNT(*) AS total FROM audit_errors WHERE is_deleted = 0"
        ) or {"total": 0}
        promotion_total_row = self.query_one(
            "SELECT COUNT(*) AS total FROM promotion_queue WHERE is_deleted = 0"
        ) or {"total": 0}
        rejected_reason_rows = self.query_all(
            """
            SELECT rejection_reason, COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status = 'rejected'
              AND TRIM(COALESCE(rejection_reason, '')) != ''
            GROUP BY rejection_reason
            ORDER BY total DESC, rejection_reason ASC
            LIMIT 5
            """
        )
        promotion_status_counts = self._count_by("promotion_queue", "status")
        approved_or_promoted_total = (
            int(promotion_status_counts.get("approved", 0)) +
            int(promotion_status_counts.get("promoted", 0))
        )
        rejected_total = int(promotion_status_counts.get("rejected", 0))
        reviewed_total = approved_or_promoted_total + rejected_total
        recent_activity = self._recent_activity(days=7)
        return {
            "audit_total": int(audit_total_row["total"]),
            "promotion_total": int(promotion_total_row["total"]),
            "promotion_status_counts": promotion_status_counts,
            "promotion_target_counts": self._count_by("promotion_queue", "target_layer"),
            "promotion_candidate_counts": self._count_by("promotion_queue", "candidate_type"),
            "promotion_target_metrics": self._promotion_breakdown("target_layer"),
            "promotion_candidate_metrics": self._promotion_breakdown("candidate_type"),
            "rejection_reason_by_target": self._rejection_reason_breakdown("target_layer"),
            "rejection_reason_by_candidate": self._rejection_reason_breakdown("candidate_type"),
            "audit_review_counts": self._count_by("audit_errors", "review_status"),
            "audit_match_source_counts": self._count_by("audit_errors", "match_source"),
            "audit_error_type_counts": self._count_by("audit_errors", "error_type"),
            "promotion_reviewed_total": reviewed_total,
            "promotion_approved_total": approved_or_promoted_total,
            "promotion_rejected_total": rejected_total,
            "promotion_approval_rate": self._safe_rate(approved_or_promoted_total, reviewed_total),
            "promotion_rejection_rate": self._safe_rate(rejected_total, reviewed_total),
            "promotion_execution_rate": self._safe_rate(
                int(promotion_status_counts.get("promoted", 0)),
                approved_or_promoted_total,
            ),
            "recent_activity": recent_activity,
            "top_rejection_reasons": [
                {
                    "reason": str(row["rejection_reason"]),
                    "count": int(row["total"]),
                }
                for row in rejected_reason_rows
            ],
        }

    def get_health_report(self, *, stale_pending_days: int = 7, limit: int = 10) -> dict[str, Any]:
        """Return governance-focused health checks for staging and formal layers."""
        stale_days = max(1, int(stale_pending_days))
        item_limit = max(1, int(limit))
        now = time.time()
        stale_cutoff = now - (stale_days * 86400)

        duplicate_group_total_row = self.query_one(
            """
            SELECT COUNT(*) AS total
            FROM (
                SELECT 1
                FROM promotion_queue
                WHERE is_deleted = 0
                GROUP BY target_layer, candidate_type, candidate_title, candidate_payload
                HAVING COUNT(*) > 1
            )
            """
        ) or {"total": 0}
        duplicate_candidate_groups = self.query_all(
            """
            SELECT
                target_layer,
                candidate_type,
                candidate_title,
                COUNT(*) AS duplicate_count,
                COUNT(DISTINCT source_table || ':' || source_record_id) AS source_count,
                MIN(created_at) AS oldest_created_at,
                MAX(created_at) AS latest_created_at,
                GROUP_CONCAT(id) AS sample_ids
            FROM promotion_queue
            WHERE is_deleted = 0
            GROUP BY target_layer, candidate_type, candidate_title, candidate_payload
            HAVING COUNT(*) > 1
            ORDER BY duplicate_count DESC, latest_created_at DESC, candidate_title ASC
            LIMIT ?
            """,
            (item_limit,),
        )

        stale_pending_total_row = self.query_one(
            """
            SELECT COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status IN ('draft', 'reviewing')
              AND created_at > 0
              AND created_at <= ?
            """,
            (stale_cutoff,),
        ) or {"total": 0}
        stale_pending_promotions = self.query_all(
            """
            SELECT
                id,
                source_table,
                source_record_id,
                candidate_type,
                target_layer,
                candidate_title,
                status,
                review_status,
                created_at,
                ROUND((? - created_at) / 86400.0, 1) AS age_days
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status IN ('draft', 'reviewing')
              AND created_at > 0
              AND created_at <= ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (now, stale_cutoff, item_limit),
        )

        rolled_back_total_row = self.query_one(
            """
            SELECT COUNT(*) AS total
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status = 'rolled_back'
            """
        ) or {"total": 0}
        recent_rollbacks = self.query_all(
            """
            SELECT
                id,
                source_table,
                source_record_id,
                candidate_type,
                target_layer,
                candidate_title,
                reviewed_at,
                reviewer,
                review_comment,
                promoted_target_ref
            FROM promotion_queue
            WHERE is_deleted = 0
              AND status = 'rolled_back'
            ORDER BY COALESCE(reviewed_at, updated_at) DESC, id DESC
            LIMIT ?
            """,
            (item_limit,),
        )

        source_conflict_total_row = self.query_one(
            """
            SELECT COUNT(*) AS total
            FROM (
                SELECT 1
                FROM promotion_queue
                WHERE is_deleted = 0
                  AND TRIM(COALESCE(source_table, '')) != ''
                  AND TRIM(COALESCE(source_record_id, '')) != ''
                GROUP BY source_table, source_record_id
                HAVING COUNT(DISTINCT target_layer) > 1
            )
            """
        ) or {"total": 0}
        source_conflict_groups = self.query_all(
            """
            SELECT
                source_table,
                source_record_id,
                COUNT(*) AS candidate_count,
                COUNT(DISTINCT target_layer) AS target_layer_count,
                GROUP_CONCAT(DISTINCT target_layer) AS target_layers,
                GROUP_CONCAT(DISTINCT candidate_type) AS candidate_types,
                MAX(updated_at) AS latest_updated_at
            FROM promotion_queue
            WHERE is_deleted = 0
              AND TRIM(COALESCE(source_table, '')) != ''
              AND TRIM(COALESCE(source_record_id, '')) != ''
            GROUP BY source_table, source_record_id
            HAVING COUNT(DISTINCT target_layer) > 1
            ORDER BY target_layer_count DESC, candidate_count DESC, latest_updated_at DESC
            LIMIT ?
            """,
            (item_limit,),
        )

        rule_db_path = config.COMMON_DB_DIR / "rule_knowledge.db"
        method_db_path = config.COMMON_DB_DIR / "method_cards.db"
        experience_db_path = config.get_experience_db_path()

        formal_layer_health = {
            "inactive_rules": self._external_scalar(
                rule_db_path,
                "SELECT COUNT(*) AS total FROM rules WHERE COALESCE(is_active, 1) = 0",
            ),
            "inactive_method_cards": self._external_scalar(
                method_db_path,
                "SELECT COUNT(*) AS total FROM method_cards WHERE COALESCE(is_active, 1) = 0",
            ),
            "experience_candidate_count": self._external_scalar(
                experience_db_path,
                "SELECT COUNT(*) AS total FROM experiences WHERE layer = 'candidate'",
            ),
            "experience_disputed_count": self._external_scalar(
                experience_db_path,
                "SELECT COUNT(*) AS total FROM experiences WHERE COALESCE(disputed, 0) > 0",
            ),
        }
        formal_layer_health["inactive_formal_total"] = (
            int(formal_layer_health["inactive_rules"]) +
            int(formal_layer_health["inactive_method_cards"])
        )

        return {
            "summary": {
                "duplicate_candidate_groups": int(duplicate_group_total_row["total"]),
                "stale_pending_promotions": int(stale_pending_total_row["total"]),
                "rolled_back_promotions": int(rolled_back_total_row["total"]),
                "source_conflict_groups": int(source_conflict_total_row["total"]),
                "inactive_formal_total": int(formal_layer_health["inactive_formal_total"]),
                "inactive_rules": int(formal_layer_health["inactive_rules"]),
                "inactive_method_cards": int(formal_layer_health["inactive_method_cards"]),
                "experience_candidate_count": int(formal_layer_health["experience_candidate_count"]),
                "experience_disputed_count": int(formal_layer_health["experience_disputed_count"]),
                "stale_pending_days": stale_days,
            },
            "duplicate_candidate_groups": duplicate_candidate_groups,
            "stale_pending_promotions": stale_pending_promotions,
            "recent_rollbacks": recent_rollbacks,
            "source_conflict_groups": source_conflict_groups,
            "formal_layer_health": formal_layer_health,
        }

    def create_audit_error(self, payload: dict[str, Any]) -> int:
        """Insert an audit error record."""
        data = self._prepare_insert_payload("audit_errors", payload)
        columns = list(data.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f"INSERT INTO audit_errors ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        conn = self._connect()
        try:
            cursor = conn.execute(sql, tuple(data[col] for col in columns))
            conn.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT id FROM audit_errors
                WHERE content_hash = ?
                  AND source_type = ?
                  AND source_id = ?
                  AND source_record_id = ?
                """,
                (
                    data["content_hash"],
                    data["source_type"],
                    data["source_id"],
                    data["source_record_id"],
                ),
            ).fetchone()
            conn.rollback()
            if row:
                return int(row[0])
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_audit_error(self, record_id: int) -> dict[str, Any] | None:
        """Fetch one audit error record by id."""
        row = self.query_one(
            "SELECT * FROM audit_errors WHERE id = ? AND is_deleted = 0",
            (record_id,),
        )
        return self._decode_json_fields("audit_errors", row) if row else None

    def list_active_audit_errors(self, limit: int = 100, *,
                                 review_statuses: list[str] | None = None,
                                 match_sources: list[str] | None = None,
                                 error_types: list[str] | None = None,
                                 source_table: str = "") -> list[dict[str, Any]]:
        """List active audit errors from the admin view."""
        where_parts = ["1 = 1"]
        params: list[Any] = []

        if review_statuses:
            statuses = [str(item or "").strip() for item in review_statuses if str(item or "").strip()]
            if statuses:
                placeholders = ", ".join("?" for _ in statuses)
                where_parts.append(f"review_status IN ({placeholders})")
                params.extend(statuses)

        if match_sources:
            sources = [str(item or "").strip() for item in match_sources if str(item or "").strip()]
            if sources:
                placeholders = ", ".join("?" for _ in sources)
                where_parts.append(f"match_source IN ({placeholders})")
                params.extend(sources)

        if error_types:
            types = [str(item or "").strip() for item in error_types if str(item or "").strip()]
            if types:
                placeholders = ", ".join("?" for _ in types)
                where_parts.append(f"error_type IN ({placeholders})")
                params.extend(types)

        if str(source_table or "").strip():
            where_parts.append("source_table = ?")
            params.append(str(source_table).strip())

        params.append(max(1, int(limit)))
        rows = self.query_all(
            f"""
            SELECT * FROM v_active_audit_errors
            WHERE {' AND '.join(where_parts)}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._decode_json_fields("audit_errors", row) for row in rows]

    def update_audit_error_status(self, record_id: int, *,
                                  status: str | None = None,
                                  review_status: str | None = None,
                                  reviewer: str | None = None,
                                  review_comment: str | None = None,
                                  reviewed_at: float | None = None) -> bool:
        """Update audit error status/review fields."""
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [time.time()]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if review_status is not None:
            updates.append("review_status = ?")
            params.append(review_status)
        if reviewer is not None:
            updates.append("reviewer = ?")
            params.append(reviewer)
        if review_comment is not None:
            updates.append("review_comment = ?")
            params.append(review_comment)
        if reviewed_at is not None:
            updates.append("reviewed_at = ?")
            params.append(reviewed_at)
        if len(updates) == 1:
            return False
        params.append(record_id)

        sql = f"UPDATE audit_errors SET {', '.join(updates)} WHERE id = ? AND is_deleted = 0"
        conn = self._connect()
        try:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def enqueue_promotion(self, payload: dict[str, Any]) -> int:
        """Insert one promotion candidate."""
        data = self._prepare_insert_payload("promotion_queue", payload)
        columns = list(data.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f"INSERT INTO promotion_queue ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        conn = self._connect()
        try:
            cursor = conn.execute(sql, tuple(data[col] for col in columns))
            conn.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT id FROM promotion_queue
                WHERE source_table = ?
                  AND source_record_id = ?
                  AND target_layer = ?
                """,
                (
                    data["source_table"],
                    data["source_record_id"],
                    data["target_layer"],
                ),
            ).fetchone()
            conn.rollback()
            if row:
                return int(row[0])
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_promotion(self, record_id: int) -> dict[str, Any] | None:
        """Fetch one promotion candidate by id."""
        row = self.query_one(
            "SELECT * FROM promotion_queue WHERE id = ? AND is_deleted = 0",
            (record_id,),
        )
        return self._decode_json_fields("promotion_queue", row) if row else None

    def list_pending_promotions(self, limit: int = 100) -> list[dict[str, Any]]:
        """List pending promotion candidates from the admin view."""
        rows = self.query_all(
            "SELECT * FROM v_pending_promotions ORDER BY priority ASC, id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [self._decode_json_fields("promotion_queue", row) for row in rows]

    def list_promotions(self, *,
                        statuses: list[str] | None = None,
                        candidate_types: list[str] | None = None,
                        target_layers: list[str] | None = None,
                        source_table: str = "",
                        limit: int = 100) -> list[dict[str, Any]]:
        """List promotion candidates by statuses."""
        statuses = [str(item or "").strip() for item in (statuses or ["draft", "reviewing", "approved", "rejected", "promoted", "rolled_back"]) if str(item or "").strip()]
        if not statuses:
            statuses = ["draft", "reviewing", "approved", "rejected", "promoted", "rolled_back"]
        placeholders = ", ".join("?" for _ in statuses)
        where_parts = [f"is_deleted = 0 AND status IN ({placeholders})"]
        params: list[Any] = list(statuses)

        if candidate_types:
            parsed_candidate_types = [str(item or "").strip() for item in candidate_types if str(item or "").strip()]
            if parsed_candidate_types:
                candidate_placeholders = ", ".join("?" for _ in parsed_candidate_types)
                where_parts.append(f"candidate_type IN ({candidate_placeholders})")
                params.extend(parsed_candidate_types)

        if target_layers:
            parsed_target_layers = [str(item or "").strip() for item in target_layers if str(item or "").strip()]
            if parsed_target_layers:
                layer_placeholders = ", ".join("?" for _ in parsed_target_layers)
                where_parts.append(f"target_layer IN ({layer_placeholders})")
                params.extend(parsed_target_layers)

        if str(source_table or "").strip():
            where_parts.append("source_table = ?")
            params.append(str(source_table).strip())

        params.append(max(1, int(limit)))
        rows = self.query_all(
            f"""
            SELECT * FROM promotion_queue
            WHERE {' AND '.join(where_parts)}
            ORDER BY
                CASE status
                    WHEN 'approved' THEN 0
                    WHEN 'reviewing' THEN 1
                    WHEN 'draft' THEN 2
                    ELSE 9
                END,
                priority ASC,
                id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [self._decode_json_fields("promotion_queue", row) for row in rows]

    def update_promotion_review(self, record_id: int, *,
                                review_status: str,
                                status: str | None = None,
                                reviewer: str = "",
                                review_comment: str = "",
                                rejection_reason: str | None = None,
                                reviewed_at: float | None = None) -> bool:
        """Update promotion review state."""
        now = time.time()
        updates = [
            "review_status = ?",
            "updated_at = ?",
            "reviewer = ?",
            "review_comment = ?",
            "reviewed_at = ?",
        ]
        params: list[Any] = [
            review_status,
            now,
            reviewer,
            review_comment,
            reviewed_at if reviewed_at is not None else now,
        ]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if rejection_reason is not None:
            updates.append("rejection_reason = ?")
            params.append(rejection_reason)
        params.append(record_id)

        sql = f"UPDATE promotion_queue SET {', '.join(updates)} WHERE id = ? AND is_deleted = 0"
        conn = self._connect()
        try:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_promotion_promoted(self, record_id: int, *,
                                promoted_target_id: str,
                                promoted_target_ref: str = "",
                                target_version: int | None = None,
                                promotion_trace: str = "",
                                promoted_at: float | None = None) -> bool:
        """Mark a promotion candidate as promoted and persist target info."""
        ts = promoted_at if promoted_at is not None else time.time()
        sql = """
            UPDATE promotion_queue
            SET status = 'promoted',
                review_status = 'promoted',
                updated_at = ?,
                promoted_at = ?,
                promoted_target_id = ?,
                promoted_target_ref = ?,
                target_version = ?,
                promotion_trace = ?
            WHERE id = ? AND is_deleted = 0
        """
        params = (
            ts,
            ts,
            promoted_target_id,
            promoted_target_ref,
            target_version,
            promotion_trace,
            record_id,
        )
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_promotion_rolled_back(self, record_id: int, *,
                                   review_comment: str = "",
                                   promotion_trace: str = "",
                                   reviewer: str = "",
                                   reviewed_at: float | None = None) -> bool:
        """Mark a promoted staging record as rolled back from the formal layer."""
        ts = reviewed_at if reviewed_at is not None else time.time()
        sql = """
            UPDATE promotion_queue
            SET status = 'rolled_back',
                review_status = 'rolled_back',
                updated_at = ?,
                reviewer = CASE WHEN ? != '' THEN ? ELSE reviewer END,
                review_comment = ?,
                reviewed_at = ?,
                promotion_trace = ?
            WHERE id = ? AND is_deleted = 0
        """
        params = (
            ts,
            reviewer,
            reviewer,
            review_comment,
            ts,
            promotion_trace,
            record_id,
        )
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_knowledge_staging(db_path: str | Path | None = None,
                           schema_path: str | Path | None = None) -> KnowledgeStaging:
    """Initialize and return a knowledge staging instance."""
    return KnowledgeStaging(db_path=db_path, schema_path=schema_path, auto_init=True)
