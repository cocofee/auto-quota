"""
经验库页面
功能：查看历史匹配记录、统计匹配成功率、管理经验数据
"""

import sys
import json
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

st.set_page_config(page_title="经验库", page_icon="🧠", layout="wide")


def get_experience_db():
    """获取经验库实例"""
    try:
        from src.experience_db import ExperienceDB
        return ExperienceDB()
    except Exception as e:
        st.warning(f"经验库加载失败: {e}")
        return None


def show_stats(exp_db):
    """展示经验库统计"""
    stats = exp_db.get_stats()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总记录数", f"{stats['total']} 条")
    with col2:
        st.metric("平均置信度", f"{stats['avg_confidence']}%")
    with col3:
        st.metric("向量索引", f"{stats['vector_count']} 条")

    # 按来源分类
    by_source = stats.get("by_source", {})
    if by_source:
        st.subheader("按来源分类")
        source_labels = {
            "auto_match": "自动匹配确认",
            "user_correction": "用户修正",
            "project_import": "项目导入",
        }
        source_rows = []
        for source, count in by_source.items():
            source_rows.append({
                "来源": source_labels.get(source, source),
                "数量": count,
            })
        st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)

    # 按省份分类
    by_province = stats.get("by_province", {})
    if by_province:
        st.subheader("按省份分类")
        province_rows = [{"省份": p, "数量": c} for p, c in by_province.items()]
        st.dataframe(pd.DataFrame(province_rows), use_container_width=True, hide_index=True)


def show_records(exp_db):
    """展示经验库记录"""
    st.subheader("历史匹配记录")

    # 从SQLite直接读取记录（分页）
    import sqlite3
    conn = sqlite3.connect(str(exp_db.db_path))
    conn.row_factory = sqlite3.Row

    # 总数
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM experiences")
    total = cursor.fetchone()[0]

    if total == 0:
        st.info("经验库为空，匹配清单后系统会自动积累经验")
        conn.close()
        return

    # 分页参数
    page_size = 50
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = st.number_input("页码", min_value=1, max_value=total_pages, value=1)
    offset = (page - 1) * page_size

    # 查询
    cursor.execute("""
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               source, confidence, confirm_count, province
        FROM experiences
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
    """, (page_size, offset))
    rows = cursor.fetchall()
    conn.close()

    st.caption(f"共 {total} 条记录，当前第 {page}/{total_pages} 页")

    # 构建DataFrame
    display_rows = []
    for r in rows:
        quota_ids = json.loads(r["quota_ids"]) if r["quota_ids"] else []
        quota_names = json.loads(r["quota_names"]) if r["quota_names"] else []

        source_labels = {
            "auto_match": "自动匹配",
            "user_correction": "用户修正",
            "project_import": "项目导入",
        }

        display_rows.append({
            "ID": r["id"],
            "清单名称": (r["bill_name"] or r["bill_text"][:40]),
            "定额编号": ", ".join(quota_ids[:3]),
            "定额名称": ", ".join(n[:20] for n in quota_names[:2]),
            "置信度": f"{r['confidence']}%",
            "确认次数": r["confirm_count"],
            "来源": source_labels.get(r["source"], r["source"]),
        })

    df = pd.DataFrame(display_rows)

    # 颜色标记
    def highlight_row(row):
        conf_str = row["置信度"].replace("%", "")
        try:
            conf = int(conf_str)
        except ValueError:
            return [""] * len(row)
        if conf >= config.CONFIDENCE_GREEN:
            return ["background-color: #C6EFCE"] * len(row)
        elif conf >= config.CONFIDENCE_YELLOW:
            return ["background-color: #FFEB9C"] * len(row)
        else:
            return ["background-color: #FFC7CE"] * len(row)

    styled = df.style.apply(highlight_row, axis=1)
    st.dataframe(styled, use_container_width=True, height=500)


def show_search(exp_db):
    """在经验库中搜索"""
    st.subheader("搜索经验库")

    query = st.text_input(
        "输入清单关键词搜索",
        placeholder="例如：镀锌钢管DN150、电力电缆YJV...",
    )

    if query:
        # 用SQLite LIKE做简单搜索（不触发向量模型加载）
        import sqlite3
        conn = sqlite3.connect(str(exp_db.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, bill_text, bill_name, quota_ids, quota_names,
                   confidence, confirm_count, source
            FROM experiences
            WHERE bill_text LIKE ? OR bill_name LIKE ?
            LIMIT 30
        """, (f"%{query}%", f"%{query}%"))
        rows = cursor.fetchall()
        conn.close()

        if rows:
            st.success(f"找到 {len(rows)} 条相关记录")
            display_rows = []
            for r in rows:
                quota_ids = json.loads(r["quota_ids"]) if r["quota_ids"] else []
                quota_names = json.loads(r["quota_names"]) if r["quota_names"] else []
                display_rows.append({
                    "清单文本": r["bill_text"][:60],
                    "定额编号": ", ".join(quota_ids),
                    "定额名称": ", ".join(n[:30] for n in quota_names),
                    "置信度": f"{r['confidence']}%",
                    "确认次数": r["confirm_count"],
                })
            st.dataframe(pd.DataFrame(display_rows), use_container_width=True)
        else:
            st.info("未找到相关记录")


def main():
    st.title("经验库")
    st.caption("系统的学习记录——越用越准的核心")

    exp_db = get_experience_db()
    if not exp_db:
        return

    tab1, tab2, tab3 = st.tabs(["统计概览", "历史记录", "搜索"])

    with tab1:
        show_stats(exp_db)

    with tab2:
        show_records(exp_db)

    with tab3:
        show_search(exp_db)


main()
