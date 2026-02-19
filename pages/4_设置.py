"""
设置页面
功能：API密钥配置、省份选择、匹配参数调整
"""

import sys
import os
import re
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

st.set_page_config(page_title="设置", page_icon="⚙️", layout="wide")


def _sanitize_env_value(value) -> str:
    """清洗.env值，阻断换行注入和空字节。"""
    text = str(value or "")
    text = text.replace("\x00", "").replace("\r", " ").replace("\n", " ").strip()
    return text


def _merge_env_updates(env_path: Path, updates: dict):
    """只更新目标key，保留.env中其他配置和注释。"""
    updates = {str(k): _sanitize_env_value(v) for k, v in (updates or {}).items()}
    existing_lines = []
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                existing_lines = f.read().splitlines()
        except Exception as e:
            raise RuntimeError(f"读取 .env 失败: {e}") from e

    out_lines = []
    seen = set()
    key_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")

    for line in existing_lines:
        m = key_pattern.match(line.strip())
        if not m:
            out_lines.append(line)
            continue

        key = m.group(1)
        if key in updates:
            if key not in seen:
                out_lines.append(f"{key}={updates[key]}")
                seen.add(key)
            # 重复key跳过（保留第一次）
            continue

        out_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")

    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
        os.replace(tmp_path, env_path)
    except Exception as e:
        raise RuntimeError(f"写入 .env 失败: {e}") from e
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def show_api_settings():
    """API密钥配置"""
    st.subheader("大模型API配置")

    st.markdown("""
    **说明：** 纯搜索模式不需要API密钥。只有使用「搜索+AI精选」模式时才需要配置。
    API密钥保存在项目根目录的 `.env` 文件中，不会上传到网络。
    """)

    env_path = config.PROJECT_ROOT / ".env"

    # 读取当前.env配置
    env_vars = {}
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_vars[key.strip()] = value.strip()
        except Exception as e:
            st.warning(f"读取 .env 失败，已使用默认值：{e}")
            env_vars = {}

    # 默认大模型选择
    llm_options = {
        "deepseek": "DeepSeek（推荐，性价比高）",
        "claude": "Claude（精度高，费用较高）",
        "openai": "OpenAI GPT（通用）",
    }
    current_llm = env_vars.get("DEFAULT_LLM", config.DEFAULT_LLM)
    default_llm = st.selectbox(
        "默认大模型",
        options=list(llm_options.keys()),
        format_func=lambda x: llm_options[x],
        index=list(llm_options.keys()).index(current_llm) if current_llm in llm_options else 0,
    )

    st.divider()

    # DeepSeek配置
    st.markdown("**DeepSeek 配置**")
    deepseek_key = st.text_input(
        "DeepSeek API Key",
        value=env_vars.get("DEEPSEEK_API_KEY", ""),
        type="password",
        help="从 https://platform.deepseek.com 获取",
    )
    deepseek_url = st.text_input(
        "DeepSeek API URL",
        value=env_vars.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        help="一般不需要修改",
    )

    st.divider()

    # Claude配置
    st.markdown("**Claude 配置**")
    anthropic_key = st.text_input(
        "Anthropic API Key",
        value=env_vars.get("ANTHROPIC_API_KEY", ""),
        type="password",
        help="从 https://console.anthropic.com 获取",
    )

    st.divider()

    # OpenAI配置
    st.markdown("**OpenAI 配置**")
    openai_key = st.text_input(
        "OpenAI API Key",
        value=env_vars.get("OPENAI_API_KEY", ""),
        type="password",
        help="从 https://platform.openai.com 获取",
    )

    # 保存按钮
    if st.button("保存API配置", type="primary"):
        updates = {
            "DEFAULT_LLM": default_llm,
            "DEEPSEEK_API_KEY": deepseek_key,
            "DEEPSEEK_BASE_URL": deepseek_url,
            "ANTHROPIC_API_KEY": anthropic_key,
            "OPENAI_API_KEY": openai_key,
        }
        try:
            _merge_env_updates(env_path, updates)
        except Exception as e:
            st.error(f"保存失败：{e}")
            return

        st.success("API配置已保存到 .env 文件。重启Streamlit后生效。")


def show_province_settings():
    """省份/版本设置"""
    st.subheader("省份/定额版本")

    st.markdown("""
    **说明：** 不同省份使用不同的定额数据库。切换省份前需要先导入该省份的定额数据。
    """)

    # 检查已有的省份数据
    provinces_dir = config.PROVINCES_DB_DIR
    available_provinces = []
    if provinces_dir.exists():
        for p in provinces_dir.iterdir():
            if p.is_dir() and (p / "quota.db").exists():
                available_provinces.append(p.name)

    if available_provinces:
        st.info(f"已有的省份数据：{', '.join(available_provinces)}")
    else:
        st.warning("暂无省份数据，请先在「定额数据库」页面导入定额")

    st.text(f"当前省份：{config.CURRENT_PROVINCE}")
    st.caption("切换省份功能需要修改 config.py 中的 CURRENT_PROVINCE，后续版本会支持界面切换")


