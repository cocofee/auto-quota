"""
经验库管理 API（管理员专属）

路由挂载在 /api/admin/experience 前缀下:
    GET    /api/admin/experience/stats       — 统计概览
    GET    /api/admin/experience/records      — 记录列表（支持按层级筛选）
    GET    /api/admin/experience/search       — 搜索经验记录
    POST   /api/admin/experience/{id}/promote — 晋升到权威层
    POST   /api/admin/experience/{id}/demote  — 降级到候选层
    DELETE /api/admin/experience/{id}         — 删除记录

通过 asyncio.to_thread() 调用核心引擎的 ExperienceDB（SQLite同步操作），
避免阻塞 FastAPI 的异步事件循环。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin

router = APIRouter()


def _get_experience_db():
    """获取经验库实例（懒加载，每次调用新建避免线程安全问题）"""
    import config as quota_config
    from src.experience_db import ExperienceDB

    db_path = quota_config.get_experience_db_path()
    return ExperienceDB(db_path)


@router.get("/provinces")
async def experience_provinces(
    admin: User = Depends(require_admin),
):
    """获取经验库中所有省份列表（含各省份记录数）"""
    try:
        def _query():
            db = _get_experience_db()
            stats = db.get_stats()
            by_province = stats.get("by_province", {})
            # 转换为列表格式，方便前端使用
            return [
                {"province": name, "count": count}
                for name, count in sorted(by_province.items(), key=lambda x: -x[1])
            ]

        provinces = await asyncio.to_thread(_query)
        return {"items": provinces}
    except Exception as e:
        logger.error(f"获取经验库省份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取省份列表失败: {e}")


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
        raise HTTPException(status_code=500, detail=f"获取经验库统计失败: {e}")


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
            if layer == "authority":
                records = db.get_authority_records(province=province, limit=0)
            elif layer == "candidate":
                records = db.get_candidate_records(province=province, limit=0)
            else:
                # 获取全部：权威层 + 候选层
                auth = db.get_authority_records(province=province, limit=0)
                cand = db.get_candidate_records(province=province, limit=0)
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
        raise HTTPException(status_code=500, detail=f"获取经验记录失败: {e}")


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

    try:
        def _query():
            db = _get_experience_db()
            return db.find_experience(
                bill_text=q.strip(),
                province=province,
                limit=limit,
            )

        results = await asyncio.to_thread(_query)
        return {"items": results, "total": len(results)}
    except Exception as e:
        logger.error(f"搜索经验库失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索经验库失败: {e}")


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
        raise HTTPException(status_code=500, detail=f"晋升失败: {e}")


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
        raise HTTPException(status_code=500, detail=f"降级失败: {e}")


@router.delete("/{record_id}")
async def delete_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """删除经验记录"""
    try:
        def _delete():
            db = _get_experience_db()
            # ExperienceDB 没有直接 delete 方法，通过内部 _connect() 获取连接后直接删
            conn = db._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM experiences WHERE id = ?", (record_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

        deleted = await asyncio.to_thread(_delete)
        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除经验记录失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
