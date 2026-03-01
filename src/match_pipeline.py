# -*- coding: utf-8 -*-
"""
匹配处理流水线 — 从 match_engine.py 拆分出的中间层函数

包含：
1. 前置构建（清单上下文、专业分类、规则预匹配）
2. 结果构建（备选定额、跳过结果、空结果）
3. Search模式结果处理（搜索+经验交叉验证）
4. 兜底策略（规则/经验备选替换）
5. 统一前置处理（_prepare_item_for_matching）

依赖 match_core 的工具函数和核心搜索，不依赖 match_engine。
"""

from loguru import logger

from src.text_parser import parser as text_parser, normalize_bill_text
from src.specialty_classifier import classify as classify_specialty
from src.rule_validator import RuleValidator
from src.match_core import (
    RULE_DIRECT_CONFIDENCE,
    calculate_confidence,
    _append_trace_step,
    _normalize_classification,
    _is_measure_item,
    try_experience_match,
    _safe_json_materials,
    _summarize_candidates_for_trace,
)
from src.review_checkers import (
    check_category_mismatch,
    check_material_mismatch,
    check_connection_mismatch,
    check_pipe_usage,
    check_parameter_deviation,
    check_sleeve_mismatch,
    check_electric_pair,
    check_elevator_type,
    check_elevator_floor,
    extract_description_lines,
)


# ============================================================
# 审核规则检查（防止经验库错误数据被无限复制）
# ============================================================

def _review_check_match_result(result: dict, item: dict) -> dict | None:
    """
    用审核规则检查匹配结果，拦截明显错误。

    经验库直通的结果以前跳过所有审核规则，一旦有错误数据进入权威层，
    就会被无限复制。这个函数在直通前加一道"安检"，发现问题就拒绝直通。

    参数:
        result: 匹配结果字典（含 quotas 列表）
        item: 清单项目字典

    返回:
        审核错误字典（如果有错误），None 表示通过
    """
    quotas = result.get("quotas", [])
    if not quotas:
        return None

    main_quota = quotas[0]
    quota_name = main_quota.get("name", "")
    quota_id = main_quota.get("quota_id", "")

    if not quota_name:
        return None

    desc = item.get("description", "") or ""
    desc_lines = extract_description_lines(desc)

    # 依次运行审核检查器（短路：发现第一个错误就返回）
    error = (
        check_category_mismatch(item, quota_name, desc_lines)
        or check_sleeve_mismatch(item, quota_name, desc_lines)
        or check_material_mismatch(item, quota_name, desc_lines)
        or check_connection_mismatch(item, quota_name, desc_lines)
        or check_pipe_usage(item, quota_name, desc_lines)
        or check_parameter_deviation(item, quota_name, desc_lines)
        or check_electric_pair(item, quota_name, desc_lines)
        or check_elevator_type(item, quota_name, desc_lines)
        or check_elevator_floor(item, quota_name, desc_lines, quota_id=quota_id)
    )

    return error


# ============================================================
# 前置构建
# ============================================================

def _build_item_context(item: dict) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    original_name = item.get("original_name", name)
    search_query = text_parser.build_quota_query(name, desc,
                                                  specialty=item.get("specialty", ""),
                                                  bill_params=item.get("params"))
    # 线缆类型标签：追加到搜索词，帮助BM25区分电线/电缆/光缆定额
    cable_type = item.get("cable_type", "")
    if cable_type:
        search_query = f"{search_query} {cable_type}"
    return {
        "name": name,
        "desc": desc,
        "section": section,
        "unit": item.get("unit"),
        "quantity": item.get("quantity"),
        "full_query": f"{name} {desc}".strip(),
        "normalized_query": normalize_bill_text(original_name, desc),
        "search_query": search_query,
        "item": item,  # L5：供跨省预热读取 _cross_province_hints
    }


def _build_classification(item: dict, name: str, desc: str, section: str,
                          province: str = None) -> dict:
    """获取并标准化专业分类结果。"""
    classification = {
        "primary": item.get("specialty"),
        "fallbacks": item.get("specialty_fallbacks", []),
    }
    if not classification["primary"]:
        classification = classify_specialty(
            name, desc, section_title=section, province=province
        )
    return _normalize_classification(classification)


