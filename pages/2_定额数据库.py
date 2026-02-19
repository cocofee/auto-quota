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


def show_db_stats(province):
    """展示定额数据库统计信息"""
    try:
        from src.quota_db import QuotaDB
        db = QuotaDB(province)
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


def show_import(province):
    """导入新定额（支持增量导入：自动识别已导入/未导入文件）"""
    from src.quota_db import QuotaDB, detect_specialty_from_excel
    import datetime

    st.subheader("导入定额Excel")

    st.markdown("""
    **操作步骤：**
    1. 从广联达导出定额Excel文件
    2. 将文件放到对应省份的 `data/quota_data/{省份}/` 目录下
    3. 下方会自动显示哪些文件已导入、哪些是新增的
    4. 点击按钮导入新增文件
    """)

    # 扫描当前省份的定额Excel目录
    quota_dir = config.get_quota_data_dir(province)
    if not quota_dir.exists():
        st.warning(f"定额目录不存在: {quota_dir}")
        st.info("请创建该目录并放入广联达导出的定额Excel文件")
        return

    xlsx_files = sorted(quota_dir.glob("*.xlsx"))
    if not xlsx_files:
        st.info(f"目录下没有xlsx文件: {quota_dir}")
        return

    # 获取导入历史，区分已导入/新增/已修改
    db = QuotaDB(province=province)
    history = db.get_import_history()
    stats = db.get_stats()

    # 旧库升级检测：有定额数据但没有导入历史 → 提示先全量重导一次
    if not history and stats.get("total", 0) > 0:
        st.warning("检测到旧数据库（无导入历史记录），请先点击「全量重导」建立导入记录，之后才能使用增量导入。")
        if st.button("全量重导（建立导入记录）", type="primary"):
            _do_import(db, province, xlsx_files, full_mode=True)
        return

    imported_map = {
        h["file_name"]: h for h in history
    }

    # 分类文件
    imported_files = []   # 已导入（跳过）
    new_files = []        # 新增（待导入）
    modified_files = []   # 已修改（需重新导入）

    for f in xlsx_files:
        stat = f.stat()
        prev = imported_map.get(f.name)
        if prev is None:
            new_files.append(f)
        elif prev["file_size"] == stat.st_size and abs(prev["file_mtime"] - stat.st_mtime) < 1:
            imported_files.append((f, prev))
        else:
            modified_files.append((f, prev))

    # 显示文件状态概览
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("已导入", f"{len(imported_files)} 个")
    with col2:
        st.metric("新增待导入", f"{len(new_files)} 个")
    with col3:
        st.metric("已修改", f"{len(modified_files)} 个")

    # 已导入的文件列表（折叠显示）
    if imported_files:
        with st.expander(f"已导入的文件（{len(imported_files)} 个）", expanded=False):
            rows = []
            for f, info in imported_files:
                imported_time = datetime.datetime.fromtimestamp(info["imported_at"])
                rows.append({
                    "文件名": f.name,
                    "专业": info.get("specialty", ""),
                    "定额条数": info.get("quota_count", 0),
                    "导入时间": imported_time.strftime("%Y-%m-%d %H:%M"),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 新增文件列表
    if new_files:
        st.markdown(f"**新增文件（{len(new_files)} 个）：**")
        for f in new_files:
            specialty = detect_specialty_from_excel(str(f))
            size_mb = f.stat().st_size / 1024 / 1024
            st.markdown(f"- {f.name} — 专业: `{specialty}`, 大小: {size_mb:.1f}MB")

    # 已修改文件列表
    if modified_files:
        st.markdown(f"**已修改文件（{len(modified_files)} 个，需重新导入）：**")
        for f, prev in modified_files:
            specialty = detect_specialty_from_excel(str(f))
            st.markdown(f"- {f.name} — 专业: `{specialty}`（文件已更新）")

    # 需要导入的文件 = 新增 + 已修改
    files_to_import = new_files + [f for f, _ in modified_files]

    st.divider()

    # 导入按钮区域
    col_inc, col_full = st.columns(2)

    with col_inc:
        if files_to_import:
            if st.button(f"导入新增文件（{len(files_to_import)} 个）", type="primary"):
                _do_import(db, province, files_to_import, full_mode=False)
        else:
            st.success("所有文件已导入，无需更新")

    with col_full:
        if st.button("全量重导（所有文件）"):
            _do_import(db, province, xlsx_files, full_mode=True)


def _do_import(db, province, files_to_import, full_mode=False):
    """执行导入操作（增量或全量）

    参数:
        db: QuotaDB实例
        province: 省份名称
        files_to_import: 要导入的文件列表
        full_mode: True=全量重导（清除旧数据），False=增量追加
    """
    from src.quota_db import detect_specialty_from_excel

    if full_mode:
        db.clear_import_history()

    mode_label = "全量重导" if full_mode else "增量导入"
    progress = st.progress(0, text=f"正在{mode_label}...")

    # 第1步：导入定额到数据库
    imported = {}  # {specialty: count}
    cleared_specialties = set()
    total_files = len(files_to_import)

    for i, xlsx_file in enumerate(files_to_import):
        specialty = detect_specialty_from_excel(str(xlsx_file))
        progress.progress(
            (i + 1) / (total_files + 2),
            text=f"导入 {xlsx_file.name}（{i+1}/{total_files}）...",
        )

        try:
            if full_mode:
                # 全量模式：同一specialty第一个文件清旧数据
                is_first = specialty not in cleared_specialties
                cleared_specialties.add(specialty)
                count = db.import_excel(str(xlsx_file), specialty=specialty,
                                        clear_existing=is_first)
            else:
                # 增量模式：追加，不清旧数据
                count = db.import_excel(str(xlsx_file), specialty=specialty,
                                        clear_existing=False)

            imported[specialty] = imported.get(specialty, 0) + count
            # 记录导入历史
            db.record_import(str(xlsx_file), specialty, count)
        except Exception as e:
            st.error(f"导入失败 {xlsx_file.name}: {e}")
            return

    total_count = sum(imported.values())
    st.success(f"导入完成！本次导入 {total_count} 条定额")
    for sp, cnt in imported.items():
        st.caption(f"  {sp}: {cnt} 条")

    # 第2步：构建BM25索引
    progress.progress(
        (total_files + 1) / (total_files + 2),
        text="构建BM25搜索索引...",
    )
    try:
        from src.bm25_engine import BM25Engine
        bm25 = BM25Engine(province=province)
        bm25.build_index()
        st.success("BM25索引构建完成")
    except Exception as e:
        st.error(f"BM25索引构建失败: {e}")

    # 第3步：构建向量索引
    progress.progress(1.0, text="构建向量索引...")
    try:
        from src.vector_engine import VectorEngine
        vec = VectorEngine(province=province)
        vec.build_index()
        st.success("向量索引构建完成")
    except Exception as e:
        st.error(f"向量索引构建失败: {e}")

    progress.empty()
    st.balloons()


def main():
    st.title("定额数据库")

    # 省份选择（列出所有已导入的省份定额库）
    available_provinces = config.list_db_provinces()
    if available_provinces:
        default_prov = st.session_state.get("current_province", config.CURRENT_PROVINCE)
        default_idx = 0
        if default_prov in available_provinces:
            default_idx = available_provinces.index(default_prov)
        selected_province = st.selectbox(
            "省份/定额版本",
            available_provinces,
            index=default_idx,
            key="db_province_selector",
        )
        st.session_state["current_province"] = selected_province
    else:
        st.warning("未找到省份数据，请先导入定额")
        selected_province = config.CURRENT_PROVINCE

    # 标签页切换
    tab1, tab2, tab3 = st.tabs(["数据库概览", "搜索定额", "导入管理"])

    with tab1:
        db = show_db_stats(selected_province)
        if db:
            show_browse(db)

    with tab2:
        try:
            from src.quota_db import QuotaDB
            db = QuotaDB(selected_province)
            show_search(db)
        except Exception as e:
            st.warning(f"定额数据库未初始化，请先导入定额数据（{e}）")

    with tab3:
        show_import(selected_province)


main()
