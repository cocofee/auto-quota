"""
Operational helpers extracted from experience_db.py.
"""

from __future__ import annotations

import json
import time

from loguru import logger


def get_reference_cases(self, query_text: str, top_k: int = 3,
                        province: str = None,
                        specialty: str = None) -> list[dict]:
    """Return a compact few-shot style view of usable experience records."""
    fetch_k = top_k * 2 if specialty else top_k
    records = self.search_similar(
        query_text, top_k=fetch_k, min_confidence=70, province=province)

    cases = []
    for r in records:
        if r.get("match_type") in {"stale", "candidate"}:
            continue

        quota_strs = []
        ids = r.get("quota_ids", [])
        names = r.get("quota_names", [])
        for i, qid in enumerate(ids):
            name = names[i] if i < len(names) else ""
            quota_strs.append(f"{qid} {name}".strip())

        cases.append({
            "record_id": r.get("id"),
            "bill": r["bill_text"],
            "quotas": quota_strs,
            "confidence": r.get("confidence", 0),
            "specialty": r.get("specialty", ""),
        })

    if specialty and len(cases) > top_k:
        same = [c for c in cases if c.get("specialty") == specialty]
        diff = [c for c in cases if c.get("specialty") != specialty]
        cases = (same + diff)[:top_k]
    else:
        cases = cases[:top_k]

    return cases


def demote_to_candidate(self, record_id: int, reason: str = ""):
    """Demote an authority record back to candidate."""
    conn = self._connect()
    try:
        notes_update = f"[体检降级 {time.strftime('%Y-%m-%d')}] {reason}" if reason else ""
        cursor = conn.execute("""
            UPDATE experiences
            SET layer = 'candidate',
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    ELSE notes || '\n' || ?
                END,
                updated_at = ?
            WHERE id = ? AND layer = 'authority'
        """, (notes_update, notes_update, time.time(), record_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def promote_to_authority(self, record_id: int, reason: str = ""):
    """Promote reviewed records in two stages: candidate -> verified -> authority."""
    conn = self._connect()
    try:
        notes_update = f"[用户确认晋升 {time.strftime('%Y-%m-%d')}] {reason}" if reason else ""
        cursor = conn.execute("""
            UPDATE experiences
            SET layer = CASE
                    WHEN layer = 'verified'
                      OR source IN ('user_correction', 'openclaw_approved', 'user_confirmed')
                    THEN 'authority'
                    ELSE 'verified'
                END,
                source = CASE
                    WHEN source IN ('user_correction', 'openclaw_approved') THEN source
                    ELSE 'user_confirmed'
                END,
                confidence = MAX(confidence, 95),
                confirm_count = CASE
                    WHEN layer = 'verified'
                      OR source IN ('user_correction', 'openclaw_approved', 'user_confirmed')
                    THEN confirm_count + 1
                    ELSE 1
                END,
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    ELSE notes || '\n' || ?
                END,
                updated_at = ?
            WHERE id = ? AND layer IN ('candidate', 'verified')
        """, (notes_update, notes_update, time.time(), record_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_stale_experiences(self, province: str, current_version: str) -> int:
    """Annotate authority records that still point at an old quota version."""
    if not current_version or not province:
        return 0
    conn = self._connect()
    try:
        note = f"[定额库已更新 {time.strftime('%Y-%m-%d')}] 此经验基于旧版定额，匹配时已降级为参考"
        cursor = conn.execute("""
            UPDATE experiences SET
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    WHEN notes NOT LIKE '%定额库已更新%' THEN notes || '\n' || ?
                    ELSE notes
                END
            WHERE province = ?
              AND quota_db_version != ?
              AND quota_db_version != ''
              AND layer = 'authority'
        """, (note, note, province, current_version))
        conn.commit()
        count = cursor.rowcount
    finally:
        conn.close()
    if count > 0:
        logger.info(f"已标记{count}条旧版本经验记录（省份={province}）")
    return count


def get_authority_records(self, province: str = None,
                          limit: int = 0) -> list[dict]:
    """Return authority-layer records for audit tooling."""
    conn = self._connect()
    try:
        sql = """
            SELECT id, bill_text, bill_name, quota_ids, quota_names, materials,
                   source, confidence, province, specialty,
                   bill_code, bill_unit, created_at
            FROM experiences
            WHERE layer = 'authority'
        """
        params = []
        if province:
            sql += " AND province = ?"
            params.append(province)
        sql += " ORDER BY id"
        if limit > 0:
            sql += f" LIMIT {limit}"

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        quota_ids = []
        quota_names = []
        materials = []
        try:
            quota_ids = json.loads(row[3]) if row[3] else []
        except Exception:
            pass
        try:
            quota_names = json.loads(row[4]) if row[4] else []
        except Exception:
            pass
        try:
            materials = json.loads(row[5]) if row[5] else []
        except Exception:
            pass
        records.append({
            "id": row[0],
            "bill_text": row[1],
            "bill_name": row[2] or "",
            "quota_ids": quota_ids,
            "quota_names": quota_names,
            "materials": materials,
            "source": row[6],
            "confidence": row[7],
            "province": row[8],
            "specialty": row[9],
            "bill_code": row[10] or "",
            "bill_unit": row[11] or "",
            "created_at": row[12] or "",
        })
    return records


def get_candidate_records(self, province: str = None,
                          limit: int = 50,
                          exclude_demoted: bool = False) -> list[dict]:
    """Return reviewable candidate/verified records for review tooling."""
    conn = self._connect()
    try:
        sql = """
            SELECT id, bill_text, bill_name, quota_ids, quota_names, materials,
                   source, confidence, province, specialty, notes, layer,
                   bill_code, bill_unit, created_at
            FROM experiences
            WHERE layer IN ('candidate', 'verified')
        """
        params = []
        if province:
            sql += " AND province = ?"
            params.append(province)
        if exclude_demoted:
            sql += " AND (notes IS NULL OR notes NOT LIKE '%体检降级%')"
        sql += " ORDER BY CASE WHEN layer = 'verified' THEN 0 ELSE 1 END, id DESC"
        if limit > 0:
            sql += f" LIMIT {limit}"

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        quota_ids = []
        quota_names = []
        materials = []
        try:
            quota_ids = json.loads(row[3]) if row[3] else []
        except Exception:
            pass
        try:
            quota_names = json.loads(row[4]) if row[4] else []
        except Exception:
            pass
        try:
            materials = json.loads(row[5]) if row[5] else []
        except Exception:
            pass
        records.append({
            "id": row[0],
            "bill_text": row[1],
            "bill_name": row[2] or "",
            "quota_ids": quota_ids,
            "quota_names": quota_names,
            "materials": materials,
            "source": row[6],
            "confidence": row[7],
            "province": row[8],
            "specialty": row[9],
            "notes": row[10] or "",
            "layer": row[11] or "",
            "bill_code": row[12] or "",
            "bill_unit": row[13] or "",
            "created_at": row[14] or "",
        })
    return records
