"""
额度管理服务

负责：额度扣减（并发安全）、余额查询、注册赠送记录、管理员调整。
所有额度变动都记录日志（quota_logs），方便审计和用户查看使用记录。
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.quota_log import QuotaLog


# ============================================================
# 额度包配置（硬编码常量，后续如需动态配置可改为从数据库读取）
# ============================================================

QUOTA_PACKAGES = [
    {"id": "pkg_500", "name": "500条额度包", "quota": 500, "price": 9.9},
    {"id": "pkg_2000", "name": "2000条额度包", "quota": 2000, "price": 19.9},
    {"id": "pkg_10000", "name": "10000条额度包", "quota": 10000, "price": 49.9},
]

# 新用户注册赠送额度
REGISTER_GIFT_QUOTA = 1000


def get_package_by_id(package_id: str) -> dict | None:
    """根据ID查找额度包配置"""
    for pkg in QUOTA_PACKAGES:
        if pkg["id"] == package_id:
            return pkg
    return None


# ============================================================
# 异步版本（FastAPI 路由中使用）
# ============================================================

async def get_balance_info(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """查询用户额度信息

    返回:
        {
            "balance": 剩余额度,
            "total_used": 已使用总量(绝对值),
            "total_purchased": 已购买总量,
        }
    """
    # 查余额
    result = await db.execute(
        select(User.quota_balance).where(User.id == user_id)
    )
    balance = result.scalar_one_or_none() or 0

    # 查已使用总量（所有 task_deduct 的 amount 之和的绝对值）
    result = await db.execute(
        select(func.coalesce(func.sum(QuotaLog.amount), 0)).where(
            QuotaLog.user_id == user_id,
            QuotaLog.change_type == "task_deduct",
        )
    )
    total_used = abs(int(result.scalar_one()))

    # 查已购买总量
    result = await db.execute(
        select(func.coalesce(func.sum(QuotaLog.amount), 0)).where(
            QuotaLog.user_id == user_id,
            QuotaLog.change_type == "purchase",
        )
    )
    total_purchased = int(result.scalar_one())

    return {
        "balance": balance,
        "total_used": total_used,
        "total_purchased": total_purchased,
    }


async def record_register_gift(db: AsyncSession, user_id: uuid.UUID):
    """记录注册赠送额度日志

    在用户注册成功后调用。User 模型的 default=1000 已经设好了余额，
    这里只是补记一条日志，方便用户在"使用记录"里看到。
    """
    log = QuotaLog(
        user_id=user_id,
        change_type="register_gift",
        amount=REGISTER_GIFT_QUOTA,
        balance_after=REGISTER_GIFT_QUOTA,
        note=f"注册赠送{REGISTER_GIFT_QUOTA}条免费额度",
    )
    db.add(log)
    # 不在这里 commit，由调用方统一提交


async def admin_adjust_quota(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    note: str,
    admin_id: uuid.UUID,
) -> int:
    """管理员调整用户额度

    参数:
        amount: 正数=增加，负数=扣减
        note: 调整原因（必填）
        admin_id: 操作的管理员ID（审计用）
    返回:
        调整后的新余额
    异常:
        ValueError: 用户不存在或余额不足以扣减
    """
    # 原子更新余额（一条SQL完成"检查+更新"，避免并发竞态）
    result = await db.execute(
        text(
            "UPDATE users SET quota_balance = quota_balance + :amount "
            "WHERE id = :user_id AND quota_balance + :amount >= 0 "
            "RETURNING quota_balance"
        ),
        {"amount": amount, "user_id": str(user_id)},
    )
    row = result.fetchone()

    if row is None:
        # 可能是用户不存在，也可能是余额不足
        check = await db.execute(
            select(User.quota_balance).where(User.id == user_id)
        )
        current = check.scalar_one_or_none()
        if current is None:
            raise ValueError("用户不存在")
        raise ValueError(f"余额不足：当前{current}条，尝试扣减{abs(amount)}条")

    new_balance = row[0]

    # 记录日志
    log = QuotaLog(
        user_id=user_id,
        change_type="admin_adjust",
        amount=amount,
        balance_after=new_balance,
        ref_id=str(admin_id),
        note=note,
    )
    db.add(log)

    logger.info(f"管理员 {admin_id} 调整用户 {user_id} 额度: {amount:+d}，新余额: {new_balance}")
    return new_balance


# ============================================================
# 同步版本（Celery worker 中使用）
# ============================================================

def deduct_quota_sync(session, user_id: uuid.UUID, count: int,
                      task_id: str, task_name: str) -> int:
    """同步扣减用户额度（Celery worker 中调用）

    使用 SQL 原子操作 UPDATE ... WHERE balance >= N，
    一条SQL完成"检查余额+扣减"，数据库行锁保证并发安全。

    参数:
        session: SQLAlchemy 同步会话
        user_id: 用户ID
        count: 扣减条数
        task_id: 关联的任务ID
        task_name: 任务名称（日志用）
    返回:
        扣减后的新余额
    """
    if count <= 0:
        return 0  # 不需要扣减，返回0表示无操作

    result = session.execute(
        text("""
            UPDATE users
            SET quota_balance = quota_balance - :count
            WHERE id = :user_id AND quota_balance >= :count
            RETURNING quota_balance
        """),
        {"user_id": str(user_id), "count": count},
    )
    row = result.fetchone()

    if row is None:
        # 余额不足，但任务已完成，不阻塞
        logger.warning(f"用户 {user_id} 额度不足，无法扣减 {count} 条（任务 {task_id}）")
        return -1

    new_balance = row[0]

    # 记录日志
    session.execute(
        text("""
            INSERT INTO quota_logs (user_id, change_type, amount, balance_after, ref_id, note)
            VALUES (:user_id, 'task_deduct', :amount, :balance, :ref_id, :note)
        """),
        {
            "user_id": str(user_id),
            "amount": -count,
            "balance": new_balance,
            "ref_id": task_id,
            "note": f"任务\"{task_name}\"匹配完成，扣减{count}条",
        },
    )

    logger.info(f"用户 {user_id} 额度扣减 {count} 条，剩余 {new_balance}（任务 {task_id}）")
    return new_balance


def add_quota_sync(session, user_id: uuid.UUID, count: int,
                   order_id: str, package_name: str) -> int:
    """同步增加用户额度（支付回调中调用）

    参数:
        count: 增加条数
        order_id: 关联的订单ID
        package_name: 额度包名称（日志用）
    返回:
        增加后的新余额
    """
    result = session.execute(
        text("""
            UPDATE users
            SET quota_balance = quota_balance + :count
            WHERE id = :user_id
            RETURNING quota_balance
        """),
        {"user_id": str(user_id), "count": count},
    )
    row = result.fetchone()
    if row is None:
        raise ValueError(f"用户 {user_id} 不存在")

    new_balance = row[0]

    # 记录日志
    session.execute(
        text("""
            INSERT INTO quota_logs (user_id, change_type, amount, balance_after, ref_id, note)
            VALUES (:user_id, 'purchase', :amount, :balance, :ref_id, :note)
        """),
        {
            "user_id": str(user_id),
            "amount": count,
            "balance": new_balance,
            "ref_id": order_id,
            "note": f"购买\"{package_name}\"，充值{count}条",
        },
    )

    logger.info(f"用户 {user_id} 充值 {count} 条，新余额 {new_balance}（订单 {order_id}）")
    return new_balance
