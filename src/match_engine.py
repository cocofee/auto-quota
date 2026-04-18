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

import re
import threading
import time
from contextlib import nullcontext

from loguru import logger

import config
from src.adaptive_strategy import AdaptiveStrategy
from src.context_builder import build_project_context, format_overview_context
from src.context_builder import summarize_batch_context_for_trace
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.rule_validator import RuleValidator
from src.final_validator import FinalValidator
from src.fallback_logger import fallback_logger
from src.reasoning_agent import ReasoningAgent
from src.runtime_cache import (
    get_experience_db,
    get_method_cards_db,
    get_rule_bundle,
    get_search_bundle,
    get_unified_data_layer,
)
from src.match_core import (
    _append_trace_step,
    _finalize_trace,
    _summarize_candidates_for_trace,
    _prepare_candidates_from_prepared,
    _result_quota_signature,
    get_fastpath_decision,
    _should_skip_agent_llm,
    _should_audit_fastpath,
    _mark_agent_fastpath,
)
from src.match_pipeline import (
    _append_item_review_rejection_trace,
    _prepare_item_for_matching,
    _resolve_search_mode_result,
    _apply_mode_backups,
)
from src.performance_monitor import PerformanceMonitor


def _annotate_adaptive_strategies(
    bill_items: list[dict],
    selector: AdaptiveStrategy | None = None,
) -> dict[str, int]:
    selector = selector or AdaptiveStrategy()
    counts = {"fast": 0, "standard": 0, "deep": 0, "unknown": 0}
    for item in bill_items or []:
        if not isinstance(item, dict):
            counts["unknown"] += 1
            continue
        existing = str(item.get("adaptive_strategy") or "").strip().lower()
        if existing in {"fast", "standard", "deep"}:
            strategy = existing
            decision = dict(item.get("_adaptive_strategy_meta") or {})
            if not decision:
                decision = {"strategy": strategy}
        else:
            decision = dict(selector.evaluate(item))
            strategy = str(decision.get("strategy") or "standard").strip().lower()
            if strategy not in {"fast", "standard", "deep"}:
                strategy = "standard"
            item["adaptive_strategy"] = strategy
        item["_adaptive_strategy_meta"] = decision
        counts[strategy] = counts.get(strategy, 0) + 1

    logger.info(
        "adaptive_strategy assigned: "
        f"fast={counts.get('fast', 0)} "
        f"standard={counts.get('standard', 0)} "
        f"deep={counts.get('deep', 0)} "
        f"unknown={counts.get('unknown', 0)}"
    )
    return counts


# ============================================================
# 进度日志
# ============================================================

def _should_log_progress(idx: int, total: int, interval: int) -> bool:
    """统一进度日志触发条件。"""
    return (idx % interval == 0) or (idx == total)


def _log_standard_progress(idx: int, total: int, exp_hits: int, rule_hits: int,
                           interval: int, show_percent: bool = False) -> None:
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
                        agent_hits: int, interval: int) -> None:
    """打印Agent模式进度日志。"""
    if not _should_log_progress(idx, total, interval):
        return
    logger.info(f"Agent进度: {idx}/{total} "
               f"(经验库{exp_hits}, 规则{rule_hits}, Agent{agent_hits})")


def _log_exp_rule_summary(exp_hits: int, rule_hits: int, total: int) -> None:
    """打印经验库/规则命中汇总。"""
    if exp_hits > 0 or rule_hits > 0:
        logger.info(f"经验库命中 {exp_hits}/{total} 条, "
                   f"规则命中 {rule_hits}/{total} 条")


# ============================================================
# 循环辅助
# ============================================================

def _top_quota_id(result: dict) -> str:
    quotas = (result or {}).get("quotas") or []
    if not quotas:
        return ""
    return str(quotas[0].get("quota_id", "") or "").strip()


def _mark_stage_top1_change(
    results: list[dict],
    stage_name: str,
    *,
    base_top1_field: str = "post_arbiter_top1_id",
) -> None:
    for result in results or []:
        base_top1 = str(result.get(base_top1_field, "") or "")
        current_top1 = _top_quota_id(result)
        if not base_top1 or not current_top1 or base_top1 == current_top1:
            continue
        if result.get("final_changed_by"):
            continue
        result["final_changed_by"] = stage_name


def _snapshot_stage_top1(results: list[dict], field_name: str) -> None:
    for result in results or []:
        result[field_name] = _top_quota_id(result)


def _append_consistency_review_trace(results: list[dict]) -> None:
    for result in results or []:
        advisory = result.get("reflection_correction") or {}
        summary = result.get("reflection_summary") or {}
        _append_trace_step(
            result,
            "consistency_review",
            reflection_conflict=bool(result.get("reflection_conflict")),
            reflection_advisory=bool(advisory),
            reflection_advisory_action=str(advisory.get("action") or ""),
            reflection_advisory_quota_id=str(advisory.get("quota_id") or ""),
            reflection_groups_checked=int(summary.get("groups_checked", 0) or 0),
            reflection_inconsistent_groups=int(summary.get("inconsistent_groups", 0) or 0),
        )


def _attach_performance_snapshot(result: dict,
                                 monitor: PerformanceMonitor | None,
                                 *,
                                 idx: int | None = None,
                                 total: int | None = None) -> None:
    if not isinstance(result, dict) or monitor is None:
        return

    stages = monitor.snapshot()
    if not stages:
        return

    total_elapsed = sum(stages.values())
    result["performance"] = {
        "stages": stages,
        "total": total_elapsed,
    }
    _append_trace_step(
        result,
        "performance_monitor",
        performance_stages=stages,
        performance_total=total_elapsed,
    )

    if not bool(getattr(config, "PERFORMANCE_MONITOR_REPORT_EACH_ITEM", False)):
        return

    item_name = str(((result.get("bill_item") or {}).get("name") or "")).strip()
    title_parts = ["性能报告"]
    if idx is not None and total:
        title_parts.append(f"#{idx}/{total}")
    if item_name:
        title_parts.append(item_name[:40])
    logger.debug(monitor.format_report(" ".join(title_parts)))


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
    if (
        early_type == "experience_exact"
        or str((early_result or {}).get("match_source", "")).startswith("experience")
    ):
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


def _update_consistency_memory(memory: dict, item: dict, result: dict) -> None:
    """
    更新同文件一致性记忆

    高置信(>=80)匹配结果产生后，提取定额名称的关键词存入记忆，
    后续同名短名称可以借用这个信息辅助搜索。
    """
    if not result or result.get("confidence", 0) < 80:
        return
    quotas = result.get("quotas", [])
    if not quotas:
        return
    item_name = item.get("name", "")
    if not item_name:
        return
    # 用 (名称, 专业) 做联合key，避免跨专业污染
    # 例如同一文件中消防分部的"阀门"和给排水分部的"阀门"是不同场景
    specialty = item.get("specialty", "")
    memory_key = (item_name, specialty)
    # 提取定额名称中的前2-4字中文关键词作为"定额族"标识
    quota_name = quotas[0].get("name", "")
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', quota_name)
    if cn_words:
        memory[memory_key] = cn_words[0]


def _prepare_match_iteration(item: dict, idx: int, total: int,
                             results: list[dict], exp_hits: int, rule_hits: int,
                             experience_db, rule_validator: RuleValidator,
                             province: str, exact_exp_direct: bool,
                             searcher: HybridSearcher, reranker,
                             validator: ParamValidator,
                             interval: int, log_types: set[str],
                             is_agent: bool = False, agent_hits: int = 0,
                             lightweight_experience: bool = False,
                             lightweight_rule_prematch: bool = False,
                             include_prior_candidates: bool = True,
                             performance_monitor: PerformanceMonitor | None = None) -> tuple[bool, int, int, dict | None]:
    """统一单条清单的前置命中消费和候选准备。"""
    prepared = _prepare_item_for_matching(
        item, experience_db, rule_validator, province=province,
        exact_exp_direct=exact_exp_direct,
        lightweight_experience=lightweight_experience,
        lightweight_rule_prematch=lightweight_rule_prematch,
        performance_monitor=performance_monitor,
    )
    prepared_item = ((prepared.get("ctx") or {}).get("item") if isinstance(prepared, dict) else None)
    if isinstance(prepared_item, dict):
        _annotate_adaptive_strategies([prepared_item])
        if prepared_item is not item:
            item["adaptive_strategy"] = prepared_item.get("adaptive_strategy")
            item["_adaptive_strategy_meta"] = prepared_item.get("_adaptive_strategy_meta")
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
        _prepare_candidates_from_prepared(
            prepared,
            searcher,
            reranker,
            validator,
            include_prior_candidates=include_prior_candidates,
            performance_monitor=performance_monitor,
        ),
    )


