# -*- coding: utf-8 -*-
"""
审核纠正器 — 错误纠正逻辑

从 tools/jarvis_auto_review.py 拆分而来。
每个 _correct_* 函数处理一种错误类型，通过调度表统一入口调用。
DB 搜索工具在 src/quota_search.py 中，本模块直接导入使用。
"""

import re

from loguru import logger

from src.quota_search import search_quota_db
from src.review_checkers import (
    MATERIAL_MAP, CONNECTION_MAP, CORRECTION_STRATEGIES, ELECTRIC_PAIR_RULES,
    extract_description_lines, extract_connection, extract_material,
)


# ============================================================
# 策略搜索工具
# ============================================================

def _run_search_chain(search_list, dn, section, province, conn):
    """按顺序尝试多组关键词搜索，第一个命中即返回"""
    if not isinstance(search_list, (list, tuple)):
        return None
    for keywords in search_list:
        if keywords is None or keywords == "":
            continue
        results = search_quota_db(keywords, dn=dn, section=section,
                                  province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]
    return None


def _find_category_by_strategy(core_noun, dn, desc_lines, province, conn):
    """根据策略表搜索类别纠正定额"""
    for key, strategy in CORRECTION_STRATEGIES.items():
        if key.startswith("_"):
            continue

        match_mode = strategy.get("match", "exact")
        if match_mode == "contains":
            if key not in core_noun:
                continue
        else:
            if key != core_noun:
                continue

        effective_dn = dn or strategy.get("default_dn", dn)
        section = strategy.get("section", None)
        stop = strategy.get("stop", True)

        if "by_connection" in strategy:
            conn_type = extract_connection(desc_lines)
            variants = strategy["by_connection"]
            chosen = None
            if conn_type:
                for conn_key, variant in variants.items():
                    if conn_key.startswith("_"):
                        continue
                    if conn_key in conn_type:
                        chosen = variant
                        break
            if not chosen:
                chosen = variants.get("_default", {})
            result = _run_search_chain(
                chosen.get("search", []), effective_dn,
                chosen.get("section", section), province, conn
            )
        else:
            result = _run_search_chain(
                strategy.get("search", []), effective_dn, section, province, conn
            )

        if result:
            return result, True
        if stop:
            return None, True
        # stop=False 表示该策略失败后允许继续尝试后续策略
        continue

    return None, False


# ============================================================
# 8 种错误类型的纠正函数
# ============================================================

def _correct_category(item, error, dn, province, conn):
    """纠正类别不匹配：用核心名词+DN搜索正确定额"""
    desc_lines = extract_description_lines(item.get("description", ""))
    core_noun = error.get("core_noun", "")

    # 先查策略表（数据驱动）
    result, should_stop = _find_category_by_strategy(
        core_noun, dn, desc_lines, province, conn
    )
    if result:
        return result
    if should_stop:
        return None

    # 通用搜索：不限制章节
    expected = error.get("expected") or [core_noun]
    kw = expected[0] if expected else core_noun
    if not kw:
        return None
    results = search_quota_db([kw], dn=dn, province=province, conn=conn)
    if results:
        return results[0][0], results[0][1]
    return None


def _correct_material(item, error, dn, province, conn):
    """纠正管材类型不匹配：用正确管材类型搜索"""
    bill_name = item.get("name", "")
    desc_lines = extract_description_lines(item.get("description", ""))
    material = error.get("material", "")
    material_rules = MATERIAL_MAP.get(material, {})

    # 优先用 search_keywords（更精确）
    search_kws = material_rules.get("search_keywords", None)
    if search_kws:
        results = search_quota_db(search_kws, dn=dn, province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]

    # 回退到 should_contain
    search_kw = material_rules.get("should_contain", [material])[0]
    full_desc = ' '.join(desc_lines)

    if "给水" in full_desc or "给水" in bill_name:
        results = search_quota_db(["室内给水", search_kw], dn=dn,
                                  province=province, conn=conn)
    elif "排水" in full_desc or "雨水" in full_desc:
        if "铸铁" in material or "W型" in material:
            results = search_quota_db(["W型", "铸铁管", "管箍"], dn=dn,
                                      province=province, conn=conn)
        else:
            results = search_quota_db(["排水", search_kw], dn=dn,
                                      province=province, conn=conn)
    else:
        results = search_quota_db([search_kw], dn=dn, province=province, conn=conn)

    if results:
        return results[0][0], results[0][1]
    return None


