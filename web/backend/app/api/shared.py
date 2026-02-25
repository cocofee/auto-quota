"""
API 共享辅助函数

提取多个 API 模块中重复的公共逻辑：
- get_user_task(): 获取用户的任务（普通用户只能查自己的，管理员可查所有）
- store_experience(): 单条数据回流经验库
- store_experience_batch(): 批量数据回流经验库
"""

import asyncio
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.task import Task
from app.models.user import User


async def get_user_task(
    task_id: uuid.UUID, user: User, db: AsyncSession
) -> Task:
    """获取任务（普通用户只能查自己的，管理员可查所有）

    找不到任务时抛出 404。
    """
    query = select(Task).where(Task.id == task_id)
    if not user.is_admin:
        query = query.where(Task.user_id == user.id)
    result = await db.execute(query)
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


async def store_experience(
    name: str,
    desc: str,
    quota_ids: list[str],
    quota_names: list[str],
    reason: str,
    specialty: str,
    province: str,
    confirmed: bool,
) -> bool:
    """将单条数据回流经验库（在线程池中执行同步操作）

    confirmed=True → 权威层（用户确认的数据）
    confirmed=False → 候选层（系统推荐或纠正的数据）

    返回是否写入成功。失败不抛异常（经验库是增值功能）。
    """
    if not quota_ids:
        return False
    try:
        from tools.jarvis_store import store_one

        def _store():
            return store_one(
                name=name,
                desc=desc,
                quota_ids=quota_ids,
                quota_names=quota_names,
                reason=reason,
                specialty=specialty,
                province=province,
                confirmed=confirmed,
            )

        result = await asyncio.to_thread(_store)
        return bool(result)
    except Exception as e:
        logger.warning(f"经验库写入失败（不影响主操作）: {e}")
        return False


async def store_experience_batch(
    records: list[dict],
    province: str,
    reason: str,
    confirmed: bool,
) -> int:
    """批量写入经验库，返回成功写入的条数

    records 中每条记录需包含: name, desc(可选), quota_ids, quota_names(可选), specialty(可选)
    """
    if not records:
        return 0
    try:
        from tools.jarvis_store import store_one

        def _store_all():
            count = 0
            for rec in records:
                if rec.get("quota_ids"):
                    ok = store_one(
                        name=rec["name"],
                        desc=rec.get("desc", ""),
                        quota_ids=rec["quota_ids"],
                        quota_names=rec.get("quota_names", []),
                        reason=reason,
                        specialty=rec.get("specialty", ""),
                        province=province,
                        confirmed=confirmed,
                    )
                    if ok:
                        count += 1
            return count

        return await asyncio.to_thread(_store_all)
    except Exception as e:
        logger.warning(f"批量经验库写入失败（不影响主操作）: {e}")
        return 0
