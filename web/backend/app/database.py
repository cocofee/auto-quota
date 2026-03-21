"""
数据库连接管理

使用 SQLAlchemy 2.0 异步引擎连接 PostgreSQL。
提供 get_db() 依赖注入函数，在每个API请求中获取数据库会话。
"""

import threading

from fastapi import HTTPException
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL


# 创建异步数据库引擎
engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,           # 连接池大小（开发10，生产可调大）
    max_overflow=5,         # 超出pool_size后最多再创建5个临时连接
    pool_recycle=3600,      # 每小时回收空闲连接（防止PostgreSQL断开长闲置连接）
    pool_pre_ping=True,     # 每次取连接前先ping一下确认有效（避免"连接已断开"错误）
    echo=False,             # 不打印SQL到控制台
)

# 创建会话工厂（每次请求创建一个独立的数据库会话）
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不自动过期对象（避免额外查询）
)


# SQLAlchemy 模型基类（所有数据库表模型都继承这个类）
class Base(DeclarativeBase):
    pass


async def get_db():
    """获取数据库会话（FastAPI依赖注入用）

    用法（在API路由函数参数中）:
        async def some_api(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(User))
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(f"数据库事务失败: {e}")
            raise
        except HTTPException:
            # 401/403等正常的HTTP响应，不需要记录错误日志
            await session.rollback()
            raise
        except Exception as e:
            await session.rollback()
            logger.error(f"请求处理异常，数据库事务已回滚: {e}")
            raise


# ============================================================
# 同步引擎（Celery worker 用，因为匹配任务是同步执行的）
# 延迟初始化：只在首次调用 get_sync_session() 时创建，
# 避免 FastAPI 进程在没装 psycopg2 时导入失败。
# ============================================================

_sync_session_factory = None
_sync_lock = threading.Lock()


def get_sync_session():
    """获取同步数据库会话（Celery worker 中使用）

    首次调用时自动创建同步引擎和会话工厂。
    使用线程锁保证多线程并发时只初始化一次。
    需要安装 psycopg2-binary 依赖。

    用法:
        session = get_sync_session()
        try:
            # 操作数据库...
            session.commit()
        except:
            session.rollback()
            raise
        finally:
            session.close()
    """
    global _sync_session_factory
    if _sync_session_factory is None:
        with _sync_lock:
            # 双重检查锁（进入锁后再检查一次，避免重复初始化）
            if _sync_session_factory is None:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker
                from app.config import DATABASE_URL_SYNC

                sync_engine = create_engine(
                    DATABASE_URL_SYNC,
                    pool_size=5,
                    pool_recycle=3600,
                    pool_pre_ping=True,
                )
                _sync_session_factory = sessionmaker(sync_engine)

    return _sync_session_factory()


async def init_db():
    """初始化数据库（创建所有表 + 自动迁移新列）

    注意: 调用前必须先 import app.models，否则 Base.metadata 为空，不会创建任何表。
    仅开发环境使用，生产环境应该用 Alembic 迁移。

    create_all 只能创建新表，不能给已有表加列。
    所以在 create_all 之后执行手动迁移语句，用 IF NOT EXISTS 保证幂等。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 手动迁移：给已有表添加新列（create_all 不会做这件事）
        # 每条语句用 IF NOT EXISTS 保证可重复执行不报错
        migrations = [
            # 2026-02-25: match_results 表新增 bill_code 列（清单项编码）
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS bill_code VARCHAR(100) DEFAULT ''",
            # 2026-02-25: match_results 表新增 sheet_name 和 section 列（分部分项展示用）
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS sheet_name VARCHAR(100) DEFAULT ''",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS section VARCHAR(200) DEFAULT ''",
            # 2026-02-27: users 表新增 quota_balance 列（额度余额，新用户默认1000条）
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_balance INTEGER DEFAULT 1000",
            # 2026-02-27: system settings table
            "CREATE TABLE IF NOT EXISTS system_settings (key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL)",
            # 2026-03-15: match_results 表新增字段（OpenClaw增强）
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS bill_unit_price FLOAT",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS bill_amount FLOAT",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS alternatives JSON",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS is_measure_item BOOLEAN DEFAULT FALSE",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS confidence_score INTEGER DEFAULT 0",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS review_risk VARCHAR(20) DEFAULT 'low'",
            "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS light_status VARCHAR(20) DEFAULT 'red'",
        ]
        from sqlalchemy import text
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                logger.warning(f"迁移语句执行跳过（可能已存在）: {e}")
