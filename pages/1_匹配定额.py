"""
匹配定额页面 - 广联达风格界面
流程：上传清单 → 匹配 → 审核修正 → 存入经验库 → 导出Excel

界面设计参考广联达计价软件：
- 左侧侧边栏：分部导航树 + 操作按钮
- 右侧主区域：清单+定额层级表格（AG Grid）
- 点击行显示详情面板，可搜索替换定额
"""

import sys
import time
import uuid
import re
import os
import tempfile
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.text_parser import normalize_bill_text

st.set_page_config(page_title="匹配定额", page_icon="🔍", layout="wide")

# 紧凑专业的CSS样式
st.markdown("""
<style>
    /* 减少页面顶部空白 */
    .block-container { padding-top: 1rem; padding-bottom: 0; }
    /* 侧边栏最小宽度 */
    [data-testid="stSidebar"] { min-width: 260px; }
    /* 详情面板样式 */
    .detail-header {
        font-size: 15px; font-weight: 600;
        color: #1565C0; margin-bottom: 8px;
        border-bottom: 2px solid #1565C0; padding-bottom: 4px;
    }
</style>
""", unsafe_allow_html=True)


# ================================================================
# 状态管理
# ================================================================

def init_session_state():
    """初始化页面状态"""
    defaults = {
        "match_results": None,      # 匹配结果列表
        "bill_items": None,         # 清单项列表
        "uploaded_file_path": None, # 上传文件路径
        "output_file_path": None,   # 导出文件路径
        "matching_done": False,     # 是否完成匹配
        "match_stats": None,        # 匹配统计
        "confirmed_set": set(),     # 已确认正确的索引集合
        "corrected_set": set(),     # 已修正的索引集合
        "editing_idx": None,        # 当前正在换定额的清单索引
        "selected_section": "全部", # 左侧导航选中的分部
        "selected_row_idx": None,   # 表格中选中行对应的清单索引
        "open_dialog_for": None,    # 点击定额行时自动打开弹窗的清单索引
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ================================================================
# 工具函数
# ================================================================

def save_uploaded_file(uploaded_file) -> str:
    """保存上传文件到临时目录"""
    temp_dir = config.OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_temp_files(temp_dir)

    max_mb = int(getattr(config, "UPLOAD_MAX_MB", 30))
    max_bytes = max_mb * 1024 * 1024
    file_size = getattr(uploaded_file, "size", None)
    if isinstance(file_size, int) and file_size > max_bytes:
        raise ValueError(f"上传文件过大：{file_size / 1024 / 1024:.1f}MB，超过限制 {max_mb}MB")

    # 仅保留文件名，阻断路径穿越；同时清洗特殊字符并加随机前缀防覆盖
    raw_name = Path(getattr(uploaded_file, "name", "upload.xlsx")).name
    suffix = Path(raw_name).suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        suffix = ".xlsx"
    stem = Path(raw_name).stem
    safe_stem = re.sub(r'[^A-Za-z0-9._\-\u4e00-\u9fff]+', "_", stem).strip("._")
    if not safe_stem:
        safe_stem = "upload"
    safe_name = f"{safe_stem}_{uuid.uuid4().hex[:8]}{suffix}"

    file_path = temp_dir / safe_name
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=suffix,
            prefix=f"{safe_stem}_tmp_",
            dir=str(temp_dir),
            delete=False,
        ) as f:
            tmp_path = f.name
            f.write(uploaded_file.getbuffer())
        os.replace(tmp_path, file_path)
    finally:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError as e:
                logger.debug(f"上传临时文件清理失败: {tmp_path} ({e})")
    return str(file_path)


def _safe_unlink(path_like, context: str = ""):
    """安全删除临时文件，失败仅记录日志，不影响主流程。"""
    if not path_like:
        return
    path = Path(path_like)
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        suffix = f" ({context})" if context else ""
        logger.debug(f"删除临时文件失败: {path}{suffix} ({e})")


def _cleanup_temp_files(temp_dir: Path,
                        max_keep: int = 300,
                        max_age_hours: int = 24 * 3):
    """
    清理临时目录中的历史文件，避免长期运行导致磁盘膨胀。
    - 超过 max_age_hours 的文件删除
    - 其余文件按修改时间保留最新 max_keep 个
    """
    try:
        files = [p for p in temp_dir.iterdir() if p.is_file()]
    except Exception as e:
        logger.debug(f"扫描临时目录失败，跳过清理: {e}")
        return

    if not files:
        return

    file_entries = []
    for f in files:
        try:
            file_entries.append((f, f.stat().st_mtime))
        except OSError:
            continue
    if not file_entries:
        return

    now = time.time()
    max_age_sec = max_age_hours * 3600
    files_sorted = sorted(file_entries, key=lambda item: item[1], reverse=True)
    survivors = files_sorted[:max_keep]
    survivors_set = {item[0] for item in survivors}

    removed = 0
    for f, mtime in files_sorted:
        should_remove = f not in survivors_set or (now - mtime) > max_age_sec
        if not should_remove:
            continue
        try:
            f.unlink(missing_ok=True)
            removed += 1
        except Exception as e:
            logger.debug(f"清理临时文件失败: {f} ({e})")

    if removed > 0:
        logger.debug(f"临时目录清理完成: 删除 {removed} 个历史文件")