def _prepare_rule_match(rule_validator: RuleValidator, full_query: str, item: dict,
                        search_query: str, classification: dict) -> tuple[dict, dict]:
    """
    规则预匹配统一入口。

    返回:
        (rule_direct, rule_backup)
        - rule_direct: 高置信直通结果
        - rule_backup: 低置信备选结果
    """
    rule_books = [classification.get("primary")] + classification.get("fallbacks", [])
    rule_books = [b for b in rule_books if b]
    rule_result = rule_validator.match_by_rules(
        full_query, item, clean_query=search_query,
        books=rule_books if rule_books else None)
    if not rule_result:
        return None, None
    _append_trace_step(
        rule_result,
        "rule_precheck",
        books=rule_books,
        confidence=rule_result.get("confidence", 0),
        quota_ids=[q.get("quota_id", "") for q in rule_result.get("quotas", [])],
    )
    if rule_result.get("confidence", 0) >= RULE_DIRECT_CONFIDENCE:
        _append_trace_step(rule_result, "rule_direct", threshold=RULE_DIRECT_CONFIDENCE)
        return rule_result, None
    _append_trace_step(rule_result, "rule_backup", threshold=RULE_DIRECT_CONFIDENCE)
    return None, rule_result


# ============================================================
# 结果构建
# ============================================================

def _build_alternatives(candidates: list[dict], selected_ids: set = None,
                        skip_obj=None, top_n: int = 3) -> list[dict]:
    """从候选中构建备选定额列表。"""
    if not candidates:
        return []
    selected_ids = selected_ids or set()
    filtered = []
    for c in candidates:
        if skip_obj is not None and c is skip_obj:
            continue
        if selected_ids and c.get("quota_id") in selected_ids:
            continue
        filtered.append(c)
    alternatives = []
    for alt in filtered[:top_n]:
        quota_id = str(alt.get("quota_id", "")).strip()
        quota_name = str(alt.get("name", "")).strip()
        if not quota_id or not quota_name:
            logger.warning(f"跳过异常候选（缺少quota_id/name）: {alt}")
            continue
        alt_ps = alt.get("param_score", 0.5)
        alt_conf = calculate_confidence(alt_ps, alt.get("param_match", True))
        alternatives.append({
            "quota_id": quota_id,
            "name": quota_name,
            "unit": alt.get("unit", ""),
            "confidence": alt_conf,
            "reason": alt.get("param_detail", ""),
        })
    return alternatives


