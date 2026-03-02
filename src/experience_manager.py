"""
经验库 — 管理运维模块

从 experience_db.py 拆分出来，负责运维操作：
1. get_reference_cases: 获取参考案例（供大模型 few-shot 使用）
2. demote_to_candidate: 降级到候选层（体检时用）
3. promote_to_authority: 晋升到权威层（用户审核确认后）
4. mark_stale_experiences: 标记过期经验（定额库更新后）
5. get_authority_records: 获取权威层记录（体检工具遍历用）
6. get_candidate_records: 获取候选层记录（审核晋升工具用）

使用方式（方法重绑定，调用方无需感知拆分）：
    from src.experience_db import ExperienceDB
    db = ExperienceDB("北京2024")
    db.get_reference_cases(...)  # 和拆分前一样调用
"""

import json
import time

from loguru import logger


def get_reference_cases(self, query_text: str, top_k: int = 3,
                        province: str = None,
                        specialty: str = None) -> list[dict]:
    """
    获取参考案例（供大模型 few-shot 使用）

    与 search_similar 的区别：
    - 这个方法用于给大模型提供参考（不要求高相似度）
    - 返回格式更简洁，适合放入 Prompt
    - 支持按专业过滤，优先返回同专业的案例

    参数:
        specialty: 专业分类（如"C10"），传入后同专业案例优先排在前面

    返回:
        [{"bill": "清单描述", "quotas": ["定额1", "定额2"]}, ...]
    """
    # 多搜一些候选，后面按专业重排序再截断
    fetch_k = top_k * 2 if specialty else top_k
    records = self.search_similar(
        query_text, top_k=fetch_k, min_confidence=70, province=province)

    cases = []
    for r in records:
        # 过期经验不进入few-shot上下文，避免把旧版定额注入提示词
        if r.get("match_type") == "stale":
            continue
        # 候选层（未经人工确认）不进入few-shot上下文，避免错误数据误导Agent
        if r.get("match_type") == "candidate":
            continue
        # 把定额编号和名称拼在一起
        quota_strs = []
        ids = r.get("quota_ids", [])
        names = r.get("quota_names", [])
        for i, qid in enumerate(ids):
            name = names[i] if i < len(names) else ""
            quota_strs.append(f"{qid} {name}".strip())

        cases.append({
            "bill": r["bill_text"],
            "quotas": quota_strs,
            "confidence": r.get("confidence", 0),
            "specialty": r.get("specialty", ""),  # 保留专业字段用于排序
        })

    # 按专业优先排序：同专业的案例排前面，避免跨专业误导Agent
    if specialty and len(cases) > top_k:
        same = [c for c in cases if c.get("specialty") == specialty]
        diff = [c for c in cases if c.get("specialty") != specialty]
        cases = (same + diff)[:top_k]
    else:
        cases = cases[:top_k]

    return cases


def demote_to_candidate(self, record_id: int, reason: str = ""):
    """
    将权威层记录降级为候选层。

    用于经验库体检时，发现审核规则不通过的记录，自动降级。
    不删除数据（数据还在，只是不再直通匹配），用户确认后可重新晋升。

    参数:
        record_id: 经验记录ID
        reason: 降级原因（写入notes字段）
    """
    conn = self._connect()
    try:
        notes_update = f"[体检降级 {time.strftime('%Y-%m-%d')}] {reason}" if reason else ""
        conn.execute("""
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
    finally:
        conn.close()


def promote_to_authority(self, record_id: int, reason: str = ""):
    """
    将候选层记录晋升为权威层。

    用于用户审核确认候选数据后，晋升为可直通匹配的权威数据。
    同时更新 source 为 user_confirmed，提高置信度到 95。

    参数:
        record_id: 经验记录ID
        reason: 晋升原因（写入notes字段）

    返回:
        True=晋升成功, False=记录不存在或已是权威层
    """
    conn = self._connect()
    try:
        notes_update = f"[用户确认晋升 {time.strftime('%Y-%m-%d')}] {reason}" if reason else ""
        cursor = conn.execute("""
            UPDATE experiences
            SET layer = 'authority',
                source = 'user_confirmed',
                confidence = MAX(confidence, 95),
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    ELSE notes || '\n' || ?
                END,
                updated_at = ?
            WHERE id = ? AND layer = 'candidate'
        """, (notes_update, notes_update, time.time(), record_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_stale_experiences(self, province: str, current_version: str) -> int:
    """标记基于旧版定额库的经验记录

    定额库重新导入后版本号变了，调用此方法在旧经验的notes中追加提醒。
    不删除也不降级layer/confidence，只标注"基于旧版定额"。
    search_similar() 搜索时已有 stale 降级逻辑，这里只补可视化提醒。

    参数:
        province: 省份名称
        current_version: 当前定额库版本号

    返回: 标记的记录数
    """
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
    """
    获取权威层的所有记录（供体检工具遍历检查）。

    参数:
        province: 可选，只获取指定省份的记录
        limit: 可选，限制返回数量（0=不限制）

    返回:
        记录列表，每条含 id, bill_text, bill_name, quota_ids, quota_names 等字段
    """
    conn = self._connect()
    try:
        sql = """
            SELECT id, bill_text, bill_name, quota_ids, quota_names,
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
        try:
            quota_ids = json.loads(row[3]) if row[3] else []
        except Exception:
            pass
        try:
            quota_names = json.loads(row[4]) if row[4] else []
        except Exception:
            pass
        records.append({
            "id": row[0],
            "bill_text": row[1],
            "bill_name": row[2] or "",
            "quota_ids": quota_ids,
            "quota_names": quota_names,
            "source": row[5],
            "confidence": row[6],
            "province": row[7],
            "specialty": row[8],
            "bill_code": row[9] or "",
            "bill_unit": row[10] or "",
            "created_at": row[11] or "",
        })
    return records


def get_candidate_records(self, province: str = None,
                           limit: int = 50) -> list[dict]:
    """
    获取候选层的记录（供审核晋升工具使用）。

    参数:
        province: 可选，只获取指定省份的记录
        limit: 返回数量（默认50条）

    返回:
        记录列表，每条含 id, bill_text, bill_name, quota_ids, quota_names, source 等字段
    """
    conn = self._connect()
    try:
        sql = """
            SELECT id, bill_text, bill_name, quota_ids, quota_names,
                   source, confidence, province, specialty, notes,
                   bill_code, bill_unit, created_at
            FROM experiences
            WHERE layer = 'candidate'
        """
        params = []
        if province:
            sql += " AND province = ?"
            params.append(province)
        sql += " ORDER BY id DESC"
        if limit > 0:
            sql += f" LIMIT {limit}"

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        quota_ids = []
        quota_names = []
        try:
            quota_ids = json.loads(row[3]) if row[3] else []
        except Exception:
            pass
        try:
            quota_names = json.loads(row[4]) if row[4] else []
        except Exception:
            pass
        records.append({
            "id": row[0],
            "bill_text": row[1],
            "bill_name": row[2] or "",
            "quota_ids": quota_ids,
            "quota_names": quota_names,
            "source": row[5],
            "confidence": row[6],
            "province": row[7],
            "specialty": row[8],
            "notes": row[9] or "",
            "bill_code": row[10] or "",
            "bill_unit": row[11] or "",
            "created_at": row[12] or "",
        })
    return records
