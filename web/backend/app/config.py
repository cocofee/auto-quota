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
# 注册邀请码（防止乱注册白嫖额度）
# ============================================================

# 默认邀请码，管理员可在"系统设置"页面修改（修改后存数据库，优先级高于此默认值）
INVITE_CODE = os.getenv("INVITE_CODE", "autoquota2026")

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

# 日志目录（loguru 按天写入，Docker 挂载到宿主机持久化）
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

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
# 匹配任务默认配置
# ============================================================

# 匹配模式：agent（大模型智能匹配）或 search（纯搜索）
# 当前固定 agent，后续可在 .env 中切换
MATCH_MODE = os.getenv("MATCH_MODE", "agent")
if MATCH_MODE not in ("search", "agent"):
    raise ValueError(f"MATCH_MODE 必须是 search 或 agent，当前值: {MATCH_MODE}")

# Agent 模式使用的大模型（claude / deepseek / kimi / qwen）
# 当前固定 claude，后续可在 .env 中切换
MATCH_LLM = os.getenv("MATCH_LLM", "deepseek")
if MATCH_LLM not in ("claude", "deepseek", "kimi", "qwen", "openai"):
    raise ValueError(f"MATCH_LLM 值不合法: {MATCH_LLM}，可选: claude/deepseek/kimi/qwen/openai")


# ============================================================
# 匹配后端模式（本机执行 或 远程API）
# ============================================================

# 匹配后端：
#   "local" — 在本机运行 main.run()（需要完整镜像+定额库+模型，默认）
#   "remote" — 转发到本地电脑的匹配API（轻量镜像，不需要定额库和模型）
MATCH_BACKEND = os.getenv("MATCH_BACKEND", "local")
if MATCH_BACKEND not in ("local", "remote"):
    raise ValueError(f"MATCH_BACKEND 必须是 local 或 remote，当前值: {MATCH_BACKEND}")

# 远程匹配API地址（MATCH_BACKEND=remote 时必填）
# 格式: http://你的电脑IP:9100
LOCAL_MATCH_URL = os.getenv("LOCAL_MATCH_URL", "")

# 远程匹配API密钥（和本地匹配服务的 LOCAL_MATCH_API_KEY 保持一致）
LOCAL_MATCH_API_KEY = os.getenv("LOCAL_MATCH_API_KEY", "")


# ============================================================
# 好易支付配置（额度购买）
# ============================================================

# 商户ID（注册好易支付后获取）
EPAY_PID = os.getenv("EPAY_PID", "")

# 商户密钥（签名用，不能泄露，只放在服务端）
EPAY_KEY = os.getenv("EPAY_KEY", "")

# 支付网关地址（好易支付部署地址）
EPAY_URL = os.getenv("EPAY_URL", "")

# 后端公网地址（好易支付服务器回调用，必须是公网可访问的）
EPAY_NOTIFY_BASE_URL = os.getenv("EPAY_NOTIFY_BASE_URL", "")

# 前端地址（用户支付完跳回来用）
EPAY_RETURN_BASE_URL = os.getenv("EPAY_RETURN_BASE_URL", "")


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
