"""
大模型配置服务

管理大模型配置：存在数据库 system_settings 表中，管理员可在设置页面修改。
优先读数据库，没有则用环境变量默认值。

存储的 key（匹配模型）：
  - llm_type: 模型类型（qwen/claude/deepseek/kimi/openai）
  - llm_api_key: API密钥
  - llm_base_url: API地址（可选，留空用默认）
  - llm_model: 模型名称（可选，留空用默认）

存储的 key（验证模型）：
  - verify_llm_type / verify_api_key / verify_base_url / verify_model
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
        "model": "gpt-5.4",
    },
}


def _validate_ascii(api_key: str, base_url: str, model: str):
    """校验输入只含ASCII字符（防止粘贴时带入中文标点导致HTTP编码失败）"""
    api_key = api_key.strip()
    base_url = base_url.strip()
    model = model.strip()
    for field_name, field_val in [("API Key", api_key), ("API地址", base_url), ("模型名称", model)]:
        if field_val:
            try:
                field_val.encode("ascii")
            except UnicodeEncodeError:
                raise ValueError(f"{field_name} 包含非法字符（中文标点等），请检查后重新输入")
    return api_key, base_url, model


def _read_config(db_config: dict, prefix: str, env_llm_key: str, env_fallback: str = "") -> dict:
    """通用读取逻辑：从 db_config 字典中读取指定前缀的配置

    参数:
        db_config: 从数据库读取的 {key: value} 字典
        prefix: 数据库key前缀，匹配模型用 "llm"，验证模型用 "verify"
        env_llm_key: 环境变量名（匹配用 AGENT_LLM，验证用 VERIFY_LLM）
        env_fallback: 回退环境变量名（验证模型回退到匹配模型类型）
    """
    import os

    # 模型类型：数据库 → 环境变量 → 回退
    type_key = f"{prefix}_type" if prefix == "verify" else f"{prefix}_type"
    llm_type = (
        db_config.get(type_key)
        or os.getenv(env_llm_key, "")
        or (os.getenv(env_fallback, "") if env_fallback else "")
        or ""
    )
    if llm_type and llm_type not in VALID_LLM_TYPES:
        llm_type = ""

    # 验证模型允许为空（表示跟匹配模型走）
    if not llm_type and prefix == "verify":
        return {"llm_type": "", "api_key": "", "base_url": "", "model": ""}

    # 匹配模型不能为空，默认 qwen
    if not llm_type:
        llm_type = "qwen"

    defaults = _DEFAULTS.get(llm_type, _DEFAULTS["qwen"])

    api_key_field = f"{prefix}_api_key" if prefix == "verify" else "llm_api_key"
    url_field = f"{prefix}_base_url" if prefix == "verify" else "llm_base_url"
    model_field = f"{prefix}_model" if prefix == "verify" else "llm_model"

    env_key_name = f"{llm_type.upper()}_API_KEY"
    api_key = db_config.get(api_key_field) or os.getenv(env_key_name, "")

    env_url_name = f"{llm_type.upper()}_BASE_URL"
    base_url = db_config.get(url_field) or os.getenv(env_url_name, "") or defaults["base_url"]

    env_model_name = f"{llm_type.upper()}_MODEL"
    # 验证模型优先用 VERIFY_MODEL 环境变量
    if prefix == "verify":
        model = db_config.get(model_field) or os.getenv("VERIFY_MODEL", "") or os.getenv(env_model_name, "") or defaults["model"]
    else:
        model = db_config.get(model_field) or os.getenv(env_model_name, "") or defaults["model"]

    return {
        "llm_type": llm_type,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


# ============================================================
# 匹配模型配置
# ============================================================

async def get_llm_config(db: AsyncSession) -> dict:
    """获取匹配模型配置"""
    result = await db.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('llm_type', 'llm_api_key', 'llm_base_url', 'llm_model')")
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}
    import os
    # 兼容旧的环境变量名
    return _read_config(db_config, "llm", env_llm_key="MATCH_LLM", env_fallback="AGENT_LLM")


async def set_llm_config(db: AsyncSession, llm_type: str, api_key: str,
                         base_url: str = "", model: str = "") -> dict:
    """保存匹配模型配置到数据库"""
    if llm_type not in VALID_LLM_TYPES:
        raise ValueError(f"不支持的模型类型: {llm_type}，可选: {', '.join(VALID_LLM_TYPES)}")

    api_key, base_url, model = _validate_ascii(api_key, base_url, model)

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
    """同步版本（Celery worker 中使用）"""
    result = session.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('llm_type', 'llm_api_key', 'llm_base_url', 'llm_model')")
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}
    return _read_config(db_config, "llm", env_llm_key="MATCH_LLM", env_fallback="AGENT_LLM")


# ============================================================
# 验证模型配置
# ============================================================

# 验证模型在数据库中的key列表
_VERIFY_DB_KEYS = ('verify_type', 'verify_api_key', 'verify_base_url', 'verify_model')


async def get_verify_config(db: AsyncSession) -> dict:
    """获取验证模型配置

    验证模型可以为空（表示跟匹配模型走同一个模型）。
    """
    result = await db.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('verify_type', 'verify_api_key', 'verify_base_url', 'verify_model')"),
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}
    return _read_config(db_config, "verify", env_llm_key="VERIFY_LLM", env_fallback="AGENT_LLM")


async def set_verify_config(db: AsyncSession, llm_type: str, api_key: str,
                            base_url: str = "", model: str = "") -> dict:
    """保存验证模型配置到数据库

    llm_type 传空字符串表示"跟匹配模型走"。
    """
    if llm_type and llm_type not in VALID_LLM_TYPES:
        raise ValueError(f"不支持的模型类型: {llm_type}，可选: {', '.join(VALID_LLM_TYPES)}")

    api_key, base_url, model = _validate_ascii(api_key, base_url, model)

    items = {
        "verify_type": llm_type,
        "verify_api_key": api_key,
        "verify_base_url": base_url,
        "verify_model": model,
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


def get_verify_config_sync(session) -> dict:
    """同步版本（Celery worker 中使用）"""
    result = session.execute(
        text("SELECT key, value FROM system_settings WHERE key IN ('verify_type', 'verify_api_key', 'verify_base_url', 'verify_model')"),
    )
    db_config = {row[0]: row[1] for row in result.fetchall()}
    return _read_config(db_config, "verify", env_llm_key="VERIFY_LLM", env_fallback="AGENT_LLM")

