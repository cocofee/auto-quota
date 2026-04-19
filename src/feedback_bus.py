"""Unified feedback event bus with lightweight persistence and reusable signals."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from db.sqlite import connect as db_connect
from db.sqlite import connect_init as db_connect_init
from loguru import logger


_DEFAULT_DB_PATH = config.COMMON_DB_DIR / "feedback_bus.db"


@dataclass(slots=True)
class FeedbackEvent:
    event_type: str
    signal: str = ""
    province: str = ""
    specialty: str = ""
    bill_text: str = ""
    item_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class FeedbackEventStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or _DEFAULT_DB_PATH)
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = db_connect_init(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    signal TEXT DEFAULT '',
                    province TEXT DEFAULT '',
                    specialty TEXT DEFAULT '',
                    bill_text TEXT DEFAULT '',
                    item_name TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_events_created ON feedback_events(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_events_type_province ON feedback_events(event_type, province, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_events_item ON feedback_events(item_name, specialty, created_at)"
            )
            conn.commit()
        finally:
            conn.close()

    def append(self, event: FeedbackEvent) -> int:
        conn = db_connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO feedback_events (
                    event_type, signal, province, specialty,
                    bill_text, item_name, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.event_type or "").strip(),
                    str(event.signal or "").strip(),
                    str(event.province or "").strip(),
                    str(event.specialty or "").strip(),
                    str(event.bill_text or "").strip(),
                    str(event.item_name or "").strip(),
                    json.dumps(event.payload or {}, ensure_ascii=False, sort_keys=True),
                    float(event.created_at or time.time()),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def list_events(self, *, limit: int = 50, event_type: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        conn = db_connect(self.db_path, row_factory=True)
        try:
            if event_type:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM feedback_events
                    WHERE event_type = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (str(event_type).strip(), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM feedback_events
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(dict(row)) for row in rows]

    def get_feedback_bias_rows(self, province: str, *, limit: int = 2000) -> list[tuple[str, str]]:
        conn = db_connect(self.db_path, row_factory=True)
        try:
            rows = conn.execute(
                """
                SELECT signal, bill_text
                FROM feedback_events
                WHERE province = ?
                  AND event_type IN ('ranking_feedback', 'user_feedback')
                  AND signal IN ('confirm', 'correct')
                  AND TRIM(COALESCE(bill_text, '')) != ''
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (str(province or "").strip(), max(1, int(limit or 2000))),
            ).fetchall()
            return [
                (
                    str(row["signal"] or "").strip(),
                    str(row["bill_text"] or "").strip(),
                )
                for row in rows
                if str(row["bill_text"] or "").strip()
            ]
        except Exception as exc:
            logger.debug(f"feedback bus bias rows skipped: {exc}")
            return []
        finally:
            conn.close()

    def get_recent_consistency_hint(
        self,
        province: str,
        item_name: str,
        specialty: str,
        *,
        max_age_sec: float = 30 * 24 * 3600,
    ) -> str:
        now = time.time()
        conn = db_connect(self.db_path, row_factory=True)
        try:
            row = conn.execute(
                """
                SELECT payload_json
                FROM feedback_events
                WHERE event_type = 'consistency_hint'
                  AND province = ?
                  AND item_name = ?
                  AND specialty = ?
                  AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    str(province or "").strip(),
                    str(item_name or "").strip(),
                    str(specialty or "").strip(),
                    float(now - max(0.0, max_age_sec)),
                ),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return ""
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            return ""
        return str(payload.get("family_hint") or "").strip()

    def get_recent_cross_province_hints(
        self,
        item_name: str,
        specialty: str,
        *,
        limit: int = 3,
        max_age_sec: float = 30 * 24 * 3600,
    ) -> list[str]:
        now = time.time()
        conn = db_connect(self.db_path, row_factory=True)
        try:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM feedback_events
                WHERE event_type = 'cross_province_hint'
                  AND item_name = ?
                  AND specialty = ?
                  AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT 20
                """,
                (
                    str(item_name or "").strip(),
                    str(specialty or "").strip(),
                    float(now - max(0.0, max_age_sec)),
                ),
            ).fetchall()
        finally:
            conn.close()

        hints: list[str] = []
        seen: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                continue
            for hint in payload.get("hints") or []:
                text = str(hint or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                hints.append(text)
                if len(hints) >= max(1, int(limit or 3)):
                    return hints
        return hints

    @staticmethod
    def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["payload"] = json.loads(row.get("payload_json") or "{}")
        except Exception:
            row["payload"] = {}
        return row


class FeedbackBus:
    def __init__(self, store: FeedbackEventStore | None = None):
        self.store = store or FeedbackEventStore()
        self._subscribers: dict[str, list] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, handler) -> None:
        key = str(event_type or "*").strip() or "*"
        with self._lock:
            self._subscribers[key].append(handler)

    def emit(self, event: FeedbackEvent) -> int:
        event_id = self.store.append(event)
        with self._lock:
            handlers = list(self._subscribers.get(event.event_type, []))
            handlers.extend(self._subscribers.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.debug(f"feedback bus subscriber skipped: {exc}")
        return event_id


class SharedConsistencyHintMemory:
    def __init__(self, store: FeedbackEventStore):
        self._store = store
        self._cache: dict[tuple[str, str, str], str] = {}
        self._lock = threading.RLock()

    def __call__(self, event: FeedbackEvent) -> None:
        if event.event_type != "consistency_hint":
            return
        hint = str((event.payload or {}).get("family_hint") or "").strip()
        province = str(event.province or "").strip()
        item_name = str(event.item_name or "").strip()
        specialty = str(event.specialty or "").strip()
        if not hint or not item_name:
            return
        with self._lock:
            self._cache[(province, item_name, specialty)] = hint

    def lookup(self, province: str, item_name: str, specialty: str) -> str:
        key = (
            str(province or "").strip(),
            str(item_name or "").strip(),
            str(specialty or "").strip(),
        )
        with self._lock:
            cached = self._cache.get(key, "")
        if cached:
            return cached
        hint = self._store.get_recent_consistency_hint(key[0], key[1], key[2])
        if hint:
            with self._lock:
                self._cache[key] = hint
        return hint


_GLOBAL_BUS: FeedbackBus | None = None
_SHARED_CONSISTENCY_MEMORY: SharedConsistencyHintMemory | None = None


def _build_bus(db_path: Path | None = None) -> FeedbackBus:
    global _SHARED_CONSISTENCY_MEMORY
    store = FeedbackEventStore(db_path=db_path)
    bus = FeedbackBus(store=store)
    _SHARED_CONSISTENCY_MEMORY = SharedConsistencyHintMemory(store)
    bus.subscribe("consistency_hint", _SHARED_CONSISTENCY_MEMORY)
    return bus


def get_feedback_bus() -> FeedbackBus:
    global _GLOBAL_BUS
    if _GLOBAL_BUS is None:
        _GLOBAL_BUS = _build_bus()
    return _GLOBAL_BUS


def reset_feedback_bus(db_path: Path | None = None) -> FeedbackBus:
    global _GLOBAL_BUS
    _GLOBAL_BUS = _build_bus(db_path=db_path)
    return _GLOBAL_BUS


def emit_feedback_event(
    event_type: str,
    *,
    signal: str = "",
    province: str = "",
    specialty: str = "",
    bill_text: str = "",
    item_name: str = "",
    payload: dict[str, Any] | None = None,
    created_at: float | None = None,
) -> int:
    event = FeedbackEvent(
        event_type=str(event_type or "").strip(),
        signal=str(signal or "").strip(),
        province=str(province or "").strip(),
        specialty=str(specialty or "").strip(),
        bill_text=str(bill_text or "").strip(),
        item_name=str(item_name or "").strip(),
        payload=dict(payload or {}),
        created_at=float(created_at or time.time()),
    )
    return get_feedback_bus().emit(event)


def get_feedback_bias_rows(province: str, *, limit: int = 2000) -> list[tuple[str, str]]:
    try:
        return get_feedback_bus().store.get_feedback_bias_rows(province, limit=limit)
    except Exception as exc:
        logger.debug(f"feedback bus bias lookup skipped: {exc}")
        return []


def remember_consistency_hint(
    *,
    province: str = "",
    item_name: str,
    specialty: str,
    family_hint: str,
    payload: dict[str, Any] | None = None,
) -> int:
    data = dict(payload or {})
    data["family_hint"] = str(family_hint or "").strip()
    try:
        return emit_feedback_event(
            "consistency_hint",
            signal="remembered",
            province=province,
            specialty=specialty,
            item_name=item_name,
            payload=data,
        )
    except Exception as exc:
        logger.debug(f"feedback bus consistency remember skipped: {exc}")
        return 0


def lookup_consistency_hint(province: str, item_name: str, specialty: str) -> str:
    try:
        get_feedback_bus()
        if _SHARED_CONSISTENCY_MEMORY is None:
            return ""
        return _SHARED_CONSISTENCY_MEMORY.lookup(province, item_name, specialty)
    except Exception as exc:
        logger.debug(f"feedback bus consistency lookup skipped: {exc}")
        return ""


def remember_cross_province_hints(
    *,
    item_name: str,
    specialty: str,
    province: str,
    bill_text: str,
    hints: list[str],
) -> int:
    cleaned_hints = [str(hint or "").strip() for hint in hints if str(hint or "").strip()]
    if not cleaned_hints:
        return 0
    return emit_feedback_event(
        "cross_province_hint",
        signal="suggested",
        province=province,
        specialty=specialty,
        bill_text=bill_text,
        item_name=item_name,
        payload={"hints": cleaned_hints},
    )


def lookup_cross_province_hints(item_name: str, specialty: str, *, limit: int = 3) -> list[str]:
    return get_feedback_bus().store.get_recent_cross_province_hints(
        item_name,
        specialty,
        limit=limit,
    )
