"""
邀请码服务

管理注册邀请码：优先从数据库 system_settings 读取，没有则用 config.py 的默认值。
管理员可通过系统设置页面修改邀请码（写入数据库）。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INVITE_CODE


async def get_invite_code(db: AsyncSession) -> str:
    """获取当前有效的邀请码

    优先从数据库读，没有则返回环境变量默认值。
    """
    result = await db.execute(
        text("SELECT value FROM system_settings WHERE key = 'invite_code'")
    )
    row = result.fetchone()
    if row and row[0]:
        return row[0]
    return INVITE_CODE


async def set_invite_code(db: AsyncSession, new_code: str) -> str:
    """修改邀请码（管理员调用）

    用 UPSERT 写入数据库，下次注册时立即生效。
    注意：不在这里 commit，由调用方统一提交事务。
    """
    await db.execute(
        text(
            "INSERT INTO system_settings (key, value) VALUES ('invite_code', :code) "
            "ON CONFLICT (key) DO UPDATE SET value = :code"
        ),
        {"code": new_code},
    )
    return new_code