def _correct_connection(item, error, dn, province, conn):
    """纠正连接方式不匹配：保持管材不变，改连接方式"""
    desc_lines = extract_description_lines(item.get("description", ""))
    connection = error.get("connection", "")
    conn_rules = CONNECTION_MAP.get(connection, {})
    conn_kw = conn_rules.get("should_contain", [connection])[0]

    material = extract_material(desc_lines)
    if material:
        mat_rules = MATERIAL_MAP.get(material, {})
        mat_kw = mat_rules.get("should_contain", [material])[0]
        results = search_quota_db([mat_kw, conn_kw], dn=dn, province=province,
                                  conn=conn)
    else:
        results = search_quota_db([conn_kw], dn=dn, province=province, conn=conn)

    if results:
        return results[0][0], results[0][1]
    return None


def _correct_parameter(item, error, dn, province, conn):
    """纠正参数偏差：搜索正确参数的定额"""
    desc_lines = extract_description_lines(item.get("description", ""))
    field = error.get("field", "")

    if field == "capacity":
        bill_value = error.get("bill_value", 0)
        results = []
        # 小容量(<=15m3)优先搜整体水箱
        if bill_value <= 15:
            results = search_quota_db(["整体水箱"], dn=bill_value,
                                      section="C10-8", province=province, conn=conn)
        if not results:
            results = search_quota_db(["水箱安装"], dn=bill_value,
                                      section="C10-8", province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]

    elif field == "pump_count":
        bill_count = error.get("bill_value", 0)
        cn_num = {2: "二", 3: "三", 4: "四"}
        count_str = cn_num.get(bill_count, str(bill_count))

        # 从描述提取出口DN
        full_desc = ' '.join(desc_lines)
        pump_dn = None
        dn_match = re.search(r'DN\s*(\d+)', full_desc, re.IGNORECASE)
        if dn_match:
            pump_dn = int(dn_match.group(1))

        # 根据流量Q估算出口DN
        if not pump_dn:
            q_match = re.search(r'Q\s*=?\s*(\d+(?:\.\d+)?)\s*m3', full_desc)
            if q_match:
                flow = float(q_match.group(1))
                if flow <= 5:
                    pump_dn = 25
                elif flow <= 12:
                    pump_dn = 50
                elif flow <= 25:
                    pump_dn = 65
                elif flow <= 50:
                    pump_dn = 80
                else:
                    pump_dn = 100

        results = search_quota_db(["变频泵组", f"{count_str}台"],
                                  dn=pump_dn, province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]
        # 回退不限台数
        results = search_quota_db(["变频泵组"], dn=pump_dn,
                                  province=province, conn=conn)
        if results:
            for r in results:
                if f"{count_str}台" in r[1]:
                    return r[0], r[1]
            return results[0][0], results[0][1]

    return None


def _correct_sleeve(item, error, dn, province, conn):
    """纠正套管类型不匹配"""
    sleeve_type = error.get("sleeve_type", "")
    if sleeve_type == "钢套管":
        results = search_quota_db(["填料套管"], dn=dn, province=province, conn=conn)
        if not results:
            results = search_quota_db(["一般", "套管", "制作安装"], dn=dn,
                                      province=province, conn=conn)
    else:
        results = search_quota_db(["防水套管"], dn=dn,
                                  section="C10-4", province=province, conn=conn)
    if results:
        return results[0][0], results[0][1]
    return None


def _correct_pipe_usage(item, error, dn, province, conn):
    """纠正管道用途不匹配（通气管→应套排水管定额）"""
    desc_lines = extract_description_lines(item.get("description", ""))
    search_kws = error.get("search_keywords", ["排水塑料管"])
    conn_type = extract_connection(desc_lines)
    if conn_type:
        conn_rules = CONNECTION_MAP.get(conn_type, {})
        conn_kw = conn_rules.get("should_contain", [])
        if conn_kw:
            search_kws = search_kws + conn_kw[:1]
    # 限制到 C10-2 章节（室内排水管）
    results = search_quota_db(search_kws, dn=dn, section="C10-2",
                              province=province, conn=conn)
    if results:
        return results[0][0], results[0][1]
    return None


def _correct_elevator_type(item, error, dn, province, conn):
    """纠正电梯类型不匹配：用正确的电梯类型搜索"""
    elev_type = error.get("elevator_type", "")
    results = search_quota_db([elev_type], section="C1-4", province=province,
                              conn=conn)
    if results:
        return results[0][0], results[0][1]
    return None


def _correct_electric_pair(item, error, dn, province, conn):
    """纠正电气配对不匹配：用正确的配对关键词搜索定额

    例如：清单写"双控"但匹配到"单控"定额，搜索含"双控"的定额替换。
    """
    keyword = error.get("keyword", "")
    scope = error.get("scope", "")

    # 从规则表中获取正确的关键词
    rules = ELECTRIC_PAIR_RULES.get(keyword, {})
    should_contain = rules.get("should_contain", [keyword])

    # 搜索策略：scope（设备类型）+ 正确关键词
    scope_words = scope.split("|") if scope else []
    search_kw = should_contain[0] if should_contain else keyword

    # 先用 scope + 正确关键词搜索（更精确）
    for sw in scope_words:
        results = search_quota_db([sw, search_kw], province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]

    # 回退：只用正确关键词搜索
    results = search_quota_db([search_kw], province=province, conn=conn)
    if results:
        return results[0][0], results[0][1]

    return None


