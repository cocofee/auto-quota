# -*- coding: utf-8 -*-
"""Search, rule, experience, and price reconciliation helpers."""

import time

from loguru import logger

from src.confidence_utils import apply_confidence_penalty
from src.match_core import _append_trace_step
from src.param_validator import ParamValidator
from src.reason_taxonomy import merge_reason_tags
from src.text_parser import parser as text_parser

from .gates import _set_result_reason
from .reasons import _append_backup_advisory, _carry_ranking_snapshot, _result_top1_id

def _api():
    import src.match_pipeline as api

    return api

def _get_rule_injection_validator() -> ParamValidator:
    api = _api()
    if api._RULE_INJECTION_VALIDATOR is not None:
        return api._RULE_INJECTION_VALIDATOR
    with api._RULE_INJECTION_VALIDATOR_LOCK:
        if api._RULE_INJECTION_VALIDATOR is None:
            api._RULE_INJECTION_VALIDATOR = api.ParamValidator()
    return api._RULE_INJECTION_VALIDATOR


def _get_price_validator():
    api = _api()
    if api._PRICE_VALIDATOR is not None:
        return api._PRICE_VALIDATOR
    if not bool(getattr(api.config, "QUOTA_MATCH_PRICE_VALIDATION_ENABLED", False)):
        return None

    with api._PRICE_VALIDATOR_LOCK:
        if api._PRICE_VALIDATOR is not None:
            return api._PRICE_VALIDATOR

        if (
            api._PRICE_VALIDATOR_LAST_FAILURE_AT is not None
            and time.monotonic() - api._PRICE_VALIDATOR_LAST_FAILURE_AT < api._PRICE_VALIDATOR_RETRY_INTERVAL_SECONDS
        ):
            return None

        try:
            from src.price_reference_db import PriceReferenceDB
            from src.price_validator import PriceValidator

            api._PRICE_VALIDATOR = PriceValidator(PriceReferenceDB())
            api._PRICE_VALIDATOR_LAST_FAILURE_AT = None
        except Exception as exc:
            api._PRICE_VALIDATOR_LAST_FAILURE_AT = time.monotonic()
            logger.warning(f"price validator unavailable, skip price validation: {exc}")
            api._PRICE_VALIDATOR = None

    return api._PRICE_VALIDATOR


def _apply_price_validation(result: dict, item: dict, best: dict | None) -> dict:
    if not best:
        return result
    validator = _api()._get_price_validator()
    if validator is None:
        return result

    validation = validator.validate(item, best, confidence=result.get("confidence"))
    result["price_validation"] = validation
    _append_trace_step(
        result,
        "price_validate",
        status=str(validation.get("status", "")),
        message=str(validation.get("message", "")),
        sample_count=int(validation.get("sample_count", 0) or 0),
        median_price=validation.get("median_price"),
        actual_price=validation.get("actual_price"),
        confidence_penalty=validation.get("confidence_penalty", 0),
    )

    if validation.get("status") != "price_mismatch":
        return result

    previous_confidence = float(result.get("confidence", 0) or 0.0)
    penalty = float(validation.get("confidence_penalty", -10) or 0.0)
    adjusted_confidence = apply_confidence_penalty(previous_confidence, penalty)
    validation["previous_confidence"] = previous_confidence
    validation["adjusted_confidence"] = adjusted_confidence
    result["confidence"] = adjusted_confidence
    result["confidence_score"] = int(round(adjusted_confidence))
    result["reason_tags"] = merge_reason_tags(
        result.get("reason_tags") or [],
        ["price_mismatch", "manual_review"],
    )
    _set_result_reason(
        result,
        "price_mismatch",
        result.get("reason_tags") or [],
        str(validation.get("message") or "price validation mismatch"),
    )
    return result


def _rule_backup_primary_quota(rule_backup: dict) -> dict:
    quotas = (rule_backup or {}).get("quotas") or []
    if not quotas:
        return {}
    quota = quotas[0] or {}
    return quota if isinstance(quota, dict) else {}


