"""
定额数据库页面
功能：查看已导入的定额数据、按12大册浏览、搜索定额、导入新定额Excel
"""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


st.set_page_config(page_title="定额数据库", page_icon="📚", layout="wide")


def _ensure_list(value):
    return value if isinstance(value, list) else []


def _open_quota_conn(db_path):
    """统一SQLite连接参数，减少页面查询时的锁等待失败。"""
    import sqlite3
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def show_db_stats():
    """展示定额数据库统计信息"""
    try:
        from src.quota_db import QuotaDB
        db = QuotaDB()
        stats = db.get_stats()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("定额总数", f"{stats.get('total', 0)} 条")
        with col2:
            st.metric("章节数", stats.get("chapters", 0))
        with col3:
            st.metric("专业数", stats.get("specialties", 0))

        # 搜索引擎状态
        col4, col5 = st.columns(2)
        with col4:
            st.metric("含管径(DN)参数", f"{stats.get('with_dn', 0)} 条")
        with col5:
            st.metric("含截面参数", f"{stats.get('with_cable_section', 0)} 条")

        return db
    except Exception as e:
        st.warning(f"定额数据库未初始化: {e}")
        return None


def show_browse(db):
    """按12大册浏览定额（先选册 → 再选章节 → 显示定额列表）"""
    st.subheader("按专业册浏览")

    # 获取12大册列表（含每册的定额数量）
    books = db.get_books()
    if not books:
        st.info("暂无定额数据")
        return

    # 第1级：选择大册（下拉菜单，只有12个选项，比103个章节好找多了）
    book_options = [f"{b['code']} {b['name']} ({b['count']}条)" for b in books]
    selected_book_display = st.selectbox("选择专业册", book_options)

    if not selected_book_display:
        return

    # 从显示文本中提取册号（如 "C10 给排水采暖燃气 (1115条)" → "C10"）
    selected_book_code = selected_book_display.split(" ")[0]

    # 第2级：显示该册下的章节列表
    chapters = db.get_chapters_by_book(selected_book_code)
    if not chapters:
        st.info(f"{selected_book_code} 册下暂无章节数据")
        return

    chapter_options = [f"{c['chapter']} ({c['count']}条)" for c in chapters]
    selected_chapter_display = st.selectbox("选择章节", chapter_options)

    if not selected_chapter_display:
        return

    # 从显示文本中提取章节名（去掉末尾的计数）
    selected_chapter = selected_chapter_display.rsplit(" (", 1)[0]

    # 第3级：显示该章节的定额列表
    conn = _open_quota_conn(db.db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT quota_id, name, unit FROM quotas WHERE chapter = ? ORDER BY quota_id LIMIT 200",
            (selected_chapter,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    if rows:
        st.caption(f"共 {len(rows)} 条（最多显示200条）")
        df = pd.DataFrame(rows)
        df.columns = ["定额编号", "名称", "单位"]
        st.dataframe(df, use_container_width=True, height=400)


def show_search(db):
    """定额搜索功能（支持按册过滤）"""
    st.subheader("定额搜索")

    # 搜索框 + 册号过滤（并排显示）
    col_search, col_filter = st.columns([3, 1])

    with col_search:
        search_keyword = st.text_input(
            "输入关键词搜索定额",
            placeholder="例如：镀锌钢管、电力电缆、防水套管...",
        )

    with col_filter:
        # 获取册号列表作为过滤选项
        from src.specialty_classifier import get_all_books
        all_books = get_all_books()
        filter_options = ["全部"] + [f"{b['code']} {b['name']}" for b in all_books]
        selected_filter = st.selectbox("限定专业", filter_options)

    if search_keyword:
        # 解析选中的册号过滤条件
        book_filter = None
        if selected_filter != "全部":
            book_filter = selected_filter.split(" ")[0]  # "C10 给排水采暖燃气" → "C10"

        results = _ensure_list(db.search_by_keywords(search_keyword, limit=50, book=book_filter))
        if results:
            filter_text = f"（{selected_filter}）" if book_filter else ""
            st.success(f"找到 {len(results)} 条定额{filter_text}")

            rows = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                rows.append({
                    "定额编号": r.get("quota_id", ""),
                    "名称": r.get("name", "")[:60],
                    "单位": r.get("unit", ""),
                    "所属册": r.get("book", ""),
                    "章节": r.get("chapter", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, height=400)
        else:
            st.info("未找到匹配的定额")


def show_import():
    """导入新定额"""
    st.subheader("导入定额Excel")

    st.markdown("""
    **操作步骤：**
    1. 从广联达导出定额Excel文件
    2. 将文件放到 `data/quota_data/` 目录下
    3. 点击下方按钮导入并构建索引

    **文件格式要求：**
    - A列：定额编号（如 C1-1-1）
    - B列：名称+特征参数
    - C列：计量单位
    - D列：工作类型
    """)

    # 显示data/quota_data/下的文件
    quota_dir = config.QUOTA_DATA_DIR
    if quota_dir.exists():
        files = list(quota_dir.glob("*.xlsx")) + list(quota_dir.glob("*.xls"))
        if files:
            st.caption("已有的定额文件：")
            for f in files:
                st.text(f"  {f.name}")
        else:
            st.info(f"目录 {quota_dir} 下没有Excel文件")
    else:
        st.warning(f"目录不存在: {quota_dir}")

    specialty = st.selectbox("定额专业", ["安装", "土建", "市政"])

    if st.button("导入定额并构建索引", type="primary"):
        # 查找对应专业的定额文件
        excel_name = config.QUOTA_EXCEL_FILES.get(specialty)
        if not excel_name:
            st.error(f"未配置 {specialty} 专业的定额文件，请在 config.py 中配置")
            return

        excel_path = quota_dir / excel_name
        if not excel_path.exists():
            st.error(f"定额文件不存在: {excel_path}")
            return

        with st.spinner("正在导入定额（可能需要几分钟）..."):
            try:
                from src.quota_db import QuotaDB
                db = QuotaDB()
                count = db.import_excel(str(excel_path), specialty)
                # 导入后自动补充book字段
                db.upgrade_add_book_field()
                st.success(f"导入完成！共 {count} 条定额")
            except Exception as e:
                st.error(f"导入失败: {e}")
                return

        with st.spinner("正在构建BM25索引..."):
            try:
                from src.bm25_engine import BM25Engine
                bm25 = BM25Engine()
                bm25.build_index()
                st.success("BM25索引构建完成")
            except Exception as e:
                st.error(f"BM25索引构建失败: {e}")

        with st.spinner("正在构建向量索引（首次需要加载模型）..."):
            try:
                from src.vector_engine import VectorEngine
                vec = VectorEngine()
                vec.build_index()
                st.success("向量索引构建完成")
            except Exception as e:
                st.error(f"向量索引构建失败: {e}")

        st.balloons()


def main():
    st.title("定额数据库")
    st.caption(f"省份：{config.CURRENT_PROVINCE}")

    # 标签页切换
    tab1, tab2, tab3 = st.tabs(["数据库概览", "搜索定额", "导入管理"])

    with tab1:
        db = show_db_stats()
        if db:
            show_browse(db)

    with tab2:
        try:
            from src.quota_db import QuotaDB
            db = QuotaDB()
            show_search(db)
        except Exception as e:
            st.warning(f"定额数据库未初始化，请先导入定额数据（{e}）")

    with tab3:
        show_import()


main()