def _append_search_result_and_log(results: list[dict], result: dict,
                                  idx: int, total: int,
                                  exp_hits: int, rule_hits: int,
                                  interval: int = 50) -> None:
    """search模式统一结果入列与进度日志。"""
    _finalize_trace(result)
    results.append(result)
    _log_standard_progress(
        idx, total, exp_hits, rule_hits, interval=interval, show_percent=True)


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


def _canonical_query_payload(ctx: dict | None,
                             *,
                             full_query: str = "",
                             search_query: str = "",
                             item: dict | None = None) -> dict:
    """Normalize canonical_query for Agent/retry links while keeping old callers compatible."""
    payload = dict((ctx or {}).get("canonical_query") or (item or {}).get("canonical_query") or {})
    raw_query = f"{(ctx or {}).get('name', '')} {(ctx or {}).get('desc', '')}".strip()
    payload.setdefault("raw_query", raw_query or full_query)
    payload.setdefault("route_query", payload.get("raw_query") or full_query)
    payload.setdefault("validation_query", full_query or payload.get("route_query") or payload.get("raw_query") or "")
    payload.setdefault("search_query", search_query or payload.get("validation_query") or "")
    payload.setdefault("normalized_query", "")
    return payload


def _canonical_query_views(canonical_query: dict | None,
                           *,
                           full_query: str = "",
                           search_query: str = "") -> tuple[dict, str, str]:
    payload = dict(canonical_query or {})
    validation_query = str(
        payload.get("validation_query")
        or full_query
        or payload.get("route_query")
        or payload.get("raw_query")
        or ""
    ).strip()
    resolved_search_query = str(
        payload.get("search_query")
        or search_query
        or validation_query
    ).strip()
    payload.setdefault("validation_query", validation_query)
    payload.setdefault("search_query", resolved_search_query)
    payload.setdefault("route_query", payload.get("raw_query") or validation_query)
    payload.setdefault("normalized_query", "")
    return payload, validation_query, resolved_search_query


_AGENT_RETRY_QUOTA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _normalize_retry_query_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _retry_query_key(value: object) -> str:
    return _normalize_retry_query_text(value).casefold()


def _is_valid_retry_query(query: str, *, allow_quota_id: bool = False) -> bool:
    if not query:
        return False
    if any(ch in query for ch in ("\r", "\n", "\t")):
        return False
    if query.startswith(("{", "[")):
        return False
    if len(query) > 120:
        return False
    if allow_quota_id and _AGENT_RETRY_QUOTA_ID_RE.fullmatch(query):
        return True
    return len(query) >= 2


def _build_agent_retry_payload(*,
                               strategy: str,
                               query: object,
                               source: str,
                               detail: str,
                               dedupe_against: list[str] | None = None,
                               allow_quota_id: bool = False) -> dict | None:
    normalized_query = _normalize_retry_query_text(query)
    if not _is_valid_retry_query(normalized_query, allow_quota_id=allow_quota_id):
        return None
    blocked = {_retry_query_key(value) for value in (dedupe_against or []) if value}
    if _retry_query_key(normalized_query) in blocked:
        return None
    return {
        "strategy": strategy,
        "query": normalized_query,
        "source": source,
        "detail": detail,
    }


def _agent_retry_from_validation_query(context: dict) -> dict | None:
    return _build_agent_retry_payload(
        strategy="canonical_validation",
        query=context.get("validation_query", ""),
        source="deterministic",
        detail="fallback to canonical validation query with route/section context",
        dedupe_against=[context.get("search_query", "")],
    )


def _agent_retry_from_primary_profile(context: dict) -> dict | None:
    profile = dict((context.get("canonical_query") or {}).get("primary_query_profile") or {})
    terms: list[str] = []
    for value in [profile.get("primary_subject"), *(profile.get("decisive_terms") or []), *(profile.get("key_specs") or [])]:
        token = _normalize_retry_query_text(value)
        if token and token not in terms:
            terms.append(token)
    if not terms:
        return None
    return _build_agent_retry_payload(
        strategy="primary_profile_core",
        query=" ".join(terms[:6]),
        source="deterministic",
        detail="strip modifiers and keep primary subject/spec anchors",
        dedupe_against=[
            context.get("search_query", ""),
            context.get("validation_query", ""),
            (context.get("canonical_query") or {}).get("normalized_query", ""),
        ],
    )


def _agent_retry_from_normalized_query(context: dict) -> dict | None:
    canonical_query = context.get("canonical_query") or {}
    return _build_agent_retry_payload(
        strategy="normalized_query",
        query=canonical_query.get("normalized_query", ""),
        source="deterministic",
        detail="fallback to normalized query without surface modifiers",
        dedupe_against=[
            context.get("search_query", ""),
            context.get("validation_query", ""),
        ],
    )


def _agent_retry_from_llm_suggested_search(context: dict) -> dict | None:
    return _build_agent_retry_payload(
        strategy="llm_suggested_search",
        query=(context.get("result") or {}).get("suggested_search", ""),
        source="llm",
        detail="use agent suggested_search after structural validation",
        dedupe_against=[
            context.get("search_query", ""),
            context.get("validation_query", ""),
            (context.get("canonical_query") or {}).get("normalized_query", ""),
        ],
    )


def _agent_retry_from_recommended_id(context: dict) -> dict | None:
    return _build_agent_retry_payload(
        strategy="ai_recommended_id",
        query=(context.get("result") or {}).get("_ai_recommended_id", ""),
        source="llm",
        detail="fallback to agent recommended quota id after structural validation",
        dedupe_against=[context.get("search_query", "")],
        allow_quota_id=True,
    )


_AGENT_RETRY_STRATEGY_REGISTRY = {
    "canonical_validation": _agent_retry_from_validation_query,
    "primary_profile_core": _agent_retry_from_primary_profile,
    "normalized_query": _agent_retry_from_normalized_query,
    "llm_suggested_search": _agent_retry_from_llm_suggested_search,
    "ai_recommended_id": _agent_retry_from_recommended_id,
}


def _select_agent_retry_strategy(*,
                                 canonical_query: dict,
                                 validation_query: str,
                                 search_query: str,
                                 result: dict,
                                 ai_not_found: bool) -> dict | None:
    context = {
        "canonical_query": canonical_query or {},
        "validation_query": validation_query,
        "search_query": search_query,
        "result": result or {},
        "ai_not_found": ai_not_found,
    }
    strategy_names = (
        ["llm_suggested_search", "ai_recommended_id", "canonical_validation", "primary_profile_core", "normalized_query"]
        if ai_not_found else
        ["canonical_validation", "primary_profile_core", "normalized_query", "llm_suggested_search", "ai_recommended_id"]
    )
    for name in strategy_names:
        builder = _AGENT_RETRY_STRATEGY_REGISTRY.get(name)
        if builder is None:
            continue
        payload = builder(context)
        if payload:
            return payload
    return None


def _attach_agent_retry_trace(result: dict | None, **fields) -> None:
    if not isinstance(result, dict):
        return
    retry_trace = dict(result.get("retry_trace") or {})
    retry_trace.update({k: v for k, v in fields.items() if v is not None})
    result["retry_trace"] = retry_trace
    _append_trace_step(result, "agent_retry", **retry_trace)


def _split_knowledge_for_prompt(
    knowledge_evidence: dict | None,
    *,
    reference_cases: list[dict] | None = None,
    rules_context: list[dict] | None = None,
    method_cards: list[dict] | None = None,
) -> tuple[dict, list[dict] | None, list[dict] | None]:
    """Keep full structured evidence for trace/output, but honor prompt toggles."""
    evidence = dict(knowledge_evidence or {})
    evidence.setdefault("reference_cases", list(reference_cases or []))
    evidence.setdefault("quota_rules", [])
    evidence.setdefault("quota_explanations", [])
    evidence.setdefault("method_cards", list(method_cards or []))
    evidence.setdefault("price_references", [])

    prompt_rules_enabled = bool(getattr(config, "AGENT_RULES_IN_PROMPT", True))
    prompt_methods_enabled = bool(getattr(config, "AGENT_METHOD_CARDS_IN_PROMPT", True))

    prompt_rules_context = rules_context if prompt_rules_enabled else None
    prompt_method_cards = method_cards if prompt_methods_enabled else None
    prompt_evidence = dict(evidence)
    if not prompt_rules_enabled:
        prompt_evidence["quota_rules"] = []
        prompt_evidence["quota_explanations"] = []
    if not prompt_methods_enabled:
        prompt_evidence["method_cards"] = []

    return prompt_evidence, prompt_rules_context, prompt_method_cards


# ============================================================
# Agent模式结果处理
# ============================================================