def _build_skip_measure_result(item: dict) -> dict:
    """构建措施项跳过结果。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "alternatives": [],
        "confidence": 0,
        "match_source": "skip_measure",
        "explanation": "措施项（管理费用），不套安装定额",
    }
    _append_trace_step(result, "skip_measure", reason="管理费用类条目")
    return result


def _build_empty_match_result(item: dict, reason: str, source: str = "search") -> dict:
    """构建空匹配结果（用于无候选时兜底）。"""
    result = {
        "bill_item": item,
        "quotas": [],
        "confidence": 0,
        "explanation": reason,
        "no_match_reason": reason,
        "match_source": source,
    }
    _append_trace_step(result, "empty_result", reason=reason)
    return result


# ============================================================
# 兜底策略
# ============================================================

def _apply_rule_backup(result: dict, rule_backup: dict, rule_hits: int,
                       prefer_label: str) -> tuple[dict, int]:
    """
    低置信规则结果兜底比较：置信度更高则替换当前结果。

    prefer_label 用于日志前缀，如"搜索/经验""LLM/经验""Agent/经验"。
    """
    if not rule_backup:
        return result, rule_hits
    if rule_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            rule_backup,
            "rule_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return rule_backup, rule_hits + 1
    _append_trace_step(
        result,
        "rule_backup_rejected",
        backup_confidence=rule_backup.get("confidence", 0),
        current_confidence=result.get("confidence", 0),
    )
    logger.debug(
        f"{prefer_label}结果优于低置信规则: "
        f"当前{result.get('confidence', 0)}分 >= "
        f"规则{rule_backup.get('confidence', 0)}分, "
        f"不使用规则结果")
    return result, rule_hits


def _apply_similar_exp_backup(result: dict, exp_backup: dict, exp_hits: int,
                              prefer_label: str) -> tuple[dict, int]:
    """经验库相似匹配兜底比较：置信度更高则替换当前结果。"""
    if not exp_backup:
        return result, exp_hits
    # 严格大于才替换（等分时保持当前结果，因为搜索+参数验证更针对当前query）
    if exp_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            exp_backup,
            "experience_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return exp_backup, exp_hits + 1
    _append_trace_step(
        result,
        "experience_backup_rejected",
        backup_confidence=exp_backup.get("confidence", 0),
        current_confidence=result.get("confidence", 0),
    )
    logger.debug(
        f"{prefer_label}结果优于经验库相似匹配: "
        f"当前{result.get('confidence', 0)}分 > "
        f"经验库{exp_backup.get('confidence', 0)}分, "
        f"保持{prefer_label}结果")
    return result, exp_hits


def _apply_mode_backups(result: dict, exp_backup: dict, rule_backup: dict,
                        exp_hits: int, rule_hits: int,
                        exp_label: str, rule_label: str) -> tuple[dict, int, int]:
    """full/agent 模式统一后处理：经验库相似兜底 + 低置信规则兜底。"""
    result, exp_hits = _apply_similar_exp_backup(
        result, exp_backup, exp_hits, prefer_label=exp_label)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label=rule_label)
    return result, exp_hits, rule_hits


# ============================================================
# Search模式结果处理
# ============================================================

def _reconcile_search_and_experience(result: dict, exp_backup: dict,
                                     exp_hits: int) -> tuple[dict, int]:
    """
    search模式下，经验库与搜索结果交叉验证。

    规则保持原逻辑：
    1) 同一主定额：抬高置信并标注 confirmed
    2) 经验库精确匹配但与搜索不一致：经验分降到88后再比较
    3) 经验库相似匹配：按置信度比较
    """
    if not exp_backup:
        return result, exp_hits

    exp_source = exp_backup.get("match_source", "")
    exp_qids = [q.get("quota_id", "") for q in exp_backup.get("quotas", [])]
    search_qids = [q.get("quota_id", "") for q in result.get("quotas", [])]

    same_quota = (exp_qids and search_qids and exp_qids[0] == search_qids[0])
    if same_quota:
        result["confidence"] = max(result.get("confidence", 0), 92)
        result["match_source"] = f"{exp_source}_confirmed"
        result["explanation"] = f"经验库+搜索一致: {result.get('explanation', '')}"
        if exp_backup.get("materials"):
            result["materials"] = exp_backup.get("materials")
        _append_trace_step(
            result,
            "experience_search_confirmed",
            experience_source=exp_source,
            quota_id=search_qids[0] if search_qids else "",
            materials_count=len(_safe_json_materials(result.get("materials"))),
        )
        return result, exp_hits + 1

    if exp_source == "experience_exact":
        exp_conf = min(exp_backup.get("confidence", 0), 88)
        search_conf = result.get("confidence", 0)
        if exp_conf >= search_conf:
            exp_backup["confidence"] = exp_conf
            _append_trace_step(
                exp_backup,
                "experience_exact_degraded_override",
                degraded_confidence=exp_conf,
                search_confidence=search_conf,
            )
            logger.debug(
                f"经验库精确匹配(降级) vs 搜索: "
                f"经验{exp_conf}分 >= 搜索{search_conf}分")
            return exp_backup, exp_hits + 1
        _append_trace_step(
            result,
            "experience_exact_degraded_rejected",
            degraded_confidence=exp_conf,
            search_confidence=search_conf,
        )
        logger.debug(
            f"搜索优于经验库精确匹配: "
            f"搜索{search_conf}分 > 经验{exp_conf}分(降级)")
        return result, exp_hits

    # 严格大于才替换（等分时保持当前结果，因为搜索+参数验证更针对当前query）
    if exp_backup.get("confidence", 0) > result.get("confidence", 0):
        _append_trace_step(
            exp_backup,
            "experience_similar_override",
            search_confidence=result.get("confidence", 0),
            backup_confidence=exp_backup.get("confidence", 0),
        )
        return exp_backup, exp_hits + 1

    _append_trace_step(
        result,
        "experience_similar_rejected",
        search_confidence=result.get("confidence", 0),
        backup_confidence=exp_backup.get("confidence", 0),
    )
    logger.debug(
        f"搜索结果优于经验库相似匹配: "
        f"搜索{result.get('confidence', 0)}分 > "
        f"经验库{exp_backup.get('confidence', 0)}分")
    return result, exp_hits


def _build_search_result_from_candidates(item: dict, candidates: list[dict]) -> dict:
    """
    search模式下，根据候选结果构建基础匹配结果。

    优先取 param_match=True 的首项；若全部不匹配，则降权回退到首候选。
    """
    best = None
    confidence = 0
    explanation = ""

    valid_candidates = [
        c for c in (candidates or [])
        if str(c.get("quota_id", "")).strip() and str(c.get("name", "")).strip()
    ]
    if candidates and not valid_candidates:
        logger.warning("候选列表存在，但全部缺少quota_id/name，按无匹配处理")

    if valid_candidates:
        matched_candidates = [c for c in valid_candidates if c.get("param_match", True)]
        if matched_candidates:
            best = matched_candidates[0]
            param_score = best.get("param_score", 0.5)
            confidence = calculate_confidence(param_score, param_match=True)
            explanation = best.get("param_detail", "")
        else:
            best = valid_candidates[0]
            param_score = best.get("param_score", 0.0)
            confidence = calculate_confidence(param_score, param_match=False)
            explanation = f"参数不完全匹配(回退候选): {best.get('param_detail', '')}"

    result = {
        "bill_item": item,
        "quotas": [{
            "quota_id": best["quota_id"],
            "name": best["name"],
            "unit": best.get("unit", ""),
            "reason": explanation,
            "db_id": best.get("id"),
        }] if best else [],
        "confidence": confidence,
        "explanation": explanation,
        "candidates_count": len(candidates),
        "match_source": "search",
    }
    _append_trace_step(
        result,
        "search_select",
        selected_quota=best.get("quota_id") if best else "",
        candidates_count=len(candidates),
        candidates=_summarize_candidates_for_trace(candidates),
    )

    if best and valid_candidates:
        result["alternatives"] = _build_alternatives(
            valid_candidates, skip_obj=best, top_n=3)
    if not best:
        result["no_match_reason"] = "搜索无匹配结果"
    return result


def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    result = _build_search_result_from_candidates(item, candidates)
    result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label="搜索/经验")
    _append_trace_step(
        result,
        "search_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
    )
    return result, exp_hits, rule_hits


# ============================================================
# 统一前置处理
# ============================================================

def _prepare_item_for_matching(item: dict, experience_db, rule_validator: RuleValidator,
                               province: str = None, exact_exp_direct: bool = False) -> dict:
    """
    三种模式统一的前置处理：
    1) 措施项跳过
    2) 专业分类
    3) 经验库预匹配（可配置精确命中是否直通）
    4) 规则预匹配（高置信直通、低置信备选）
    """
    ctx = _build_item_context(item)
    name = ctx["name"]
    desc = ctx["desc"]
    full_query = ctx["full_query"]
    search_query = ctx["search_query"]
    normalized_query = ctx["normalized_query"]

    if _is_measure_item(name, desc, ctx["unit"], ctx["quantity"]):
        return {
            "early_result": _build_skip_measure_result(item),
            "early_type": "skip_measure",
        }

    classification = _build_classification(
        item, name, desc, ctx["section"], province=province
    )
    exp_result = try_experience_match(
        normalized_query, item, experience_db, rule_validator, province=province)

    # 审核规则检查：经验库命中后，用审核规则验证一遍
    # 防止错误数据进入权威层后被无限复制
    if exp_result:
        review_error = _review_check_match_result(exp_result, item)
        if review_error:
            # 在 item 上标记审核拦截（后续统计时从 result.bill_item 中读取）
            item["_review_rejected"] = True
            bill_name = item.get("name", "")
            logger.warning(
                f"经验库匹配被审核规则拦截: '{bill_name[:40]}' "
                f"→ {review_error.get('type')}: {review_error.get('reason')}")
            _append_trace_step(exp_result, "experience_review_rejected",
                               error_type=review_error.get("type"),
                               error_reason=review_error.get("reason"))
            exp_result = None  # 丢弃，走搜索兜底

    exp_backup = exp_result if exp_result else None

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        _append_trace_step(exp_result, "experience_exact_direct_return")
        return {
            "early_result": exp_result,
            "early_type": "experience_exact",
        }

    rule_direct, rule_backup = _prepare_rule_match(
        rule_validator, full_query, item, search_query, classification)
    if rule_direct:
        _append_trace_step(rule_direct, "rule_direct_return")
        return {
            "early_result": rule_direct,
            "early_type": "rule_direct",
        }

    return {
        "early_result": None,
        "early_type": None,
        "ctx": ctx,
        "classification": classification,
        "exp_backup": exp_backup,
        "rule_backup": rule_backup,
    }
