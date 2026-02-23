# -*- coding: utf-8 -*-
"""
匹配引擎模块 — 公开 API 与编排

对外公开 API：
- init_search_components(province, aux_provinces) — 初始化搜索引擎
- init_experience_db(no_experience, province=None) — 初始化经验库
- match_by_mode(mode, ...) — 按模式执行匹配
- match_search_only(...) — 纯搜索模式
- match_agent(...) — Agent模式

底层组件见 match_core.py，处理流水线见 match_pipeline.py。
"""

import time

from loguru import logger

import config
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.rule_validator import RuleValidator
from src.match_core import (
    _append_trace_step,
    _finalize_trace,
    _summarize_candidates_for_trace,
    _prepare_candidates_from_prepared,
    _result_quota_signature,
    _should_skip_agent_llm,
    _should_audit_fastpath,
    _mark_agent_fastpath,
)
from src.match_pipeline import (
    _prepare_item_for_matching,
    _resolve_search_mode_result,
    _apply_mode_backups,
)


# ============================================================
# 进度日志
# ============================================================

def _should_log_progress(idx: int, total: int, interval: int) -> bool:
    """统一进度日志触发条件。"""
    return (idx % interval == 0) or (idx == total)


def _log_standard_progress(idx: int, total: int, exp_hits: int, rule_hits: int,
                           interval: int, show_percent: bool = False):
    """打印常规模式进度日志。"""
    if not _should_log_progress(idx, total, interval):
        return
    if show_percent:
        logger.info(f"匹配进度: {idx}/{total} "
                   f"({idx * 100 // total}%, "
                   f"经验库{exp_hits}, 规则{rule_hits})")
    else:
        logger.info(f"匹配进度: {idx}/{total} "
                   f"(经验库{exp_hits}, 规则{rule_hits})")


def _log_agent_progress(idx: int, total: int, exp_hits: int, rule_hits: int,
                        agent_hits: int, interval: int):
    """打印Agent模式进度日志。"""
    if not _should_log_progress(idx, total, interval):
        return
    logger.info(f"Agent进度: {idx}/{total} "
               f"(经验库{exp_hits}, 规则{rule_hits}, Agent{agent_hits})")


def _log_exp_rule_summary(exp_hits: int, rule_hits: int, total: int):
    """打印经验库/规则命中汇总。"""
    if exp_hits > 0 or rule_hits > 0:
        logger.info(f"经验库命中 {exp_hits}/{total} 条, "
                   f"规则命中 {rule_hits}/{total} 条")


# ============================================================
# 循环辅助
# ============================================================

def _consume_early_result(results: list[dict], early_result: dict, early_type: str,
                          idx: int, total: int, interval: int,
                          exp_hits: int, rule_hits: int,
                          log_types: set[str], is_agent: bool = False,
                          agent_hits: int = 0) -> tuple[bool, int, int]:
    """统一处理前置阶段提前命中的结果。"""
    if early_result is None:
        return False, exp_hits, rule_hits

    _append_trace_step(early_result, "early_return", early_type=early_type)
    _finalize_trace(early_result)
    results.append(early_result)
    if early_type == "experience_exact":
        exp_hits += 1
    elif early_type == "rule_direct":
        rule_hits += 1

    if early_type in log_types:
        if is_agent:
            _log_agent_progress(idx, total, exp_hits, rule_hits, agent_hits, interval)
        else:
            _log_standard_progress(
                idx, total, exp_hits, rule_hits, interval, show_percent=False)

    return True, exp_hits, rule_hits


def _prepare_match_iteration(item: dict, idx: int, total: int,
                             results: list[dict], exp_hits: int, rule_hits: int,
                             experience_db, rule_validator: RuleValidator,
                             province: str, exact_exp_direct: bool,
                             searcher: HybridSearcher, reranker,
                             validator: ParamValidator,
                             interval: int, log_types: set[str],
                             is_agent: bool = False, agent_hits: int = 0):
    """统一单条清单的前置命中消费和候选准备。"""
    prepared = _prepare_item_for_matching(
        item, experience_db, rule_validator, province=province,
        exact_exp_direct=exact_exp_direct)
    consumed, exp_hits, rule_hits = _consume_early_result(
        results=results,
        early_result=prepared.get("early_result"),
        early_type=prepared.get("early_type"),
        idx=idx,
        total=total,
        interval=interval,
        exp_hits=exp_hits,
        rule_hits=rule_hits,
        log_types=log_types,
        is_agent=is_agent,
        agent_hits=agent_hits,
    )
    if consumed:
        return True, exp_hits, rule_hits, None
    return (
        False,
        exp_hits,
        rule_hits,
        _prepare_candidates_from_prepared(prepared, searcher, reranker, validator),
    )


