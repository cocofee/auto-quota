"""
大模型配置服务

管理大模型配置：存在数据库 system_settings 表中，管理员可在设置页面修改。
优先读数据库，没有则用环境变量默认值。

存储的 key：
  - llm_type: 模型类型（qwen/claude/deepseek/kimi/openai）
  - llm_api_key: API密钥
  - llm_base_url: API地址（可选，留空用默认）
  - llm_model: 模型名称（可选，留空用默认）
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 允许的模型类型
VALID_LLM_TYPES = ("qwen", "claude", "deepseek", "kimi", "openai")

# 每种模型的默认配置（和 config.py 保持一致）
_DEFAULTS = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "claude": {
        "base_url": "",
        "model": "claude-sonnet-4-20250514",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "kimi": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "kimi-k2.5",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
    },
}


async def get_llm_config(db: AsyncSession) -> dict:
    """获取当前大模型配置

    优先从数据库读取，没有则用环境变量默认值。
    返回: {"llm_type": "qwen", "api_key": "sk-xxx", "base_url": "...", "model": "qwen-plus"}
    """
    import os

    # 从数据库读取各配置项
    result = await db.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('llm_type', 'llm_api_key', 'llm_base_url', 'llm_model')")
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}

    # 模型类型：数据库 → 环境变量MATCH_LLM → 环境变量AGENT_LLM → 默认qwen
    llm_type = (
        db_config.get("llm_type")
        or os.getenv("MATCH_LLM")
        or os.getenv("AGENT_LLM")
        or "qwen"
    )
    if llm_type not in VALID_LLM_TYPES:
        llm_type = "qwen"

    defaults = _DEFAULTS.get(llm_type, _DEFAULTS["qwen"])

    # API Key：数据库 → 对应模型的环境变量
    env_key_name = f"{llm_type.upper()}_API_KEY"
    api_key = db_config.get("llm_api_key") or os.getenv(env_key_name, "")

    # Base URL：数据库 → 对应模型的环境变量 → 默认值
    env_url_name = f"{llm_type.upper()}_BASE_URL"
    base_url = db_config.get("llm_base_url") or os.getenv(env_url_name, "") or defaults["base_url"]

    # 模型名称：数据库 → 对应模型的环境变量 → 默认值
    env_model_name = f"{llm_type.upper()}_MODEL"
    model = db_config.get("llm_model") or os.getenv(env_model_name, "") or defaults["model"]

    return {
        "llm_type": llm_type,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


async def set_llm_config(db: AsyncSession, llm_type: str, api_key: str,
                         base_url: str = "", model: str = "") -> dict:
    """保存大模型配置到数据库（管理员调用）

    用 UPSERT 写入，修改后下次任务立即生效。
    """
    if llm_type not in VALID_LLM_TYPES:
        raise ValueError(f"不支持的模型类型: {llm_type}，可选: {', '.join(VALID_LLM_TYPES)}")

    # 逐项 UPSERT
    items = {
        "llm_type": llm_type,
        "llm_api_key": api_key,
        "llm_base_url": base_url,
        "llm_model": model,
    }
    for key, value in items.items():
        await db.execute(
            text(
                "INSERT INTO system_settings (key, value) VALUES (:key, :value) "
                "ON CONFLICT (key) DO UPDATE SET value = :value"
            ),
            {"key": key, "value": value},
        )

    return {"llm_type": llm_type, "base_url": base_url, "model": model}


def get_llm_config_sync(session) -> dict:
    """同步版本（Celery worker 中使用）

    和 get_llm_config 逻辑一致，但用同步 session。
    """
    import os

    result = session.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('llm_type', 'llm_api_key', 'llm_base_url', 'llm_model')")
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}

    llm_type = (
        db_config.get("llm_type")
        or os.getenv("MATCH_LLM")
        or os.getenv("AGENT_LLM")
        or "qwen"
    )
    if llm_type not in VALID_LLM_TYPES:
        llm_type = "qwen"

    defaults = _DEFAULTS.get(llm_type, _DEFAULTS["qwen"])

    env_key_name = f"{llm_type.upper()}_API_KEY"
    api_key = db_config.get("llm_api_key") or os.getenv(env_key_name, "")

    env_url_name = f"{llm_type.upper()}_BASE_URL"
    base_url = db_config.get("llm_base_url") or os.getenv(env_url_name, "") or defaults["base_url"]

    env_model_name = f"{llm_type.upper()}_MODEL"
    model = db_config.get("llm_model") or os.getenv(env_model_name, "") or defaults["model"]

    return {
        "llm_type": llm_type,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }
