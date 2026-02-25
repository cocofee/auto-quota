"""
数据库连接管理

使用 SQLAlchemy 2.0 异步引擎连接 PostgreSQL。
提供 get_db() 依赖注入函数，在每个API请求中获取数据库会话。
"""

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


def get_sync_session():
    """获取同步数据库会话（Celery worker 中使用）

    首次调用时自动创建同步引擎和会话工厂。
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
    """初始化数据库（创建所有表）

    注意: 调用前必须先 import app.models，否则 Base.metadata 为空，不会创建任何表。
    仅开发环境使用，生产环境应该用 Alembic 迁移。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