def _append_search_result_and_log(results: list[dict], result: dict,
                                  idx: int, total: int,
                                  exp_hits: int, rule_hits: int):
    """search模式统一结果入列与进度日志。"""
    _finalize_trace(result)
    results.append(result)
    _log_standard_progress(
        idx, total, exp_hits, rule_hits, interval=50, show_percent=True)


def _append_agent_result_and_log(results: list[dict], result: dict,
                                 idx: int, total: int,
                                 exp_hits: int, rule_hits: int,
                                 agent_hits: int) -> int:
    """agent模式统一结果入列、命中计数与进度日志。"""
    _finalize_trace(result)
    results.append(result)
    if result.get("match_source", "").startswith("agent"):
        agent_hits += 1
    _log_agent_progress(
        idx, total, exp_hits, rule_hits, agent_hits, interval=10)
    return agent_hits


# ============================================================
# Agent模式结果处理
# ============================================================

def _resolve_agent_mode_result(agent, item: dict, candidates: list[dict],
                               experience_db, full_query: str, search_query: str,
                               rule_kb, name: str, desc: str,
                               exp_backup: dict, rule_backup: dict,
                               exp_hits: int, rule_hits: int,
                               province: str = None,
                               reference_cases_cache: dict = None,
                               rules_context_cache: dict = None,
                               method_cards_db=None,
                               overview_context: str = ""):
    """agent模式统一结果决策：Agent分析 + 经验/规则兜底。"""
    if reference_cases_cache is None:
        reference_cases_cache = {}
    if rules_context_cache is None:
        rules_context_cache = {}

    reference_cases = _get_reference_cases_cached(
        reference_cases_cache, experience_db, full_query, province=province,
        top_k=3, specialty=item.get("specialty"),
        tolerate_error=True, default=None,
        error_prefix="参考案例获取失败（不影响Agent主流程）")
    rules_context = _get_agent_rules_context_cached(
        rules_context_cache, rule_kb, name, desc, province=province, top_k=3)

    # 查询方法论卡片（按清单名称+专业匹配）
    relevant_cards = None
    if method_cards_db:
        try:
            relevant_cards = method_cards_db.find_relevant(
                name, desc, specialty=item.get("specialty"),
                province=province, top_k=2)
        except Exception as e:
            logger.debug(f"方法卡片查询失败（不影响主流程）: {e}")

    result = agent.match_single(
        bill_item=item,
        candidates=candidates,
        reference_cases=reference_cases,
        rules_context=rules_context,
        method_cards=relevant_cards,
        search_query=search_query,
        overview_context=overview_context,
    )
    _append_trace_step(
        result,
        "agent_llm",
        candidates=_summarize_candidates_for_trace(candidates),
        reference_cases_count=len(reference_cases or []),
        rules_context_count=len(rules_context or []),
        method_cards_count=len(relevant_cards or []),
        method_card_categories=[c.get("category", "") for c in (relevant_cards or [])],
        province=province or "",
    )
    result, exp_hits, rule_hits = _apply_mode_backups(
        result, exp_backup, rule_backup,
        exp_hits, rule_hits,
        exp_label="Agent", rule_label="Agent/经验")
    _append_trace_step(
        result,
        "agent_mode_final",
        final_source=result.get("match_source", ""),
        final_confidence=result.get("confidence", 0),
    )
    return result, exp_hits, rule_hits


# ============================================================
# 初始化函数
# ============================================================