def _resolve_agent_mode_result(agent, item: dict, candidates: list[dict],
                               experience_db, canonical_query: dict | None,
                               rule_kb, name: str, desc: str,
                               exp_backup: dict, rule_backup: dict,
                               exp_hits: int, rule_hits: int,
                               full_query: str = "", search_query: str = "",
                               province: str = None,
                               unified_knowledge_retriever=None,
                               unified_knowledge_cache: dict = None,
                               unified_knowledge_cache_lock=None,
                               reference_cases_cache: dict = None,
                               reference_cases_cache_lock=None,
                               rules_context_cache: dict = None,
                               rules_context_cache_lock=None,
                               method_cards_db=None,
                               overview_context: str = "") -> tuple[dict | None, int, int]:
    """agent模式统一结果决策：Agent分析 + 经验/规则兜底。"""
    if reference_cases_cache is None:
        reference_cases_cache = {}
    if rules_context_cache is None:
        rules_context_cache = {}
    if unified_knowledge_cache is None:
        unified_knowledge_cache = {}
    canonical_query, full_query, search_query = _canonical_query_views(
        canonical_query or item.get("canonical_query") or {},
        full_query=full_query,
        search_query=search_query,
    )
    if canonical_query:
        item["canonical_query"] = canonical_query
    performance_monitor = PerformanceMonitor()
    with performance_monitor.measure("agent_reasoning_packet"):
        reasoning_packet = ReasoningAgent().build_packet(
            item,
            candidates,
            route_profile=item.get("query_route"),
            exp_backup=exp_backup,
            rule_backup=rule_backup,
        )

    reference_cases = None if unified_knowledge_retriever else _get_reference_cases_cached(
        reference_cases_cache, experience_db, full_query, province=province,
        top_k=3, specialty=item.get("specialty"),
        tolerate_error=True, default=None,
        error_prefix="参考案例获取失败（不影响Agent主流程）",
        cache_lock=reference_cases_cache_lock)
    rules_context = None if unified_knowledge_retriever else _get_agent_rules_context_cached(
        rules_context_cache, rule_kb, name, desc, province=province, top_k=3,
        cache_lock=rules_context_cache_lock)

    # 查询方法论卡片（按清单名称+专业匹配）
    relevant_cards = None
    if method_cards_db and not unified_knowledge_retriever:
        try:
            relevant_cards = method_cards_db.find_relevant(
                name, desc, specialty=item.get("specialty"),
                province=province, top_k=2)
        except Exception as e:
            logger.debug(f"方法卡片查询失败（不影响主流程）: {e}")

    with performance_monitor.measure("agent_knowledge_lookup"):
        if unified_knowledge_retriever:
            knowledge_context = _get_unified_knowledge_context_cached(
                unified_knowledge_cache,
                unified_knowledge_retriever,
                query_text=search_query or full_query or f"{name} {desc}".strip(),
                bill_name=name,
                bill_desc=desc,
                province=province,
                specialty=item.get("specialty", ""),
                unit=item.get("unit", ""),
                materials_signature=item.get("materials_signature", ""),
                cache_lock=unified_knowledge_cache_lock,
            )
            reference_cases = knowledge_context.get("reference_cases") or None
            rules_context = knowledge_context.get("rules_context") or None
            relevant_cards = knowledge_context.get("method_cards") or None
            knowledge_evidence = knowledge_context.get("knowledge_evidence") or {}
            knowledge_meta = knowledge_context.get("meta") or {}
        else:
            knowledge_evidence = {
                "reference_cases": reference_cases or [],
                "quota_rules": [],
                "quota_explanations": [],
                "method_cards": relevant_cards or [],
                "price_references": [],
            }
            knowledge_meta = {
                "reference_cases_count": len(reference_cases or []),
                "rules_context_count": len(rules_context or []),
                "method_cards_count": len(relevant_cards or []),
                "quota_rules_count": 0,
                "quota_explanations_count": 0,
                "price_references_count": 0,
            }

    quota_rules = knowledge_evidence.get("quota_rules") or []
    quota_explanations = knowledge_evidence.get("quota_explanations") or []
    prompt_knowledge_evidence, prompt_rules_context, prompt_method_cards = _split_knowledge_for_prompt(
        knowledge_evidence,
        reference_cases=reference_cases,
        rules_context=rules_context,
        method_cards=relevant_cards,
    )
    with performance_monitor.measure("agent_llm_match_single"):
        result = agent.match_single(
            bill_item=item,
            candidates=candidates,
            reference_cases=reference_cases,
            rules_context=prompt_rules_context,
            method_cards=prompt_method_cards,
            knowledge_evidence=prompt_knowledge_evidence,
            reasoning_packet=reasoning_packet,
            canonical_query=canonical_query,
            search_query=search_query,
            overview_context=overview_context,
        )
    if canonical_query:
        result["canonical_query"] = canonical_query
        result.setdefault("search_query", canonical_query.get("search_query") or search_query)
    result["reasoning_decision"] = reasoning_packet.get("decision", {})
    result["needs_reasoning"] = bool(reasoning_packet.get("decision", {}).get("is_ambiguous"))
    result["require_final_review"] = bool(
        reasoning_packet.get("decision", {}).get("require_final_review")
    )
    result["knowledge_evidence"] = knowledge_evidence
    result["knowledge_summary"] = {
        "reference_cases_count": len(reference_cases or []),
        "quota_rules_count": len(quota_rules),
        "quota_explanations_count": len(quota_explanations),
        "method_cards_count": len(relevant_cards or []),
        "price_references_count": len(knowledge_evidence.get("price_references") or []),
    }
    _append_item_review_rejection_trace(result, item)
    result["agent_stage_performance"] = {
        "stages": performance_monitor.snapshot(),
        "total": sum(performance_monitor.snapshot().values()),
    }
    _append_trace_step(
        result,
        "agent_llm",
        candidates=_summarize_candidates_for_trace(candidates),
        reference_cases_count=len(reference_cases or []),
        reference_case_ids=[str(c.get("record_id", "")) for c in (reference_cases or []) if c.get("record_id") not in (None, "")],
        rules_context_count=len(rules_context or []),
        rule_context_ids=[str(r.get("id", "")) for r in (rules_context or []) if r.get("id") not in (None, "")],
        quota_rules_count=len(quota_rules),
        quota_rule_ids=[str(r.get("id", "")) for r in quota_rules if r.get("id") not in (None, "")],
        quota_explanations_count=len(quota_explanations),
        quota_explanation_ids=[str(r.get("id", "")) for r in quota_explanations if r.get("id") not in (None, "")],
        method_cards_count=len(relevant_cards or []),
        method_card_ids=[str(c.get("id", "")) for c in (relevant_cards or []) if c.get("id") not in (None, "")],
        method_card_categories=[c.get("category", "") for c in (relevant_cards or [])],
        knowledge_evidence=knowledge_evidence,
        knowledge_summary=result.get("knowledge_summary") or {},
        knowledge_basis=result.get("knowledge_basis") or {},
        reasoning_engaged=bool(reasoning_packet.get("engaged")),
        reasoning_conflicts=reasoning_packet.get("conflict_summaries", []),
        reasoning_decision=reasoning_packet.get("decision", {}),
        reasoning_compare_points=reasoning_packet.get("compare_points", []),
        unified_knowledge_meta=knowledge_meta,
        agent_stage_performance=result.get("agent_stage_performance") or {},
        canonical_query=canonical_query,
        query_route=item.get("query_route") or {},
        batch_context=summarize_batch_context_for_trace(item),
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

def _init_search_components_legacy_broken(resolved_province: str, aux_provinces: list = None) -> tuple[HybridSearcher, ParamValidator]:
    """初始化搜索引擎与参数校验器，并做状态检查。

    如果指定了辅助定额库（aux_provinces），会为每个辅助库创建独立的搜索器，
    并按定额库类型（土建/市政/园林）挂载到主搜索器上，供 cascade_search 路由使用。
    """
    logger.info("第2步：初始化搜索引擎...")
    validator = ParamValidator()

    # 预加载所有AI模型（向量模型+Reranker，避免第一条清单处理时等待）
    try:
        from src.model_cache import ModelCache
        ModelCache.preload_all()
    except Exception as e:
        try:
            from db.sqlite import describe_db_path
            import config as _quota_config
            logger.warning(
                "经验库路径诊断: "
                f"{describe_db_path(_quota_config.get_experience_db_path())} "
                f"chroma_dir={_quota_config.get_chroma_experience_dir()}"
            )
        except Exception as diag_error:
            fallback_logger.maybe_alert(
                diag_error,
                severity="warning",
                component="match_engine.experience_path_diagnostics",
                message="Experience path diagnostics failed while handling preload failure",
            )
        logger.warning(f"模型预加载失败（不影响运行，会延迟加载）: {e}")

    # 检查引擎状态
    searcher = get_search_bundle(resolved_province, aux_provinces)
    status = searcher.get_status()
    logger.info(f"  BM25索引: {status['bm25_count']} 条定额")
    logger.info(f"  向量索引: {status['vector_count']} 条定额")

    if not status["bm25_ready"]:
        raise RuntimeError("BM25索引未就绪，请先运行: python -m src.bm25_engine")

    # ---- 辅助定额库初始化 ----
    # aux_provinces=None 时自动挂载同省同年份兄弟库；
    # 显式传 [] 视为调用方有意禁用辅助库。
    if aux_provinces is None:
        try:
            aux_provinces = config.get_sibling_provinces(resolved_province)
            if aux_provinces:
                logger.info(f"  自动挂载兄弟库: {aux_provinces}")
        except Exception as e:
            logger.warning(f"  自动发现兄弟库失败: {e}")
            aux_provinces = []

    searcher = get_search_bundle(resolved_province, aux_provinces)
    if aux_provinces:
        for aux_searcher, aux_p in zip(searcher.aux_searchers, aux_provinces):
            try:
                aux_status = aux_searcher.get_status()
                logger.info(f"  辅助定额库: {aux_p} ({aux_status['bm25_count']}条)")
            except Exception as e:
                """
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=True,
                    status="error",
                    attempt=retry_attempt,
                    max_attempts=max_retry_attempts,
                    strategy=retry_plan["strategy"],
                    strategy_source=retry_plan["source"],
                    strategy_detail=retry_plan["detail"],
                    original_search_query=search_query,
                    retry_search_query=retry_search_query,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.exception(
                    f"#{idx} Agent 查询改写重试失败，保留原结果 "
                    f"[strategy={retry_plan['strategy']}, query='{retry_search_query}']"
                )
                logger.warning(f"  杈呭姪瀹氶搴?{aux_p} 鐘舵€佹鏌ュけ璐? {e}")
                continue
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=True,
                    status="error",
                    attempt=retry_attempt,
                    max_attempts=max_retry_attempts,
                    strategy=retry_plan["strategy"],
                    strategy_source=retry_plan["source"],
                    strategy_detail=retry_plan["detail"],
                    original_search_query=search_query,
                    retry_search_query=retry_search_query,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.exception(
                    f"#{idx} Agent 查询改写重试失败，保留原结果 "
                    f"[strategy={retry_plan['strategy']}, query='{retry_search_query}']"
                )
                logger.warning(f"  辅助定额库 {aux_p} 状态检查失败: {e}")

                """
                logger.warning(f"  杈呭姪瀹氶搴?{aux_p} 鐘舵€佹鏌ュけ璐? {e}")

    return searcher, validator


def init_search_components(resolved_province: str, aux_provinces: list = None) -> tuple[HybridSearcher, ParamValidator]:
    """初始化搜索引擎与参数校验器，并做状态检查。

    如果指定了辅助定额库（aux_provinces），会为每个辅助库创建独立的搜索器，
    并按定额库类型（土建/市政/园林）挂载到主搜索器上，供 cascade_search 路由使用。
    """
    logger.info("第2步：初始化搜索引擎...")
    validator = ParamValidator()

    # 预加载所有AI模型（向量模型+Reranker，避免第一条清单处理时等待）
    try:
        from src.model_cache import ModelCache
        ModelCache.preload_all()
    except Exception as e:
        try:
            from db.sqlite import describe_db_path
            import config as _quota_config
            logger.warning(
                "经验库路径诊断: "
                f"{describe_db_path(_quota_config.get_experience_db_path())} "
                f"chroma_dir={_quota_config.get_chroma_experience_dir()}"
            )
        except Exception as diag_error:
            fallback_logger.maybe_alert(
                diag_error,
                severity="warning",
                component="match_engine.experience_path_diagnostics",
                message="Experience path diagnostics failed while handling preload failure",
            )
        logger.warning(f"模型预加载失败（不影响运行，会延迟加载）: {e}")

    # 检查引擎状态
    searcher = get_search_bundle(resolved_province, aux_provinces)
    status = searcher.get_status()
    logger.info(f"  BM25索引: {status['bm25_count']} 条定额")
    logger.info(f"  向量索引: {status['vector_count']} 条定额")

    if not status["bm25_ready"]:
        raise RuntimeError("BM25索引未就绪，请先运行: python -m src.bm25_engine")

    # ---- 辅助定额库初始化 ----
    # aux_provinces=None 时自动挂载同省同年份兄弟库；
    # 显式传 [] 视为调用方有意禁用辅助库。
    if aux_provinces is None:
        try:
            aux_provinces = config.get_sibling_provinces(resolved_province)
            if aux_provinces:
                logger.info(f"  自动挂载兄弟库: {aux_provinces}")
        except Exception as e:
            logger.warning(f"  自动发现兄弟库失败: {e}")
            aux_provinces = []

    searcher = get_search_bundle(resolved_province, aux_provinces)
    if aux_provinces:
        for aux_searcher, aux_p in zip(searcher.aux_searchers, aux_provinces):
            try:
                aux_status = aux_searcher.get_status()
                logger.info(f"  辅助定额库: {aux_p} ({aux_status['bm25_count']}条)")
            except Exception as e:
                logger.warning(f"  辅助定额库{aux_p} 状态检查失败: {e}")
                continue

    return searcher, validator


def init_experience_db(no_experience: bool, province: str = None) -> 'ExperienceDB | None':
    """按配置初始化经验库（可选）。"""
    experience_db = None
    if no_experience:
        return experience_db
    try:
        experience_db = get_experience_db(province=province)
        exp_total = experience_db.get_total_count_fast(province=province)
        exp_stats = {"total": exp_total}
        logger.info(f"  经验库: {exp_stats['total']} 条历史记录")
    except Exception as e:
        logger.warning(f"经验库加载失败，将跳过经验库: {e}")
        experience_db = None
    return experience_db


def _get_reference_cases(experience_db, full_query: str, province: str = None,
                         top_k: int = 3, specialty: str = None,
                         tolerate_error: bool = False,
                         default=None, error_prefix: str = "参考案例获取失败（不影响主流程）") -> list[dict]:
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
                                error_prefix: str = "参考案例获取失败（不影响主流程）",
                                cache_lock=None) -> list[dict]:
    """带缓存获取经验案例，减少重复查询。specialty传入后同专业优先。"""
    from src.utils import cached_with_key_lock
    key = (province or "", full_query, top_k, specialty or "", tolerate_error)
    return cached_with_key_lock(
        cache, key,
        lambda: _get_reference_cases(
            experience_db, full_query, province=province, top_k=top_k,
            specialty=specialty, tolerate_error=tolerate_error, default=default,
            error_prefix=error_prefix),
        cache_lock)


