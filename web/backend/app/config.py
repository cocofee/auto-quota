"""
Web后端配置

此文件管理 Web 层专用配置（数据库、JWT、Redis 等）。
与项目根目录的 config.py（定额匹配配置）分离，避免相互污染。

配置加载顺序：
1. 项目根目录的 .env（包含大模型API Key等共享密钥）
2. 本文件的默认值（Web层专有配置）
3. 环境变量覆盖（部署时优先级最高）
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（复用已有的API Key等配置）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # web/backend/app → 项目根目录
load_dotenv(PROJECT_ROOT / ".env")

# ============================================================
# 数据库配置
# ============================================================

# PostgreSQL 连接地址
# 格式: postgresql+asyncpg://用户名:密码@地址:端口/数据库名
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://autoquota:autoquota@localhost:5432/autoquota"
)

# 同步版本（Alembic迁移用）
DATABASE_URL_SYNC = DATABASE_URL.replace("+asyncpg", "")

# ============================================================
# JWT 认证配置
# ============================================================

# JWT 密钥：从环境变量读取
# 生产环境必须在 .env 中设置固定值:
#   JWT_SECRET_KEY=<至少32字节随机字符串>
#   生成方法: python -c "import secrets; print(secrets.token_urlsafe(32))"
#
# 未设置时使用固定的开发专用密钥（多worker进程必须共享同一密钥，不能随机生成）
# 注意：此默认值仅供本地开发，生产环境必须替换！
_DEV_FALLBACK_KEY = "dev-only-insecure-key-DO-NOT-USE-IN-PRODUCTION"
_jwt_from_env = os.getenv("JWT_SECRET_KEY", "")
_is_production = os.getenv("ENVIRONMENT", "").lower() in ("production", "prod")

if _jwt_from_env:
    JWT_SECRET_KEY = _jwt_from_env
elif _is_production:
    # 生产环境必须设置JWT密钥，否则拒绝启动
    raise RuntimeError(
        "生产环境必须设置 JWT_SECRET_KEY 环境变量！\n"
        "生成方法: python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
        "然后在 .env 中设置: JWT_SECRET_KEY=<生成的字符串>"
    )
else:
    JWT_SECRET_KEY = _DEV_FALLBACK_KEY

# JWT 算法
JWT_ALGORITHM = "HS256"

# Token 有效期（分钟）
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24小时
JWT_REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7天

# ============================================================
# Redis 配置
# ============================================================

# Redis 连接地址（Celery任务队列 + SSE进度推送）
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ============================================================
# 文件上传配置
# ============================================================

# 上传文件存储目录
UPLOAD_DIR = PROJECT_ROOT / "output" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 单文件最大体积（MB）
UPLOAD_MAX_MB = 30

# 任务输出目录（每个任务一个子目录，存放匹配结果Excel和JSON）
TASK_OUTPUT_DIR = PROJECT_ROOT / "output" / "tasks"
TASK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# CORS 跨域配置
# ============================================================

# 允许的前端地址（开发时允许localhost:3000，生产时改成实际域名）
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")

# ============================================================
# 认证Cookie配置（HttpOnly）
# ============================================================

ACCESS_TOKEN_COOKIE_NAME = os.getenv("ACCESS_TOKEN_COOKIE_NAME", "access_token")
REFRESH_TOKEN_COOKIE_NAME = os.getenv("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()


# ============================================================
# 配置验证（启动时检查关键配置）
# ============================================================

def validate_config():
    """验证关键配置项，有问题时打印警告（不阻止启动）"""
    from loguru import logger

    warnings = []

    if not _jwt_from_env:
        warnings.append(
            "JWT_SECRET_KEY 未设置，正在使用不安全的开发默认密钥！\n"
            "  ⚠️ 生产环境必须在 .env 中设置: JWT_SECRET_KEY=<随机字符串>\n"
            "  生成方法: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    if not DATABASE_URL:
        warnings.append("DATABASE_URL 未设置，无法连接数据库")

    if not CORS_ORIGINS or CORS_ORIGINS == [""]:
        warnings.append("CORS_ORIGINS 未设置，前端将无法访问API")

    for w in warnings:
        logger.warning(f"[配置检查] {w}")

    return len(warnings) == 0
