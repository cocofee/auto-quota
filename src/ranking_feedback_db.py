"""
Ranking feedback persistence for result correction/confirmation flows.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import config
from db.sqlite import connect as db_connect
from db.sqlite import connect_init as db_connect_init


STANDARD_FACTORS = ("text", "specialty", "unit", "material", "source", "consensus")


def _json_dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_load(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_dimension_scores(raw_scores: dict | None) -> dict:
    """
    Normalize whatever score payload we have into the Part 4 standard factor keys.

    Stage A often lacks full structural rerank fields. In that case we only map the
    fields that can be inferred safely.
    """
    if not isinstance(raw_scores, dict):
        return {}

    normalized: dict[str, float] = {}
    for factor in STANDARD_FACTORS:
        value = _safe_float(raw_scores.get(factor))
        if value is not None:
            normalized[factor] = value

    if "text" not in normalized:
        rerank_score = _safe_float(raw_scores.get("rerank_score"))
        if rerank_score is None:
            rerank_score = _safe_float(raw_scores.get("semantic_rerank_score"))
        if rerank_score is not None:
            normalized["text"] = rerank_score

    return normalized


def infer_misrank_primary_factor(top1_scores: dict | None, correct_scores: dict | None) -> str:
    left = normalize_dimension_scores(top1_scores)
    right = normalize_dimension_scores(correct_scores)
    if not left or not right:
        return ""

    best_factor = ""
    best_gap = 0.0
    for factor in STANDARD_FACTORS:
        gap = float(right.get(factor, 0.0)) - float(left.get(factor, 0.0))
        if gap > best_gap:
            best_gap = gap
            best_factor = factor
    return best_factor


class RankingFeedbackDB:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else (config.COMMON_DB_DIR / "ranking_feedback.db")
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = db_connect_init(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ranking_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT DEFAULT '',
                    result_id TEXT DEFAULT '',
                    province TEXT DEFAULT '',
                    query_text TEXT NOT NULL,
                    selected_experience_id INTEGER,
                    original_top1_experience_id INTEGER,
                    selected_candidate_key TEXT DEFAULT '',
                    original_top1_candidate_key TEXT DEFAULT '',
                    original_rank_of_selected INTEGER,
                    gate_bucket TEXT DEFAULT '',
                    topk_snapshot TEXT DEFAULT '[]',
                    dimension_scores_json TEXT DEFAULT '{}',
                    misrank_primary_factor TEXT DEFAULT '',
                    feedback_source TEXT DEFAULT '',
                    action TEXT DEFAULT '',
                    actor TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ranking_feedback_task_result ON ranking_feedback(task_id, result_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ranking_feedback_created ON ranking_feedback(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ranking_feedback_factor ON ranking_feedback(misrank_primary_factor)"
            )
            self._ensure_column(conn, "ranking_feedback", "selected_candidate_key", "TEXT DEFAULT ''")
            self._ensure_column(conn, "ranking_feedback", "original_top1_candidate_key", "TEXT DEFAULT ''")
            self._ensure_column(conn, "ranking_feedback", "action", "TEXT DEFAULT ''")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def record_feedback(
        self,
        *,
        task_id: str,
        result_id: str,
        province: str,
        query_text: str,
        selected_experience_id: int | None = None,
        original_top1_experience_id: int | None = None,
        selected_candidate_key: str = "",
        original_top1_candidate_key: str = "",
        original_rank_of_selected: int | None = None,
        gate_bucket: str = "",
        topk_snapshot: list[dict] | None = None,
        dimension_scores_json: dict | None = None,
        misrank_primary_factor: str = "",
        feedback_source: str = "",
        action: str = "",
        actor: str = "",
        created_at: float | None = None,
    ) -> int:
        timestamp = float(created_at or time.time())
        conn = db_connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ranking_feedback (
                    task_id, result_id, province, query_text,
                    selected_experience_id, original_top1_experience_id,
                    selected_candidate_key, original_top1_candidate_key,
                    original_rank_of_selected, gate_bucket, topk_snapshot,
                    dimension_scores_json, misrank_primary_factor, feedback_source,
                    action, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _safe_text(task_id),
                    _safe_text(result_id),
                    _safe_text(province),
                    _safe_text(query_text),
                    selected_experience_id,
                    original_top1_experience_id,
                    _safe_text(selected_candidate_key),
                    _safe_text(original_top1_candidate_key),
                    original_rank_of_selected,
                    _safe_text(gate_bucket),
                    _json_dump(topk_snapshot or []),
                    _json_dump(dimension_scores_json or {}),
                    _safe_text(misrank_primary_factor),
                    _safe_text(feedback_source),
                    _safe_text(action),
                    _safe_text(actor),
                    timestamp,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def list_feedback(self, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), 500))
        conn = db_connect(self.db_path, row_factory=True)
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM ranking_feedback
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(dict(row)) for row in rows]

    def run_weight_calibration_report(self, *, min_rows: int = 50) -> dict:
        conn = db_connect(self.db_path, row_factory=True)
        try:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM ranking_feedback"
            ).fetchone()
            factor_rows = conn.execute(
                """
                SELECT misrank_primary_factor, COUNT(*) AS cnt
                FROM ranking_feedback
                WHERE TRIM(COALESCE(misrank_primary_factor, '')) != ''
                GROUP BY misrank_primary_factor
                ORDER BY cnt DESC, misrank_primary_factor ASC
                """
            ).fetchall()
            rank_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN original_rank_of_selected IS NULL THEN 'unknown'
                        WHEN original_rank_of_selected <= 1 THEN 'top1'
                        WHEN original_rank_of_selected <= 3 THEN 'top3'
                        WHEN original_rank_of_selected <= 5 THEN 'top5'
                        ELSE 'beyond_top5'
                    END AS bucket,
                    COUNT(*) AS cnt
                FROM ranking_feedback
                GROUP BY bucket
                ORDER BY cnt DESC, bucket ASC
                """
            ).fetchall()
            gate_rows = conn.execute(
                """
                SELECT gate_bucket, COUNT(*) AS cnt
                FROM ranking_feedback
                GROUP BY gate_bucket
                ORDER BY cnt DESC, gate_bucket ASC
                """
            ).fetchall()
        finally:
            conn.close()

        total = int(total_row["total"] or 0) if total_row else 0
        return {
            "enough_samples": total >= max(int(min_rows or 50), 1),
            "total_feedback": total,
            "misrank_factor_counts": [
                {"factor": str(row["misrank_primary_factor"] or ""), "count": int(row["cnt"] or 0)}
                for row in factor_rows
            ],
            "selected_rank_buckets": [
                {"bucket": str(row["bucket"] or ""), "count": int(row["cnt"] or 0)}
                for row in rank_rows
            ],
            "gate_buckets": [
                {"bucket": str(row["gate_bucket"] or ""), "count": int(row["cnt"] or 0)}
                for row in gate_rows
            ],
        }

    def _row_to_dict(self, row: dict) -> dict:
        row["topk_snapshot"] = _json_load(row.get("topk_snapshot"), [])
        row["dimension_scores_json"] = _json_load(row.get("dimension_scores_json"), {})
        return row