def _get_agent_rules_context(rule_kb, name: str, desc: str, province: str = None,
                             top_k: int = 3) -> list[dict] | None:
    """Agent模式获取规则上下文（失败时降级继续）。"""
    if not rule_kb:
        return None
    try:
        return rule_kb.search_rules(f"{name} {desc}", top_k=top_k, province=province)
    except Exception as e:
        logger.debug(f"规则上下文获取失败（不影响Agent主流程）: {e}")
        return None


def _get_agent_rules_context_cached(cache: dict, rule_kb, name: str, desc: str,
                                    province: str = None, top_k: int = 3,
                                    cache_lock=None) -> list[dict] | None:
    """带缓存获取规则上下文，减少重复检索。"""
    from src.utils import cached_with_key_lock
    key = (province or "", name, desc, top_k)
    return cached_with_key_lock(
        cache, key,
        lambda: _get_agent_rules_context(rule_kb, name, desc, province=province, top_k=top_k),
        cache_lock)


def _get_unified_knowledge_context(retriever, *, query_text: str, bill_name: str,
                                   bill_desc: str, province: str = None,
                                   specialty: str = "", unit: str = "",
                                   materials_signature: str = "") -> dict:
    if not retriever:
        return {
            "reference_cases": None,
            "rules_context": None,
            "method_cards": None,
            "knowledge_evidence": {},
            "meta": {},
        }
    try:
        return retriever.search_context(
            query_text=query_text,
            bill_name=bill_name,
            bill_desc=bill_desc,
            province=province,
            specialty=specialty,
            unit=unit,
            materials_signature=materials_signature,
        )
    except Exception as e:
        logger.debug(f"统一知识入口检索失败（不影响主流程）: {e}")
        return {
            "reference_cases": None,
            "rules_context": None,
            "method_cards": None,
            "knowledge_evidence": {},
            "meta": {},
        }