def init_search_components(resolved_province: str, aux_provinces: list = None):
    """初始化搜索引擎与参数校验器，并做状态检查。

    如果指定了辅助定额库（aux_provinces），会为每个辅助库创建独立的搜索器，
    并按定额库类型（土建/市政/园林）挂载到主搜索器上，供 cascade_search 路由使用。
    """
    logger.info("第2步：初始化搜索引擎...")
    searcher = HybridSearcher(resolved_province)
    validator = ParamValidator()

    # 预加载所有AI模型（向量模型+Reranker，避免第一条清单处理时等待）
    try:
        from src.model_cache import ModelCache
        ModelCache.preload_all()
    except Exception as e:
        logger.warning(f"模型预加载失败（不影响运行，会延迟加载）: {e}")

    # 检查引擎状态
    status = searcher.get_status()
    logger.info(f"  BM25索引: {status['bm25_count']} 条定额")
    logger.info(f"  向量索引: {status['vector_count']} 条定额")

    if not status["bm25_ready"]:
        raise RuntimeError("BM25索引未就绪，请先运行: python -m src.bm25_engine")

    # ---- 辅助定额库初始化 ----
    # aux_searchers: [HybridSearcher, ...] 列表
    # 附加到主搜索器上，cascade_search() 会在非安装项目时搜索这些库
    searcher.aux_searchers = []
    if aux_provinces:
        for aux_p in aux_provinces:
            try:
                aux_searcher = HybridSearcher(aux_p)
                aux_status = aux_searcher.get_status()
                searcher.aux_searchers.append(aux_searcher)
                logger.info(f"  辅助定额库: {aux_p} ({aux_status['bm25_count']}条)")
            except Exception as e:
                logger.warning(f"  辅助定额库 {aux_p} 初始化失败: {e}")

    return searcher, validator


def init_experience_db(no_experience: bool, province: str = None):
    """按配置初始化经验库（可选）。"""
    experience_db = None
    if no_experience:
        return experience_db
    try:
        from src.experience_db import ExperienceDB
        experience_db = ExperienceDB(province=province)
        exp_stats = experience_db.get_stats()
        logger.info(f"  经验库: {exp_stats['total']} 条历史记录")
    except Exception as e:
        logger.warning(f"经验库加载失败，将跳过经验库: {e}")
        experience_db = None
    return experience_db


def _get_reference_cases(experience_db, full_query: str, province: str = None,
                         top_k: int = 3, specialty: str = None,
                         tolerate_error: bool = False,
                         default=None, error_prefix: str = "参考案例获取失败（不影响主流程）"):
    """统一获取经验库参考案例。specialty传入后同专业案例优先。"""
    if not experience_db:
        return default
    if not tolerate_error:
        return experience_db.get_reference_cases(
            full_query, top_k=top_k, province=province, specialty=specialty)
    try:
        return experience_db.get_reference_cases(
            full_query, top_k=top_k, province=province, specialty=specialty)
    except Exception as e:
        logger.debug(f"{error_prefix}: {e}")
        return default


def _get_reference_cases_cached(cache: dict, experience_db, full_query: str,
                                province: str = None, top_k: int = 3,
                                specialty: str = None,
                                tolerate_error: bool = False, default=None,
                                error_prefix: str = "参考案例获取失败（不影响主流程）"):
    """带缓存获取经验案例，减少重复查询。specialty传入后同专业优先。"""
    key = (province or "", full_query, top_k, specialty or "", tolerate_error)
    if key not in cache:
        cache[key] = _get_reference_cases(
            experience_db, full_query, province=province, top_k=top_k,
            specialty=specialty,
            tolerate_error=tolerate_error, default=default,
            error_prefix=error_prefix)
    return cache[key]


def _get_agent_rules_context(rule_kb, name: str, desc: str, province: str = None,
                             top_k: int = 3):
    """Agent模式获取规则上下文（失败时降级继续）。"""
    if not rule_kb:
        return None
    try:
        return rule_kb.search_rules(f"{name} {desc}", top_k=top_k, province=province)
    except Exception as e:
        logger.debug(f"规则上下文获取失败（不影响Agent主流程）: {e}")
        return None


def _get_agent_rules_context_cached(cache: dict, rule_kb, name: str, desc: str,
                                    province: str = None, top_k: int = 3):
    """带缓存获取规则上下文，减少重复检索。"""
    key = (province or "", name, desc, top_k)
    if key not in cache:
        cache[key] = _get_agent_rules_context(
            rule_kb, name, desc, province=province, top_k=top_k)
    return cache[key]


def _load_rule_kb(province: str = None):
    """Agent模式按需加载规则知识库，失败时降级为None。"""
    try:
        from src.rule_knowledge import RuleKnowledge
        rule_kb = RuleKnowledge(province=province)
        return rule_kb if rule_kb.get_stats()["total"] > 0 else None
    except Exception as e:
        logger.debug(f"规则知识库不可用（Agent模式降级继续）: {e}")
        return None


