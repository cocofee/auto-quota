"""
自动套定额系统 - Streamlit Web界面
启动方式：streamlit run app.py
"""

import streamlit as st
from pathlib import Path

# 页面配置（必须是第一个Streamlit命令）
st.set_page_config(
    page_title="自动套定额系统",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 加载项目配置
import config


def main():
    """主页：系统概览"""

    st.title("自动套定额系统")
    st.caption("基于AI的工程造价自动套定额工具")

    st.divider()

    # 系统状态概览
    col1, col2, col3 = st.columns(3)

    # 定额数据库状态
    with col1:
        try:
            from src.quota_db import QuotaDB
            db = QuotaDB()
            quota_stats = db.get_stats()
            st.metric("定额数据库", f"{quota_stats['total']} 条",
                      help="已导入的定额总数")
            st.caption(f"{quota_stats['chapters']} 个章节")
        except Exception:
            st.metric("定额数据库", "未初始化")
            st.caption("请先导入定额数据")

    # 经验库状态
    with col2:
        try:
            from src.experience_db import ExperienceDB
            exp_db = ExperienceDB()
            exp_stats = exp_db.get_stats()
            st.metric("经验库", f"{exp_stats['total']} 条",
                      help="历史匹配记录数")
            st.caption(f"平均置信度 {exp_stats['avg_confidence']}%")
        except Exception:
            st.metric("经验库", "0 条")
            st.caption("匹配后自动积累")

    # 搜索引擎状态
    with col3:
        try:
            from src.hybrid_searcher import HybridSearcher
            searcher = HybridSearcher()
            status = searcher.get_status()
            if status["bm25_ready"]:
                st.metric("搜索引擎", "就绪",
                          help="BM25+向量混合搜索")
                st.caption(f"BM25: {status['bm25_count']} 条")
            else:
                st.metric("搜索引擎", "未就绪")
                st.caption("请先构建搜索索引")
        except Exception:
            st.metric("搜索引擎", "未就绪")
            st.caption("请先导入定额并构建索引")

    st.divider()

    # 快速开始
    st.subheader("快速开始")
    st.markdown("""
    1. **匹配定额** → 上传清单Excel，系统自动匹配定额，下载结果导入广联达
    2. **定额数据库** → 查看已导入的定额数据，导入新定额
    3. **经验库** → 查看历史匹配记录和统计
    4. **设置** → 配置API密钥、选择省份等
    """)

    # 当前配置信息
    st.divider()
    st.subheader("当前配置")
    info_col1, info_col2 = st.columns(2)
    with info_col1:
        st.text(f"省份/版本：{config.CURRENT_PROVINCE}")
        st.text(f"默认大模型：{config.DEFAULT_LLM}")
    with info_col2:
        st.text(f"向量模型：{config.VECTOR_MODEL_NAME}")
        st.text(f"混合搜索Top K：{config.HYBRID_TOP_K}")


if __name__ == "__main__":
    main()