def _get_unified_knowledge_context_cached(cache: dict, retriever, *,
                                          query_text: str, bill_name: str,
                                          bill_desc: str, province: str = None,
                                          specialty: str = "", unit: str = "",
                                          materials_signature: str = "",
                                          cache_lock=None) -> dict:
    from src.utils import cached_with_key_lock
    key = (province or "", query_text, bill_name, bill_desc, specialty, unit, materials_signature)
    return cached_with_key_lock(
        cache, key,
        lambda: _get_unified_knowledge_context(
            retriever, query_text=query_text, bill_name=bill_name,
            bill_desc=bill_desc, province=province, specialty=specialty,
            unit=unit, materials_signature=materials_signature),
        cache_lock)


def _load_rule_kb(province: str = None) -> 'RuleKnowledge | None':
    """Agent模式按需加载规则知识库，失败时降级为None。"""
    try:
        from src.rule_knowledge import RuleKnowledge
        rule_kb = RuleKnowledge(province=province)
        return rule_kb if rule_kb.get_stats()["total"] > 0 else None
    except Exception as e:
        logger.debug(f"规则知识库不可用（Agent模式降级继续）: {e}")
        return None


def _create_rule_validator_and_reranker(province: str = None) -> 'tuple[RuleValidator, Reranker]':
    """统一创建规则校验器和Reranker。"""
    return get_rule_bundle(province=province)


# ============================================================
# 核心匹配函数
# ============================================================

def match_search_only(bill_items: list[dict], searcher: HybridSearcher,
                      validator: ParamValidator,
                      experience_db=None,
                      province: str = None,
                      progress_callback=None) -> list[dict]:
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
    if experience_db:
        searcher.set_experience_db(experience_db)
    total = len(bill_items)
    search_lightweight_prep = bool(getattr(config, "SEARCH_LIGHTWEIGHT_PREP_ENABLED", True))
    search_rule_prematch_enabled = bool(getattr(config, "SEARCH_RULE_PREMATCH_ENABLED", False))
    include_prior_candidates = bool(getattr(config, "SEARCH_PRIOR_CANDIDATES_LIGHTWEIGHT", False))
    progress_log_interval = max(1, int(getattr(config, "SEARCH_PROGRESS_LOG_INTERVAL", 10) or 10))
    search_log_types = {"rule_direct"}
    if search_lightweight_prep:
        search_log_types.add("experience_exact")

    # 进度回调辅助（30%~90% 之间线性映射）
    def _notify_progress(current_idx, result=None):
        if progress_callback:
            try:
                pct = 30 + int(60 * current_idx / max(total, 1))
                progress_callback(pct, current_idx, f"匹配中 {current_idx}/{total}", result=result)
            except Exception:
                pass

    # 同文件一致性先验：记录已高置信匹配的"短名称→定额族"映射
    # 当后续遇到同名短名称时，把之前匹配好的定额关键词作为搜索提示
    consistency_memory = {}  # {(清单名称, 专业): "定额族关键词"}

    for idx, item in enumerate(bill_items, start=1):
        performance_monitor = PerformanceMonitor()
        # 同文件一致性注入：如果之前同名同专业清单已高置信匹配，给短名称补提示
        item_name = item.get("name", "")
        memory_key = (item_name, item.get("specialty", ""))
        if memory_key in consistency_memory and item.get("_is_ambiguous_short"):
            family_kw = consistency_memory[memory_key]
            hints = item.get("_context_hints", [])
            if family_kw not in hints:
                item.setdefault("_context_hints", []).append(family_kw)

        consumed, exp_hits, rule_hits, prepared_bundle = _prepare_match_iteration(
            item=item,
            idx=idx,
            total=total,
            results=results,
            exp_hits=exp_hits,
            rule_hits=rule_hits,
            experience_db=experience_db,
            rule_validator=rule_validator,
            province=province,
            exact_exp_direct=search_lightweight_prep,
            searcher=searcher,
            reranker=reranker,
            validator=validator,
            interval=progress_log_interval,
            log_types=search_log_types,
            lightweight_experience=search_lightweight_prep,
            lightweight_rule_prematch=not search_rule_prematch_enabled,
            include_prior_candidates=include_prior_candidates,
            performance_monitor=performance_monitor,
        )
        if consumed:
            _attach_performance_snapshot(
                results[-1] if results else None,
                performance_monitor,
                idx=idx,
                total=total,
            )
            _notify_progress(idx, result=results[-1] if results else None)
            # 经验库/规则直通的结果也更新一致性记忆
            if results:
                _update_consistency_memory(consistency_memory, item, results[-1])
            continue

        _, _, _, candidates, exp_backup, rule_backup = prepared_bundle

        result, exp_hits, rule_hits = _resolve_search_mode_result(
            item, candidates, exp_backup, rule_backup, exp_hits, rule_hits)
        _attach_performance_snapshot(
            result,
            performance_monitor,
            idx=idx,
            total=total,
        )

        _append_search_result_and_log(
            results, result, idx, total, exp_hits, rule_hits,
            interval=progress_log_interval)
        _update_consistency_memory(consistency_memory, item, result)
        _notify_progress(idx, result=result)

    _log_exp_rule_summary(exp_hits, rule_hits, total)

    # 纯匹配耗时统计（不含模型加载）
    match_elapsed = time.time() - match_start_time
    n = len(bill_items)
    if n > 0:
        per_item_sec = match_elapsed / n
        logger.info(f"纯匹配耗时: {match_elapsed:.1f}秒 ({per_item_sec:.2f}秒/条, 共{n}条)")

    # L3 一致性反思：同类清单定额一致性检查
    try:
        from src.consistency_checker import check_and_fix
        results = check_and_fix(results)
    except Exception as e:
        logger.warning(f"L3一致性反思跳过（不影响输出）: {e}")

    _append_consistency_review_trace(results)
    _snapshot_stage_top1(results, "pre_final_validator_top1_id")
    FinalValidator(
        province=province,
        auto_correct=bool(getattr(config, "FINAL_VALIDATOR_AUTO_CORRECT", False)),
    ).validate_results(results)
    _mark_stage_top1_change(results, "final_validator", base_top1_field="pre_final_validator_top1_id")

    for result in results:
        result["post_final_top1_id"] = _top_quota_id(result)
        _append_trace_step(
            result,
            "final_validate",
            final_source=result.get("match_source", ""),
            final_confidence=result.get("confidence", 0),
            post_final_top1_id=result.get("post_final_top1_id", ""),
            final_changed_by=result.get("final_changed_by", ""),
            final_validation=result.get("final_validation", {}),
            final_review_correction=result.get("final_review_correction", {}),
            batch_context=summarize_batch_context_for_trace(result.get("bill_item") or {}),
        )
        _finalize_trace(result)

    return results


