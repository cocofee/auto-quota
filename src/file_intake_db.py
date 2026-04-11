"""
Unified file intake metadata store.
"""

from __future__ import annotations

import json
import time
import uuid

import config
from db.sqlite import connect as db_connect
from db.sqlite import connect_init as db_connect_init


def _json_dump(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_load(value: str | None):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


class FileIntakeDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.get_price_reference_db_path()
        self._init_db()

    def _init_db(self):
        conn = db_connect_init(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_intake_files (
                    file_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mime_type TEXT DEFAULT '',
                    file_ext TEXT DEFAULT '',
                    file_size INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'uploaded',
                    file_type TEXT DEFAULT '',
                    source_hint TEXT DEFAULT '',
                    province TEXT DEFAULT '',
                    project_name TEXT DEFAULT '',
                    project_stage TEXT DEFAULT '',
                    ingest_intent TEXT DEFAULT '',
                    evidence_level TEXT DEFAULT '',
                    business_type TEXT DEFAULT '',
                    actor TEXT DEFAULT '',
                    source_context TEXT DEFAULT '{}',
                    classify_result TEXT DEFAULT '{}',
                    parse_summary TEXT DEFAULT '{}',
                    route_result TEXT DEFAULT '{}',
                    current_stage TEXT DEFAULT '',
                    next_action TEXT DEFAULT '',
                    receipt_summary TEXT DEFAULT '{}',
                    failure_type TEXT DEFAULT '',
                    failure_stage TEXT DEFAULT '',
                    needs_manual_review INTEGER DEFAULT 0,
                    manual_review_reason TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    created_by TEXT DEFAULT '',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_intake_status ON file_intake_files(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_intake_type ON file_intake_files(file_type)"
            )
            self._ensure_column(conn, "file_intake_files", "ingest_intent", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "evidence_level", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "business_type", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "actor", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "source_context", "TEXT DEFAULT '{}'")
            self._ensure_column(conn, "file_intake_files", "current_stage", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "next_action", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "receipt_summary", "TEXT DEFAULT '{}'")
            self._ensure_column(conn, "file_intake_files", "failure_type", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "failure_stage", "TEXT DEFAULT ''")
            self._ensure_column(conn, "file_intake_files", "needs_manual_review", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "file_intake_files", "manual_review_reason", "TEXT DEFAULT ''")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def create_file(
        self,
        *,
        filename: str,
        stored_path: str,
        mime_type: str = "",
        file_ext: str = "",
        file_size: int = 0,
        source_hint: str = "",
        province: str = "",
        project_name: str = "",
        project_stage: str = "",
        ingest_intent: str = "",
        evidence_level: str = "",
        business_type: str = "",
        actor: str = "",
        source_context: dict | None = None,
        created_by: str = "",
    ) -> dict:
        file_id = f"fi_{uuid.uuid4().hex[:16]}"
        now = time.time()
        conn = db_connect(self.db_path, row_factory=True)
        try:
            conn.execute(
                """
                INSERT INTO file_intake_files (
                    file_id, filename, stored_path, mime_type, file_ext, file_size,
                    status, source_hint, province, project_name, project_stage,
                    ingest_intent, evidence_level, business_type, actor, source_context,
                    current_stage, next_action, receipt_summary,
                    needs_manual_review, manual_review_reason,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    filename,
                    stored_path,
                    mime_type,
                    file_ext,
                    int(file_size or 0),
                    "uploaded",
                    source_hint,
                    province,
                    project_name,
                    project_stage,
                    ingest_intent,
                    evidence_level,
                    business_type,
                    actor,
                    _json_dump(source_context or {}),
                    "receive-file",
                    "classify-file",
                    _json_dump({"message": "file uploaded"}),
                    0,
                    "",
                    created_by,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)

    def get_file(self, file_id: str) -> dict | None:
        conn = db_connect(self.db_path, row_factory=True)
        try:
            row = conn.execute(
                "SELECT * FROM file_intake_files WHERE file_id=?",
                (file_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        record = dict(row)
        record["classify_result"] = _json_load(record.get("classify_result"))
        record["parse_summary"] = _json_load(record.get("parse_summary"))
        record["route_result"] = _json_load(record.get("route_result"))
        record["source_context"] = _json_load(record.get("source_context"))
        record["receipt_summary"] = _json_load(record.get("receipt_summary"))
        record["needs_manual_review"] = bool(record.get("needs_manual_review"))
        return record

    def update_classify(self, file_id: str, *, file_type: str, classify_result: dict) -> dict | None:
        now = time.time()
        conn = db_connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE file_intake_files
                SET status='classified',
                    file_type=?,
                    classify_result=?,
                    current_stage='classify-file',
                    next_action='parse-file',
                    receipt_summary=?,
                    failure_type='',
                    failure_stage='',
                    needs_manual_review=0,
                    manual_review_reason='',
                    error_message='',
                    updated_at=?
                WHERE file_id=?
                """,
                (
                    file_type,
                    _json_dump(classify_result),
                    _json_dump({
                        "message": "file classified",
                        "file_type": file_type,
                        "confidence": classify_result.get("confidence") or 0,
                        "signals": classify_result.get("signals") or [],
                    }),
                    now,
                    file_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)

    def update_parse(self, file_id: str, *, status: str, parse_summary: dict) -> dict | None:
        now = time.time()
        conn = db_connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE file_intake_files
                SET status=?,
                    parse_summary=?,
                    current_stage='parse-file',
                    next_action='route-decision',
                    receipt_summary=?,
                    failure_type='',
                    failure_stage='',
                    needs_manual_review=0,
                    manual_review_reason='',
                    error_message='',
                    updated_at=?
                WHERE file_id=?
                """,
                (
                    status,
                    _json_dump(parse_summary),
                    _json_dump({
                        "message": "file parsed",
                        "summary": parse_summary,
                    }),
                    now,
                    file_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)

    def update_route(self, file_id: str, *, route_result: dict) -> dict | None:
        now = time.time()
        conn = db_connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE file_intake_files
                SET status='routed',
                    route_result=?,
                    current_stage='route-decision',
                    next_action='observe-downstream',
                    receipt_summary=?,
                    failure_type='',
                    failure_stage='',
                    needs_manual_review=0,
                    manual_review_reason='',
                    error_message='',
                    updated_at=?
                WHERE file_id=?
                """,
                (
                    _json_dump(route_result),
                    _json_dump({
                        "message": "file routed",
                        "route_result": route_result,
                    }),
                    now,
                    file_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)

    def update_failure(
        self,
        file_id: str,
        *,
        error_message: str,
        failure_type: str = '',
        failure_stage: str = '',
        needs_manual_review: bool = False,
        manual_review_reason: str = '',
    ) -> dict | None:
        now = time.time()
        conn = db_connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE file_intake_files
                SET status=?,
                    current_stage=?,
                    next_action=?,
                    receipt_summary=?,
                    failure_type=?,
                    failure_stage=?,
                    needs_manual_review=?,
                    manual_review_reason=?,
                    error_message=?,
                    updated_at=?
                WHERE file_id=?
                """,
                (
                    'waiting_human' if needs_manual_review else 'failed',
                    failure_stage or '',
                    'manual-review' if needs_manual_review else '',
                    _json_dump({
                        "message": "file intake failed" if not needs_manual_review else "manual review required",
                        "failure_type": failure_type,
                        "failure_stage": failure_stage,
                    }),
                    failure_type,
                    failure_stage,
                    1 if needs_manual_review else 0,
                    manual_review_reason,
                    error_message[:500],
                    now,
                    file_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)

    def confirm_manual_review(
        self,
        file_id: str,
        *,
        file_type: str,
        continue_from: str,
    ) -> dict | None:
        now = time.time()
        next_action = 'parse-file' if continue_from == 'parse' else 'route-decision'
        status = 'classified' if continue_from == 'parse' else 'parsed'
        current_stage = 'classify-file' if continue_from == 'parse' else 'parse-file'
        conn = db_connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE file_intake_files
                SET status=?,
                    file_type=?,
                    current_stage=?,
                    next_action=?,
                    receipt_summary=?,
                    failure_type='',
                    failure_stage='',
                    needs_manual_review=0,
                    manual_review_reason='',
                    error_message='',
                    updated_at=?
                WHERE file_id=?
                """,
                (
                    status,
                    file_type,
                    current_stage,
                    next_action,
                    _json_dump({
                        "message": "manual review confirmed",
                        "file_type": file_type,
                        "continue_from": continue_from,
                    }),
                    now,
                    file_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_file(file_id)
