"""
经验库管理 API（管理员专属）

路由挂载在 /api/admin/experience 前缀下:
    GET    /api/admin/experience/stats       — 统计概览（含 by_province 省份数据）
    GET    /api/admin/experience/records      — 记录列表（支持按层级筛选）
    GET    /api/admin/experience/search       — 搜索经验记录
    POST   /api/admin/experience/{id}/promote — 晋升到权威层
    POST   /api/admin/experience/{id}/demote  — 降级到候选层
    DELETE /api/admin/experience/{id}         — 删除记录
    POST   /api/admin/experience/batch-promote — 智能批量晋升候选层

注意：原 /provinces 端点已合并到 /stats（通过 by_province 字段返回省份数据）。
前端从 stats 响应中提取省份列表，避免重复请求。

通过 asyncio.to_thread() 调用核心引擎的 ExperienceDB（SQLite同步操作），
避免阻塞 FastAPI 的异步事件循环。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin

router = APIRouter()


def _get_experience_db():
    """获取经验库实例（懒加载，每次调用新建避免线程安全问题）

    不传 province 参数，让 ExperienceDB 使用默认省份。
    搜索时由调用方显式传入 province 参数覆盖。
    """
    from src.experience_db import ExperienceDB

    return ExperienceDB()


@router.get("/stats")
async def experience_stats(
    admin: User = Depends(require_admin),
):
    """经验库统计概览"""
    try:
        def _query():
            db = _get_experience_db()
            return db.get_stats()

        stats = await asyncio.to_thread(_query)
        return stats
    except Exception as e:
        logger.error(f"获取经验库统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取经验库统计失败")


@router.get("/records")
async def experience_records(
    layer: str = Query(default="all", description="层级: all/authority/candidate"),
    province: str | None = Query(default=None, description="省份筛选"),
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
):
    """获取经验记录列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    try:
        def _query():
            db = _get_experience_db()
            # 根据层级调用不同方法（取全部记录，在内存中分页）
            # 由于底层方法不返回 layer 字段，这里手动补上 layer_type
            if layer == "authority":
                records = db.get_authority_records(province=province, limit=0)
                for r in records:
                    r["layer_type"] = "authority"
            elif layer == "candidate":
                records = db.get_candidate_records(province=province, limit=0)
                for r in records:
                    r["layer_type"] = "candidate"
            else:
                # 获取全部：权威层 + 候选层
                auth = db.get_authority_records(province=province, limit=0)
                for r in auth:
                    r["layer_type"] = "authority"
                cand = db.get_candidate_records(province=province, limit=0)
                for r in cand:
                    r["layer_type"] = "candidate"
                records = auth + cand
            return records

        all_records = await asyncio.to_thread(_query)

        # 手动分页
        total = len(all_records)
        start = (page - 1) * size
        end = start + size
        items = all_records[start:end]

        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
        }
    except Exception as e:
        logger.error(f"获取经验记录失败: {e}")
        raise HTTPException(status_code=500, detail="获取经验记录失败")