def show_match_settings():
    """匹配参数设置"""
    st.subheader("匹配参数")

    st.markdown("以下是当前的匹配参数配置（修改需要编辑 config.py）：")

    params = {
        "向量搜索Top K": config.VECTOR_TOP_K,
        "BM25搜索Top K": config.BM25_TOP_K,
        "混合搜索最终Top K": config.HYBRID_TOP_K,
        "向量搜索权重": f"{config.VECTOR_WEIGHT} ({int(config.VECTOR_WEIGHT*100)}%)",
        "BM25搜索权重": f"{config.BM25_WEIGHT} ({int(config.BM25_WEIGHT*100)}%)",
        "RRF融合常数K": config.RRF_K,
        "经验库直通阈值": f"{config.EXPERIENCE_DIRECT_THRESHOLD}%",
        "多Agent纠偏阈值": f"<{config.MULTI_AGENT_THRESHOLD}% 时自动触发",
        "高置信度阈值（绿色）": f"{config.CONFIDENCE_GREEN}%",
        "中置信度阈值（黄色）": f"{config.CONFIDENCE_YELLOW}%",
    }

    for name, value in params.items():
        col1, col2 = st.columns([2, 1])
        with col1:
            st.text(name)
        with col2:
            st.text(str(value))


def show_system_info():
    """系统信息"""
    st.subheader("系统信息")

    info = {
        "项目根目录": str(config.PROJECT_ROOT),
        "数据目录": str(config.DATA_DIR),
        "数据库目录": str(config.DB_DIR),
        "输出目录": str(config.OUTPUT_DIR),
        "向量模型": config.VECTOR_MODEL_NAME,
        "Python路径": sys.executable,
    }

    for name, value in info.items():
        st.text(f"{name}：{value}")

    # 检查知识库状态
    st.divider()
    st.markdown("**知识库状态**")

    # 通用知识库
    try:
        from src.universal_kb import UniversalKB
        ukb = UniversalKB()
        ukb_stats = ukb.get_stats()
        st.text(f"通用知识库：权威层 {ukb_stats.get('authority', 0)} 条，候选层 {ukb_stats.get('candidate', 0)} 条")
    except Exception as e:
        st.text("通用知识库：未初始化")
        st.caption(f"原因: {e}")

    # 规则知识库
    try:
        from src.rule_knowledge import RuleKnowledge
        rkb = RuleKnowledge()
        rkb_stats = rkb.get_stats()
        st.text(f"规则知识库：{rkb_stats.get('total', 0)} 条规则段")
        if rkb_stats.get("by_province"):
            for prov, count in rkb_stats["by_province"].items():
                st.text(f"  - {prov}：{count} 条")
    except Exception as e:
        st.text("规则知识库：未初始化")
        st.caption(f"原因: {e}")

    # 经验库
    try:
        from src.experience_db import ExperienceDB
        edb = ExperienceDB()
        edb_stats = edb.get_stats()
        auth_count = edb_stats.get("authority", 0)
        cand_count = edb_stats.get("candidate", 0)
        st.text(f"经验库：权威层 {auth_count} 条，候选层 {cand_count} 条（共 {edb_stats.get('total', 0)} 条）")
    except Exception as e:
        st.text("经验库：未初始化")
        st.caption(f"原因: {e}")

    # 检查GPU
    st.divider()
    st.markdown("**GPU状态**")
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            total_memory = getattr(props, "total_memory", getattr(props, "total_mem", 0))
            gpu_mem = total_memory / (1024**3) if total_memory else 0
            st.success(f"GPU可用：{gpu_name}（{gpu_mem:.1f}GB显存）")
        else:
            st.warning("未检测到GPU，向量搜索将使用CPU（速度较慢）")
    except ImportError:
        st.warning("PyTorch未安装")
    except Exception as e:
        st.warning(f"GPU状态检测失败: {e}")


def main():
    st.title("设置")

    tab1, tab2, tab3, tab4 = st.tabs(["API配置", "省份设置", "匹配参数", "系统信息"])

    with tab1:
        show_api_settings()

    with tab2:
        show_province_settings()

    with tab3:
        show_match_settings()

    with tab4:
        show_system_info()


main()