def _promote_rule_candidate_prior(candidate: dict, candidates: list[dict]) -> dict:
    peers = list(candidates or [])
    def _median(values: list[float], default: float) -> float:
        if not values:
            return default
        values = sorted(values)
        return values[len(values) // 2]

    median_rerank = _median(
        [float(c.get("rerank_score", c.get("hybrid_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("rerank_score", candidate.get("hybrid_score", 0.0)) or 0.0),
    )
    median_hybrid = _median(
        [float(c.get("hybrid_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    median_semantic = _median(
        [float(c.get("semantic_rerank_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("semantic_rerank_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    median_spec = _median(
        [float(c.get("spec_rerank_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
        float(candidate.get("spec_rerank_score", candidate.get("rerank_score", 0.0)) or 0.0),
    )
    candidate["rerank_score"] = median_rerank
    candidate["hybrid_score"] = median_hybrid
    candidate["semantic_rerank_score"] = median_semantic
    candidate["spec_rerank_score"] = median_spec
    candidate["active_rerank_score"] = candidate["rerank_score"]
    return candidate


def _materialize_rule_backup_candidate(item: dict, rule_backup: dict, candidates: list[dict]) -> dict | None:
    quota = _rule_backup_primary_quota(rule_backup)
    quota_id = str(quota.get("quota_id", "") or "").strip()
    quota_name = str(quota.get("name", "") or "").strip()
    if not quota_id or not quota_name:
        return None

    canonical_query = (item or {}).get("canonical_query") or {}
    validation_query = str(canonical_query.get("validation_query") or item.get("name") or "").strip()
    search_query = str(canonical_query.get("search_query") or validation_query).strip()

    candidate = {
        "quota_id": quota_id,
        "name": quota_name,
        "unit": str(quota.get("unit", "") or ""),
        "id": quota.get("db_id"),
        "db_id": quota.get("db_id"),
        "match_source": "rule_injected",
        "is_rule_candidate": 1,
        "rule_confidence": float(rule_backup.get("confidence", 0) or 0.0),
        "rule_prior_score": float(rule_backup.get("confidence", 0) or 0.0) / 100.0,
        "rule_family": rule_backup.get("rule_family", ""),
        "rule_score": rule_backup.get("rule_score", 0.0),
        "rule_reason": quota.get("reason", rule_backup.get("explanation", "")),
        "candidate_canonical_features": text_parser.parse_canonical(quota_name),
    }
    candidate = _promote_rule_candidate_prior(candidate, candidates)
    validated = _get_rule_injection_validator().validate_candidates(
        validation_query,
        [candidate],
        supplement_query=search_query or None,
        bill_params=item.get("params"),
        canonical_features=item.get("canonical_features"),
        context_prior=item.get("context_prior"),
    )
    if not validated:
        return None
    injected = validated[0]
    injected["match_source"] = "rule_injected"
    injected["is_rule_candidate"] = 1
    injected["rule_confidence"] = candidate["rule_confidence"]
    injected["rule_prior_score"] = candidate["rule_prior_score"]
    injected["rule_family"] = candidate["rule_family"]
    injected["rule_score"] = candidate["rule_score"]
    injected["rule_reason"] = candidate["rule_reason"]
    return _promote_rule_candidate_prior(injected, candidates)


def _inject_rule_backup_candidate(item: dict, candidates: list[dict], rule_backup: dict) -> tuple[list[dict], str]:
    if not rule_backup:
        return list(candidates or []), ""
    quota = _rule_backup_primary_quota(rule_backup)
    quota_id = str(quota.get("quota_id", "") or "").strip()
    if not quota_id:
        return list(candidates or []), ""

    working = list(candidates or [])
    for idx, existing in enumerate(working):
        if str(existing.get("quota_id", "") or "").strip() != quota_id:
            continue
        merged = dict(existing)
        merged["match_source"] = "rule_injected"
        merged["is_rule_candidate"] = 1
        merged["rule_confidence"] = float(rule_backup.get("confidence", 0) or 0.0)
        merged["rule_prior_score"] = float(rule_backup.get("confidence", 0) or 0.0) / 100.0
        merged["rule_family"] = rule_backup.get("rule_family", "")
        merged["rule_score"] = rule_backup.get("rule_score", 0.0)
        merged["rule_reason"] = quota.get("reason", rule_backup.get("explanation", ""))
        merged = _promote_rule_candidate_prior(merged, working)
        return [merged] + [c for j, c in enumerate(working) if j != idx], quota_id

    injected = _materialize_rule_backup_candidate(item, rule_backup, working)
    if not injected:
        return working, ""
    return [injected] + working, quota_id

def _apply_rule_backup(result: dict, rule_backup: dict, rule_hits: int,
                       prefer_label: str) -> tuple[dict, int]:
    """
    低置信规则结果兜底比较：置信度更高则替换当前结果。

    prefer_label 用于日志前缀，如"搜索/经验""LLM/经验""Agent/经验"。
    """
    if not rule_backup:
        return result, rule_hits
    has_prior_knowledge = bool((result or {}).get("knowledge_evidence"))
    if (not has_prior_knowledge) and rule_backup.get("confidence", 0) > result.get("confidence", 0):
        _carry_ranking_snapshot(rule_backup, result, changed_by="rule_backup")
        _append_trace_step(
            rule_backup,
            "rule_backup_override",
            replaced_source=result.get("match_source", ""),
            replaced_confidence=result.get("confidence", 0),
        )
        return rule_backup, rule_hits + 1
    _append_backup_advisory(
        result,
        advisory_type="rule_backup",
        backup=rule_backup,
        stage="rule_backup_advisory",
    )
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
        _carry_ranking_snapshot(exp_backup, result, changed_by="experience_backup")
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
        result["post_final_top1_id"] = _result_top1_id(result)
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
        # 严格大于才替换（与相似匹配一致，等分时信任搜索+参数验证）
        if exp_conf > search_conf:
            exp_backup["confidence"] = exp_conf
            _carry_ranking_snapshot(exp_backup, result, changed_by="experience_exact")
            _append_trace_step(
                exp_backup,
                "experience_exact_degraded_override",
                degraded_confidence=exp_conf,
                search_confidence=search_conf,
            )
            logger.debug(
                f"经验库精确匹配(降级) vs 搜索: "
                f"经验{exp_conf}分 > 搜索{search_conf}分")
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

    # search 模式下的 experience_similar 只做 advisory，不覆盖已产出的搜索主结果。
    # 搜索结果已经经过当前 query 的召回和参数排序，经验相似命中只保留为辅助证据。
    if not search_qids:
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

    _append_backup_advisory(
        result,
        advisory_type="experience_similar",
        backup=exp_backup,
        stage="experience_similar_advisory",
    )
    _append_trace_step(
        result,
        "experience_similar_rejected",
        search_confidence=result.get("confidence", 0),
        backup_confidence=exp_backup.get("confidence", 0),
    )
    logger.debug(
        f"搜索结果保留主选，经验库相似命中仅作参考: "
        f"搜索{result.get('confidence', 0)}分, "
        f"经验库{exp_backup.get('confidence', 0)}分")
    return result, exp_hits