@router.get("/search")
async def experience_search(
    q: str = Query(description="搜索关键词"),
    province: str | None = Query(default=None, description="省份筛选"),
    limit: int = 20,
    admin: User = Depends(require_admin),
):
    """搜索经验记录"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    # 限制查询条数，防止过大查询影响性能
    if limit < 1 or limit > 200:
        limit = 20

    try:
        def _query():
            db = _get_experience_db()
            # 管理员搜索：不选省份时搜全库（直接用SQL查，绕过 find_experience 的省份默认值）
            text = q.strip()
            # 转义 LIKE 通配符（防止用户输入 % 或 _ 改变查询语义）
            escaped = text.replace("%", "\\%").replace("_", "\\_")
            like_pattern = f"%{escaped}%"
            conn = db._connect(row_factory=True)
            try:
                cursor = conn.cursor()
                text_match = """(
                    bill_text = ? OR COALESCE(bill_name, '') = ?
                    OR bill_text LIKE ? ESCAPE '\\' OR COALESCE(bill_name, '') LIKE ? ESCAPE '\\'
                )"""
                rank_order = """
                    CASE
                        WHEN bill_text = ? THEN 0
                        WHEN COALESCE(bill_name, '') = ? THEN 1
                        WHEN bill_text LIKE ? ESCAPE '\\' THEN 2
                        WHEN COALESCE(bill_name, '') LIKE ? ESCAPE '\\' THEN 3
                        ELSE 4
                    END ASC,
                    confidence DESC, id DESC
                """
                if province:
                    where = f"province = ? AND {text_match}"
                    params = [province, text, text, like_pattern, like_pattern,
                              text, text, like_pattern, like_pattern, limit]
                else:
                    where = text_match
                    params = [text, text, like_pattern, like_pattern,
                              text, text, like_pattern, like_pattern, limit]

                cursor.execute(f"""
                    SELECT * FROM experiences
                    WHERE {where}
                    ORDER BY {rank_order}
                    LIMIT ?
                """, params)
                rows = cursor.fetchall()
                records = [db._normalize_record_quota_fields(dict(row)) for row in rows]
                # 补上 layer_type 字段（数据库字段叫 layer，前端期望 layer_type）
                for r in records:
                    r["layer_type"] = r.get("layer", "candidate")
                return records
            finally:
                conn.close()

        results = await asyncio.to_thread(_query)
        return {"items": results, "total": len(results)}
    except Exception as e:
        logger.error(f"搜索经验库失败: {e}")
        raise HTTPException(status_code=500, detail="搜索经验库失败")


@router.post("/{record_id}/promote")
async def promote_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """晋升经验记录到权威层"""
    try:
        def _promote():
            db = _get_experience_db()
            return db.promote_to_authority(record_id)

        success = await asyncio.to_thread(_promote)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在权威层")
        return {"message": "晋升成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"晋升经验记录失败: {e}")
        raise HTTPException(status_code=500, detail="晋升失败")


@router.post("/{record_id}/demote")
async def demote_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """降级经验记录到候选层"""
    try:
        def _demote():
            db = _get_experience_db()
            return db.demote_to_candidate(record_id)

        success = await asyncio.to_thread(_demote)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在候选层")
        return {"message": "降级成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"降级经验记录失败: {e}")
        raise HTTPException(status_code=500, detail="降级失败")


@router.delete("/{record_id}")
async def delete_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """删除经验记录"""
    try:
        def _delete():
            db = _get_experience_db()
            # 1. 从 SQLite 删除记录
            conn = db._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM experiences WHERE id = ?", (record_id,)
                )
                conn.commit()
                deleted = cursor.rowcount > 0
            finally:
                conn.close()

            # 2. 同步清理 ChromaDB 向量索引（防止"幽灵"向量残留）
            if deleted:
                try:
                    coll = db.collection
                    if coll is not None:
                        coll.delete(ids=[str(record_id)])
                except Exception as e:
                    # 向量清理失败不影响主流程（下次重建索引会自动修复）
                    logger.warning(f"清理向量索引失败（id={record_id}）: {e}")

            return deleted

        deleted = await asyncio.to_thread(_delete)
        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除经验记录失败: {e}")
        raise HTTPException(status_code=500, detail="删除失败")


# ============================================================
# 智能批量晋升候选层
# ============================================================

class BatchPromoteRequest(BaseModel):
    province: str | None = None  # 按省份过滤（None=全部）
    dry_run: bool = True         # 预览模式（不实际修改）


@router.post("/batch-promote")
async def batch_promote(
    req: BatchPromoteRequest,
    admin: User = Depends(require_admin),
):
    """智能批量晋升候选层记录

    逻辑：
    1. 取出候选层记录（排除 project_import_suspect 来源）
    2. 对每条运行定额校验
    3. 校验通过 → 晋升权威层
    4. 校验失败 → 跳过

    dry_run=true 时只返回预览统计，不实际修改。
    """
    try:
        def _batch():
            import json as _json
            db = _get_experience_db()

            # 取候选层记录（不限数量）
            records = db.get_candidate_records(province=req.province, limit=0)

            # 排除 project_import_suspect（这些被审核规则检测到问题，不能自动晋升）
            records = [r for r in records if r.get("source") != "project_import_suspect"]

            promoted = 0
            skipped = 0
            errors = []  # 记录前几条失败原因（便于前端展示）

            for r in records:
                # 解析定额编号
                quota_ids_raw = r.get("quota_ids", "[]")
                if isinstance(quota_ids_raw, str):
                    try:
                        quota_ids = _json.loads(quota_ids_raw)
                    except Exception:
                        quota_ids = []
                else:
                    quota_ids = quota_ids_raw

                if not quota_ids:
                    skipped += 1
                    if len(errors) < 5:
                        bill = r.get("bill_name") or r.get("bill_text", "")[:30]
                        errors.append(f"{bill}: 无定额编号")
                    continue

                # 运行定额校验
                bill_text = r.get("bill_text", "")
                try:
                    validation = db._validate_quota_ids(bill_text, quota_ids, r.get("province", ""))
                except Exception:
                    # 校验方法异常（如定额库未找到），视为跳过
                    skipped += 1
                    continue

                if not validation.get("valid", False):
                    skipped += 1
                    if len(errors) < 5:
                        bill = r.get("bill_name") or bill_text[:30]
                        err_msg = "; ".join(validation.get("errors", []))[:50]
                        errors.append(f"{bill}: {err_msg}")
                    continue

                # 校验通过：dry_run 模式下只计数，不修改
                if not req.dry_run:
                    ok = db.promote_to_authority(r["id"], reason="智能批量晋升（定额校验通过）")
                    if ok:
                        promoted += 1
                    else:
                        skipped += 1
                else:
                    promoted += 1  # 预览模式下计为"可晋升"

            return {
                "total": len(records),
                "promoted": promoted,
                "skipped": skipped,
                "errors": errors,
                "dry_run": req.dry_run,
            }

        result = await asyncio.to_thread(_batch)
        return result
    except Exception as e:
        logger.error(f"批量晋升失败: {e}")
        raise HTTPException(status_code=500, detail="批量晋升失败")
