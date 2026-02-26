"""
auto-quota Web后端 - FastAPI 入口

启动命令:
    cd web/backend
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

访问 API 文档:
    http://localhost:8000/docs      (Swagger UI 交互式文档)
    http://localhost:8000/redoc     (ReDoc 阅读式文档)
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import CORS_ORIGINS, LOG_DIR

# 把项目根目录加入Python路径，这样后端代码可以直接 import main, config, src.* 等
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# 日志持久化：写入文件（按天轮转，保留30天）
# 容器内路径 /app/logs/ 通过 docker-compose volumes 挂载到宿主机 ./logs/
logger.add(
    str(LOG_DIR / "web_{time:YYYY-MM-DD}.log"),
    rotation="00:00",     # 每天零点新建一个日志文件
    retention="30 days",  # 保留30天
    encoding="utf-8",
    level="INFO",
)
if not (PROJECT_ROOT / "config.py").exists():
    raise RuntimeError(
        f"项目根目录定位失败（找不到 config.py）: {PROJECT_ROOT}\n"
        "请确认 web/backend/app/main.py 相对于项目根目录的层级未变"
    )
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理

    启动时: 导入模型 + 初始化数据库表
    关闭时: 清理资源
    """
    logger.info("auto-quota Web后端启动中...")

    # 启动时检查关键配置（不阻止启动，只打警告）
    from app.config import validate_config
    validate_config()

    # 必须先导入模型，让 Base.metadata 发现所有表定义
    # 否则 create_all 会创建0张表（Codex审查发现的问题）
    import app.models  # noqa: F401 — 导入是为了注册模型到 Base.metadata

    from app.database import init_db
    await init_db()
    logger.info("数据库初始化完成")

    yield  # 应用运行中...

    logger.info("auto-quota Web后端关闭")


# 创建 FastAPI 应用实例
app = FastAPI(
    title="auto-quota API",
    description="自动套定额系统 Web API —— 上传清单Excel，自动匹配定额，输出广联达格式",
    version="1.0.0",
    lifespan=lifespan,
)

# 配置跨域（CORS）
# 前端和后端运行在不同端口时，浏览器会拦截跨域请求，需要后端明确允许
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,                                 # 允许的前端地址
    allow_credentials=True,                                     # 允许携带Cookie
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # 只允许必要的HTTP方法
    allow_headers=["Content-Type", "Authorization", "Accept"],  # 只允许必要的请求头
    max_age=3600,                                               # 预检请求缓存1小时
)


# ============================================================
# 注册 API 路由
# ============================================================

from app.auth import auth_router
app.include_router(auth_router, prefix="/api/auth", tags=["认证"])

from app.api.tasks import router as tasks_router
app.include_router(tasks_router, prefix="/api/tasks", tags=["匹配任务"])

from app.api.results import router as results_router
app.include_router(results_router, prefix="/api", tags=["匹配结果"])

from app.api.admin import router as admin_router
app.include_router(admin_router, prefix="/api/admin", tags=["管理员-用户管理"])

from app.api.analytics import router as analytics_router
app.include_router(analytics_router, prefix="/api/admin/analytics", tags=["管理员-准确率分析"])

from app.api.experience import router as experience_router
app.include_router(experience_router, prefix="/api/admin/experience", tags=["管理员-经验库"])

from app.api.quota_manage import router as quota_manage_router
app.include_router(quota_manage_router, prefix="/api/admin/quotas", tags=["管理员-定额库"])

from app.api.feedback import router as feedback_router
app.include_router(feedback_router, prefix="/api", tags=["反馈"])

from app.api.consult import router as consult_router
app.include_router(consult_router, prefix="/api/consult", tags=["定额咨询"])

from app.api.logs import router as logs_router
app.include_router(logs_router, prefix="/api/admin/logs", tags=["管理员-系统日志"])

from app.api.knowledge import router as knowledge_router
app.include_router(knowledge_router, prefix="/api/admin/knowledge", tags=["管理员-知识库"])


# ============================================================
# 基础端点（健康检查）
# ============================================================

@app.get("/api/health", tags=["系统"])
async def health_check():
    """健康检查 —— 用于确认服务是否正常运行"""
    return {
        "status": "ok",
        "service": "auto-quota-api",
        "version": "1.0.0",
    }


@app.get("/api/provinces", tags=["系统"])
async def list_provinces():
    """获取可用省份列表 —— 前端下拉选择用

    返回 provinces（名称列表）和 groups（分组映射），
    分组来自 data/quota_data/ 的文件夹结构。
    """
    try:
        import config as quota_config
        provinces = quota_config.list_db_provinces()
        groups = quota_config.get_province_groups()
        return {"provinces": provinces, "groups": groups}
    except Exception as e:
        logger.error(f"获取省份列表失败: {e}")
        raise HTTPException(
            status_code=500,
            detail="获取省份列表失败，请检查定额库是否已导入"
        )


# ============================================================
# 管理员初始化 API（将指定用户设为管理员）
# ============================================================

@app.post("/api/admin/make-admin", tags=["管理员"])
async def make_admin(
    email: str = Body(description="用户邮箱"),
    admin_secret: str = Body(description="管理员密钥（JWT_SECRET_KEY）"),
):
    """将指定邮箱的用户设为管理员

    需要提供 JWT_SECRET_KEY 作为验证（防止未授权调用）。
    这是一次性初始化用的接口，用于设置第一个管理员账号。

    安全策略：当 JWT_SECRET_KEY 使用开发默认值时拒绝服务，
    防止源码中可见的硬编码密钥被滥用。
    """
    from app.config import JWT_SECRET_KEY, _DEV_FALLBACK_KEY
    # 安全检查：使用开发默认密钥时拒绝此接口（防止硬编码密钥被滥用）
    if JWT_SECRET_KEY == _DEV_FALLBACK_KEY:
        raise HTTPException(
            status_code=403,
            detail="当前使用开发默认密钥，make-admin 接口已禁用。"
                   "请在 .env 中设置 JWT_SECRET_KEY 后重试。"
        )
    if admin_secret != JWT_SECRET_KEY:
        raise HTTPException(status_code=403, detail="密钥错误")

    from sqlalchemy import select, update
    from app.database import async_session
    from app.models.user import User

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if user.is_admin:
            return {"message": f"{email} 已经是管理员"}

        user.is_admin = True
        await session.commit()
        return {"message": f"已将 {email} 设为管理员"}