def _correct_elevator_floor(item, error, dn, province, conn):
    """纠正电梯层站数不匹配：用计算出的正确编号搜索"""
    expected_id = error.get("expected_id", "")
    if expected_id:
        results = search_quota_db([], section=expected_id, province=province,
                                  conn=conn)
        if results:
            return results[0][0], results[0][1]
        # 回退：用电梯类型+层数搜索
        elev_type = error.get("elevator_type", "")
        floors = error.get("floors", 0)
        results = search_quota_db([elev_type, f"层数{floors}"],
                                  section="C1-4", province=province, conn=conn)
        if results:
            return results[0][0], results[0][1]
    return None


# ============================================================
# 统一调度入口
# ============================================================

# 调度表：错误类型 → 对应的纠正函数
_CORRECTOR_DISPATCH = {
    "category_mismatch": _correct_category,
    "material_mismatch": _correct_material,
    "connection_mismatch": _correct_connection,
    "parameter_deviation": _correct_parameter,
    "sleeve_mismatch": _correct_sleeve,
    "pipe_usage_mismatch": _correct_pipe_usage,
    "elevator_type_mismatch": _correct_elevator_type,
    "elevator_floor_mismatch": _correct_elevator_floor,
    "electric_pair_mismatch": _correct_electric_pair,
}


def validate_correction(error: dict, corrected_id: str, corrected_name: str) -> bool:
    """二次验真：检查纠正结果是否真的修复了检测到的问题。

    原理：自动纠错搜索到定额后，验证搜索结果中是否包含"应有"的关键词。
    防止关键词泛化/章节重叠导致搜到的定额和原错误一样。

    返回: True = 通过验真; False = 纠正结果不可信，应转人工
    """
    error_type = error.get("type", "")

    # 材质纠错：纠正结果必须包含正确材质关键词
    if error_type == "material_mismatch":
        material = error.get("material", "")
        mat_rules = MATERIAL_MAP.get(material, {})
        should_contain = mat_rules.get("should_contain", [])
        if should_contain and not any(kw in corrected_name for kw in should_contain):
            return False

    # 连接方式纠错：纠正结果必须包含正确连接方式关键词
    elif error_type == "connection_mismatch":
        connection = error.get("connection", "")
        conn_rules = CONNECTION_MAP.get(connection, {})
        should_contain = conn_rules.get("should_contain", [])
        if should_contain and not any(kw in corrected_name for kw in should_contain):
            return False

    # 电气配对纠错：纠正结果必须包含正确配对关键词
    elif error_type == "electric_pair_mismatch":
        keyword = error.get("keyword", "")
        rules = ELECTRIC_PAIR_RULES.get(keyword, {})
        should_contain = rules.get("should_contain", [keyword])
        if should_contain and not any(kw in corrected_name for kw in should_contain):
            return False

    return True


def correct_error(item, error, dn, province=None, conn=None):
    """统一入口：根据错误类型自动调度到对应纠正函数

    参数:
        item: 清单项字典（含 name, description 等）
        error: 检测器返回的错误字典（含 type, reason 等）
        dn: 公称直径
        province: 省份
        conn: 可选的共享数据库连接
    返回: (quota_id, quota_name) 或 None
           失败时返回 None，失败原因记录到日志
    """
    error_type = ""
    if isinstance(error, dict):
        error_type = str(error.get("type", ""))
    corrector = _CORRECTOR_DISPATCH.get(error_type)
    if not corrector:
        logger.debug(f"纠正跳过: 未知错误类型 '{error_type}'")
        return None
    try:
        result = corrector(item, error, dn, province, conn)
    except Exception as e:
        # 搜索过程出错（如DB连接异常），记录错误并返回 None（转人工）
        bill_name = item.get("name", "")[:30]
        logger.error(f"纠正搜索出错: [{error_type}] {bill_name} - {e}")
        return None
    # 二次验真：搜索结果是否真的修复了问题
    if result and not validate_correction(error, result[0], result[1]):
        logger.debug(f"纠正验真不通过: [{error_type}] 纠正结果 {result[0]} 未通过验真，转人工")
        return None  # 验真不通过，转人工
    if not result:
        bill_name = item.get("name", "")[:30]
        logger.debug(f"纠正无结果: [{error_type}] {bill_name} 搜索未找到合适定额，转人工")
    return result