def match_agent(bill_items: list[dict], searcher: HybridSearcher,
                validator: ParamValidator,
                experience_db=None, llm_type: str = None,
                province: str = None,
                project_overview: str = "",
                progress_callback=None) -> list[dict]:
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

    bootstrap_timings_ms = {}

    # 初始化Agent（使用指定的或config中配置的大模型）
    agent_llm = llm_type or config.AGENT_LLM
    _bootstrap_started_at = time.perf_counter()
    agent = AgentMatcher(llm_type=agent_llm, province=province)
    bootstrap_timings_ms["agent_matcher"] = (time.perf_counter() - _bootstrap_started_at) * 1000

    # 初始化方法卡片（从经验中提炼的选定额方法论，注入Agent Prompt）
    # L6: prompt 注入可关闭，但统一知识链仍应持续检索方法卡。
    method_cards_db = None
    _bootstrap_started_at = time.perf_counter()
    try:
        mc = get_method_cards_db()
        mc_stats = mc.get_stats()
        if mc_stats["total_cards"] > 0:
            method_cards_db = mc
            logger.info(f"方法卡片知识源已加载: {mc_stats['total_cards']}张")
    except Exception as e:
        logger.debug(f"方法卡片加载跳过（不影响主流程）: {e}")
    bootstrap_timings_ms["method_cards"] = (time.perf_counter() - _bootstrap_started_at) * 1000
    if not getattr(config, "AGENT_METHOD_CARDS_IN_PROMPT", True):
        logger.info("L6: 方法卡片 prompt 注入已关闭（统一知识检索仍启用）")

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
    # L6: prompt 注入可关闭，但统一知识链仍应持续检索规则/解释。
    _bootstrap_started_at = time.perf_counter()
    rule_kb = _load_rule_kb(province=province)
    bootstrap_timings_ms["rule_kb"] = (time.perf_counter() - _bootstrap_started_at) * 1000
    if not getattr(config, "AGENT_RULES_IN_PROMPT", True):
        logger.info("L6: 规则知识 prompt 注入已关闭（统一知识检索仍启用）")
    unified_knowledge_retriever = None
    unified_data_layer = None
    _bootstrap_started_at = time.perf_counter()
    try:
        unified_data_layer = get_unified_data_layer(
            province=province,
            experience_db=experience_db,
        )
    except Exception as e:
        logger.debug(f"统一数据层加载跳过（不影响主流程）: {e}")
        unified_data_layer = None
    bootstrap_timings_ms["unified_data_layer"] = (time.perf_counter() - _bootstrap_started_at) * 1000
    _bootstrap_started_at = time.perf_counter()
    if experience_db or rule_kb or method_cards_db or unified_data_layer:
        try:
            from src.unified_knowledge import UnifiedKnowledgeRetriever
            unified_knowledge_retriever = UnifiedKnowledgeRetriever(
                province=province,
                experience_db=experience_db,
                rule_kb=rule_kb,
                method_cards_db=method_cards_db,
                unified_data_layer=unified_data_layer,
            )
        except Exception as e:
            logger.debug(f"统一知识入口加载失败（降级继续）: {e}")
            unified_knowledge_retriever = None
    bootstrap_timings_ms["unified_knowledge_retriever"] = (time.perf_counter() - _bootstrap_started_at) * 1000
    unified_knowledge_cache = {}
    unified_knowledge_cache_lock = threading.Lock()
    reference_cases_cache = {}
    reference_cases_cache_lock = threading.Lock()
    rules_context_cache = {}
    rules_context_cache_lock = threading.Lock()

    bootstrap_total_ms = sum(bootstrap_timings_ms.values())
    bootstrap_summary = ", ".join(
        f"{name}={elapsed:.1f}ms" for name, elapsed in bootstrap_timings_ms.items()
    )
    logger.info(
        f"Agent模式启动，大模型: {agent_llm}，LLM并发数: {config.LLM_CONCURRENT}"
    )
    logger.info(
        f"agent_bootstrap_summary: total={bootstrap_total_ms:.1f}ms, {bootstrap_summary}"
    )

    # 表级匹配统计（用于构建上下文摘要传给LLM，帮助保持同类清单一致性）
    match_stats = {}  # {"清单名称片段 → 定额编号": 计数}
    match_stats_lock = threading.Lock()
    project_context_summary = build_project_context(bill_items)

    def _build_overview_context(current_item: dict | None = None) -> str:
        """合并项目概览 + 批次主题 + 已处理项统计，构建完整的上下文摘要"""
        with match_stats_lock:
            sorted_stats = sorted(match_stats.items(), key=lambda x: x[1], reverse=True)[:5]
            stat_lines = [f"{desc}: {count}条" for desc, count in sorted_stats]

        return format_overview_context(
            item=current_item,
            project_context=project_context_summary,
            project_overview=project_overview,
            match_stats=stat_lines,
        )

    def _update_match_stats(result: dict):
        """从匹配结果中更新统计"""
        bill_item_info = result.get("bill_item", {})
        bill_name = bill_item_info.get("name", "")[:10]  # 取前10字作为摘要
        quotas = result.get("quotas", [])
        if quotas and bill_name:
            main_id = quotas[0].get("quota_id", "")
            main_name = quotas[0].get("name", "")[:15]
            key = f"{bill_name} → {main_id}({main_name})"
            with match_stats_lock:
                match_stats[key] = match_stats.get(key, 0) + 1

    # ========== 第1阶段：串行搜索 + 快通道 ==========
    # 收集需要LLM的条目
    llm_tasks = []  # [{idx,item,candidates,canonical_query,name,desc,exp_backup,rule_backup,is_audit}]
    _consumed_buf = []  # _prepare_match_iteration 会往这里 append 消耗掉的结果
    total = len(bill_items)

    def _make_llm_task(*, idx: int, item: dict, candidates: list[dict], canonical_query: dict,
                       name: str, desc: str, exp_backup: dict, rule_backup: dict,
                       is_audit: bool,
                       performance_monitor: PerformanceMonitor | None = None) -> dict:
        canonical_query, validation_query, resolved_search_query = _canonical_query_views(canonical_query)
        return {
            "idx": idx,
            "item": item,
            "candidates": candidates,
            "canonical_query": canonical_query,
            "full_query": validation_query,
            "search_query": resolved_search_query,
            "name": name,
            "desc": desc,
            "exp_backup": exp_backup,
            "rule_backup": rule_backup,
            "is_audit": is_audit,
            "performance_monitor": performance_monitor,
            "retry_attempts": 0,
        }

    # 进度回调辅助（第1阶段: 30%~60%, 第2阶段: 60%~90%）
    def _notify_progress(current_idx, phase=1, phase_total=None):
        if not progress_callback:
            return
        try:
            if phase == 1:
                # 第1阶段: 30% ~ 60%
                pct = 30 + int(30 * current_idx / max(total, 1))
                progress_callback(pct, current_idx, f"搜索中 {current_idx}/{total}")
            else:
                # 第2阶段: 60% ~ 90%
                pt = phase_total or 1
                pct = 60 + int(30 * current_idx / max(pt, 1))
                base_completed = max(total - pt, 0)
                overall_completed = min(total, base_completed + current_idx)
                if pt < total:
                    message = f"AI分析中 {overall_completed}/{total} (AI阶段 {current_idx}/{pt})"
                else:
                    message = f"AI分析中 {overall_completed}/{total}"
                progress_callback(pct, overall_completed, message)
        except Exception:
            pass

    # 同文件一致性先验（agent模式同样适用）
    consistency_memory_agent = {}

    for idx, item in enumerate(bill_items, start=1):
        performance_monitor = PerformanceMonitor()
        # 同文件一致性注入
        item_name = item.get("name", "")
        memory_key = (item_name, item.get("specialty", ""))
        if memory_key in consistency_memory_agent and item.get("_is_ambiguous_short"):
            family_kw = consistency_memory_agent[memory_key]
            hints = item.get("_context_hints", [])
            if family_kw not in hints:
                item.setdefault("_context_hints", []).append(family_kw)

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
            performance_monitor=performance_monitor,
        )
        if consumed:
            # 经验库/规则直通命中，结果在 _consumed_buf 最后一条
            _attach_performance_snapshot(
                _consumed_buf[-1],
                performance_monitor,
                idx=idx,
                total=total,
            )
            results_by_idx[idx] = _consumed_buf[-1]
            _update_match_stats(_consumed_buf[-1])
            _update_consistency_memory(consistency_memory_agent, item, _consumed_buf[-1])
            _notify_progress(idx, phase=1)
            continue

        ctx, full_query, search_query, candidates, exp_backup, rule_backup = prepared_bundle
        canonical_query = _canonical_query_payload(
            ctx,
            full_query=full_query,
            search_query=search_query,
            item=item,
        )
        name = ctx["name"]
        desc = ctx["desc"]
        item["query_route"] = ctx.get("query_route")
        item["canonical_query"] = canonical_query

        fastpath_decision = None
        try:
            fastpath_decision = get_fastpath_decision(
                candidates,
                exp_backup=exp_backup,
                rule_backup=rule_backup,
                route_profile=ctx.get("query_route"),
                adaptive_strategy=item.get("adaptive_strategy"),
            )
            should_skip_agent = bool(fastpath_decision and fastpath_decision.can_fastpath)
        except TypeError:
            should_skip_agent = _should_skip_agent_llm(
                candidates,
                exp_backup=exp_backup,
                rule_backup=rule_backup,
                adaptive_strategy=item.get("adaptive_strategy"),
            )

        if should_skip_agent:
            fast_result, exp_hits, rule_hits = _resolve_search_mode_result(
                item, candidates, exp_backup, rule_backup, exp_hits, rule_hits)
            _mark_agent_fastpath(fast_result)
            _attach_performance_snapshot(
                fast_result,
                performance_monitor,
                idx=idx,
                total=total,
            )
            fastpath_hits += 1
            results_by_idx[idx] = fast_result
            _update_match_stats(fast_result)
            _update_consistency_memory(consistency_memory_agent, item, fast_result)

            # 质量护栏：优先抽检高风险快通道，其余按比例抽检（收集到LLM任务里一起并发）
            if _should_audit_fastpath(fastpath_decision):
                fastpath_audit_total += 1
                llm_tasks.append(_make_llm_task(
                    idx=idx,
                    item=item,
                    candidates=candidates,
                    canonical_query=canonical_query,
                    name=name,
                    desc=desc,
                    exp_backup=exp_backup,
                    rule_backup=rule_backup,
                    is_audit=True,
                    performance_monitor=performance_monitor,
                ))
        else:
            # 需要LLM分析
            llm_tasks.append(_make_llm_task(
                idx=idx,
                item=item,
                candidates=candidates,
                canonical_query=canonical_query,
                name=name,
                desc=desc,
                exp_backup=exp_backup,
                rule_backup=rule_backup,
                is_audit=False,
                performance_monitor=performance_monitor,
            ))

        _notify_progress(idx, phase=1)

    logger.info(f"第1阶段完成: 快通道{fastpath_hits}条, 需LLM{len(llm_tasks)}条")

    # ========== 第2阶段：并发LLM调用 ==========
    if llm_tasks:
        individual_tasks = llm_tasks

        def _maybe_retry_low_confidence(result, task, overview_ctx=None):
            """低置信度/AI推荐不在候选时，用AI建议的搜索词重搜+重选。

            批量模式和逐条模式共用此函数，修复批量模式跳过重试的流程漏洞。
            返回: (result, exp_hits, rule_hits) — 可能是改善后的结果或原结果
            """
            idx = task["idx"]
            item = task["item"]
            candidates = task["candidates"]
            name = task["name"]
            desc = task["desc"]
            exp_backup = task["exp_backup"]
            rule_backup = task["rule_backup"]
            is_audit = task["is_audit"]
            canonical_query, full_query, search_query = _canonical_query_views(
                task.get("canonical_query"),
                full_query=task.get("full_query", ""),
                search_query=task.get("search_query", ""),
            )
            if is_audit or not candidates:
                return result, 0, 0

            retry_threshold = getattr(config, "LOW_CONFIDENCE_RETRY_THRESHOLD", 70)
            max_retry_attempts = min(
                1, max(0, int(getattr(config, "LOW_CONFIDENCE_RETRY_MAX_ATTEMPTS", 1)))
            )
            current_retry_attempts = int(task.get("retry_attempts", 0) or 0)
            confidence = result.get("confidence", 100)
            ai_not_found = result.get("_ai_recommended_not_found", False)
            retry_reason = "AI推荐定额不在候选中" if ai_not_found else f"置信度{confidence}<{retry_threshold}"

            # LLM已熔断时跳过重试（避免放大失败开销）
            if hasattr(agent, "is_circuit_open"):
                llm_circuit_open = bool(agent.is_circuit_open())
            else:
                llm_circuit_open = bool(getattr(agent, "_llm_circuit_open", False))

            need_retry = (confidence < retry_threshold) or ai_not_found
            if not need_retry:
                return result, 0, 0
            if current_retry_attempts >= max_retry_attempts:
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=False,
                    status="skipped_retry_budget_exhausted",
                    attempt=current_retry_attempts,
                    max_attempts=max_retry_attempts,
                    original_search_query=search_query,
                )
                return result, 0, 0
            if llm_circuit_open and False:
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=False,
                    status="skipped_llm_circuit_open",
                    attempt=current_retry_attempts,
                    max_attempts=max_retry_attempts,
                    original_search_query=search_query,
                )
                return result, 0, 0

            # 优先用AI建议的搜索词，其次用AI推荐的定额编号，最后用原始query
            retry_plan = _select_agent_retry_strategy(
                canonical_query=canonical_query,
                validation_query=full_query,
                search_query=search_query,
                result=result,
                ai_not_found=ai_not_found,
            )
            if not retry_plan:
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=False,
                    status="skipped_no_strategy",
                    attempt=current_retry_attempts,
                    max_attempts=max_retry_attempts,
                    original_search_query=search_query,
                )
                return result, 0, 0
            if llm_circuit_open and retry_plan.get("source") == "llm":
                _attach_agent_retry_trace(
                    result,
                    trigger=retry_reason,
                    attempted=False,
                    status="skipped_llm_circuit_open",
                    attempt=current_retry_attempts,
                    max_attempts=max_retry_attempts,
                    original_search_query=search_query,
                    strategy=retry_plan.get("strategy"),
                    strategy_source=retry_plan.get("source"),
                )
                return result, 0, 0

            retry_query = retry_plan["query"]
            retry_canonical_query = dict(canonical_query or {})
            retry_canonical_query["search_query"] = retry_query
            retry_canonical_query, retry_validation_query, retry_search_query = _canonical_query_views(
                retry_canonical_query,
                full_query=full_query,
                search_query=retry_query,
            )
            retry_reason = "AI推荐定额不在候选中" if ai_not_found else f"置信度{confidence}<{retry_threshold}"
            logger.info(f"#{idx} {retry_reason}，触发AI引导重试搜索: '{retry_search_query}'")

            ctx = overview_ctx or _build_overview_context(item)
            task_exp_hits, task_rule_hits = 0, 0
            retry_attempt = current_retry_attempts + 1

            try:
                # 全库搜索（不限册号），增加候选数
                retry_candidates = searcher.search(
                    retry_search_query, top_k=config.HYBRID_TOP_K + 5, books=None)
                if retry_candidates and len(retry_candidates) > 1:
                    retry_candidates = reranker.rerank(retry_search_query, retry_candidates)
                if retry_candidates:
                    retry_candidates = validator.validate_candidates(
                        retry_validation_query, retry_candidates, supplement_query=retry_search_query)
                if retry_candidates:
                    # 合并原候选和重试候选，按quota_id去重，重复ID保留param_score更高的
                    seen_ids = {}
                    for c in candidates:
                        qid = c.get("quota_id", "")
                        if qid:
                            seen_ids[qid] = c
                    new_added = 0
                    for c in retry_candidates:
                        qid = c.get("quota_id", "")
                        if not qid:
                            continue
                        if qid not in seen_ids:
                            seen_ids[qid] = c
                            new_added += 1
                        else:
                            # 重复ID保留param_score更高的
                            old_score = seen_ids[qid].get("param_score", 0)
                            new_score = c.get("param_score", 0)
                            if new_score > old_score:
                                seen_ids[qid] = c
                    if not seen_ids:
                        _attach_agent_retry_trace(
                            result,
                            trigger=retry_reason,
                            attempted=True,
                            status="empty_merged_candidates",
                            attempt=retry_attempt,
                            max_attempts=max_retry_attempts,
                            strategy=retry_plan["strategy"],
                            strategy_source=retry_plan["source"],
                            strategy_detail=retry_plan["detail"],
                            original_search_query=search_query,
                            retry_search_query=retry_search_query,
                        )
                        logger.debug(f"#{idx} 候选融合后为空，跳过重试")
                    else:
                        # 按param_score降序排列，取前20条
                        merged = sorted(seen_ids.values(),
                                        key=lambda x: (x.get("param_tier", 1),
                                                       x.get("param_score", 0)),
                                        reverse=True)[:20]
                        logger.info(f"#{idx} 候选融合: 原{len(candidates)}+新增{new_added}=合并{len(merged)}条")
                        # 用合并候选重新调用LLM
                        retry_result, r_exp, r_rule = _resolve_agent_mode_result(
                            agent=agent, item=item, candidates=merged,
                            experience_db=experience_db, canonical_query=retry_canonical_query, rule_kb=rule_kb,
                            name=name, desc=desc, exp_backup=exp_backup,
                            rule_backup=rule_backup, exp_hits=0, rule_hits=0,
                            full_query=retry_validation_query,
                            search_query=retry_search_query,
                            province=province,
                            unified_knowledge_retriever=unified_knowledge_retriever,
                            unified_knowledge_cache=unified_knowledge_cache,
                            unified_knowledge_cache_lock=unified_knowledge_cache_lock,
                            reference_cases_cache=reference_cases_cache,
                            reference_cases_cache_lock=reference_cases_cache_lock,
                            rules_context_cache=rules_context_cache,
                            rules_context_cache_lock=rules_context_cache_lock,
                            method_cards_db=method_cards_db,
                            overview_context=ctx,
                        )
                        retry_conf = retry_result.get("confidence", 0)
                        _attach_agent_retry_trace(
                            retry_result,
                            trigger=retry_reason,
                            attempted=True,
                            status="completed" if retry_conf > confidence else "not_improved",
                            attempt=retry_attempt,
                            max_attempts=max_retry_attempts,
                            strategy=retry_plan["strategy"],
                            strategy_source=retry_plan["source"],
                            strategy_detail=retry_plan["detail"],
                            original_search_query=search_query,
                            retry_search_query=retry_search_query,
                            confidence_before=confidence,
                            confidence_after=retry_conf,
                        )
                        if retry_conf > confidence:
                            logger.info(f"#{idx} Agent 重试成功: {confidence}->{retry_conf}")
                            logger.info(f"#{idx} AI引导重试成功: {confidence}→{retry_conf}")
                            return retry_result, r_exp, r_rule
                        _attach_agent_retry_trace(
                            result,
                            trigger=retry_reason,
                            attempted=True,
                            status="not_improved",
                            attempt=retry_attempt,
                            max_attempts=max_retry_attempts,
                            strategy=retry_plan["strategy"],
                            strategy_source=retry_plan["source"],
                            strategy_detail=retry_plan["detail"],
                            original_search_query=search_query,
                            retry_search_query=retry_search_query,
                            confidence_before=confidence,
                            confidence_after=retry_conf,
                        )
            except Exception as e:
                logger.debug(f"#{idx} AI引导重试失败（保留原结果）: {e}")

            return result, task_exp_hits, task_rule_hits

        # ===== ??LLM?? =====
        def _process_llm_task(task):
            """单个LLM任务处理（线程安全）"""
            idx = task["idx"]
            item = task["item"]
            candidates = task["candidates"]
            name = task["name"]
            desc = task["desc"]
            exp_backup = task["exp_backup"]
            rule_backup = task["rule_backup"]
            is_audit = task["is_audit"]
            canonical_query, full_query, search_query = _canonical_query_views(
                task.get("canonical_query"),
                full_query=task.get("full_query", ""),
                search_query=task.get("search_query", ""),
            )
            # 构建表级上下文摘要（在LLM调用前快照，线程安全）
            ctx_summary = _build_overview_context(item)
            ctx_summary = _build_overview_context(item)
            result, task_exp_hits, task_rule_hits = _resolve_agent_mode_result(
                agent=agent,
                item=item,
                candidates=candidates,
                experience_db=experience_db,
                canonical_query=canonical_query,
                rule_kb=rule_kb,
                name=name,
                desc=desc,
                exp_backup=exp_backup,
                rule_backup=rule_backup,
                exp_hits=0,
                rule_hits=0,
                full_query=full_query,
                search_query=search_query,
                province=province,
                unified_knowledge_retriever=unified_knowledge_retriever,
                unified_knowledge_cache=unified_knowledge_cache,
                unified_knowledge_cache_lock=unified_knowledge_cache_lock,
                reference_cases_cache=reference_cases_cache,
                reference_cases_cache_lock=reference_cases_cache_lock,
                rules_context_cache=rules_context_cache,
                rules_context_cache_lock=rules_context_cache_lock,
                method_cards_db=method_cards_db,
                overview_context=ctx_summary,
            )
            # 低置信度重试（复用提取的公共函数）
            result, retry_exp, retry_rule = _maybe_retry_low_confidence(
                result, task, overview_ctx=ctx_summary)
            if retry_exp:
                task_exp_hits = retry_exp
            if retry_rule:
                task_rule_hits = retry_rule

            return idx, result, task_exp_hits, task_rule_hits, is_audit

        # 并发执行逐条LLM任务（L6: 只处理individual_tasks，批量已在上面处理）
        concurrent = max(1, config.LLM_CONCURRENT)
        if individual_tasks:
            logger.info(f"第2阶段逐条: {len(individual_tasks)}条LLM任务，{concurrent}路并发")

        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            futures = {pool.submit(_process_llm_task, task): task for task in individual_tasks}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                task = futures[future]
                idx = task["idx"]
                item = task["item"]
                candidates = task["candidates"]
                exp_backup = task["exp_backup"]
                rule_backup = task["rule_backup"]
                is_audit = task["is_audit"]
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
                        if completed % 10 == 0 or completed == len(individual_tasks):
                            logger.info(f"LLM进度: {completed}/{len(individual_tasks)}")
                        _notify_progress(completed, phase=2, phase_total=len(individual_tasks))
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

                _attach_performance_snapshot(
                    result,
                    task.get("performance_monitor"),
                    idx=idx,
                    total=total,
                )
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
                    _update_match_stats(result)
                    exp_hits += task_exp_hits
                    rule_hits += task_rule_hits
                    agent_hits += 1

                if completed % 10 == 0 or completed == len(individual_tasks):
                    logger.info(f"LLM进度: {completed}/{len(individual_tasks)}")
                _notify_progress(completed, phase=2, phase_total=len(individual_tasks))

    # ========== 组装最终结果（按原始顺序）==========
    results = []
    for idx in range(1, len(bill_items) + 1):
        if idx in results_by_idx:
            _finalize_trace(results_by_idx[idx])
            results.append(results_by_idx[idx])
        else:
            # 兜底：idx缺失时生成空结果，防止清单被静默丢弃
            item = bill_items[idx - 1] if idx <= len(bill_items) else {}
            item_name = item.get("name", f"#{idx}")
            logger.warning(f"#{idx} [{item_name}] 缺失匹配结果，生成兜底空结果")
            fallback = {
                "bill_item": item,
                "quotas": [],
                "confidence": 0,
                "explanation": "匹配过程异常，未能产生结果",
                "match_source": "missing_fallback",
                "no_match_reason": "处理过程中结果丢失",
                "candidates_count": 0,
            }
            _finalize_trace(fallback)
            results.append(fallback)

    logger.info(f"Agent匹配完成: 经验库{exp_hits}, 规则{rule_hits}, "
               f"Agent分析{agent_hits}/{len(bill_items)}条, 快速通道{fastpath_hits}条")
    if fastpath_audit_total > 0:
        audit_ok = fastpath_audit_total - fastpath_audit_mismatch
        consistency = (audit_ok * 100.0) / fastpath_audit_total
        logger.info(f"快速通道抽检: {fastpath_audit_total} 条, 不一致 {fastpath_audit_mismatch} 条, "
                   f"一致率 {consistency:.1f}%")

    # L3 一致性反思：同类清单定额一致性检查
    try:
        from src.consistency_checker import check_and_fix
        results = check_and_fix(results)
    except Exception as e:
        logger.warning(f"L3一致性反思跳过（不影响输出）: {e}")

    _append_consistency_review_trace(results)
    _snapshot_stage_top1(results, "pre_final_validator_top1_id")
    FinalValidator(
        province=province,
        auto_correct=bool(getattr(config, "FINAL_VALIDATOR_AUTO_CORRECT", False)),
    ).validate_results(results)
    _mark_stage_top1_change(results, "final_validator", base_top1_field="pre_final_validator_top1_id")

    for result in results:
        result["post_final_top1_id"] = _top_quota_id(result)
        _append_trace_step(
            result,
            "final_validate",
            final_source=result.get("match_source", ""),
            final_confidence=result.get("confidence", 0),
            post_final_top1_id=result.get("post_final_top1_id", ""),
            final_changed_by=result.get("final_changed_by", ""),
            final_validation=result.get("final_validation", {}),
            final_review_correction=result.get("final_review_correction", {}),
            batch_context=summarize_batch_context_for_trace(result.get("bill_item") or {}),
        )
        _finalize_trace(result)

    return results