def get_sections(results):
    """从匹配结果中提取分部名称和条目计数

    返回 dict: {"给排水工程": 20, "采暖工程": 15, ...}
    """
    sections = {}
    for r in results:
        section = r.get("bill_item", {}).get("section", "其他") or "其他"
        sections[section] = sections.get(section, 0) + 1
    return sections


def _ensure_list(value):
    return value if isinstance(value, list) else []


def _safe_confidence(value, default: int = 0) -> int:
    try:
        conf = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, conf))


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_stats(stats, results):
    """把统计字段标准化，防止缺字段导致页面渲染报错。"""
    results = _ensure_list(results)
    if not isinstance(stats, dict):
        stats = {}
    total = len(results)
    matched = sum(1 for r in results if _ensure_list((r or {}).get("quotas", [])))
    high_conf = sum(1 for r in results
                    if _safe_confidence((r or {}).get("confidence", 0)) >= config.CONFIDENCE_GREEN)
    mid_conf = sum(1 for r in results
                   if config.CONFIDENCE_YELLOW <= _safe_confidence((r or {}).get("confidence", 0)) < config.CONFIDENCE_GREEN)
    low_conf = sum(1 for r in results
                   if 0 < _safe_confidence((r or {}).get("confidence", 0)) < config.CONFIDENCE_YELLOW)
    return {
        "total": _safe_int(stats.get("total", total), total),
        "matched": _safe_int(stats.get("matched", matched), matched),
        "high_conf": _safe_int(stats.get("high_conf", high_conf), high_conf),
        "mid_conf": _safe_int(stats.get("mid_conf", mid_conf), mid_conf),
        "low_conf": _safe_int(stats.get("low_conf", low_conf), low_conf),
        "exp_hits": _safe_int(stats.get("exp_hits", 0), 0),
        "elapsed": _safe_float(stats.get("elapsed", 0.0), 0.0),
    }


def _resolve_selected_quota(selected_row, quota_list: list[dict]):
    """兼容st.dataframe不同版本的选中行结构，返回选中的quota dict。"""
    if isinstance(selected_row, int):
        return quota_list[selected_row] if 0 <= selected_row < len(quota_list) else None
    if not isinstance(selected_row, dict):
        return None

    qid = str(selected_row.get("定额编号", "")).strip()
    if qid:
        for q in quota_list:
            if str(q.get("quota_id", "")).strip() == qid:
                return q

    idx_val = selected_row.get("_index", selected_row.get("index"))
    try:
        idx = int(idx_val)
    except (TypeError, ValueError):
        return None
    return quota_list[idx] if 0 <= idx < len(quota_list) else None


# ================================================================
# 匹配流程（通过subprocess调用main.py，速度和批处理一样快）
# ================================================================

def run_matching(bill_items, mode, use_experience, progress_bar, status_text):
    """执行匹配，返回 (results, stats)

    核心思路：不在Streamlit进程内做匹配，而是启动一个独立的Python子进程
    运行 main.py，这样匹配速度和命令行批处理完全一样。
    结果通过JSON文件传回。
    """
    import subprocess
    import json

    file_path = st.session_state.uploaded_file_path
    sheet_name = st.session_state.get("_selected_sheet", "")

    # JSON结果文件路径（临时文件）
    temp_dir = config.OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_temp_files(temp_dir)
    json_file = temp_dir / f"_match_result_{uuid.uuid4().hex}.json"
    json_path = str(json_file)

    # 构建命令行（和批处理bat文件调用的是同一个main.py）
    project_dir = str(Path(__file__).parent.parent)
    cmd = [
        sys.executable, str(Path(project_dir) / "main.py"),
        file_path,
        "--mode", mode,
        "--json-output", json_path,
        "--province", st.session_state.get("current_province", config.CURRENT_PROVINCE),
    ]
    if sheet_name:
        cmd.extend(["--sheet", sheet_name])
    if not use_experience:
        cmd.append("--no-experience")

    # 启动子进程执行匹配
    status_text.text("正在匹配（后台运行中，速度和批处理一样）...")
    progress_bar.progress(0.15)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=600,  # 最长10分钟
        )
    except subprocess.TimeoutExpired:
        st.error("匹配超时（超过10分钟），请检查清单数量或搜索引擎状态")
        _safe_unlink(json_file, "匹配超时后清理结果文件")
        return None, None

    if result.returncode != 0:
        st.error("匹配过程出错")
        # 显示错误信息（stderr中有详细日志）
        error_msg = result.stderr or result.stdout or "未知错误"
        st.code(error_msg[-2000:], language="text")  # 只显示最后2000字符
        _safe_unlink(json_file, "匹配失败后清理结果文件")
        return None, None

    # 读取JSON结果
    progress_bar.progress(0.9)
    status_text.text("正在读取匹配结果...")

    json_file = Path(json_path)
    if not json_file.exists():
        st.error(f"匹配结果文件未生成，请检查日志")
        if result.stderr:
            st.code(result.stderr[-1000:], language="text")
        return None, None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        st.error(f"读取匹配结果失败: {e}")
        _safe_unlink(json_file, "结果JSON解析失败后清理文件")
        return None, None
    if not isinstance(data, dict):
        st.error("匹配结果格式异常：JSON根节点不是对象")
        logger.warning(f"匹配结果JSON结构异常: type={type(data).__name__}, path={json_path}")
        _safe_unlink(json_file, "结果JSON结构异常后清理文件")
        return None, None

    progress_bar.progress(1.0)
    status_text.text("匹配完成")
    _safe_unlink(json_file, "匹配完成后清理结果文件")

    results = data.get("results")
    if not isinstance(results, list):
        st.error("匹配结果格式异常：results 不是列表")
        logger.warning(f"匹配结果results异常: type={type(results).__name__}, path={json_path}")
        return None, None
    stats = _normalize_stats(data.get("stats"), results)
    return results, stats