def _create_rule_validator_and_reranker(province: str = None):
    """统一创建规则校验器和Reranker。"""
    from src.reranker import Reranker
    return RuleValidator(province=province), Reranker()


# ============================================================
# 核心匹配函数
# ============================================================

def match_search_only(bill_items: list[dict], searcher: HybridSearcher,
                      validator: ParamValidator,
                      experience_db=None,
                      province: str = None) -> list[dict]:
    """
    纯搜索模式：经验库 → 混合搜索 + 参数验证（不调用大模型API）

    优点：完全免费，速度快
    缺点：没有大模型精选，可能不够精确

    逻辑：
    1. 先查经验库，命中则直接返回
    2. 未命中则走混合搜索+参数验证
    3. 取参数验证后排名第1的候选作为主定额
    4. 结果用于人工审核；经验仅在审核确认后存入经验库
    """
    results = []
    exp_hits = 0  # 经验库命中计数
    rule_hits = 0  # 规则预匹配命中计数
    match_start_time = time.time()  # 纯匹配阶段开始时间

    rule_validator, reranker = _create_rule_validator_and_reranker(province=province)

    for idx, item in enumerate(bill_items, start=1):
        consumed, exp_hits, rule_hits, prepared_bundle = _prepare_match_iteration(
            item=item,
            idx=idx,
            total=len(bill_items),
            results=results,
            exp_hits=exp_hits,
            rule_hits=rule_hits,
            experience_db=experience_db,
            rule_validator=rule_validator,
            province=province,
            exact_exp_direct=False,
            searcher=searcher,
            reranker=reranker,
            validator=validator,
            interval=50,
            log_types={"rule_direct"},
        )
        if consumed:
            continue

        _, _, _, candidates, exp_backup, rule_backup = prepared_bundle

        result, exp_hits, rule_hits = _resolve_search_mode_result(
            item, candidates, exp_backup, rule_backup, exp_hits, rule_hits)

        _append_search_result_and_log(
            results, result, idx, len(bill_items), exp_hits, rule_hits)

    _log_exp_rule_summary(exp_hits, rule_hits, len(bill_items))

    # 纯匹配耗时统计（不含模型加载）
    match_elapsed = time.time() - match_start_time
    n = len(bill_items)
    if n > 0:
        per_item_sec = match_elapsed / n
        logger.info(f"纯匹配耗时: {match_elapsed:.1f}秒 ({per_item_sec:.2f}秒/条, 共{n}条)")

    # 规则后置校验：对搜索出来的结果校验档位，纠正选错的档位
    rule_validator.validate_results(results)
    for result in results:
        _append_trace_step(
            result,
            "rule_post_validate",
            final_source=result.get("match_source", ""),
            final_confidence=result.get("confidence", 0),
        )
        _finalize_trace(result)

    return results


