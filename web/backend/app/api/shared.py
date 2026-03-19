"""
API 共享辅助函数

提取多个 API 模块中重复的公共逻辑：
- get_user_task(): 获取用户的任务（普通用户只能查自己的，管理员可查所有）
- store_experience(): 单条数据回流经验库
- store_experience_batch(): 批量数据回流经验库

远程模式（MATCH_BACKEND=remote）下，经验库写入转发到本地匹配服务。
"""

import asyncio
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models.task import Task
from app.models.user import User
from app.config import MATCH_BACKEND, LOCAL_MATCH_URL, LOCAL_MATCH_API_KEY


def _is_remote() -> bool:
    """是否使用远程模式"""
    return MATCH_BACKEND == "remote" and LOCAL_MATCH_URL


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


async def _remote_store(path: str, payload: dict) -> dict:
    """转发经验库写入请求到本地匹配服务"""
    import httpx

    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"远程经验库写入返回 {resp.status_code}: {resp.text[:200]}")
        return {}
    except Exception as e:
        logger.warning(f"远程经验库写入失败: [{type(e).__name__}] {e} | url={url}")
        return {}


async def store_experience(
    name: str,
    desc: str,
    quota_ids: list[str],
    quota_names: list[str],
    reason: str,
    specialty: str,
    province: str,
    confirmed: bool,
    feedback_payload: dict | None = None,
) -> bool:
    """将单条数据回流经验库

    confirmed=True → 权威层（用户确认的数据）
    confirmed=False → 候选层（系统推荐或纠正的数据）

    远程模式下转发到本地匹配服务。
    返回是否写入成功。失败不抛异常（经验库是增值功能）。
    """
    if not quota_ids:
        return False

    # 远程模式：转发到本地匹配服务
    if _is_remote():
        result = await _remote_store("/experience/store", {
            "name": name,
            "desc": desc,
            "quota_ids": quota_ids,
            "quota_names": quota_names,
            "reason": reason,
            "specialty": specialty,
            "province": province,
            "confirmed": confirmed,
            "feedback_payload": feedback_payload,
        })
        return result.get("success", False)

    # 本地模式：直接调用
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
                feedback_payload=feedback_payload,
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

    # 远程模式：转发到本地匹配服务
    if _is_remote():
        result = await _remote_store("/experience/store-batch", {
            "records": records,
            "province": province,
            "reason": reason,
            "confirmed": confirmed,
        })
        return result.get("count", 0)

    # 本地模式：直接调用
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
                        feedback_payload=rec.get("feedback_payload"),
                    )
                    if ok:
                        count += 1
            return count

        return await asyncio.to_thread(_store_all)
    except Exception as e:
        logger.warning(f"批量经验库写入失败（不影响主操作）: {e}")
        return 0


async def flag_disputed_experience(
    bill_name: str,
    province: str,
    reason: str,
) -> int:
    """标记经验库权威层记录为有争议

    当用户纠正了一条经验库直通命中的结果时调用。
    返回被标记的记录数。失败不抛异常。
    """
    if not bill_name or not province:
        return 0

    # 远程模式：转发到本地匹配服务
    if _is_remote():
        result = await _remote_store("/experience/flag-disputed", {
            "bill_name": bill_name,
            "province": province,
            "reason": reason,
        })
        return result.get("affected", 0)

    # 本地模式：直接调用
    try:
        from src.experience_db import ExperienceDB

        def _flag():
            db = ExperienceDB(province=province)
            return db.flag_disputed(bill_name, province, reason)

        return await asyncio.to_thread(_flag)
    except Exception as e:
        logger.warning(f"经验库争议标记失败（不影响主操作）: {e}")
        return 0
