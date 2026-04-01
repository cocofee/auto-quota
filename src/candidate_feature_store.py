from __future__ import annotations

import json
import time
from pathlib import Path

from db.sqlite import connect as _db_connect, connect_init as _db_connect_init

import config


CANDIDATE_FEATURE_CACHE_VERSION = "v3"


def get_candidate_feature_store_db_path() -> Path:
    return config.COMMON_DB_DIR / "candidate_features.db"


class CandidateFeatureStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path or get_candidate_feature_store_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = _db_connect_init(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_features (
                    province TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    quota_id TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    canonical_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (province, cache_key)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_candidate_features_quota
                ON candidate_features(province, quota_id)
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def build_cache_key(candidate: dict) -> str:
        quota_id = str(candidate.get("quota_id") or "").strip()
        if quota_id:
            return f"{CANDIDATE_FEATURE_CACHE_VERSION}|quota:{quota_id}"
        name = str(candidate.get("name") or "").strip()
        desc = str(candidate.get("description") or "").strip()
        return f"{CANDIDATE_FEATURE_CACHE_VERSION}|text:{name}|{desc}"

    def get(self, province: str, candidate: dict) -> dict | None:
        province = province or ""
        cache_key = self.build_cache_key(candidate)
        conn = _db_connect(self.db_path, row_factory=True)
        try:
            row = conn.execute(
                "SELECT canonical_json FROM candidate_features WHERE province = ? AND cache_key = ?",
                (province, cache_key),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            value = json.loads(row["canonical_json"])
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def put(self, province: str, candidate: dict, canonical_features: dict):
        province = province or ""
        cache_key = self.build_cache_key(candidate)
        payload = json.dumps(dict(canonical_features or {}), ensure_ascii=False)
        conn = _db_connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO candidate_features (
                    province, cache_key, quota_id, name, description,
                    canonical_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(province, cache_key) DO UPDATE SET
                    quota_id = excluded.quota_id,
                    name = excluded.name,
                    description = excluded.description,
                    canonical_json = excluded.canonical_json,
                    updated_at = excluded.updated_at
            """, (
                province,
                cache_key,
                str(candidate.get("quota_id") or ""),
                str(candidate.get("name") or ""),
                str(candidate.get("description") or ""),
                payload,
                time.time(),
            ))
            conn.commit()
        finally:
            conn.close()


_STORE: CandidateFeatureStore | None = None


def get_candidate_feature_store() -> CandidateFeatureStore:
    global _STORE
    if _STORE is None:
        _STORE = CandidateFeatureStore()
    return _STORE