# ============================================================
# 模式分派
# ============================================================

def match_by_mode(mode: str, bill_items: list[dict], searcher: HybridSearcher,
                  validator: ParamValidator, experience_db,
                  resolved_province: str, agent_llm: str = None,
                  project_overview: str = "",
                  progress_callback=None) -> list[dict]:
    """按模式执行匹配。"""
    if experience_db:
        searcher.set_experience_db(experience_db)
    if mode == "search":
        results = match_search_only(
            bill_items, searcher, validator, experience_db, province=resolved_province,
            progress_callback=progress_callback)
    elif mode == "agent":
        results = match_agent(
            bill_items, searcher, validator, experience_db,
            llm_type=agent_llm, province=resolved_province,
            project_overview=project_overview,
            progress_callback=progress_callback)
    else:
        raise ValueError(f"不支持的匹配模式: {mode}")

    # L6: 规则知识后置校验——给每条匹配结果添加相关规则提示
    _apply_rule_hints(results, bill_items, resolved_province)

    return results


def _apply_rule_hints(results: list[dict], bill_items: list[dict],
                      province: str = None) -> None:
    """给匹配结果添加规则知识提示（L6规则代码化）

    从 rule_knowledge.db 搜索相关规则，提取系数、包含/不包含等信息，
    写入 result["rule_hints"] 供输出时显示。

    这是"提醒"而非"校验"——不影响匹配结果，用户可参考或忽略。
    """
    try:
        from src.rule_post_checker import check_by_rules, format_rule_hints
    except ImportError:
        return  # 模块不存在时静默跳过

    hint_count = 0
    for result in results:
        item = result.get("bill_item", {})
        hints = check_by_rules(item, result, province=province)
        if hints:
            result["rule_hints"] = format_rule_hints(hints)
            hint_count += 1

    if hint_count > 0:
        logger.info(f"L6规则提示: {hint_count}/{len(results)}条匹配结果附带规则提示")