# ================================================================
# 广联达风格层级表格（清单+定额混合显示）
# ================================================================

def build_grid_data(results, section_filter="全部"):
    """构建清单+定额混合的层级表格数据

    类似广联达的分部分项表：
    - 清单行 = 父行（根据置信度上不同背景色）
    - 定额行 = 子行（蓝色背景，缩进显示）
    - 未匹配 = 红色子行
    """
    rows = []
    for idx, r in enumerate(results):
        if not isinstance(r, dict):
            continue
        item = r.get("bill_item", {})
        quotas = [q for q in _ensure_list(r.get("quotas", [])) if isinstance(q, dict)]
        conf = _safe_confidence(r.get("confidence", 0))
        section = item.get("section", "其他") or "其他"

        # 按分部过滤
        if section_filter != "全部" and section != section_filter:
            continue

        # 审核状态
        if idx in st.session_state.corrected_set:
            status_text = "已修正"
        elif idx in st.session_state.confirmed_set:
            status_text = "已确认"
        else:
            status_text = ""

        # 清单行（父行）
        rows.append({
            "data_idx": idx,         # 内部索引，用于关联数据
            "row_type": "bill",      # 行类型标记
            "row_uid": f"bill_{idx}",
            "序号": idx + 1,
            "类型": "清单",
            "编码": item.get("code", ""),
            "名称": item.get("name", ""),
            "特征描述": (item.get("description", "") or "")[:80],
            "单位": item.get("unit", ""),
            "工程量": item.get("quantity", ""),
            "置信度": conf,
            "状态": status_text,
        })

        # 定额子行（可以有多条）
        if quotas:
            for q_idx, q in enumerate(quotas):
                rows.append({
                    "data_idx": idx,
                    "row_type": "quota",
                    "row_uid": f"quota_{idx}_{q_idx}",
                    "序号": "",
                    "类型": "定额",
                    "编码": q.get("quota_id", ""),
                    "名称": q.get("name", ""),
                    "特征描述": q.get("reason", "") or "",
                    "单位": q.get("unit", ""),
                    "工程量": "",
                    "置信度": "",
                    "状态": "",
                })
        else:
            rows.append({
                "data_idx": idx,
                "row_type": "no_match",
                "row_uid": f"no_match_{idx}",
                "序号": "",
                "类型": "未匹配",
                "编码": "",
                "名称": r.get("no_match_reason", "无匹配结果"),
                "特征描述": "",
                "单位": "",
                "工程量": "",
                "置信度": "",
                "状态": "",
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def show_grid_table(results, section_filter="全部"):
    """用AG Grid展示广联达风格的层级表格

    清单行和定额行交替显示，用颜色和缩进区分层级关系
    """
    try:
        from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode
    except ImportError:
        st.error("需要安装 streamlit-aggrid 组件：pip install streamlit-aggrid")
        return

    df = build_grid_data(results, section_filter)
    if df.empty:
        st.info("当前分部下没有清单项")
        return

    # 表格显示的列（隐藏内部字段 data_idx / row_type / row_uid）
    display_cols = ["row_uid", "序号", "类型", "编码", "名称", "特征描述", "单位", "工程量", "置信度", "状态"]
    display_df = df[display_cols].copy()

    # 置信度列：数字转百分比文字（只处理清单行有数字的情况）
    display_df["置信度"] = display_df["置信度"].apply(
        lambda x: f"{int(x)}%" if isinstance(x, (int, float)) and x > 0 else ""
    )

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_column("row_uid", hide=True)

    # 列宽配置（参考广联达的表格比例）
    gb.configure_column("序号", width=55, pinned="left")
    gb.configure_column("类型", width=62)
    gb.configure_column("编码", width=125)
    gb.configure_column("名称", width=280, wrapText=True, autoHeight=True)
    gb.configure_column("特征描述", width=200, wrapText=True, autoHeight=True)
    gb.configure_column("单位", width=55)
    gb.configure_column("工程量", width=75)
    gb.configure_column("置信度", width=68)
    gb.configure_column("状态", width=68)

    # 单行选择（点击行高亮，触发详情面板）
    gb.configure_selection("single", use_checkbox=False)

    # 行样式JS：根据类型和置信度设置背景色
    # - 定额行：浅蓝色背景
    # - 未匹配行：浅红色背景
    # - 清单行：根据置信度 绿/黄/橙
    row_style_js = JsCode("""
    function(params) {
        var rowType = params.data['类型'];
        if (rowType === '定额') {
            return {
                'background-color': '#E3F2FD',
                'color': '#1565C0',
                'font-size': '13px',
                'padding-left': '20px'
            };
        }
        if (rowType === '未匹配') {
            return {
                'background-color': '#FFEBEE',
                'color': '#C62828',
                'font-size': '13px'
            };
        }
        // 清单行 - 解析置信度数字
        var confText = params.data['置信度'] || '';
        var conf = parseInt(confText) || 0;
        if (conf >= 85) {
            return {'background-color': '#E8F5E9', 'font-weight': '500'};
        } else if (conf >= 60) {
            return {'background-color': '#FFF8E1', 'font-weight': '500'};
        } else if (conf > 0) {
            return {'background-color': '#FFF3E0', 'font-weight': '500'};
        }
        return {'font-weight': '500'};
    }
    """)

    grid_options = gb.build()
    grid_options["getRowStyle"] = row_style_js
    grid_options["rowHeight"] = 36
    grid_options["headerHeight"] = 38

    # 计算表格高度（最小400，最大800，留更大浏览空间）
    grid_height = min(max(len(display_df) * 37 + 50, 400), 800)

    # 渲染AG Grid
    response = AgGrid(
        display_df,
        gridOptions=grid_options,
        height=grid_height,
        theme="alpine",
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=False,
        key=f"grid_{section_filter}",
    )

    # 处理行选择 - 定额行→打开换定额弹窗，清单行→显示详情面板
    selected_rows = response.selected_rows
    if selected_rows is not None:
        # 兼容不同版本的 streamlit-aggrid（返回 DataFrame 或 list）
        row_data = None
        if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
            row_data = selected_rows.iloc[0].to_dict()
        elif (
            isinstance(selected_rows, list)
            and len(selected_rows) > 0
            and isinstance(selected_rows[0], dict)
        ):
            row_data = selected_rows[0]

        if row_data:
            row_uid = row_data.get("row_uid", "")
            if row_uid:
                match_rows = df[df["row_uid"] == row_uid]
                if not match_rows.empty:
                    orig_row = match_rows.iloc[0]
                    bill_idx = int(orig_row["data_idx"])
                    row_type_val = orig_row.get("类型", "")

                    if row_type_val in ("定额", "未匹配"):
                        # 点击定额行或未匹配行 → 直接打开换定额弹窗
                        st.session_state.open_dialog_for = bill_idx
                    else:
                        # 点击清单行 → 显示详情面板
                        st.session_state.selected_row_idx = bill_idx


# ================================================================
# 详情面板（点击行后显示）
# ================================================================

def show_detail_panel(results, idx):
    """显示选中清单项的详细信息和操作按钮

    包含：清单信息、当前定额、确认/换定额操作
    """
    if idx < 0 or idx >= len(results):
        return

    r = results[idx]
    item = r.get("bill_item", {})
    quotas = [q for q in _ensure_list(r.get("quotas", [])) if isinstance(q, dict)]
    conf = _safe_confidence(r.get("confidence", 0))

    st.markdown("---")

    # 两列布局：左边清单信息，右边定额信息
    left_col, right_col = st.columns(2)

    with left_col:
        st.markdown(f'<div class="detail-header">清单信息（第{idx + 1}条）</div>',
                     unsafe_allow_html=True)
        st.markdown(f"**名称**：{item.get('name', '')}")
        if item.get("code"):
            st.markdown(f"**编码**：{item.get('code', '')}")
        desc = item.get("description", "") or ""
        if desc:
            st.markdown(f"**特征描述**：{desc}")
        st.markdown(f"**单位**：{item.get('unit', '')}　**工程量**：{item.get('quantity', '')}")
        if item.get("section"):
            st.caption(f"分部：{item.get('section', '')}")

    with right_col:
        st.markdown('<div class="detail-header">当前匹配定额</div>',
                     unsafe_allow_html=True)
        if quotas:
            # 置信度颜色
            if conf >= config.CONFIDENCE_GREEN:
                conf_color = "green"
            elif conf >= config.CONFIDENCE_YELLOW:
                conf_color = "orange"
            else:
                conf_color = "red"

            st.markdown(f"**置信度**：:{conf_color}[{conf}%]")

            # 显示所有定额（一条清单可能挂多条定额）
            for i, q in enumerate(quotas):
                prefix = f"定额{i + 1}" if len(quotas) > 1 else "定额"
                st.markdown(f"**{prefix}**：{q.get('quota_id', '')} | {q.get('name', '')}"
                             f"（{q.get('unit', '')}）")
                if q.get("reason"):
                    st.caption(f"  说明：{q['reason']}")
        else:
            st.error(f"未匹配：{r.get('no_match_reason', '无候选')}")

    # 操作按钮行
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        if idx in st.session_state.confirmed_set:
            st.success("已确认正确")
        elif idx in st.session_state.corrected_set:
            st.info("已修正定额")
        elif quotas:
            if st.button("确认正确", key=f"confirm_{idx}", use_container_width=True):
                st.session_state.confirmed_set.add(idx)
                st.session_state.corrected_set.discard(idx)
                st.rerun()

    with btn_col2:
        label = "重新换定额" if idx in st.session_state.corrected_set else "换定额"
        if st.button(label, key=f"swap_{idx}", use_container_width=True):
            show_quota_dialog(idx)

    with btn_col3:
        st.empty()


@st.dialog("查询定额", width="large")
def show_quota_dialog(idx):
    """广联达风格的定额查询弹窗

    布局：
    - 顶部：搜索框（多关键词AND搜索，搜索范围跟随左侧章节选择）
    - 左侧：可滚动的章节目录树（专业→章节）
    - 右侧：定额列表（可选中行）+ 插入/替换按钮
    - 底部：当前已有的定额列表（可删除）
    """
    from src.quota_db import QuotaDB
    db = QuotaDB()

    results = _ensure_list(st.session_state.match_results)
    if idx < 0 or idx >= len(results):
        st.warning("目标清单索引无效，已忽略本次操作。")
        return
    r = results[idx]
    item = r.get("bill_item", {})
    current_quotas = [q for q in _ensure_list(r.get("quotas", [])) if isinstance(q, dict)]
    if not isinstance(results[idx].get("quotas"), list):
        results[idx]["quotas"] = current_quotas

    # 显示当前清单信息
    st.caption(f"清单第{idx+1}条：{item.get('name', '')}　|　当前 {len(current_quotas)} 条定额")

    # 注入JS：让弹窗可拖动（拖标题栏）+ 可缩放（拖右下角）
    import streamlit.components.v1 as components
    components.html("""
    <script>
    (function() {
        var doc = window.parent.document;
        var overlay = doc.querySelector('[data-testid="stDialog"]');
        if (!overlay) return;
        var dialog = overlay.querySelector(':scope > div > div > div');
        if (!dialog) return;

        // 防止重复绑定
        if (dialog.dataset.draggable) return;
        dialog.dataset.draggable = 'true';

        // 样式：可缩放、有最小尺寸
        dialog.style.resize = 'both';
        dialog.style.overflow = 'auto';
        dialog.style.minWidth = '700px';
        dialog.style.minHeight = '400px';
        dialog.style.maxHeight = '90vh';
        dialog.style.position = 'fixed';
        dialog.style.cursor = 'default';

        // 顶部拖动条样式
        var header = dialog.querySelector(':scope > div:first-child');
        if (header) {
            header.style.cursor = 'move';
            header.title = '拖动此处移动窗口';
        }

        // 拖动逻辑
        var isDragging = false, startX, startY, origLeft, origTop;

        dialog.addEventListener('mousedown', function(e) {
            // 只在顶部50px区域可拖动
            var rect = dialog.getBoundingClientRect();
            if (e.clientY - rect.top > 50) return;
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;

            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
            origLeft = rect.left;
            origTop = rect.top;
            e.preventDefault();
        });

        doc.addEventListener('mousemove', function(e) {
            if (!isDragging) return;
            dialog.style.left = (origLeft + e.clientX - startX) + 'px';
            dialog.style.top = (origTop + e.clientY - startY) + 'px';
            dialog.style.transform = 'none';
            dialog.style.margin = '0';
        });

        doc.addEventListener('mouseup', function() { isDragging = false; });
    })();
    </script>
    """, height=0)

    # ---- 搜索框放在最上面，不受左右列影响 ----
    search_text = st.text_input(
        "搜索定额（多个关键词用空格分隔，如：室外 镀锌钢管）",
        placeholder="输入关键词搜索全库，或留空浏览左侧章节...",
        key="dialog_search_input",
    )

    # ---- 左右两栏 ----
    left_col, right_col = st.columns([1, 3])

    with left_col:
        # 获取专业和章节数据
        specialties = db.get_specialties()
        if not specialties:
            st.warning("定额库为空")
            return

        # 专业选择（目前只有安装，以后会有土建、市政等）
        if len(specialties) > 1:
            selected_specialty = st.selectbox(
                "专业", specialties,
                format_func=lambda s: f"C {s}" if s == "安装" else s,
            )
        else:
            selected_specialty = specialties[0]
            st.caption(f"专业：{selected_specialty}")

        # 章节列表放在可滚动容器中（固定高度，内容超出时可滚动）
        chapters = db.get_chapters_by_specialty(selected_specialty)
        if chapters:
            with st.container(height=300):
                selected_chapter = st.radio(
                    "章节", chapters,
                    label_visibility="collapsed",
                )
        else:
            selected_chapter = None

    with right_col:
        # ---- 加载定额列表 ----
        quota_list = []
        if search_text.strip():
            # 搜索模式：全库搜索（不限制章节，找到所有匹配结果）
            quota_list = db.search_by_keywords(
                search_text,
                chapter=None,
                limit=50,
            )
            if not quota_list:
                st.info("未找到结果，试试换个关键词")
        elif selected_chapter:
            # 浏览模式：显示选中章节下的所有定额
            quota_list = db.get_quotas_by_chapter(selected_chapter, limit=200)

        # ---- 定额列表（可滚动、可选中行） ----
        if quota_list:
            display_data = [{
                "定额编号": q["quota_id"],
                "名称": q["name"],
                "单位": q.get("unit", ""),
            } for q in quota_list]
            df = pd.DataFrame(display_data)
            st.caption(f"共 {len(quota_list)} 条")

            event = st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                height=280,
                on_select="rerun",
                selection_mode="single-row",
                key="quota_select_df",
            )

            # 获取选中行
            selected_rows = event.selection.rows if event.selection else []

            if selected_rows:
                chosen = _resolve_selected_quota(selected_rows[0], quota_list)
                if not chosen:
                    st.warning("选中行无法解析，请重新选择。")
                    return

                st.success(f"已选：{chosen['quota_id']} | {chosen['name']} | {chosen.get('unit', '')}")

                # 插入 / 替换 按钮
                btn_left, btn_right, _ = st.columns([1, 1, 2])
                with btn_left:
                    if st.button("插入（追加）", type="primary", use_container_width=True):
                        new_quota = {
                            "quota_id": chosen["quota_id"],
                            "name": chosen["name"],
                            "unit": chosen.get("unit", ""),
                            "reason": "用户手动插入",
                        }
                        qlist = _ensure_list(results[idx].get("quotas", []))
                        qlist.append(new_quota)
                        results[idx]["quotas"] = qlist
                        results[idx]["confidence"] = max(_safe_confidence(results[idx].get("confidence", 0)), 90)
                        results[idx]["match_source"] = "user_correction"
                        st.session_state.corrected_set.add(idx)
                        st.session_state.confirmed_set.discard(idx)
                        st.rerun()
                with btn_right:
                    if st.button("替换（第1条）", use_container_width=True):
                        new_quota = {
                            "quota_id": chosen["quota_id"],
                            "name": chosen["name"],
                            "unit": chosen.get("unit", ""),
                            "reason": "用户手动替换",
                        }
                        qlist = _ensure_list(results[idx].get("quotas", []))
                        if qlist:
                            qlist[0] = new_quota
                        else:
                            qlist.append(new_quota)
                        results[idx]["quotas"] = qlist
                        results[idx]["confidence"] = 95
                        results[idx]["match_source"] = "user_correction"
                        st.session_state.corrected_set.add(idx)
                        st.session_state.confirmed_set.discard(idx)
                        st.rerun()
            else:
                st.caption("点击上方列表中的一行来选中定额")

    # ---- 底部：当前清单已挂的定额 ----
    if current_quotas:
        st.divider()
        st.markdown(f"**当前定额（{len(current_quotas)}条）：**")
        for i, q in enumerate(current_quotas):
            col_info, col_del = st.columns([5, 1])
            with col_info:
                st.text(f"  {i+1}. {q.get('quota_id', '')} | {q.get('name', '')}")
            with col_del:
                if st.button("删除", key=f"del_q_{idx}_{i}"):
                    qlist = _ensure_list(results[idx].get("quotas", []))
                    if 0 <= i < len(qlist):
                        qlist.pop(i)
                    results[idx]["quotas"] = qlist
                    st.session_state.corrected_set.add(idx)
                    st.rerun()


# ================================================================
# 经验库存储（只存用户确认/修正的结果）
# ================================================================

def save_to_experience_db():
    """将已确认和已修正的结果存入经验库

    只有用户审核过的条目才会进入经验库（纠偏机制）
    """
    results = _ensure_list(st.session_state.match_results)
    to_save = st.session_state.confirmed_set | st.session_state.corrected_set

    if not to_save:
        st.warning("没有已审核的条目")
        return 0

    try:
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB()
    except Exception as e:
        st.error(f"经验库加载失败: {e}")
        return 0

    # 尝试加载通用知识库（用于同步学习，失败不影响经验库保存）
    universal_kb = None
    try:
        from src.universal_kb import UniversalKB
        universal_kb = UniversalKB()
    except Exception as e:
        logger.warning(f"通用知识库加载失败，跳过同步学习: {e}")

    saved = 0
    failed = 0
    valid_indexes = sorted(i for i in to_save if isinstance(i, int) and i >= 0)
    for idx in valid_indexes:
        if idx >= len(results):
            continue
        r = results[idx]
        if not isinstance(r, dict):
            failed += 1
            continue
        quotas = [q for q in _ensure_list(r.get("quotas", [])) if isinstance(q, dict)]
        if not quotas:
            continue

        item = r.get("bill_item", {})
        bill_text = normalize_bill_text(item.get('name', ''), item.get('description', ''))
        if not bill_text:
            continue

        # 从定额列表中收集编号和名称
        quota_ids = [str(q.get("quota_id", "")).strip() for q in quotas if str(q.get("quota_id", "")).strip()]
        quota_names = [str(q.get("name", "")).strip() for q in quotas if str(q.get("quota_id", "")).strip()]
        if not quota_ids:
            continue

        source = "user_correction" if idx in st.session_state.corrected_set else "user_confirmed"
        base_conf = _safe_confidence(r.get("confidence", 80), default=80)
        if source == "user_confirmed":
            # 用户显式确认应满足经验直通门槛，避免“确认了但下次仍命不中”
            save_confidence = max(base_conf, int(config.EXPERIENCE_DIRECT_THRESHOLD))
        else:
            # 用户修正是高信任反馈
            save_confidence = max(base_conf, 95)

        try:
            record_id = exp_db.add_experience(
                bill_text=bill_text, quota_ids=quota_ids, quota_names=quota_names,
                bill_name=item.get("name"), bill_code=item.get("code"),
                bill_unit=item.get("unit"), source=source,
                confidence=save_confidence,
                province=st.session_state.get("current_province", config.CURRENT_PROVINCE),
            )
            if record_id <= 0:
                failed += 1
                continue
            saved += 1

            # 同步更新通用知识库（用定额名称，不用编号，全国通用）
            if universal_kb and quota_names:
                try:
                    universal_kb.learn_from_correction(
                        bill_text=bill_text,
                        quota_names=quota_names,
                    )
                except Exception as e:
                    logger.warning(
                        f"通用知识库同步失败（不影响经验库保存）: idx={idx}, "
                        f"bill='{item.get('name', '')[:40]}', error={e}"
                    )

        except Exception as e:
            logger.warning(
                f"经验保存失败: idx={idx}, bill='{item.get('name', '')[:40]}', error={e}"
            )
            failed += 1

    if failed > 0:
        st.warning(f"有 {failed} 条经验保存失败，请查看日志后重试。")

    return saved


# ================================================================
# 导出Excel（广联达格式）
# ================================================================

def export_excel():
    """用当前结果生成广联达格式Excel"""
    from src.output_writer import OutputWriter
    writer = OutputWriter()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = str(config.OUTPUT_DIR / f"匹配结果_{timestamp}_{uuid.uuid4().hex[:6]}.xlsx")
    writer.write_results(
        st.session_state.match_results, output_path,
        original_file=st.session_state.uploaded_file_path,
    )
    st.session_state.output_file_path = output_path
    return output_path


# ================================================================
# 主页面
# ================================================================

def main():
    init_session_state()

    # ========================================================
    # 未匹配状态：显示上传界面
    # ========================================================
    if not st.session_state.matching_done:
        st.title("匹配定额")
        st.caption("上传工程量清单Excel，自动从定额库中匹配对应定额")

        # 侧边栏：匹配设置
        with st.sidebar:
            st.subheader("匹配设置")
            mode = st.radio(
                "匹配模式",
                ["search", "full"],
                format_func=lambda x: "纯搜索（免费）" if x == "search" else "AI精选（需API）",
            )
            use_exp = st.checkbox("使用经验库", value=True)
            st.divider()

            # 省份选择（列出所有已导入的省份定额库）
            available_provinces = config.list_db_provinces()
            if available_provinces:
                # 确定默认选中项
                default_prov = st.session_state.get(
                    "current_province", config.CURRENT_PROVINCE)
                default_idx = 0
                if default_prov in available_provinces:
                    default_idx = available_provinces.index(default_prov)
                selected_province = st.selectbox(
                    "省份/定额版本",
                    available_provinces,
                    index=default_idx,
                    key="province_selector",
                )
                st.session_state["current_province"] = selected_province
            else:
                st.warning("未找到省份数据，请先导入定额")
                st.session_state["current_province"] = config.CURRENT_PROVINCE

        # 上传文件
        uploaded = st.file_uploader("上传清单Excel", type=["xlsx", "xls"])

        if uploaded:
            try:
                file_path = save_uploaded_file(uploaded)
            except ValueError as e:
                st.error(str(e))
                st.stop()
            st.session_state.uploaded_file_path = file_path

            # 第一步：检测Sheet并让用户选择
            from src.bill_reader import BillReader
            reader = BillReader()

            # 获取所有Sheet信息（缓存到session_state避免重复读取）
            if "sheet_info" not in st.session_state or st.session_state.get("_last_file") != file_path:
                st.session_state.sheet_info = reader.get_sheet_info(file_path)
                st.session_state._last_file = file_path
                st.session_state.bill_items = None  # 换文件了，清空旧数据

            sheet_info = st.session_state.sheet_info

            # 构建选择框的选项：标记哪些是分部分项表
            sheet_options = []
            default_idx = 0
            for i, info in enumerate(sheet_info):
                label = info["name"]
                if info["is_bill"]:
                    label += "（推荐 - 检测到分部分项表头）"
                    if default_idx == 0:
                        default_idx = i  # 默认选第一个推荐的
                sheet_options.append(label)

            selected_idx = st.selectbox(
                "选择工作表（Sheet）",
                range(len(sheet_options)),
                index=default_idx,
                format_func=lambda i: sheet_options[i],
                help="只有包含序号、名称、项目特征、单位、工程量等表头的Sheet才是分部分项工程量表",
            )
            selected_sheet = sheet_info[selected_idx]["name"]

            # 第二步：读取选中Sheet的清单
            if st.session_state.bill_items is None or st.session_state.get("_selected_sheet") != selected_sheet:
                with st.spinner(f"读取 [{selected_sheet}] 中..."):
                    try:
                        st.session_state.bill_items = reader.read_excel(file_path, sheet_name=selected_sheet)
                        st.session_state._selected_sheet = selected_sheet
                    except Exception as e:
                        st.error(f"读取失败: {e}")
                        return

            items = st.session_state.bill_items
            if not items:
                st.warning(f"Sheet [{selected_sheet}] 中未读取到清单项目，请换一个Sheet试试")
                return

            st.success(f"从 [{selected_sheet}] 读取到 {len(items)} 条清单项目")

            # 开始匹配按钮
            if st.button("开始匹配", type="primary", use_container_width=True):
                # 重置所有状态
                st.session_state.matching_done = False
                st.session_state.match_results = None
                st.session_state.confirmed_set = set()
                st.session_state.corrected_set = set()
                st.session_state.editing_idx = None
                st.session_state.output_file_path = None
                st.session_state.selected_row_idx = None

                bar = st.progress(0)
                txt = st.empty()

                try:
                    results, stats = run_matching(items, mode, use_exp, bar, txt)
                    if results is not None:
                        st.session_state.match_results = results
                        st.session_state.match_stats = _normalize_stats(stats, results)
                        st.session_state.matching_done = True
                        st.rerun()  # 切换到结果界面
                except Exception as e:
                    st.error(f"匹配出错: {e}")
                    import traceback
                    st.code(traceback.format_exc())
        return

    # ========================================================
    # 已匹配状态：广联达风格结果界面
    # ========================================================
    results = _ensure_list(st.session_state.match_results)
    stats = _normalize_stats(st.session_state.match_stats, results)

    # ---- 侧边栏：分部导航 + 操作 ----
    with st.sidebar:
        # 当前省份（只读显示，匹配完成后不允许切换）
        st.caption(f"当前省份：{st.session_state.get('current_province', config.CURRENT_PROVINCE)}")
        st.divider()

        # 分部导航
        st.subheader("分部导航")
        sections = get_sections(results)
        total_items = sum(sections.values())

        # 构建导航选项
        section_keys = ["全部"] + list(sections.keys())

        def format_section(s):
            """格式化分部名称，附带条目数"""
            if s == "全部":
                return f"全部（{total_items}条）"
            return f"{s}（{sections.get(s, 0)}条）"

        # 当前选中分部的索引
        current_idx = 0
        if st.session_state.selected_section in section_keys:
            current_idx = section_keys.index(st.session_state.selected_section)

        chosen_section = st.radio(
            "选择分部查看",
            section_keys,
            index=current_idx,
            format_func=format_section,
            label_visibility="collapsed",
        )
        st.session_state.selected_section = chosen_section

        st.divider()

        # 审核进度条
        reviewed = len(st.session_state.confirmed_set) + len(st.session_state.corrected_set)
        st.caption(f"审核进度：{reviewed} / {total_items}")
        st.progress(reviewed / max(total_items, 1))

        st.divider()

        # 批量操作
        st.subheader("批量操作")
        if st.button("一键确认所有绿色", use_container_width=True,
                      help="确认所有置信度≥85%的匹配结果"):
            for i, r in enumerate(results):
                if _safe_confidence(r.get("confidence", 0)) >= config.CONFIDENCE_GREEN and _ensure_list(r.get("quotas", [])):
                    st.session_state.confirmed_set.add(i)
            st.rerun()

        if st.button("确认所有已匹配", use_container_width=True,
                      help="确认所有有匹配结果的条目"):
            for i, r in enumerate(results):
                if _ensure_list(r.get("quotas", [])):
                    st.session_state.confirmed_set.add(i)
            st.rerun()

        if st.button("清除所有确认", use_container_width=True):
            st.session_state.confirmed_set.clear()
            st.session_state.corrected_set.clear()
            st.rerun()

        st.divider()

        # 保存和导出
        st.subheader("保存导出")
        n_reviewed = len(st.session_state.confirmed_set) + len(st.session_state.corrected_set)

        if st.button(f"存入经验库（{n_reviewed}条已审核）",
                      use_container_width=True, disabled=(n_reviewed == 0)):
            saved = save_to_experience_db()
            if saved:
                st.success(f"已存入 {saved} 条")

        if st.button("导出Excel（广联达格式）", type="primary", use_container_width=True):
            with st.spinner("生成中..."):
                path = export_excel()
            st.success(f"已生成: {Path(path).name}")

        # 下载按钮
        output_path = st.session_state.output_file_path
        if output_path and Path(output_path).exists():
            with open(output_path, "rb") as f:
                st.download_button(
                    "下载Excel文件",
                    f.read(),
                    file_name=Path(output_path).name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        st.divider()

        # 重新上传
        if st.button("重新上传清单", use_container_width=True):
            for key in ["match_results", "bill_items", "matching_done", "match_stats",
                        "confirmed_set", "corrected_set", "editing_idx",
                        "selected_row_idx", "output_file_path"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    # ---- 主内容区 ----

    # 顶部统计栏
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("清单总数", stats["total"])
    pct = stats["matched"] * 100 // max(stats["total"], 1)
    c2.metric("已匹配", f"{stats['matched']}（{pct}%）")
    c3.metric("高置信度", stats["high_conf"])
    c4.metric("需审核", stats["mid_conf"] + stats["low_conf"])
    c5.metric("经验库命中", stats["exp_hits"])

    # 耗时提示
    if stats.get("elapsed"):
        st.caption(f"匹配耗时 {stats['elapsed']:.1f} 秒")

    # 层级表格（广联达风格：清单+定额交替显示）
    show_grid_table(results, st.session_state.selected_section)

    # 点定额行 → 直接弹出换定额弹窗
    if st.session_state.open_dialog_for is not None:
        dialog_idx = st.session_state.open_dialog_for
        st.session_state.open_dialog_for = None  # 重置，防止重复打开
        show_quota_dialog(dialog_idx)

    # 点清单行 → 显示详情面板
    if st.session_state.selected_row_idx is not None:
        show_detail_panel(results, st.session_state.selected_row_idx)
    else:
        st.caption("点击定额行可直接换定额，点击清单行可查看详情")


main()