def match_agent(bill_items: list[dict], searcher: HybridSearcher,
                validator: ParamValidator,
                experience_db=None, llm_type: str = None,
                province: str = None) -> list[dict]:
    """
    Agent模式（造价员贾维斯）：经验库 → 规则 → 搜索+Agent分析

    两阶段架构（提速核心）：
    1. 第1阶段（串行搜索）：逐条跑搜索+参数验证+快通道判断
       - 经验库/规则命中 → 直接采用
       - 快通道命中 → 直接采用搜索结果
       - 需要LLM → 收集到待处理列表
    2. 第2阶段（并发LLM）：对需要LLM的条目并发调用API
       - 并发数由 config.LLM_CONCURRENT 控制（默认5路）
       - 大幅减少LLM等待时间

    和search模式的区别：第3步不是直接取参数验证第1名，而是让大模型分析选择
    和full模式的区别：Prompt更强（造价员角色）、自动记录学习笔记
    """
    from src.agent_matcher import AgentMatcher
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 新实例自带干净的熔断器状态，无需手动重置

    # 初始化Agent（使用指定的或config中配置的大模型）
    agent_llm = llm_type or config.AGENT_LLM
    agent = AgentMatcher(llm_type=agent_llm, province=province)

    # 初始化方法卡片（从经验中提炼的选定额方法论，注入Agent Prompt）
    method_cards_db = None
    try:
        from src.method_cards import MethodCards
        mc = MethodCards()
        mc_stats = mc.get_stats()
        if mc_stats["total_cards"] > 0:
            method_cards_db = mc
            logger.info(f"方法卡片已加载: {mc_stats['total_cards']}张")
    except Exception as e:
        logger.debug(f"方法卡片加载跳过（不影响主流程）: {e}")

    # 结果数组：按原始顺序存放，用 index 定位
    results_by_idx = {}  # {idx: result}
    exp_hits = 0
    rule_hits = 0
    agent_hits = 0
    fastpath_hits = 0
    fastpath_audit_total = 0
    fastpath_audit_mismatch = 0

    rule_validator, reranker = _create_rule_validator_and_reranker(province=province)

    # 查规则知识库（Agent需要规则上下文）
    rule_kb = _load_rule_kb(province=province)
    reference_cases_cache = {}
    rules_context_cache = {}

    logger.info(f"Agent模式启动，大模型: {agent_llm}，LLM并发数: {config.LLM_CONCURRENT}")

    # 表级匹配统计（用于构建上下文摘要传给LLM，帮助保持同类清单一致性）
    match_stats = {}  # {"清单名称片段 → 定额编号": 计数}

    def _build_overview_context() -> str:
        """从已完成的匹配结果中构建表级统计摘要"""
        if not match_stats:
            return ""
        # 只取出现次数最多的前5条
        sorted_stats = sorted(match_stats.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [f"- {desc}: {count}条" for desc, count in sorted_stats]
        return f"[上下文] 当前表已处理的同类清单匹配情况：\n" + "\n".join(lines) + "\n同类清单请保持一致。"

    def _update_match_stats(result: dict):
        """从匹配结果中更新统计"""
        bill_item_info = result.get("bill_item", {})
        bill_name = bill_item_info.get("name", "")[:10]  # 取前10字作为摘要
        quotas = result.get("quotas", [])
        if quotas and bill_name:
            main_id = quotas[0].get("quota_id", "")
            main_name = quotas[0].get("name", "")[:15]
            key = f"{bill_name} → {main_id}({main_name})"
            match_stats[key] = match_stats.get(key, 0) + 1

    # ========== 第1阶段：串行搜索 + 快通道 ==========
    # 收集需要LLM的条目
    llm_tasks = []  # [(idx, item, candidates, full_query, search_query, name, desc, exp_backup, rule_backup, is_audit)]
    _consumed_buf = []  # _prepare_match_iteration 会往这里 append 消耗掉的结果

    for idx, item in enumerate(bill_items, start=1):
        consumed, exp_hits, rule_hits, prepared_bundle = _prepare_match_iteration(
            item=item,
            idx=idx,
            total=len(bill_items),
            results=_consumed_buf,  # 消耗掉的结果追加到缓冲区
            exp_hits=exp_hits,
            rule_hits=rule_hits,
            experience_db=experience_db,
            rule_validator=rule_validator,
            province=province,
            exact_exp_direct=True,
            searcher=searcher,
            reranker=reranker,
            validator=validator,
            interval=50,
            log_types={"experience_exact", "rule_direct"},
            is_agent=True,
            agent_hits=agent_hits,
        )
        if consumed:
            # 经验库/规则直通命中，结果在 _consumed_buf 最后一条
            results_by_idx[idx] = _consumed_buf[-1]
            _update_match_stats(_consumed_buf[-1])
            continue

        ctx, full_query, search_query, candidates, exp_backup, rule_backup = prepared_bundle
        name = ctx["name"]
        desc = ctx["desc"]

        if _should_skip_agent_llm(candidates, exp_backup=exp_backup, rule_backup=rule_backup):
            fast_result, exp_hits, rule_hits = _resolve_search_mode_result(
                item, candidates, exp_backup, rule_backup, exp_hits, rule_hits)
            _mark_agent_fastpath(fast_result)
            fastpath_hits += 1
            results_by_idx[idx] = fast_result
            _update_match_stats(fast_result)

            # 质量护栏：抽检走快通道的条目（收集到LLM任务里一起并发）
            if _should_audit_fastpath():
                fastpath_audit_total += 1
                llm_tasks.append((idx, item, candidates, full_query, search_query,
                                  name, desc, exp_backup, rule_backup, True))  # True=审计模式
        else:
            # 需要LLM分析
            llm_tasks.append((idx, item, candidates, full_query, search_query,
                              name, desc, exp_backup, rule_backup, False))  # False=正常模式

    logger.info(f"第1阶段完成: 快通道{fastpath_hits}条, 需LLM{len(llm_tasks)}条")

    # ========== 第2阶段：并发LLM调用 ==========
    if llm_tasks:
        def _process_llm_task(task):
            """单个LLM任务处理（线程安全）"""
            idx, item, candidates, full_query, search_query, name, desc, exp_backup, rule_backup, is_audit = task
            # 构建表级上下文摘要（在LLM调用前快照，线程安全）
            ctx_summary = _build_overview_context()
            result, task_exp_hits, task_rule_hits = _resolve_agent_mode_result(
                agent=agent,
                item=item,
                candidates=candidates,
                experience_db=experience_db,
                full_query=full_query,
                search_query=search_query,
                rule_kb=rule_kb,
                name=name,
                desc=desc,
                exp_backup=exp_backup,
                rule_backup=rule_backup,
                exp_hits=0,
                rule_hits=0,
                province=province,
                reference_cases_cache=reference_cases_cache,
                rules_context_cache=rules_context_cache,
                method_cards_db=method_cards_db,
                overview_context=ctx_summary,
            )
            # 低置信度重试：confidence < 阈值时，扩大搜索范围重试一次
            retry_threshold = getattr(config, "LOW_CONFIDENCE_RETRY_THRESHOLD", 60)
            confidence = result.get("confidence", 100)
            # LLM已熔断时跳过重试（避免放大失败开销）
            if hasattr(agent, "is_circuit_open"):
                llm_circuit_open = bool(agent.is_circuit_open())
            else:
                llm_circuit_open = bool(getattr(agent, "_llm_circuit_open", False))
            if not is_audit and confidence < retry_threshold and candidates and not llm_circuit_open:
                logger.info(f"#{idx} 置信度{confidence}<{retry_threshold}，触发全库重试搜索")
                try:
                    # 全库搜索（不限册号），增加候选数
                    retry_candidates = searcher.search(
                        search_query, top_k=config.HYBRID_TOP_K + 5, books=None)
                    if retry_candidates and len(retry_candidates) > 1:
                        retry_candidates = reranker.rerank(search_query, retry_candidates)
                    if retry_candidates:
                        retry_candidates = validator.validate_candidates(
                            full_query, retry_candidates, supplement_query=search_query)
                    if retry_candidates:
                        # 用扩展候选重新调用LLM
                        retry_result, r_exp, r_rule = _resolve_agent_mode_result(
                            agent=agent, item=item, candidates=retry_candidates,
                            experience_db=experience_db, full_query=full_query,
                            search_query=search_query, rule_kb=rule_kb,
                            name=name, desc=desc, exp_backup=exp_backup,
                            rule_backup=rule_backup, exp_hits=0, rule_hits=0,
                            province=province,
                            reference_cases_cache=reference_cases_cache,
                            rules_context_cache=rules_context_cache,
                            method_cards_db=method_cards_db,
                            overview_context=ctx_summary,
                        )
                        retry_conf = retry_result.get("confidence", 0)
                        if retry_conf > confidence:
                            logger.info(f"#{idx} 重试成功: {confidence}→{retry_conf}")
                            result = retry_result
                            task_exp_hits = r_exp
                            task_rule_hits = r_rule
                except Exception as e:
                    logger.debug(f"#{idx} 低置信度重试失败（保留原结果）: {e}")

            return idx, result, task_exp_hits, task_rule_hits, is_audit

        # 并发执行LLM任务
        concurrent = max(1, config.LLM_CONCURRENT)
        logger.info(f"第2阶段: {len(llm_tasks)}条LLM任务，{concurrent}路并发")

        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            futures = {pool.submit(_process_llm_task, task): task for task in llm_tasks}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                task = futures[future]
                idx, item, candidates, full_query, search_query, name, desc, exp_backup, rule_backup, is_audit = task
                try:
                    idx, result, task_exp_hits, task_rule_hits, is_audit = future.result()
                except Exception as e:
                    logger.error(f"LLM并发任务失败(#{idx}): {e}")
                    if is_audit:
                        fast_result = results_by_idx.get(idx)
                        if fast_result:
                            _append_trace_step(
                                fast_result,
                                "agent_task_exception",
                                error=str(e),
                                mode="audit_keep_fastpath",
                            )
                        if completed % 10 == 0 or completed == len(llm_tasks):
                            logger.info(f"LLM杩涘害: {completed}/{len(llm_tasks)}")
                        continue
                    try:
                        result, task_exp_hits, task_rule_hits = _resolve_search_mode_result(
                            item, candidates, exp_backup, rule_backup, 0, 0)
                        _append_trace_step(
                            result,
                            "agent_task_exception",
                            error=str(e),
                            mode="fallback_search",
                        )
                    except Exception as fallback_e:
                        logger.error(f"LLM任务降级也失败(#{idx}): {fallback_e}")
                        result = {
                            "bill_item": item,
                            "quotas": [],
                            "confidence": 0,
                            "explanation": f"LLM任务异常且降级失败: {fallback_e}",
                            "match_source": "agent_error",
                            "no_match_reason": f"LLM任务异常: {e}",
                            "candidates_count": len(candidates) if candidates else 0,
                        }
                        task_exp_hits = 0
                        task_rule_hits = 0

                if is_audit:
                    # 审计模式：对比快通道结果
                    fast_result = results_by_idx.get(idx)
                    if fast_result and _result_quota_signature(result) != _result_quota_signature(fast_result):
                        fastpath_audit_mismatch += 1
                        result["agent_fastpath_overruled"] = True
                        _append_trace_step(
                            result,
                            "agent_fastpath_overruled",
                            fastpath_signature=list(_result_quota_signature(fast_result)),
                            llm_signature=list(_result_quota_signature(result)),
                        )
                        results_by_idx[idx] = result  # 以LLM结果为准
                        # 记录不一致详情，便于后续分析优化
                        fast_sig = _result_quota_signature(fast_result)
                        llm_sig = _result_quota_signature(result)
                        item_name = (fast_result.get("bill_item") or {}).get("name",
                                    fast_result.get("original_name", fast_result.get("name", "?")))
                        logger.info(f"抽检不一致 #{idx} [{item_name}]: "
                                    f"快通道={fast_sig} → LLM={llm_sig}")
                    # 否则保持快通道结果不变
                else:
                    # 正常LLM结果
                    results_by_idx[idx] = result
                    exp_hits += task_exp_hits
                    rule_hits += task_rule_hits
                    agent_hits += 1

                if completed % 10 == 0 or completed == len(llm_tasks):
                    logger.info(f"LLM进度: {completed}/{len(llm_tasks)}")

    # ========== 组装最终结果（按原始顺序）==========
    results = []
    for idx in range(1, len(bill_items) + 1):
        if idx in results_by_idx:
            _finalize_trace(results_by_idx[idx])
            results.append(results_by_idx[idx])

    logger.info(f"Agent匹配完成: 经验库{exp_hits}, 规则{rule_hits}, "
               f"Agent分析{agent_hits}/{len(bill_items)}条, 快速通道{fastpath_hits}条")
    if fastpath_audit_total > 0:
        audit_ok = fastpath_audit_total - fastpath_audit_mismatch
        consistency = (audit_ok * 100.0) / fastpath_audit_total
        logger.info(f"快速通道抽检: {fastpath_audit_total} 条, 不一致 {fastpath_audit_mismatch} 条, "
                   f"一致率 {consistency:.1f}%")

    # 规则后置校验
    rule_validator.validate_results(results)
    for result in results:
        _append_trace_step(
            result,
            "rule_post_validate",
            final_source=result.get("match_source", ""),
            final_confidence=result.get("confidence", 0),
        )
        _finalize_trace(result)

    return results


# ============================================================
# 模式分派
# ============================================================

def match_by_mode(mode: str, bill_items: list[dict], searcher: HybridSearcher,
                  validator: ParamValidator, experience_db,
                  resolved_province: str, agent_llm: str = None) -> list[dict]:
    """按模式执行匹配。"""
    if mode == "search":
        return match_search_only(
            bill_items, searcher, validator, experience_db, province=resolved_province)
    if mode == "agent":
        return match_agent(
            bill_items, searcher, validator, experience_db,
            llm_type=agent_llm, province=resolved_province)
    raise ValueError(f"不支持的匹配模式: {mode}")
