# -*- coding: utf-8 -*-
"""Docstring omitted."""

import inspect
import json
import random
import re
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

from loguru import logger

from src.utils import safe_float

import config
from src.ambiguity_gate import analyze_ambiguity
from src.bill_item_context import BillItemContext
from src.feedback_bus import lookup_cross_province_hints, remember_cross_province_hints
from src.experience_confidence import allows_direct_pass, describe_effective_confidence
from src.text_parser import parser as text_parser
from src.hybrid_searcher import HybridSearcher
from src.candidate_canonicalizer import build_candidate_canonical_features_no_store
from src.param_validator import ParamValidator
from src.performance_monitor import PerformanceMonitor, measure_call
from src.policy_engine import PolicyEngine
from src.province_book_mapper import map_db_book_to_route_book, normalize_route_book_code
from src.specialty_classifier import detect_db_type
from src.candidate_scoring import sort_candidates_with_stage_priority
from src.confidence_calibrator import compute_confidence_score


# ============================================================
# 常量
# ============================================================

# 级联搜索最少要返回的候选数量（少于此值则扩大搜索范围）
CASCADE_MIN_CANDIDATES = 5
# 规则预匹配直通阈值（低于该值时仅作为备选，不提前截断后续流程）
RULE_DIRECT_CONFIDENCE = 80

# P2-3a: 关键词规则从 JSON 配置文件加载，硬编码值仅作后备
def _load_keyword_rules():
    """Load keyword rules from data/keyword_rules.json, fall back to hardcoded defaults."""
    import json
    from pathlib import Path
    try:
        rules_path = Path(__file__).resolve().parent.parent / "data" / "keyword_rules.json"
        if rules_path.exists():
            rules = json.loads(rules_path.read_text(encoding="utf-8"))
            mk = rules.get("measure_keywords", {})
            return (
                mk.get("weak", MEASURE_KEYWORDS_DEFAULT),
                mk.get("strong", STRONG_MEASURE_KEYWORDS_DEFAULT),
                set(mk.get("exact_names", _MEASURE_EXACT_NAMES_DEFAULT)),
                tuple(mk.get("contains", _MEASURE_CONTAINS_KEYWORDS_DEFAULT)),
            )
    except Exception:
        pass
    return (
        MEASURE_KEYWORDS_DEFAULT,
        STRONG_MEASURE_KEYWORDS_DEFAULT,
        _MEASURE_EXACT_NAMES_DEFAULT,
        _MEASURE_CONTAINS_KEYWORDS_DEFAULT,
    )

# 硬编码默认值（后备）
MEASURE_KEYWORDS_DEFAULT = [
    "操作高度增加", "超高增加",
    "特殊地区施工", "干扰增加",
]
STRONG_MEASURE_KEYWORDS_DEFAULT = [
    "施工费", "增加费", "复测费", "措施费",
    "临时设施费", "工程保险费", "进出场费",
    "赶工费", "疫情防控",
    "管理费", "利润", "税金", "规费",
    "企业管理费", "附加费",
    "小计", "合计",
    "施工脚手架", "脚手架搭拆", "脚手架费", "综合脚手架",
    "系统调整费", "系统调试费",
    "大型机械进出场", "大型机械安拆",
    "高层施工增加", "高层增加费",
    "材料补差", "材料价差", "价差调整",
    "垃圾清运", "垃圾弃置",
    "安全文明施工", "文明施工费",
    "夜间施工", "冬雨季施工",
    "二次搬运费", "已完工程及设备保护费", "暂列金额",
]
_MEASURE_EXACT_NAMES_DEFAULT = {
    "暂列金额", "暂估价", "专业工程暂估价",
    "二次搬运费", "已完工程及设备保护费",
    "总承包服务费", "预算包干费", "工程优质费",
    "现场签证费用", "税前工程造价", "总造价",
    "人工费", "概算幅度差", "索赔费用",
    "其他项目", "其他费用", "计日工",
    "地下管线交叉降效费", "增值税销项税额",
}
_MEASURE_CONTAINS_KEYWORDS_DEFAULT = (
    "二次搬运", "暂列金额", "暂估价", "已完工程及设备保护",
)

# P2-3a: 模块加载时从 JSON 读取，不可用时回退到硬编码默认值
_loaded = _load_keyword_rules()
MEASURE_KEYWORDS = _loaded[0]
STRONG_MEASURE_KEYWORDS = _loaded[1]
_MEASURE_EXACT_NAMES = _loaded[2]
_MEASURE_CONTAINS_KEYWORDS = _loaded[3]


def calculate_confidence(param_score: float, param_match: bool = True,
                         name_bonus: float = 0.0,
                         score_gap: float = 0.0,
                         rerank_score: float = 0.0,
                         family_aligned: bool = False,
                         family_hard_conflict: bool = False,
                         candidates_count: int = 20,
                         is_ambiguous_short: bool = False) -> int:
    """Docstring omitted."""
    return compute_confidence_score(
        param_score=param_score,
        param_match=param_match,
        name_bonus=name_bonus,
        score_gap=score_gap,
        rerank_score=rerank_score,
        family_aligned=family_aligned,
        family_hard_conflict=family_hard_conflict,
        candidates_count=candidates_count,
        is_ambiguous_short=is_ambiguous_short,
    )

def infer_confidence_family_alignment(candidate: dict) -> bool:
    """Docstring omitted."""
    candidate = candidate or {}
    if bool(candidate.get("family_gate_hard_conflict", False)):
        return False
    if bool(candidate.get("feature_alignment_hard_conflict", False)):
        return False
    if bool(candidate.get("logic_hard_conflict", False)):
        return False

    if safe_float(candidate.get("family_gate_score"), 0.0) >= 1.0:
        return True

    param_score = safe_float(candidate.get("param_score"), 0.0)
    name_bonus = safe_float(candidate.get("name_bonus"), 0.0)
    feature_score = safe_float(candidate.get("feature_alignment_score"), 0.0)
    feature_comp = int(candidate.get("feature_alignment_comparable_count", 0) or 0)
    feature_anchors = int(candidate.get("feature_alignment_exact_anchor_count", 0) or 0)
    context_score = safe_float(candidate.get("context_alignment_score"), 0.0)
    context_comp = int(candidate.get("context_alignment_comparable_count", 0) or 0)

    if param_score < 0.55 or name_bonus < 0.20:
        return False
    if feature_score >= 0.90 and (feature_anchors >= 2 or feature_comp >= 3):
        return True
    if feature_score >= 0.84 and feature_comp >= 2:
        return context_comp <= 0 or context_score >= 0.65
    return False


def _safe_json_materials(raw_value) -> list[dict]:
    """Normalize the materials field into list[dict]."""
    if isinstance(raw_value, list):
        return [m for m in raw_value if isinstance(m, dict)]
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, dict)]
    return []


def _annotate_experience_confidence(candidate: dict, experience_db=None) -> dict:
    if not isinstance(candidate, dict):
        return candidate
    factors = None
    calculator = getattr(experience_db, "_annotate_effective_confidence", None)
    if callable(calculator):
        try:
            annotated = calculator(candidate)
            if isinstance(annotated, dict):
                return annotated
        except Exception:
            factors = None
    try:
        factors = describe_effective_confidence(candidate)
    except Exception:
        factors = None
    if not isinstance(factors, dict):
        return candidate
    candidate["effective_confidence"] = int(factors["effective_confidence"])
    candidate["confidence_factors"] = {
        "base_confidence": int(factors["base_confidence"]),
        "time_decay": float(factors["time_decay"]),
        "reviewer_weight": float(factors["reviewer_weight"]),
        "confirm_count_weight": float(factors["confirm_count_weight"]),
    }
    return candidate


def _experience_effective_confidence(candidate: dict, experience_db=None) -> int:
    annotated = _annotate_experience_confidence(candidate, experience_db)
    return int(annotated.get("effective_confidence", annotated.get("confidence", 0)) or 0)


def _experience_allows_direct(candidate: dict, experience_db=None) -> bool:
    annotated = _annotate_experience_confidence(candidate, experience_db)
    try:
        return allows_direct_pass(annotated)
    except Exception:
        return False


def _summarize_candidates_for_trace(candidates: list[dict], top_n: int = 3) -> list[dict]:
    """Build a compact candidate summary for trace output."""
    summary = []
    for c in (candidates or [])[:top_n]:
        summary.append({
            "quota_id": c.get("quota_id", ""),
            "name": c.get("name", ""),
            "param_match": bool(c.get("param_match", True)),
            "param_score": safe_float(c.get("param_score"), 0.0),
            "rerank_score": safe_float(
                c.get("rerank_score", c.get("hybrid_score", 0)), 0.0
            ),
            "reasoning": summarize_candidate_reasoning(c),
        })
    return summary


def summarize_candidate_reasoning(candidate: dict) -> dict:
    """Extract reusable reasoning fields from a candidate."""
    candidate = candidate or {}
    reasoning = {
        "param_match": bool(candidate.get("param_match", True)),
        "param_score": safe_float(candidate.get("param_score"), 0.0),
        "param_tier": int(candidate.get("param_tier", 1) or 1),
        "name_bonus": safe_float(candidate.get("name_bonus"), 0.0),
        "rerank_score": safe_float(
            candidate.get("rerank_score", candidate.get("hybrid_score", 0)), 0.0
        ),
        "hybrid_score": safe_float(candidate.get("hybrid_score", 0), 0.0),
    }

    layers = {}
    for prefix, key in (
        ("feature_alignment", "feature"),
        ("logic", "logic"),
        ("context_alignment", "context"),
    ):
        score = candidate.get(f"{prefix}_score")
        detail = str(candidate.get(f"{prefix}_detail", "") or "").strip()
        comparable_count = int(candidate.get(f"{prefix}_comparable_count", 0) or 0)
        hard_conflict = bool(candidate.get(f"{prefix}_hard_conflict", False))
        if score is None and not detail and comparable_count <= 0 and not hard_conflict:
            continue
        layers[key] = {
            "score": safe_float(score, 0.0),
            "detail": detail,
            "comparable_count": comparable_count,
            "hard_conflict": hard_conflict,
        }
        if prefix == "logic":
            layers[key]["exact_primary_match"] = bool(
                candidate.get("logic_exact_primary_match", False)
            )

    if layers:
        reasoning["layers"] = layers

    detail = str(candidate.get("param_detail", "") or "").strip()
    if detail:
        reasoning["detail"] = detail
    return reasoning


def is_param_hard_fail_candidate(candidate: dict | None) -> bool:
    """Return True when candidate should be vetoed before ranking."""
    if not isinstance(candidate, dict):
        return False
    if bool(candidate.get("param_hard_fail")):
        return True
    if str(candidate.get("param_validation_tier", "") or "").strip() == "hard_fail":
        return True
    try:
        return int(candidate.get("param_tier", 1) or 1) == 0
    except (TypeError, ValueError):
        return False


def filter_param_hard_fail_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split validated candidates into rankable and hard-fail buckets."""
    rankable: list[dict] = []
    rejected: list[dict] = []
    for candidate in list(candidates or []):
        if is_param_hard_fail_candidate(candidate):
            rejected.append(candidate)
        else:
            rankable.append(candidate)
    return rankable, rejected


def _append_trace_step(result: dict, stage: str, **fields):
    """Append a normalized trace step to a result."""
    trace = result.get("trace")
    if not isinstance(trace, dict):
        trace = {}
    steps = trace.get("steps")
    if not isinstance(steps, list):
        steps = []
    step = {"stage": stage}
    for k, v in fields.items():
        if v is not None:
            step[k] = v
    steps.append(step)
    trace["steps"] = steps
    trace["path"] = [s.get("stage", "") for s in steps if s.get("stage")]
    trace["final_source"] = result.get("match_source", "")
    trace["final_confidence"] = safe_float(result.get("confidence"), 0.0)
    result["trace"] = trace


def _finalize_trace(result: dict):
    """Ensure every result carries a normalized trace object."""
    if not isinstance(result, dict):
        return
    recall_topk_ids = result.get("recall_topk_ids")
    if isinstance(recall_topk_ids, list):
        result["recall_topk_ids"] = [
            str(quota_id).strip()
            for quota_id in recall_topk_ids
            if str(quota_id).strip()
        ]
    else:
        result["recall_topk_ids"] = []
    trace = result.get("trace")
    if not isinstance(trace, dict):
        trace = {"steps": [], "path": []}
    if not isinstance(trace.get("steps"), list):
        trace["steps"] = []
    if not isinstance(trace.get("path"), list):
        trace["path"] = [s.get("stage", "") for s in trace["steps"] if s.get("stage")]
    trace["final_source"] = result.get("match_source", "")
    trace["final_confidence"] = safe_float(result.get("confidence"), 0.0)
    result["trace"] = trace


def _normalize_fallbacks(value) -> list[str]:
    """Normalize fallback inputs into a deduplicated string list."""
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = []

    cleaned = []
    seen = set()
    for item in raw_items:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_classification_legacy(classification: dict) -> dict:
    """Normalize legacy classification payloads defensively."""
    base = dict(classification) if isinstance(classification, dict) else {}

    primary = base.get("primary")
    primary = str(primary).strip() if primary is not None else ""
    primary = primary or None

    fallbacks = _normalize_fallbacks(base.get("fallbacks", []))
    if primary:
        fallbacks = [b for b in fallbacks if b != primary]

    base["primary"] = primary
    base["fallbacks"] = fallbacks
    return base


def _normalize_routing_classification(classification: dict) -> dict:
    """Normalize routing output into a stable search contract."""
    base = dict(classification) if isinstance(classification, dict) else {}
    primary = base.get("primary")
    primary = str(primary).strip() if primary is not None else ""
    primary = primary or None

    fallbacks = _normalize_fallbacks(base.get("fallbacks", []))
    if primary:
        fallbacks = [b for b in fallbacks if b != primary]

    candidate_books = _normalize_fallbacks(base.get("candidate_books", []))
    search_books = _normalize_fallbacks(base.get("search_books", []))
    hard_book_constraints = _normalize_fallbacks(base.get("hard_book_constraints", []))
    hard_search_books = _normalize_fallbacks(base.get("hard_search_books", []))
    advisory_search_books = _normalize_fallbacks(base.get("advisory_search_books", []))

    if primary and primary not in candidate_books:
        candidate_books.insert(0, primary)
    for book in fallbacks:
        if book not in candidate_books:
            candidate_books.append(book)

    if not search_books:
        search_books = list(candidate_books)
    elif primary and primary not in search_books:
        search_books.insert(0, primary)

    route_mode = str(base.get("route_mode") or "").strip().lower()
    if route_mode not in {"strict", "moderate", "open"}:
        route_mode = "moderate" if primary else "open"

    allow_cross_book_escape = base.get("allow_cross_book_escape")
    if allow_cross_book_escape is None:
        allow_cross_book_escape = route_mode != "strict"
    allow_cross_book_escape = bool(allow_cross_book_escape)

    if not hard_search_books:
        hard_search_books = [
            book for book in search_books
            if book in hard_book_constraints
        ] if hard_book_constraints else []
    else:
        hard_search_books = [
            book for book in hard_search_books
            if not hard_book_constraints or book in hard_book_constraints
        ]

    if not advisory_search_books:
        advisory_search_books = [
            book for book in search_books
            if book not in hard_search_books
        ]
    else:
        advisory_search_books = [
            book for book in advisory_search_books
            if book not in hard_search_books
        ]

    search_books = _normalize_fallbacks(hard_search_books + advisory_search_books)

    raw_evidence = base.get("routing_evidence") or {}
    routing_evidence = {}
    if isinstance(raw_evidence, dict):
        for book, reasons in raw_evidence.items():
            book_key = str(book).strip()
            if not book_key:
                continue
            routing_evidence[book_key] = _normalize_fallbacks(reasons)

    raw_scores = base.get("book_scores") or {}
    book_scores = {}
    if isinstance(raw_scores, dict):
        for book, score in raw_scores.items():
            book_key = str(book).strip()
            if not book_key:
                continue
            try:
                book_scores[book_key] = float(score)
            except (TypeError, ValueError):
                continue

    base["primary"] = primary
    base["fallbacks"] = fallbacks
    base["candidate_books"] = candidate_books
    base["search_books"] = search_books
    base["hard_book_constraints"] = hard_book_constraints
    base["hard_search_books"] = hard_search_books
    base["advisory_search_books"] = advisory_search_books
    base["route_mode"] = route_mode
    base["allow_cross_book_escape"] = allow_cross_book_escape
    base["routing_evidence"] = routing_evidence
    base["book_scores"] = book_scores
    return base


def _normalize_classification(classification: dict) -> dict:
    return _normalize_routing_classification(classification)


def _sanitize_cross_province_hints(raw_hints: object, *, limit: int = 5) -> list[str]:
    if isinstance(raw_hints, str):
        candidates = [raw_hints]
    elif isinstance(raw_hints, (list, tuple, set)):
        candidates = list(raw_hints)
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for hint in candidates:
        text = " ".join(str(hint or "").split()).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _validate_candidates_with_context(validator: ParamValidator,
                                      full_query: str,
                                      candidates: list[dict],
                                      *,
                                      supplement_query: str = None,
                                      bill_params: dict = None,
                                      search_books: list[str] = None,
                                      canonical_features: dict = None,
                                      context_prior: dict = None,
                                      bill_item_context: BillItemContext | None = None) -> list[dict]:
    kwargs = {
        "supplement_query": supplement_query,
        "bill_params": bill_params,
        "search_books": search_books,
    }
    try:
        param_names = set(inspect.signature(validator.validate_candidates).parameters)
    except (TypeError, ValueError):
        param_names = set()

    if "canonical_features" in param_names:
        kwargs["canonical_features"] = canonical_features
    if "context_prior" in param_names:
        kwargs["context_prior"] = context_prior
    if "bill_item_context" in param_names:
        kwargs["bill_item_context"] = bill_item_context
    if "reorder_candidates" in param_names:
        kwargs["reorder_candidates"] = False
    return validator.validate_candidates(full_query, candidates, **kwargs)


# ============================================================
# 缁忛獙搴撳尮閰?# ============================================================

def _validate_experience_params_legacy(exp_result: dict, item: dict,
                                       rule_validator=None, is_exact=False) -> dict:
    """
    验证经验库匹配结果的参数是否正确

    问题场景：
    - 经验库存了"配电箱安装 规格(回路以内) 4"给某个清单
    - 当前清单虽然也是配电箱，但参数不同（如7回路）
    - 7回路应该套"8回路以内"的定额，不能沿用经验库的"4回路以内"

    验证方式：
    1. 规则校验器：检查定额所在家族的档位是否匹配（处理回路/容量/截面等）
    2. 参数提取器：对比清单和定额名称中的数值参数（DN/截面/kVA等）
       精确匹配(is_exact=True)且方法1已验证时用宽松模式：
       只拦截硬参数超档（DN/截面/kVA，score=0.0），
       不因材质名称差异（如"射频同轴电缆"≠"同轴电缆"）误杀正确结果。

    如果发现参数不匹配，返回 None（拒绝经验库结果，让后续流程重新匹配）
    """
    quotas = exp_result.get("quotas", [])
    if not quotas:
        return exp_result  # 没有定额信息，无法验证，保持原样

    bill_text = f"{item.get('name', '')} {item.get('description', '')}".strip()

    main_quota = quotas[0]
    main_quota_id = main_quota.get("quota_id", "")
    main_quota_name = main_quota.get("name", "")

    rule_validated = False
    if rule_validator and rule_validator.rules and main_quota_id:
        family = rule_validator.family_index.get(main_quota_id)
        if family:
            tiers = family.get("tiers")
            if tiers:
                bill_value = rule_validator._extract_param_value(bill_text, family)
                if bill_value is not None:
                    correct_tier = rule_validator._find_correct_tier(bill_value, tiers)
                    if correct_tier is not None:
                        correct_quota_id = rule_validator._find_quota_by_tier(
                            family, correct_tier)
                        if correct_quota_id and correct_quota_id != main_quota_id:
                            logger.info(
                                f"经验库参数校验失败: '{bill_text[:40]}' "
                                f"参数值{bill_value}→应套档位{correct_tier}, "
                                f"但经验库给的是{main_quota_id}, 拒绝经验库结果")
                            return None
                        else:
                            rule_validated = True

    if main_quota_name:
        bill_params = item.get("params") or text_parser.parse(bill_text)
        quota_params = text_parser.parse(main_quota_name)
        if bill_params and quota_params:
            is_match, score = text_parser.params_match(bill_params, quota_params)
            if not is_match:
                if rule_validated and is_exact and score > 0.0:
                    logger.debug(
                        f"经验库精确匹配参数软差异(放行): '{bill_text[:40]}' "
                        f"score={score:.2f}, 方法1已确认档位正确")
                else:
                    logger.info(
                        f"经验库参数校验失败(方法2): '{bill_text[:40]}' "
                        f"清单参数{bill_params} vs 定额'{main_quota_name[:30]}'参数{quota_params}, "
                        f"拒绝经验库结果")
                    return None

    return exp_result


def _validate_experience_params(exp_result: dict, item: dict,
                                rule_validator=None, is_exact=False) -> dict:
    """Validate every quota attached to an experience hit."""
    _ = is_exact
    quotas = exp_result.get("quotas", [])
    if not quotas:
        return exp_result

    bill_text = f"{item.get('name', '')} {item.get('description', '')}".strip()
    bill_params = item.get("params") or text_parser.parse(bill_text)

    for index, quota in enumerate(quotas):
        quota_id = quota.get("quota_id", "")
        quota_name = quota.get("name", "")

        if rule_validator and rule_validator.rules and quota_id:
            family = rule_validator.family_index.get(quota_id)
            if family:
                tiers = family.get("tiers")
                if tiers:
                    bill_value = rule_validator._extract_param_value(bill_text, family)
                    if bill_value is not None:
                        correct_tier = rule_validator._find_correct_tier(bill_value, tiers)
                        if correct_tier is not None:
                            correct_quota_id = rule_validator._find_quota_by_tier(
                                family, correct_tier)
                            if correct_quota_id and correct_quota_id != quota_id:
                                logger.info(
                                    f"经验库参数校验失败: '{bill_text[:40]}' "
                                    f"第{index + 1}条定额参数值{bill_value}→应套档位{correct_tier}, "
                                    f"但经验库给的是{quota_id}, 拒绝经验库结果")
                                return None

        if quota_name and bill_params:
            quota_params = text_parser.parse(quota_name)
            if quota_params:
                is_match, score = text_parser.params_match(bill_params, quota_params)
                if not is_match:
                    logger.info(
                        f"经验库参数校验失败(方法2): '{bill_text[:40]}' "
                        f"第{index + 1}条定额{quota_id or '<missing>'} "
                        f"清单参数{bill_params} vs 定额'{quota_name[:30]}'参数{quota_params}, "
                        f"score={score:.2f}, 拒绝经验库结果")
                    return None

        validated_quota = ParamValidator().validate_candidate_against_item(item, quota)
        if validated_quota.get("param_hard_fail"):
            logger.info(
                f"经验库参数校验失败(ParamValidator): '{bill_text[:40]}' "
                f"第{index + 1}条定额{quota_id or '<missing>'} "
                f"detail={validated_quota.get('param_detail', '')}, 拒绝经验库结果")
            return None
        quotas[index] = validated_quota

    return exp_result


def try_experience_match(query: str, item: dict, experience_db,
                         rule_validator=None, province: str = None) -> dict:
    """
    ????????

    ??:
        query: ??????
        item: ??????
        experience_db: ?????
        rule_validator: ?????????????????????????
        province: ???????????????

    ??:
        ?????????????????? None
    """
    if experience_db is None:
        return None

    similar = experience_db.search_similar(
        query, top_k=3,
        min_confidence=config.EXPERIENCE_DIRECT_THRESHOLD,
        province=province,
    )

    if not similar:
        if getattr(config, "CROSS_PROVINCE_WARMUP_ENABLED", False) and experience_db:
            try:
                cross_refs = experience_db.search_cross_province(
                    query, current_province=province or "")
                if cross_refs:
                    hint_keywords = []
                    for ref in cross_refs:
                        names = ref.get("quota_names", [])
                        if isinstance(names, str):
                            names = [names]
                        elif isinstance(names, list):
                            names = [str(n) for n in names if n]
                        else:
                            names = []
                        hint_keywords.extend(names)
                    sanitized_hints = _sanitize_cross_province_hints(hint_keywords)
                    if sanitized_hints:
                        item["_cross_province_hints"] = sanitized_hints
                        remember_cross_province_hints(
                            item_name=str(item.get("name", "") or query),
                            specialty=str(item.get("specialty", "") or ""),
                            province=str(province or ""),
                            bill_text=str(query or ""),
                            hints=sanitized_hints,
                        )
                        logger.debug(
                            f"L5????: {query[:30]} ? "
                            f"??={sanitized_hints[:3]}")
            except (KeyError, TypeError, ValueError, AttributeError,
                    OSError, RuntimeError, ImportError) as e:
                logger.debug(f"L5??????: {e}")
        return None

    best = None
    for candidate in similar:
        if candidate.get("match_type") not in ("stale", "candidate"):
            best = candidate
            break

    if best is None:
        logger.debug(f"???????????????: {query[:50]}")
        return None
    best = _annotate_experience_confidence(dict(best), experience_db)
    similarity = safe_float(best.get("similarity"), 0.0)
    exp_materials = _safe_json_materials(best.get("materials"))

    if best.get("match_type") == "exact":
        quota_ids = best.get("quota_ids", [])
        quota_names = best.get("quota_names", [])
        if not quota_ids:
            logger.debug(f"?????????????????: {query[:50]}")
            return None
        if not _experience_allows_direct(best, experience_db):
            logger.debug(f"experience exact direct rejected by dynamic confidence: {query[:50]}")
            return None
        confidence = min(_experience_effective_confidence(best, experience_db), 98)

        quotas = []
        for i, qid in enumerate(quota_ids):
            quotas.append({
                "quota_id": qid,
                "name": quota_names[i] if i < len(quota_names) else "",
                "unit": "",
                "reason": f"??????? (???{confidence}%, ??{best.get('confirm_count', 1)}?)",
            })

        result = {
            "bill_item": item,
            "quotas": quotas,
            "materials": exp_materials,
            "confidence": confidence,
            "explanation": f"??????? (??{best.get('confirm_count', 1)}?)",
            "match_source": "experience_exact",
        }
        _append_trace_step(
            result,
            "experience_exact",
            record_id=best.get("id"),
            similarity=1.0,
            effective_confidence=confidence,
            confirm_count=best.get("confirm_count", 0),
            quota_ids=[q.get("quota_id", "") for q in quotas],
            materials_count=len(exp_materials),
        )

        validated = _validate_experience_params(result, item, rule_validator, is_exact=True)
        if validated is None:
            return None
        return validated

    if similarity >= 0.80:
        quota_ids = best.get("quota_ids", [])
        quota_names = best.get("quota_names", [])
        if not quota_ids:
            logger.debug(f"?????????????????: {query[:50]}")
            return None
        confidence = min(int(similarity * _experience_effective_confidence(best, experience_db)), 90)

        quotas = []
        for i, qid in enumerate(quota_ids):
            quotas.append({
                "quota_id": qid,
                "name": quota_names[i] if i < len(quota_names) else "",
                "unit": "",
                "reason": f"??????? (???{similarity:.2f}, ??: {best.get('bill_text', '')[:50]})",
            })

        result = {
            "bill_item": item,
            "quotas": quotas,
            "materials": exp_materials,
            "confidence": confidence,
            "explanation": f"??????? (???{similarity:.2f})",
            "match_source": "experience_similar",
        }
        _append_trace_step(
            result,
            "experience_similar",
            record_id=best.get("id"),
            similarity=similarity,
            effective_confidence=_experience_effective_confidence(best, experience_db),
            confirm_count=best.get("confirm_count", 0),
            quota_ids=[q.get("quota_id", "") for q in quotas],
            materials_count=len(exp_materials),
        )

        validated = _validate_experience_params(result, item, rule_validator)
        if validated is None:
            return None
        return validated

    return None


def try_experience_exact_match(
    query: str,
    item: dict,
    experience_db,
    rule_validator=None,
    province: str = None,
    *,
    authority_only: bool = True,
) -> dict:
    """?????????? exact/normalized exact???????????"""
    if experience_db is None:
        return None

    exact_lookup = getattr(experience_db, "_find_exact_match", None)
    if not callable(exact_lookup):
        return None

    target_province = province or getattr(experience_db, "province", "")
    if not target_province:
        return None

    try:
        best = exact_lookup(query, target_province, authority_only=authority_only)
    except TypeError:
        best = exact_lookup(query, target_province)

    if not best:
        return None

    best = _annotate_experience_confidence(dict(best), experience_db)
    normalizer = getattr(experience_db, "_normalize_record_quota_fields", None)
    if callable(normalizer):
        best = normalizer(best)

    quota_ids = best.get("quota_ids", [])
    quota_names = best.get("quota_names", [])
    if not quota_ids:
        logger.debug(f"???????????????????: {query[:50]}")
        return None

    if not _experience_allows_direct(best, experience_db):
        logger.debug(f"experience exact lightweight direct rejected by dynamic confidence: {query[:50]}")
        return None
    confidence = min(_experience_effective_confidence(best, experience_db), 98)
    exp_materials = _safe_json_materials(best.get("materials"))
    quotas = []
    for i, qid in enumerate(quota_ids):
        quotas.append({
            "quota_id": qid,
            "name": quota_names[i] if i < len(quota_names) else "",
            "unit": "",
            "reason": f"??????? (???{confidence}%, ??{best.get('confirm_count', 1)}?)",
        })

    result = {
        "bill_item": item,
        "quotas": quotas,
        "materials": exp_materials,
        "confidence": confidence,
        "explanation": f"??????? (??{best.get('confirm_count', 1)}?)",
        "match_source": "experience_exact",
    }
    _append_trace_step(
        result,
        "experience_exact_lightweight",
        record_id=best.get("id"),
        similarity=1.0,
        effective_confidence=confidence,
        confirm_count=best.get("confirm_count", 0),
        quota_ids=[q.get("quota_id", "") for q in quotas],
        materials_count=len(exp_materials),
        match_method=str(best.get("_match_method", "exact") or "exact"),
        authority_only=bool(authority_only),
    )

    validated = _validate_experience_params(result, item, rule_validator, is_exact=True)
    if validated is None:
        return None
    return validated


# ============================================================
# ?????
# ============================================================

def try_experience_exact_match(
    query: str,
    item: dict,
    experience_db,
    rule_validator=None,
    province: str = None,
    *,
    authority_only: bool = True,
) -> dict:
    """?????????? exact/normalized exact???????????"""
    if experience_db is None:
        return None

    exact_lookup = getattr(experience_db, "_find_exact_match", None)
    if not callable(exact_lookup):
        return None

    target_province = province or getattr(experience_db, "province", "")
    if not target_province:
        return None

    try:
        best = exact_lookup(query, target_province, authority_only=authority_only)
    except TypeError:
        best = exact_lookup(query, target_province)

    if not best:
        return None

    best = _annotate_experience_confidence(dict(best), experience_db)
    normalizer = getattr(experience_db, "_normalize_record_quota_fields", None)
    if callable(normalizer):
        best = normalizer(best)

    quota_ids = best.get("quota_ids", [])
    quota_names = best.get("quota_names", [])
    if not quota_ids:
        logger.debug(f"???????????????????: {query[:50]}")
        return None

    if not _experience_allows_direct(best, experience_db):
        logger.debug(f"experience exact lightweight direct rejected by dynamic confidence: {query[:50]}")
        return None
    confidence = min(_experience_effective_confidence(best, experience_db), 98)
    exp_materials = _safe_json_materials(best.get("materials"))
    quotas = []
    for i, qid in enumerate(quota_ids):
        quotas.append({
            "quota_id": qid,
            "name": quota_names[i] if i < len(quota_names) else "",
            "unit": "",
            "reason": f"??????? (???{confidence}%, ??{best.get('confirm_count', 1)}?)",
        })

    result = {
        "bill_item": item,
        "quotas": quotas,
        "materials": exp_materials,
        "confidence": confidence,
        "explanation": f"??????? (??{best.get('confirm_count', 1)}?)",
        "match_source": "experience_exact",
    }
    _append_trace_step(
        result,
        "experience_exact_lightweight",
        record_id=best.get("id"),
        similarity=1.0,
        effective_confidence=confidence,
        confirm_count=best.get("confirm_count", 0),
        quota_ids=[q.get("quota_id", "") for q in quotas],
        materials_count=len(exp_materials),
        match_method=str(best.get("_match_method", "exact") or "exact"),
        authority_only=bool(authority_only),
    )

    validated = _validate_experience_params(result, item, rule_validator, is_exact=True)
    if validated is None:
        return None
    return validated


def _translate_books_for_industry(c_books: list[str],
                                  quota_books: dict) -> list[str]:
    """?C1-C12????????????book??

    ???????/???????C1-C12???book??????"1"-"9"?
    ?????C4 ? "4"?C10 ? "10"??C????????
    ??????????????book???????
    """
    actual_books = set(quota_books.values()) if quota_books else set()

    translated = set()
    for book in c_books:
        if not book:
            continue
        if book.startswith("C") and book[1:].isdigit():
            translated.add(book[1:])
        else:
            translated.add(book)

    valid = [b for b in translated if b in actual_books]

    # ??????????????????????
    # ???????books=None?????Reranker??????????
    if not valid:
        return None

    return valid

def _merge_with_aux(main_candidates: list[dict], aux_candidates: list[dict],
                    top_k: int) -> list[dict]:
    """Docstring omitted."""
    if not aux_candidates:
        return main_candidates

    # Keep main-library results as-is. Main-library dedupe already happened upstream.
    merged = list(main_candidates)

    # Dedupe auxiliary results by quota and source province, keeping the best score.
    aux_seen = {}
    for r in aux_candidates:
        qid = r.get("quota_id") or id(r)  # 鏃爍uota_id鏃剁敤瀵硅薄id锛岄伩鍏嶈鍚堝苟
        source = r.get("_source_province", "aux")
        key = f"{qid}@{source}"
        score = r.get("hybrid_score", 0)
        if key not in aux_seen or score > aux_seen[key][0]:
            aux_seen[key] = (score, r)

    merged.extend(v[1] for v in aux_seen.values())
    merged.sort(key=HybridSearcher._stable_result_identity)
    merged.sort(key=HybridSearcher._hybrid_result_sort_key)
    return merged[:top_k]


def _merge_stage_candidates(previous: list[dict], current: list[dict]) -> list[dict]:
    """Keep earlier scoped hits when later cascade stages add broader candidates."""
    if not previous:
        return list(current or [])
    if not current:
        return list(previous or [])

    merged_by_identity: dict[object, dict] = {}
    for candidate in list(previous or []) + list(current or []):
        identity = HybridSearcher._stable_result_identity(candidate)
        existing = merged_by_identity.get(identity)
        if existing is None:
            merged_by_identity[identity] = candidate
            continue
        existing_score = float(existing.get("hybrid_score", 0.0) or 0.0)
        candidate_score = float(candidate.get("hybrid_score", 0.0) or 0.0)
        if candidate_score > existing_score:
            existing_stages = list(existing.get("_cascade_stages") or [])
            candidate = dict(candidate)
            candidate["_cascade_stages"] = _normalize_fallbacks(
                existing_stages + list(candidate.get("_cascade_stages") or [])
            )
            merged_by_identity[identity] = candidate

    merged = list(merged_by_identity.values())
    merged.sort(key=HybridSearcher._stable_result_identity)
    merged.sort(key=HybridSearcher._hybrid_result_sort_key)
    return merged


def _normalize_route_book_code(value: object) -> str:
    return normalize_route_book_code(value)


def _candidate_book_code(candidate: dict) -> str:
    quota_id = str((candidate or {}).get("quota_id", "") or "").strip()
    if not quota_id:
        return ""

    def _accept_book(raw_value: str) -> str:
        normalized = _normalize_route_book_code(raw_value)
        if re.match(r"^(?:[ADE]|[A-Z]{1,3}\d+)$", normalized, re.IGNORECASE):
            return normalized
        return ""

    match = re.match(r"^(C\d+)-", quota_id, re.IGNORECASE)
    if match:
        return _accept_book(match.group(1))
    match = re.match(r"^(A\d+)-", quota_id, re.IGNORECASE)
    if match:
        return _accept_book(match.group(1))
    match = re.match(r"^(\d+)-", quota_id)
    if match:
        return _accept_book(match.group(1))
    match = re.match(r"^([A-Z]{1,3}\d+)-", quota_id, re.IGNORECASE)
    if match:
        return _accept_book(match.group(1))
    match = re.match(r"^([A-Z])-", quota_id, re.IGNORECASE)
    if match:
        return _accept_book(match.group(1))
    return ""


def _candidate_route_book(candidate: dict, province_name: str | None = None) -> str:
    candidate = dict(candidate or {})
    for field in (
        "candidate_route_book",
        "plugin_route_book",
        "book",
        "quota_book",
    ):
        book = candidate.get(field)
        if field in {"book", "quota_book"}:
            book = map_db_book_to_route_book(book, province=province_name)
        else:
            book = _normalize_route_book_code(book)
        if book:
            return book
    return map_db_book_to_route_book(_candidate_book_code(candidate), province=province_name)


def _candidate_matches_allowed_books(
    candidate: dict,
    allowed_set: set[str],
    province_name: str | None = None,
) -> bool:
    book_code = _candidate_route_book(candidate, province_name=province_name)
    if not book_code:
        return True
    if book_code in allowed_set:
        return True

    candidate = dict(candidate or {})
    raw_route_book = ""
    for field in (
        "candidate_route_book",
        "plugin_route_book",
        "book",
        "quota_book",
    ):
        raw_value = str(candidate.get(field) or "").strip().upper()
        if raw_value:
            raw_route_book = raw_value
            break

    if raw_route_book in {"03", "3"}:
        return any(book.startswith("C") for book in allowed_set)
    if raw_route_book in {"01", "1", "02", "2"}:
        return "A" in allowed_set
    if raw_route_book in {"04", "4"}:
        return "D" in allowed_set
    if raw_route_book in {"05", "5"}:
        return "E" in allowed_set
    return False


def _effective_route_scope_books(classification: dict | None) -> list[str]:
    raw = dict(classification or {}) if isinstance(classification, dict) else {}
    normalized = _normalize_classification(classification)
    route_mode = str(normalized.get("route_mode") or "open").strip().lower()
    allow_cross_book_escape = bool(
        normalized.get("allow_cross_book_escape", route_mode != "strict")
    )
    if allow_cross_book_escape:
        return []

    def _normalized_books(values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            book = _normalize_route_book_code(value)
            if not book or book in seen:
                continue
            seen.add(book)
            normalized.append(book)
        return normalized

    resolution = dict(raw.get("retrieval_resolution") or normalized.get("retrieval_resolution") or {})
    main_calls = [
        call for call in list(resolution.get("calls") or [])
        if str((call or {}).get("target") or "").strip() == "main"
    ]
    if main_calls:
        resolved_books = _normalize_fallbacks([
            book
            for call in main_calls
            for book in list((call or {}).get("resolved_books") or [])
        ])
        if resolved_books:
            return _normalized_books(resolved_books)
        return []

    hard_books = _normalize_fallbacks(
        list(normalized.get("hard_search_books") or [])
        or list(normalized.get("hard_book_constraints") or [])
    )
    if hard_books:
        return _normalized_books(hard_books)
    return _normalized_books(_normalize_fallbacks(list(normalized.get("search_books") or [])))


def _filter_candidates_to_route_scope(
    candidates: list[dict],
    classification: dict | None,
) -> tuple[list[dict], dict]:
    raw = dict(classification or {}) if isinstance(classification, dict) else {}
    normalized = _normalize_classification(classification)
    allowed_books = _effective_route_scope_books(classification)
    meta = {
        "applied": False,
        "allowed_books": list(allowed_books),
        "dropped_count": 0,
        "dropped_quota_ids": [],
        "reason": "",
    }
    if not candidates or not allowed_books:
        return list(candidates or []), meta

    province_name = str(
        raw.get("province")
        or normalized.get("province")
        or config.get_current_province()
        or ""
    ).strip()
    allowed_set = {str(book).strip().upper() for book in allowed_books if str(book).strip()}
    kept: list[dict] = []
    dropped_quota_ids: list[str] = []
    for candidate in candidates or []:
        quota_id = str((candidate or {}).get("quota_id", "") or "").strip()
        if candidate.get("knowledge_prior_sources"):
            kept.append(candidate)
            continue
        if _candidate_matches_allowed_books(candidate, allowed_set, province_name=province_name):
            kept.append(candidate)
            continue
        if quota_id:
            dropped_quota_ids.append(quota_id)

    if not dropped_quota_ids:
        return list(candidates or []), meta
    if not kept:
        meta["applied"] = True
        meta["reason"] = "empty_after_filter"
        meta["dropped_count"] = len(dropped_quota_ids)
        meta["dropped_quota_ids"] = dropped_quota_ids[:20]
        return [], meta

    meta["applied"] = True
    meta["reason"] = "strict_route_scope"
    meta["dropped_count"] = len(dropped_quota_ids)
    meta["dropped_quota_ids"] = dropped_quota_ids[:20]
    return kept, meta


def _effective_candidate_guard_books(classification: dict | None) -> list[str]:
    raw = dict(classification or {}) if isinstance(classification, dict) else {}
    normalized = _normalize_classification(classification)
    resolution = dict(raw.get("retrieval_resolution") or normalized.get("retrieval_resolution") or {})
    main_calls = [
        call for call in list(resolution.get("calls") or [])
        if str((call or {}).get("target") or "").strip() == "main"
    ]
    if any(str((call or {}).get("stage") or "").strip() in {"open", "escape"} for call in main_calls):
        return []

    books: list[str] = []
    seen: set[str] = set()
    resolved_books: list[str] = []
    for call in main_calls:
        for value in list((call or {}).get("resolved_books") or []):
            book = _normalize_route_book_code(value)
            if not book or book in seen:
                continue
            seen.add(book)
            resolved_books.append(book)
    if resolved_books:
        return resolved_books

    for value in list(normalized.get("search_books") or []):
        book = _normalize_route_book_code(value)
        if not book or book in seen:
            continue
        seen.add(book)
        books.append(book)
    return books


def _filter_candidates_to_effective_guard_scope(
    candidates: list[dict],
    classification: dict | None,
) -> tuple[list[dict], dict]:
    raw = dict(classification or {}) if isinstance(classification, dict) else {}
    normalized = _normalize_classification(classification)
    allowed_books = _effective_candidate_guard_books(classification)
    meta = {
        "applied": False,
        "allowed_books": list(allowed_books),
        "dropped_count": 0,
        "dropped_quota_ids": [],
        "reason": "",
    }
    if not candidates or not allowed_books:
        return list(candidates or []), meta

    province_name = str(
        raw.get("province")
        or normalized.get("province")
        or config.get_current_province()
        or ""
    ).strip()
    allowed_set = {str(book).strip().upper() for book in allowed_books if str(book).strip()}
    kept: list[dict] = []
    dropped_quota_ids: list[str] = []
    for candidate in candidates or []:
        quota_id = str((candidate or {}).get("quota_id", "") or "").strip()
        if candidate.get("knowledge_prior_sources"):
            kept.append(candidate)
            continue
        if _candidate_matches_allowed_books(candidate, allowed_set, province_name=province_name):
            kept.append(candidate)
            continue
        if quota_id:
            dropped_quota_ids.append(quota_id)

    if not dropped_quota_ids:
        return list(candidates or []), meta
    if not kept:
        meta["applied"] = True
        meta["reason"] = "empty_after_guard"
        meta["dropped_count"] = len(dropped_quota_ids)
        meta["dropped_quota_ids"] = dropped_quota_ids[:20]
        return [], meta

    meta["applied"] = True
    meta["reason"] = "effective_scope_guard"
    meta["dropped_count"] = len(dropped_quota_ids)
    meta["dropped_quota_ids"] = dropped_quota_ids[:20]
    return kept, meta


def _resolve_search_books_for_target(target_searcher, search_query: str,
                                     c_books: list[str],
                                     allow_classifier_fallback: bool = True) -> list[str]:
    if not c_books:
        return []
    resolved = list(c_books)
    uses_standard_books = getattr(target_searcher, "uses_standard_books", True)
    if uses_standard_books:
        return resolved

    bm25_engine = getattr(target_searcher, "bm25_engine", None)
    quota_books = getattr(bm25_engine, "quota_books", {})
    if isinstance(quota_books, dict):
        available_books = set(quota_books.values())
    else:
        available_books = set(quota_books or [])
    if not available_books:
        return []

    normalized = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        resolved,
        available_books,
        province=getattr(target_searcher, "province", None),
    )
    if normalized:
        normalized = [
            str(book).strip()
            for book in normalized
            if str(book).strip() in available_books
        ]
    broad_groups = {"A", "D", "E"}
    has_unresolved_broad_group = normalized is None and any(
        str(book or "").strip().upper() in broad_groups
        for book in resolved
    )
    if has_unresolved_broad_group:
        return []

    translated = list(normalized or (_translate_books_for_industry(resolved, quota_books) or []))
    classify_to_books = getattr(bm25_engine, "classify_to_books", None)
    fallback_books = []
    if allow_classifier_fallback and callable(classify_to_books):
        fallback_books = classify_to_books(search_query, top_k=3) or []

    if translated and fallback_books:
        return _normalize_fallbacks(list(fallback_books) + list(translated))
    if fallback_books:
        return fallback_books
    if translated:
        return translated
    requested_available = any(str(book or "").strip() in available_books for book in resolved)
    if requested_available:
        return resolved
    return []


def _should_search_target_for_books(target_searcher, requested_books: list[str] | None) -> bool:
    requested = [
        str(book or "").strip().upper()
        for book in (requested_books or [])
        if str(book or "").strip()
    ]
    if not requested:
        return True

    broad_route_books = {"A", "D", "E"}
    broad_only = [book for book in requested if book in broad_route_books]
    if not broad_only or len(broad_only) != len(requested):
        return True

    target_province = str(getattr(target_searcher, "province", "") or "").strip()
    target_db_type = detect_db_type(target_province)
    db_type_to_book = {
        "civil": "A",
        "municipal": "D",
        "landscape": "E",
    }
    target_book = db_type_to_book.get(target_db_type, "")
    return bool(target_book) and target_book in broad_only


def _record_retrieval_resolution_call(
    resolution_trace: dict | None,
    *,
    target: str,
    stage: str,
    requested_books: list[str] | None,
    resolved_books: list[str] | None,
    source_province: str = "",
    uses_standard_books: bool | None = None,
    open_search: bool | None = None,
) -> None:
    if not isinstance(resolution_trace, dict):
        return
    calls = resolution_trace.setdefault("calls", [])
    calls.append({
        "target": str(target or "").strip(),
        "stage": str(stage or "").strip(),
        "source_province": str(source_province or "").strip(),
        "requested_books": list(requested_books or []),
        "resolved_books": list(resolved_books or []),
        "open_search": (not bool(resolved_books) if open_search is None else bool(open_search)),
        "uses_standard_books": (
            None if uses_standard_books is None else bool(uses_standard_books)
        ),
    })


def _record_mixed_search_trace(
    resolution_trace: dict | None,
    *,
    target: str,
    stage: str,
    results: list[dict] | None,
) -> None:
    if not isinstance(resolution_trace, dict):
        return
    trace = {}
    for candidate in results or []:
        if isinstance(candidate, dict) and isinstance(candidate.get("_mixed_search_trace"), dict):
            trace = dict(candidate.get("_mixed_search_trace") or {})
            break
    if not trace:
        return

    row = {
        "target": str(target or ""),
        "stage": str(stage or ""),
        **trace,
    }
    resolution_trace.setdefault("mixed_search_traces", []).append(row)

    numeric_fields = [
        "hybrid_search_call_count",
        "session_cache_hit_count",
        "session_cache_miss_count",
        "bm25_call_count",
        "vector_call_count",
        "vector_filter_fallback_count",
        "old_vector_index_fallback_count",
        "query_variant_count",
        "bm25_hit_count",
        "vector_hit_count",
    ]
    totals = resolution_trace.setdefault("mixed_search_totals", {})
    for field in numeric_fields:
        totals[field] = int(totals.get(field, 0) or 0) + int(trace.get(field, 0) or 0)
    if str(target or "") == "main":
        totals["cascade_stage_count"] = int(totals.get("cascade_stage_count", 0) or 0) + 1
    substage_totals = totals.setdefault("substage_sec", {})
    for name, elapsed in dict(trace.get("substage_sec") or {}).items():
        substage_totals[str(name)] = round(
            float(substage_totals.get(str(name), 0.0) or 0.0) + float(elapsed or 0.0),
            6,
        )
    if substage_totals:
        slowest_name, _elapsed = max(
            substage_totals.items(),
            key=lambda item: float(item[1] or 0.0),
        )
        totals["slowest_substage"] = str(slowest_name)


def _allows_unresolved_open_search(target_searcher, requested_books: list[str] | None) -> bool:
    requested = [
        str(book or "").strip().upper()
        for book in (requested_books or [])
        if str(book or "").strip()
    ]
    if not requested:
        return True
    if getattr(target_searcher, "uses_standard_books", True):
        return False
    broad_route_books = {"A", "D", "E"}
    return (
        all(book in broad_route_books for book in requested)
        and _should_search_target_for_books(target_searcher, requested)
    )


def _search_with_optional_context(searcher, search_query: str, *,
                                  top_k: int,
                                  books,
                                  item: dict | None = None,
                                  context_prior: dict | None = None):
    try:
        return searcher.search(
            search_query,
            top_k=top_k,
            books=books,
            item=item,
            context_prior=context_prior,
        )
    except TypeError:
        return searcher.search(search_query, top_k=top_k, books=books)


@dataclass(frozen=True)
class SearchStage:
    name: str
    books: list[str] | None
    should_run: Callable[[list[dict]], bool]
    min_candidates: int
    max_candidates: int
    stop_when: Callable[["SearchStage", list[dict], list[str]], bool]
    skip_stage_name: str | None = None
    keep_previous_on_empty: bool = False


def _cascade_search_legacy(searcher: HybridSearcher, search_query: str,
                           classification: dict, top_k: int = None,
                           item: dict | None = None,
                           context_prior: dict | None = None,
                           adaptive_strategy: str | None = None) -> list[dict]:
    """Docstring omitted."""
    top_k = top_k or config.HYBRID_TOP_K
    raw_classification = classification if isinstance(classification, dict) else None
    classification = _normalize_classification(classification)
    retrieval_resolution = {"calls": []}
    if raw_classification is not None:
        raw_classification["retrieval_resolution"] = retrieval_resolution
    classification["retrieval_resolution"] = retrieval_resolution

    primary = classification.get("primary")
    fallbacks = classification.get("fallbacks", [])
    candidate_books = classification.get("candidate_books", [])
    search_books = classification.get("search_books", [])
    hard_book_constraints = classification.get("hard_book_constraints", [])
    route_mode = classification.get("route_mode", "open")
    allow_cross_book_escape = bool(
        classification.get("allow_cross_book_escape", route_mode != "strict")
    )
    strategy = str(adaptive_strategy or "standard").strip().lower()

    expanded_books = search_books or candidate_books or (
        [primary] + fallbacks if primary else []
    )
    expanded_books = _normalize_fallbacks(expanded_books)
    if hard_book_constraints:
        constrained_books = [
            book for book in expanded_books
            if book in hard_book_constraints
        ]
        expanded_books = constrained_books or list(hard_book_constraints)

    primary_stage_books = [primary] if primary else expanded_books[:1]
    expanded_stage_books = expanded_books
    defer_aux_search = bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True))

    def _search_is_good_enough(found: list[dict], *, min_candidates: int = CASCADE_MIN_CANDIDATES) -> bool:
        required_candidates = max(1, int(min_candidates or 0))
        if len(found) < required_candidates:
            return False
        top_score = found[0].get("hybrid_score", 0)
        # 缁濆鍒嗘暟澶綆鏃讹紝涓嶇鍒嗗樊澶氬ぇ閮界户缁悳锛堥槻姝富鍐屾悳鍒板急缁撴灉灏卞仠姝級
        ABSOLUTE_QUALITY_FLOOR = 0.6
        if top_score < ABSOLUTE_QUALITY_FLOOR:
            return False
        if len(found) >= required_candidates + 2:
            return True
        third_idx = min(2, len(found) - 1)
        third_score = found[third_idx].get("hybrid_score", 0)
        quality_threshold = getattr(config, "CASCADE_QUALITY_THRESHOLD", 0.3)
        if top_score > 0 and (top_score - third_score) / top_score >= quality_threshold:
            return True
        logger.debug(
            f"cascade quality insufficient: len={len(found)} "
            f"gap_ratio={(top_score - third_score) / max(top_score, 1e-9):.2f}"
        )
        return False

    aux_searchers = getattr(searcher, "aux_searchers", [])
    aux_books = expanded_stage_books or primary_stage_books
    aux_candidates: list[dict] | None = None

    def _collect_aux_candidates() -> list[dict]:
        nonlocal aux_candidates
        if aux_candidates is not None:
            return aux_candidates
        aux_candidates = []
        for aux in aux_searchers:
            try:
                if not _should_search_target_for_books(aux, aux_books):
                    _record_retrieval_resolution_call(
                        retrieval_resolution,
                        target="aux",
                        stage="aux_skipped",
                        requested_books=aux_books,
                        resolved_books=[],
                        source_province=str(getattr(aux, "province", "") or ""),
                        uses_standard_books=getattr(aux, "uses_standard_books", None),
                    )
                    continue
                resolved_aux_books = _resolve_search_books_for_target(aux, search_query, aux_books) or []
                aux_open_search = (
                    not bool(resolved_aux_books)
                    and _allows_unresolved_open_search(aux, aux_books)
                )
                _record_retrieval_resolution_call(
                    retrieval_resolution,
                    target="aux",
                    stage="aux",
                    requested_books=aux_books,
                    resolved_books=resolved_aux_books,
                    source_province=str(getattr(aux, "province", "") or ""),
                    uses_standard_books=getattr(aux, "uses_standard_books", None),
                    open_search=aux_open_search,
                )
                if not resolved_aux_books and not aux_open_search:
                    continue
                aux_results = _search_with_optional_context(
                    aux,
                    search_query,
                    top_k=top_k,
                    books=resolved_aux_books or None,
                    item=item,
                    context_prior=context_prior,
                )
                _record_mixed_search_trace(
                    retrieval_resolution,
                    target="aux",
                    stage="aux",
                    results=aux_results,
                )
                for result in aux_results:
                    result["_source_province"] = aux.province
                aux_candidates.extend(aux_results)
            except (KeyError, TypeError, ValueError, AttributeError,
                    OSError, RuntimeError, ImportError) as e:
                logger.warning(f"aux search failed: {getattr(aux, 'province', '')}: {e}")
        return aux_candidates

    def _finalize_candidates(candidates: list[dict], limit: int) -> list[dict]:
        if defer_aux_search:
            return candidates[:limit]
        return _merge_with_aux(candidates, _collect_aux_candidates(), limit)

    def _main_pool_saturates_aux_budget(candidates: list[dict]) -> tuple[bool, str]:
        if strategy not in {"standard", "deep"}:
            return False, ""
        if strategy == "standard" and len(candidates or []) >= CASCADE_MIN_CANDIDATES:
            return True, "standard_main_pool_saturated"
        if strategy == "deep" and len(candidates or []) >= max(top_k, CASCADE_MIN_CANDIDATES * 2):
            return True, "deep_main_pool_saturated"
        return False, ""

    def _standard_main_pool_saturates_aux_budget(candidates: list[dict]) -> bool:
        if strategy not in {"standard", "deep"}:
            return False
        if not aux_searchers:
            return False
        saturated, _reason = _main_pool_saturates_aux_budget(candidates)
        return saturated

    def _record_aux_budget_deferred(reason: str) -> None:
        for aux in aux_searchers:
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="aux",
                stage="aux_budget_deferred",
                requested_books=aux_books,
                resolved_books=[],
                source_province=str(getattr(aux, "province", "") or ""),
                uses_standard_books=getattr(aux, "uses_standard_books", None),
                open_search=False,
            )
        retrieval_resolution["aux_budget_reason"] = reason

    executed_main_stages: set[str] = set()

    def _rankable_candidate_count(candidates: list[dict]) -> int:
        count = 0
        for candidate in candidates or []:
            if not isinstance(candidate, dict):
                continue
            if not str(candidate.get("quota_id", "") or "").strip():
                continue
            if not str(candidate.get("name", "") or "").strip():
                continue
            count += 1
        return count

    def _should_defer_deep_escape(candidates: list[dict]) -> tuple[bool, str]:
        if strategy != "deep":
            return False, ""
        if "expanded" not in executed_main_stages:
            return False, ""
        if not candidates:
            return False, ""
        required = max(top_k, CASCADE_MIN_CANDIDATES * 2)
        if len(candidates or []) >= required:
            return True, "deep_escape_candidate_pool_saturated"
        if _rankable_candidate_count(candidates) >= required:
            return True, "deep_escape_rankable_pool_saturated"
        return False, ""

    def _record_escape_budget_deferred(reason: str, candidates: list[dict]) -> None:
        _record_retrieval_resolution_call(
            retrieval_resolution,
            target="main",
            stage="escape_budget_deferred",
            requested_books=[],
            resolved_books=[],
            source_province=str(getattr(searcher, "province", "") or ""),
            uses_standard_books=getattr(searcher, "uses_standard_books", None),
            open_search=False,
        )
        retrieval_resolution["escape_budget_reason"] = reason
        retrieval_resolution["escape_budget_candidate_count"] = len(candidates or [])
        retrieval_resolution["escape_budget_rankable_count"] = _rankable_candidate_count(candidates)

    def _default_stage_stop(stage: SearchStage, found: list[dict], _resolved_books: list[str]) -> bool:
        return _search_is_good_enough(found, min_candidates=stage.min_candidates)

    def _primary_stage_stop(stage: SearchStage, found: list[dict], resolved_books: list[str]) -> bool:
        if strategy == "standard" and len(found) >= top_k:
            return True
        if (
            resolved_books
            and len(found) >= top_k
            and not getattr(searcher, "uses_standard_books", True)
        ):
            return True
        return _default_stage_stop(stage, found, resolved_books)

    def _run_main_stage(stage: SearchStage) -> tuple[list[dict] | None, list[str], int | None]:
        source_province = str(getattr(searcher, "province", "") or "")
        uses_standard_books = getattr(searcher, "uses_standard_books", None)
        if stage.books is None:
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage=stage.name,
                requested_books=[],
                resolved_books=[],
                source_province=source_province,
                uses_standard_books=uses_standard_books,
            )
            stage_results = _search_with_optional_context(
                searcher,
                search_query,
                top_k=stage.max_candidates,
                books=None,
                item=item,
                context_prior=context_prior,
            )
            _record_mixed_search_trace(
                retrieval_resolution,
                target="main",
                stage=stage.name,
                results=stage_results,
            )
            return (stage_results, [], stage.max_candidates)

        if not _should_search_target_for_books(searcher, stage.books):
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage=stage.skip_stage_name or f"{stage.name}_skipped",
                requested_books=stage.books,
                resolved_books=[],
                source_province=source_province,
                uses_standard_books=uses_standard_books,
            )
            return None, [], None

        resolved_books = _resolve_search_books_for_target(
            searcher,
            search_query,
            stage.books,
            allow_classifier_fallback=False,
        ) or []
        open_search = (
            not bool(resolved_books)
            and _allows_unresolved_open_search(searcher, stage.books)
        )
        _record_retrieval_resolution_call(
            retrieval_resolution,
            target="main",
            stage=stage.name,
            requested_books=stage.books,
            resolved_books=resolved_books,
            source_province=source_province,
            uses_standard_books=uses_standard_books,
            open_search=open_search,
        )
        if not resolved_books and not open_search:
            return [], resolved_books, None
        stage_results = _search_with_optional_context(
            searcher,
            search_query,
            top_k=stage.max_candidates,
            books=resolved_books or None,
            item=item,
            context_prior=context_prior,
        )
        _record_mixed_search_trace(
            retrieval_resolution,
            target="main",
            stage=stage.name,
            results=stage_results,
        )
        for candidate in stage_results or []:
            stages_seen = list(candidate.get("_cascade_stages") or [])
            if stage.name not in stages_seen:
                stages_seen.append(stage.name)
            candidate["_cascade_stage"] = candidate.get("_cascade_stage") or stage.name
            candidate["_cascade_stages"] = stages_seen
        return stage_results, resolved_books, stage.max_candidates

    stages: list[SearchStage]
    if not primary_stage_books and not expanded_stage_books:
        stages = [
            SearchStage(
                name="open",
                books=None,
                should_run=lambda _current: True,
                min_candidates=CASCADE_MIN_CANDIDATES,
                max_candidates=top_k,
                stop_when=_default_stage_stop,
            )
        ]
    else:
        stages = [
            SearchStage(
                name="primary",
                books=primary_stage_books or None,
                should_run=lambda _current: bool(primary_stage_books),
                min_candidates=CASCADE_MIN_CANDIDATES,
                max_candidates=top_k * 2,
                stop_when=_primary_stage_stop,
                skip_stage_name="primary_skipped",
            ),
            SearchStage(
                name="expanded",
                books=expanded_stage_books or None,
                should_run=lambda _current: (
                    bool(expanded_stage_books)
                    and expanded_stage_books != primary_stage_books
                ),
                min_candidates=CASCADE_MIN_CANDIDATES,
                max_candidates=top_k * 2,
                stop_when=_default_stage_stop,
                skip_stage_name="expanded_skipped",
                keep_previous_on_empty=True,
            ),
            SearchStage(
                name="escape",
                books=None,
                should_run=lambda _current: allow_cross_book_escape,
                min_candidates=CASCADE_MIN_CANDIDATES,
                max_candidates=top_k,
                stop_when=_default_stage_stop,
                keep_previous_on_empty=True,
            ),
        ]

    best_candidates: list[dict] = []
    for stage in stages:
        if not stage.should_run(best_candidates):
            continue
        if stage.name == "escape":
            defer_escape, reason = _should_defer_deep_escape(best_candidates)
            if defer_escape:
                _record_escape_budget_deferred(reason, best_candidates)
                continue
        stage_candidates, resolved_books, stage_limit = _run_main_stage(stage)
        if stage_candidates is None:
            continue
        executed_main_stages.add(stage.name)
        if stage_candidates or not stage.keep_previous_on_empty:
            if stage.keep_previous_on_empty and best_candidates:
                best_candidates = _merge_stage_candidates(best_candidates, stage_candidates)
            else:
                best_candidates = stage_candidates
        if stage.stop_when(stage, best_candidates, resolved_books):
            return _finalize_candidates(best_candidates, stage_limit or top_k)

    if defer_aux_search and _standard_main_pool_saturates_aux_budget(best_candidates):
        _saturated, reason = _main_pool_saturates_aux_budget(best_candidates)
        _record_aux_budget_deferred(reason or "main_pool_saturated")
        return best_candidates[:top_k]

    return _merge_with_aux(best_candidates, _collect_aux_candidates(), top_k)


def cascade_search(searcher: HybridSearcher, search_query: str,
                   classification: dict, top_k: int = None,
                   item: dict | None = None,
                   context_prior: dict | None = None,
                   adaptive_strategy: str | None = None) -> list[dict]:
    return _cascade_search_legacy(
        searcher,
        search_query,
        classification,
        top_k=top_k,
        item=item,
        context_prior=context_prior,
        adaptive_strategy=adaptive_strategy,
    )


def _resolve_adaptive_search_top_k(adaptive_strategy: str | None) -> int:
    base_top_k = max(1, int(getattr(config, "HYBRID_TOP_K", 10) or 10))
    strategy = str(adaptive_strategy or "standard").strip().lower()
    if strategy == "deep":
        return max(base_top_k + 5, int(base_top_k * 1.5))
    if strategy == "fast":
        return max(3, min(base_top_k, 5))
    return base_top_k


def _is_measure_item(name: str, desc: str, unit, quantity) -> bool:
    """判断是否为措施项/章节分隔行，这类行不应套安装定额。"""
    clean_name = name.replace("\n", "").replace("\r", "").replace(" ", "").strip()
    if clean_name in _MEASURE_EXACT_NAMES:
        return True
    if any(kw in clean_name for kw in _MEASURE_CONTAINS_KEYWORDS):
        return True

    if any(kw in name for kw in STRONG_MEASURE_KEYWORDS):
        return True
    if any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity:
        return True
    if name.strip() == "其他" and not unit and not quantity and not desc.strip():
        return True

    _SECTION_TITLES = {
        "电气工程", "给排水工程", "通风空调工程", "消防工程", "智能化工程",
        "建筑工程", "装饰工程", "装饰装修工程", "建筑装饰工程",
        "市政工程", "园林工程", "园林景观工程", "绿化工程",
        "土建工程", "安装工程", "机电工程", "机电安装工程",
        "弱电工程", "强电工程", "照明工程", "动力工程",
        "排水工程", "给水工程", "采暖工程", "通风工程", "空调工程",
    }
    if clean_name in _SECTION_TITLES:
        return True

    _SUBSIDIARY_PATTERNS = [
        "外表面积",
        "保护层面积",
        "超高外表面积",
        "超高保护层面积",
        "超高长度",
        "超高数量",
    ]
    if clean_name in _SUBSIDIARY_PATTERNS or any(
        clean_name.startswith(p) for p in _SUBSIDIARY_PATTERNS
    ):
        return True
    if clean_name.startswith("数量") and ("个" in clean_name or "只" in clean_name):
        return True
    return False


# ============================================================
# 候选准备
# ============================================================

_RANKABLE_POOL_CORE_FEATURES = (
    "family",
    "entity",
    "canonical_name",
    "material",
    "connection",
    "install_method",
)

_RANKABLE_POOL_PRIMARY_PARAM_FIELDS = (
    "dn",
    "conduit_dn",
    "cable_section",
    "cable_cores",
    "kw",
    "kva",
    "ampere",
    "circuits",
    "perimeter",
    "half_perimeter",
    "large_side",
    "bridge_type",
    "valve_type",
    "lamp_type",
    "laying_method",
    "box_mount_mode",
)


def _rankable_feature_value(features: dict, key: str):
    value = (features or {}).get(key)
    if value not in (None, "", [], {}):
        return value
    for nested_key in ("numeric_params", "specs"):
        nested = (features or {}).get(nested_key) or {}
        if isinstance(nested, dict):
            value = nested.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _rankable_pool_feature_missing_fields(features: dict) -> list[str]:
    missing = [
        field for field in _RANKABLE_POOL_CORE_FEATURES
        if _rankable_feature_value(features, field) in (None, "", [], {})
    ]
    if not any(
        _rankable_feature_value(features, field) not in (None, "", [], {})
        for field in _RANKABLE_POOL_PRIMARY_PARAM_FIELDS
    ):
        missing.append("primary_params")
    return missing


def _merge_rankable_candidate_features(existing: dict, rebuilt: dict) -> dict:
    """Merge no-store parser output into a cached feature shell.

    Existing values win because they may come from a curated feature store. The
    rebuilt no-store values only fill gaps, so this step supplies ranking fuel
    without changing the feature contract into a hidden final-answer rule.
    """
    merged = dict(rebuilt or {})
    existing = dict(existing or {})
    for key, value in existing.items():
        if key == "numeric_params":
            continue
        if value not in (None, "", [], {}):
            merged[key] = value

    numeric_params = dict((rebuilt or {}).get("numeric_params") or {})
    existing_numeric = existing.get("numeric_params") or {}
    if isinstance(existing_numeric, dict):
        for key, value in existing_numeric.items():
            if value not in (None, "", [], {}):
                numeric_params[key] = value
    if numeric_params:
        merged["numeric_params"] = numeric_params
    return merged


def _materialize_rankable_pool_candidate_features(
    candidates: list[dict],
    *,
    classification: dict | None = None,
) -> list[dict]:
    """Ensure rankable-pool candidates carry canonical features before rerank.

    This is intentionally no-store: it must not write candidate_features.db or
    make any candidate a final answer by itself. It only fills the candidate
    contract consumed by downstream ranking/validation.
    """
    if not candidates:
        return list(candidates or [])

    classification = classification if isinstance(classification, dict) else {}
    default_specialty = str(
        classification.get("primary")
        or next(iter(classification.get("search_books") or []), "")
        or next(iter(classification.get("candidate_books") or []), "")
        or ""
    ).strip()
    meta = {
        "checked_count": 0,
        "existing_count": 0,
        "hydrated_existing_count": 0,
        "no_store_count": 0,
        "empty_count": 0,
        "core_ready_count": 0,
    }

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        meta["checked_count"] += 1
        features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features")
        source = "existing" if isinstance(features, dict) and features else ""
        if source:
            features = dict(features)
            meta["existing_count"] += 1

        missing_before = _rankable_pool_feature_missing_fields(features if isinstance(features, dict) else {})
        existing_features = dict(features or {}) if source else {}
        should_rebuild_no_store = not source or bool(missing_before)
        if should_rebuild_no_store:
            specialty = str(
                candidate.get("specialty")
                or default_specialty
                or ""
            ).strip()
            try:
                features = build_candidate_canonical_features_no_store(
                    candidate,
                    specialty=specialty,
                )
            except Exception as exc:
                logger.debug(
                    "rankable pool candidate feature materialization failed: "
                    f"{candidate.get('quota_id', '')} {exc}"
                )
                rebuilt_features = {}
            else:
                rebuilt_features = dict(features or {})

            if rebuilt_features:
                meta["no_store_count"] += 1
                if source:
                    meta["hydrated_existing_count"] += 1
                    features = _merge_rankable_candidate_features(existing_features, rebuilt_features)
                    source = "existing+no_store"
                else:
                    features = rebuilt_features
                    source = "no_store"
            elif not source:
                features = {}
                source = "empty"
                meta["empty_count"] += 1

        features = dict(features or {})
        if features:
            candidate["candidate_canonical_features"] = dict(features)
            candidate.setdefault("canonical_features", dict(features))

        missing_fields = _rankable_pool_feature_missing_fields(features)
        if len(missing_fields) <= len(_RANKABLE_POOL_CORE_FEATURES) - 2:
            meta["core_ready_count"] += 1
        candidate["candidate_feature_materialized"] = bool(features)
        candidate["candidate_feature_source"] = source
        candidate["candidate_feature_missing_fields"] = missing_fields

    if classification is not None:
        resolution = classification.setdefault("retrieval_resolution", {})
        resolution["rankable_pool_feature_materialization"] = meta
    return candidates

def _prepare_candidates(searcher: HybridSearcher, reranker, validator: ParamValidator,
                        search_query: str, full_query: str,
                        classification: dict,
                        bill_params: dict = None,
                        canonical_features: dict = None,
                        context_prior: dict = None,
                        bill_item_context: BillItemContext | None = None,
                        route_profile=None,
                        item: dict | None = None,
                        include_prior_candidates: bool = True,
                        adaptive_strategy: str = "standard",
                        performance_monitor: PerformanceMonitor | None = None) -> list[dict]:
    """Run hybrid search, rerank, and parameter validation for candidate preparation."""

    def _record_outer_substage(name: str, elapsed: float) -> None:
        if not isinstance(classification, dict) or not name:
            return
        resolution = classification.setdefault("retrieval_resolution", {})
        mixed_totals = resolution.setdefault("mixed_search_totals", {})
        outer = mixed_totals.setdefault("outer_substage_sec", {})
        outer[name] = round(float(outer.get(name, 0.0) or 0.0) + float(elapsed or 0.0), 6)
        mixed_totals["outer_substage_total_sec"] = round(
            sum(float(value or 0.0) for value in outer.values()),
            6,
        )
        slowest_name, _slowest_elapsed = max(
            outer.items(),
            key=lambda item: float(item[1] or 0.0),
        )
        mixed_totals["slowest_outer_substage"] = str(slowest_name)

    with (
        performance_monitor.measure("混合搜索")
        if performance_monitor is not None else nullcontext()
    ):
        search_top_k = _resolve_adaptive_search_top_k(adaptive_strategy)
        substage_started = time.perf_counter()
        try:
            candidates = cascade_search(
                searcher,
                search_query,
                classification,
                top_k=search_top_k,
                item=item,
                context_prior=context_prior,
                adaptive_strategy=adaptive_strategy,
            )
        except TypeError:
            try:
                candidates = cascade_search(
                    searcher,
                    search_query,
                    classification,
                    item=item,
                    context_prior=context_prior,
                )
            except TypeError:
                candidates = cascade_search(searcher, search_query, classification)
        _record_outer_substage("cascade_search", time.perf_counter() - substage_started)
        include_prior_candidates = bool(include_prior_candidates) and adaptive_strategy != "fast"
        if (
            include_prior_candidates
            and str(adaptive_strategy or "").strip().lower() == "deep"
            and len(candidates) >= max(3, search_top_k)
            and not bool(getattr(config, "SEARCH_PRIOR_CANDIDATES_DEEP_FULL_POOL_ENABLED", True))
        ):
            include_prior_candidates = False
        if include_prior_candidates:
            substage_started = time.perf_counter()
            prior_candidates = _collect_all_prior_candidates(
                searcher,
                search_query=search_query,
                full_query=full_query,
                classification=classification,
                item=item,
                existing_candidates=candidates,
                adaptive_strategy=adaptive_strategy,
            )
            _record_outer_substage("prior_collect", time.perf_counter() - substage_started)
            substage_started = time.perf_counter()
            neighbor_prior_candidates = _collect_existing_candidate_neighbor_priors(
                searcher,
                candidates,
                classification=classification,
            )
            _record_outer_substage("neighbor_prior_collect", time.perf_counter() - substage_started)
            if isinstance(classification, dict):
                resolution = classification.setdefault("retrieval_resolution", {})
                mixed_totals = resolution.setdefault("mixed_search_totals", {})
                mixed_totals["prior_collect_count"] = (
                    int(mixed_totals.get("prior_collect_count", 0) or 0) + 1
                )
                mixed_totals["prior_candidate_count"] = (
                    int(mixed_totals.get("prior_candidate_count", 0) or 0)
                    + len(prior_candidates or [])
                )
                mixed_totals["neighbor_prior_collect_count"] = (
                    int(mixed_totals.get("neighbor_prior_collect_count", 0) or 0) + 1
                )
                mixed_totals["neighbor_prior_candidate_count"] = (
                    int(mixed_totals.get("neighbor_prior_candidate_count", 0) or 0)
                    + len(neighbor_prior_candidates or [])
                )
            substage_started = time.perf_counter()
            prior_candidates.extend(neighbor_prior_candidates)
            if prior_candidates:
                candidates = _merge_prior_candidates(candidates, prior_candidates)
            _record_outer_substage("prior_merge", time.perf_counter() - substage_started)
        substage_started = time.perf_counter()
        candidates, route_scope_filter = _filter_candidates_to_route_scope(candidates, classification)
        _record_outer_substage("route_scope_filter", time.perf_counter() - substage_started)
        if isinstance(classification, dict):
            classification["route_scope_filter"] = route_scope_filter
        substage_started = time.perf_counter()
        candidates, candidate_scope_guard = _filter_candidates_to_effective_guard_scope(candidates, classification)
        _record_outer_substage("effective_guard_scope", time.perf_counter() - substage_started)
        if isinstance(classification, dict):
            classification["candidate_scope_guard"] = candidate_scope_guard
        substage_started = time.perf_counter()
        candidates = _materialize_rankable_pool_candidate_features(
            candidates,
            classification=classification,
        )
        _record_outer_substage("rankable_feature_materialization", time.perf_counter() - substage_started)

    # ? (quota_id + ???) ???RRF????????????????????
    # ??hybrid_score??????????reranker?LLM?????
    # ??????????????????quota_id?????????
    if candidates:
        seen_ids = {}
        for c in candidates:
            qid = c.get("quota_id", "")
            if not qid:
                # ?quota_id???????????????key?????
                seen_ids[f"_no_id_{len(seen_ids)}"] = c
                continue
            # ??? = quota_id + ??????????????????
            dedup_key = (qid, c.get("_source_province", ""))
            existing = seen_ids.get(dedup_key)
            if existing is None or c.get("hybrid_score", 0) > existing.get("hybrid_score", 0):
                seen_ids[dedup_key] = c
        candidates = list(seen_ids.values())
        # ?????? hybrid_score ????????????????????
        candidates.sort(key=HybridSearcher._stable_result_identity)
        candidates.sort(key=HybridSearcher._hybrid_result_sort_key)

    # ???????????????????
    if candidates and len(candidates) > 1:
        with (
            performance_monitor.measure("重排")
            if performance_monitor is not None else nullcontext()
        ):
            prerank_candidates = list(candidates)
            rerank_input = prerank_candidates
            if str(adaptive_strategy or "").strip().lower() == "fast":
                rerank_input = prerank_candidates[: min(len(prerank_candidates), 8)]
            try:
                candidates = reranker.rerank(
                    search_query,
                    rerank_input,
                    route_profile=route_profile,
                )
            except TypeError:
                candidates = reranker.rerank(search_query, rerank_input)
            candidates = _retain_knowledge_prior_candidates(candidates, prerank_candidates)
            candidates = _retain_primary_stage_candidates(candidates, prerank_candidates)
    if candidates:
        # ?classification???search_books???v3 LTR??book_match?
        search_books = classification.get("search_books", []) if classification else []
        candidates = measure_call(
            performance_monitor,
            "候选打分",
            _validate_candidates_with_context,
            validator,
            full_query,
            candidates,
            supplement_query=search_query,
            bill_params=bill_params,
            search_books=search_books,
            canonical_features=canonical_features,
            context_prior=context_prior,
            bill_item_context=bill_item_context,
        )
        rankable_candidates, rejected_hard_fail_candidates = filter_param_hard_fail_candidates(candidates)
        if len(rankable_candidates) > 1:
            rankable_candidates = sort_candidates_with_stage_priority(rankable_candidates)
        candidates = rankable_candidates + rejected_hard_fail_candidates
    return candidates


def _collect_all_prior_candidates(searcher: HybridSearcher, *,
                                  search_query: str,
                                  full_query: str,
                                  classification: dict | None,
                                  item: dict | None,
                                  existing_candidates: list[dict] | None = None,
                                  adaptive_strategy: str | None = None) -> list[dict]:
    prior_candidates: list[dict] = []
    classification_ref = classification if isinstance(classification, dict) else None
    classification = dict(classification or {})
    base_books = list(classification.get("search_books", []) or [])
    strategy = str(adaptive_strategy or "standard").strip().lower()

    def _record_prior_collect_trace(search_target) -> None:
        trace = getattr(search_target, "_last_prior_collect_trace", None)
        if not isinstance(trace, dict) or classification_ref is None:
            return
        resolution = classification_ref.setdefault("retrieval_resolution", {})
        mixed_totals = resolution.setdefault("mixed_search_totals", {})
        source_sec = mixed_totals.setdefault("prior_source_substage_sec", {})
        for key, value in dict(trace.get("source_substage_sec") or {}).items():
            source_sec[key] = round(
                float(source_sec.get(key, 0.0) or 0.0) + float(value or 0.0),
                6,
            )
        source_counts = mixed_totals.setdefault("prior_source_candidate_count", {})
        for key, value in dict(trace.get("source_candidate_count") or {}).items():
            source_counts[key] = int(source_counts.get(key, 0) or 0) + int(value or 0)
        skipped = list(trace.get("skipped_sources") or [])
        if skipped:
            mixed_totals.setdefault("prior_source_skipped", []).extend(skipped)
        if trace.get("lightweight_standard_c5"):
            mixed_totals["prior_lightweight_standard_c5_count"] = (
                int(mixed_totals.get("prior_lightweight_standard_c5_count", 0) or 0) + 1
            )

    def _collect_from(search_target, books: list[str] | None, *, source_province: str = "") -> None:
        collector = getattr(search_target, "collect_prior_candidates", None)
        if not callable(collector):
            return
        try:
            rows = collector(
                search_query,
                full_query=full_query,
                books=books,
                item=item,
                exact_only=bool(source_province),
                adaptive_strategy=adaptive_strategy or "standard",
            )
        except TypeError:
            try:
                rows = collector(
                    search_query,
                    full_query=full_query,
                    books=books,
                    item=item,
                )
            except Exception as e:
                logger.debug(
                    f"prior candidate collect failed: "
                    f"{getattr(search_target, 'province', source_province) or 'unknown'} {e}"
                )
                return
        except Exception as e:
            logger.debug(
                f"prior candidate collect failed: "
                f"{getattr(search_target, 'province', source_province) or 'unknown'} {e}"
            )
            return
        _record_prior_collect_trace(search_target)
        for row in rows or []:
            candidate = dict(row)
            if source_province and not candidate.get("_source_province"):
                candidate["_source_province"] = source_province
            prior_candidates.append(candidate)

    if _should_search_target_for_books(searcher, base_books):
        _collect_from(searcher, base_books)

    if (
        strategy == "standard"
        and len(existing_candidates or []) >= CASCADE_MIN_CANDIDATES
    ):
        return prior_candidates

    aux_books = _normalize_fallbacks(
        base_books
        or list(classification.get("candidate_books", []) or [])
        or (
            ([classification.get("primary")] if classification.get("primary") else [])
            + list(classification.get("fallbacks", []) or [])
        )
    )
    for aux in list(getattr(searcher, "aux_searchers", []) or []):
        if not _should_search_target_for_books(aux, aux_books):
            continue
        resolved_books = _resolve_search_books_for_target(aux, search_query, aux_books) or []
        if not resolved_books and not _allows_unresolved_open_search(aux, aux_books):
            continue
        _collect_from(
            aux,
            resolved_books or None,
            source_province=str(getattr(aux, "province", "") or ""),
        )

    return prior_candidates


def _book_code_aliases(books: list[str] | None) -> set[str]:
    aliases: set[str] = set()
    for book in books or []:
        value = str(book or "").strip().upper()
        if not value:
            continue
        aliases.add(value)
        match = re.match(r"^C0*(\d+)$", value)
        if match:
            aliases.add(match.group(1))
        elif re.match(r"^\d+$", value):
            aliases.add(f"C{int(value)}")
    return aliases


def _collect_existing_candidate_neighbor_priors(
    searcher: HybridSearcher,
    candidates: list[dict],
    *,
    classification: dict | None,
    top_k: int = 8,
) -> list[dict]:
    if not candidates:
        return []
    classification = dict(classification or {})
    allowed_books = _book_code_aliases(
        list(classification.get("search_books", []) or [])
        or list(classification.get("candidate_books", []) or [])
        or ([classification.get("primary")] if classification.get("primary") else [])
    )
    if not allowed_books:
        return []

    materialize = getattr(searcher, "_materialize_quota_candidate", None)
    if not callable(materialize):
        return []

    existing_ids = {
        str(candidate.get("quota_id", "") or "").strip()
        for candidate in candidates
        if str(candidate.get("quota_id", "") or "").strip()
    }
    collected: list[dict] = []
    for candidate in candidates[:20]:
        quota_id = str(candidate.get("quota_id", "") or "").strip()
        match = re.match(r"^(.+-)(\d+)$", quota_id)
        if not match:
            continue
        prefix = match.group(1)
        number = int(match.group(2))
        prefix_book = prefix.rstrip("-").split("-")[0].upper()
        if prefix_book and prefix_book not in allowed_books:
            continue
        for offset in (-2, -1, 1, 2):
            neighbor_id = f"{prefix}{number + offset}"
            if neighbor_id in existing_ids:
                continue
            materialized = materialize(neighbor_id)
            if not isinstance(materialized, dict):
                continue
            if str(materialized.get("quota_id", "") or "").strip() != neighbor_id:
                continue
            if not str(materialized.get("name", "") or "").strip():
                continue
            neighbor = dict(materialized)
            neighbor.update({
                "quota_id": neighbor_id,
                "name": str(materialized.get("name", "") or "").strip(),
                "unit": str(materialized.get("unit", "") or "").strip(),
                "candidate_canonical_features": materialized.get("candidate_canonical_features")
                or candidate.get("candidate_canonical_features"),
                "canonical_features": materialized.get("canonical_features")
                or candidate.get("canonical_features"),
                "neighbor_materialized_name": materialized.get("name", ""),
            })
            neighbor["match_source"] = "existing_candidate_neighbor"
            neighbor["candidate_neighbor_seed"] = quota_id
            neighbor["knowledge_prior_sources"] = ["candidate_neighbor"]
            neighbor["knowledge_prior_score"] = 0.50 - min(abs(offset), 2) * 0.03
            collected.append(neighbor)
            existing_ids.add(neighbor_id)
            if len(collected) >= top_k:
                return collected
    return collected


def _merge_prior_candidates(candidates: list[dict], prior_candidates: list[dict]) -> list[dict]:
    working = list(candidates or [])

    def _median(values: list[float], default: float = 0.0) -> float:
        if not values:
            return default
        values = sorted(values)
        return values[len(values) // 2]

    def _promote(candidate: dict, peers: list[dict]) -> dict:
        candidate = dict(candidate)
        candidate["hybrid_score"] = _median(
            [float(c.get("hybrid_score", c.get("rerank_score", 0.0)) or 0.0) for c in peers],
            float(candidate.get("knowledge_prior_score", 0.0) or 0.0),
        )
        candidate["rerank_score"] = _median(
            [float(c.get("rerank_score", c.get("hybrid_score", 0.0)) or 0.0) for c in peers],
            candidate["hybrid_score"],
        )
        candidate["semantic_rerank_score"] = float(candidate.get("semantic_rerank_score", candidate["rerank_score"]) or candidate["rerank_score"])
        candidate["spec_rerank_score"] = float(candidate.get("spec_rerank_score", candidate["rerank_score"]) or candidate["rerank_score"])
        candidate["active_rerank_score"] = candidate["rerank_score"]
        return candidate

    for prior in prior_candidates or []:
        quota_id = str(prior.get("quota_id", "") or "").strip()
        quota_name = str(prior.get("name", "") or "").strip()
        prior_source_province = str(prior.get("_source_province", "") or "").strip()
        if not quota_id or not quota_name:
            continue
        merged = False
        for idx, existing in enumerate(working):
            if str(existing.get("quota_id", "") or "").strip() != quota_id:
                continue
            if str(existing.get("_source_province", "") or "").strip() != prior_source_province:
                continue
            updated = dict(existing)
            for field, value in prior.items():
                if field in {"quota_id", "name", "unit"}:
                    continue
                if field == "knowledge_prior_sources":
                    updated[field] = list(set(updated.get(field) or []) | set(value or []))
                    continue
                if field not in updated or updated.get(field) in (None, "", 0):
                    updated[field] = value
            if prior.get("knowledge_prior_score"):
                updated["knowledge_prior_score"] = max(
                    float(updated.get("knowledge_prior_score", 0.0) or 0.0),
                    float(prior.get("knowledge_prior_score", 0.0) or 0.0),
                )
            working[idx] = updated
            merged = True
            break
        if merged:
            continue
        working.append(_promote(prior, working))

    working.sort(key=HybridSearcher._stable_result_identity)
    working.sort(
        key=lambda candidate: (
            float(candidate.get("hybrid_score", 0.0) or 0.0),
            float(candidate.get("knowledge_prior_score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return working


def _retain_knowledge_prior_candidates(candidates: list[dict],
                                       prerank_candidates: list[dict]) -> list[dict]:
    retained = list(candidates or [])
    retained_keys = {
        HybridSearcher._stable_result_identity(candidate)
        for candidate in retained
    }
    for candidate in prerank_candidates or []:
        if not candidate.get("knowledge_prior_sources"):
            continue
        identity = HybridSearcher._stable_result_identity(candidate)
        if identity in retained_keys:
            continue
        retained.append(candidate)
        retained_keys.add(identity)
    return retained


def _retain_primary_stage_candidates(candidates: list[dict],
                                     prerank_candidates: list[dict],
                                     *,
                                     limit: int = 5) -> list[dict]:
    retained = list(candidates or [])
    retained_keys = {
        HybridSearcher._stable_result_identity(candidate)
        for candidate in retained
    }
    added = 0
    for candidate in prerank_candidates or []:
        stages = set(candidate.get("_cascade_stages") or [])
        if candidate.get("_cascade_stage") != "primary" and "primary" not in stages:
            continue
        identity = HybridSearcher._stable_result_identity(candidate)
        if identity in retained_keys:
            continue
        retained.append(candidate)
        retained_keys.add(identity)
        added += 1
        if added >= limit:
            break
    if added:
        retained.sort(key=HybridSearcher._stable_result_identity)
        retained.sort(key=HybridSearcher._hybrid_result_sort_key)
    return retained


def _build_support_surface_process_quotas(item: dict, searcher: HybridSearcher, reranker,
                                          classification: dict) -> list[dict]:
    if not isinstance(item, dict):
        return []

    full_text = " ".join(
        part for part in (item.get("name", ""), item.get("description", ""))
        if part
    ).strip()
    if not full_text:
        return []

    params = item.get("params") or text_parser.parse(full_text)
    support_scope = str(params.get("support_scope") or "").strip()
    if support_scope != "????":
        return []

    if not any(token in full_text for token in ("??", "??", "??", "???", "???", "???")):
        return []

    search_books = [
        str(book).strip()
        for book in ((classification or {}).get("search_books") or [])
        if str(book).strip() == "C12"
    ] or ["C12"]

    specs: list[tuple[str, str, object]] = []
    if "??" in full_text:
        specs.append((
            "surface_rust_remove",
            "???? ????? ??",
            lambda name: "??" in name,
        ))

    primer_token = "?????" if "?????" in full_text else ("???" if "???" in full_text else "")
    primer_count = _extract_surface_process_count(full_text, primer_token) if primer_token else 0
    if primer_token:
        specs.append((
            "surface_primer_first",
            f"????? {primer_token} ???",
            lambda name, token=primer_token: token in name and any(flag in name for flag in ("???", "??")),
        ))
        if primer_count >= 2:
            specs.append((
                "surface_primer_extra",
                f"????? {primer_token} ???",
                lambda name, token=primer_token: token in name and any(flag in name for flag in ("???", "????")),
            ))

    finish_count = max(
        _extract_surface_process_count(full_text, "???"),
        _extract_surface_process_count(full_text, "???"),
    )
    if any(token in full_text for token in ("???", "???")):
        specs.append((
            "surface_finish_first",
            "????? ??? ???",
            lambda name: ("???" in name or "???" in name) and any(flag in name for flag in ("???", "??")),
        ))
        if finish_count >= 2:
            specs.append((
                "surface_finish_extra",
                "????? ??? ???",
                lambda name: ("???" in name or "???" in name) and any(flag in name for flag in ("???", "????")),
            ))

    supplemental: list[dict] = []
    seen_ids: set[str] = set()
    for role, query, matcher in specs:
        candidate = _search_surface_process_candidate(
            searcher,
            reranker,
            query=query,
            books=search_books,
            matcher=matcher,
        )
        if not candidate:
            continue
        quota_id = str(candidate.get("quota_id") or "").strip()
        quota_name = str(candidate.get("name") or "").strip()
        if not quota_id or not quota_name or quota_id in seen_ids:
            continue
        seen_ids.add(quota_id)
        supplemental.append({
            "quota_id": quota_id,
            "name": quota_name,
            "unit": candidate.get("unit", "") or item.get("unit", ""),
            "reason": f"????:{query}",
            "reasoning": summarize_candidate_reasoning(candidate),
            "db_id": candidate.get("id"),
            "quota_role": role,
            "is_supplemental": True,
        })

    return supplemental


def _prepare_candidates_from_prepared(prepared: dict, searcher: HybridSearcher,
                                      reranker, validator: ParamValidator,
                                      *,
                                      include_prior_candidates: bool = True,
                                      performance_monitor: PerformanceMonitor | None = None):
    """??? prepared ????????????????"""
    raw_ctx = prepared["ctx"]
    if isinstance(raw_ctx, BillItemContext):
        ctx = raw_ctx
    else:
        ctx = BillItemContext.from_legacy_dict(raw_ctx)
    canonical_query = ctx.get("canonical_query") or {}
    full_query = canonical_query.get("validation_query") or ctx["full_query"]
    search_query = canonical_query.get("search_query") or ctx["search_query"]
    classification = prepared["classification"]
    adaptive_strategy = str(
        prepared.get("adaptive_strategy")
        or ctx.get("adaptive_strategy")
        or ((ctx.get("item") or {}).get("adaptive_strategy") if isinstance(ctx.get("item"), dict) else "")
        or "standard"
    ).strip().lower()

    # L5??????????miss???????????????
    item = ctx.get("item", {})
    cross_hints = []
    if isinstance(item, dict):
        cross_hints = _sanitize_cross_province_hints(item.get("_cross_province_hints", []))
        if not cross_hints:
            cross_hints = lookup_cross_province_hints(
                str(item.get("name", "") or ""),
                str(item.get("specialty", "") or ""),
                limit=3,
            )
        if cross_hints:
            item["_cross_province_hints"] = cross_hints
        else:
            item.pop("_cross_province_hints", None)
    if cross_hints:
        # ??3???????????????????query?
        appended_hints = [hint for hint in cross_hints[:3] if hint and hint not in search_query]
        if appended_hints:
            hint_text = " ".join(appended_hints)
            search_query = f"{search_query} {hint_text}"

    # L6???????????????????????
    context_hints = item.get("_context_hints", []) if isinstance(item, dict) else []
    if context_hints:
        context_text = " ".join(str(h) for h in context_hints[:3] if h)
        search_query = f"{search_query} {context_text}"

    # ????????????????????????????????
    prior_family = item.get("_prior_family", "") if isinstance(item, dict) else ""
    if prior_family and prior_family not in search_query:
        search_query = f"{search_query} {prior_family}"

    # ???????????????????????????
    item_params = ctx.get("params") or (item.get("params") if isinstance(item, dict) else None)
    candidates = _prepare_candidates(
        searcher, reranker, validator, search_query, full_query, classification,
        bill_params=item_params,
        canonical_features=ctx.get("canonical_features"),
        context_prior=ctx.get("context_prior"),
        bill_item_context=ctx,
        route_profile=item.get("query_route") if isinstance(item, dict) else None,
        item=item if isinstance(item, dict) else None,
        include_prior_candidates=(
            bool(include_prior_candidates) or adaptive_strategy == "deep"
        ) and adaptive_strategy != "fast",
        adaptive_strategy=adaptive_strategy,
        performance_monitor=performance_monitor,
    )
    if isinstance(item, dict):
        item["_supplemental_quotas"] = _build_support_surface_process_quotas(
            item,
            searcher,
            reranker,
            classification,
        )
    return (
        ctx,
        full_query,
        search_query,
        candidates,
        prepared["exp_backup"],
        prepared["rule_backup"],
    )


def _result_quota_signature(result: dict) -> tuple:
    """?????????????????????"""
    quotas = result.get("quotas") or []
    return tuple(str(q.get("quota_id", "")).strip() for q in quotas if q.get("quota_id"))

# ============================================================
# Agent快速通道
# ============================================================

def _has_fastpath_conflict(candidates: list[dict],
                           exp_backup: dict = None,
                           rule_backup: dict = None) -> bool:
    """
    快速通道冲突闸门：
    若经验/规则备选给出中高置信且与top候选不一致，则强制走LLM。
    """
    if not candidates:
        return True
    top_id = str(candidates[0].get("quota_id", "")).strip()
    if not top_id:
        return True

    for backup in (exp_backup, rule_backup):
        if not backup:
            continue
        backup_conf = safe_float(backup.get("confidence"), 0.0)
        if backup_conf < config.CONFIDENCE_YELLOW:
            continue
        backup_sig = _result_quota_signature(backup)
        if backup_sig and backup_sig[0] != top_id:
            return True
    return False


def _should_skip_agent_llm_legacy(candidates: list[dict],
                                  exp_backup: dict = None,
                                  rule_backup: dict = None,
                                  route_profile=None) -> bool:
    """
    Agent快速通道：参数匹配通过且分数达标的候选，跳过LLM直接采用搜索结果。

    设计思路：
    - 搜索+reranker+参数验证 已经能给出高质量候选
    - LLM 对"同类定额不同档位"的选择帮助有限（还是靠参数匹配定档）
    - 只有搜索结果不确定（param_match=False 或分数低）时才需要 LLM 介入
    - 通过抽检监控快通道质量，发现问题可随时调严

    快通道放行条件（全部满足才跳过LLM）：
    1. 快通道开启
    2. 候选列表非空
    3. top1参数匹配通过
    4. Reranker打分成功
    5. 无经验库/规则冲突
    6. param_score达标（≥0.60）
    7. 候选数≥2（单候选搜索质量不可靠，强制走LLM）
    8. top1和top2的reranker分差足够大（≥SCORE_GAP）
    """
    if not config.AGENT_FASTPATH_ENABLED:
        return False
    if not candidates:
        return False

    top = candidates[0]
    if not top.get("param_match", True):
        return False
    if any(c.get("reranker_failed") for c in candidates[:3]):
        return False
    if _has_fastpath_conflict(candidates, exp_backup=exp_backup, rule_backup=rule_backup):
        return False

    policy = PolicyEngine.get_route_policy(route_profile)
    top_score = safe_float(top.get("param_score"), 0.0)
    if top_score < policy.agent_fastpath_score:
        return False

    if policy.require_param_match:
        top_detail = str(top.get("param_detail", ""))
        if ("定额无" in top_detail or "未指定" in top_detail) and top_score < 0.7:
            return False

    if len(candidates) < policy.agent_fastpath_min_candidates:
        logger.debug("FastPath拦截: 单候选，强制走LLM")
        return False

    score_gap_threshold = policy.agent_fastpath_score_gap
    if score_gap_threshold > 0:
        top1_rs = safe_float(
            candidates[0].get("rerank_score", candidates[0].get("hybrid_score", 0)), 0.0)
        top2_rs = safe_float(
            candidates[1].get("rerank_score", candidates[1].get("hybrid_score", 0)), 0.0)
        gap = top1_rs - top2_rs
        logger.debug(f"FastPath分差: top1={top1_rs:.3f} top2={top2_rs:.3f} gap={gap:.3f} "
                     f"阈值={score_gap_threshold} → {'放行' if gap >= score_gap_threshold else '拦截'}")
        if gap < score_gap_threshold:
            return False

    return True


def get_fastpath_decision(candidates: list[dict],
                          exp_backup: dict = None,
                          rule_backup: dict = None,
                          route_profile=None,
                          adaptive_strategy: str | None = None):
    """Return fast-path decision details for the current candidates."""
    if str(adaptive_strategy or "").strip().lower() == "deep":
        return None

    decision = analyze_ambiguity(
        candidates,
        exp_backup=exp_backup,
        rule_backup=rule_backup,
        route_profile=route_profile,
    )
    if decision.top_quota_id:
        logger.debug(
            "FastPath判定: quota={} reason={} gap={:.3f} candidates={} audit={}".format(
                decision.top_quota_id,
                decision.reason,
                decision.top_score_gap,
                decision.candidates_count,
                getattr(decision, "audit_recommended", False),
            )
        )
    return decision


def _should_skip_agent_llm(candidates: list[dict],
                           exp_backup: dict = None,
                           rule_backup: dict = None,
                           route_profile=None,
                           adaptive_strategy: str | None = None) -> bool:
    """Decide whether the current candidates can skip Agent LLM."""
    decision = get_fastpath_decision(
        candidates,
        exp_backup=exp_backup,
        rule_backup=rule_backup,
        route_profile=route_profile,
        adaptive_strategy=adaptive_strategy,
    )
    return bool(decision and decision.can_fastpath)


def _should_audit_fastpath(decision=None) -> bool:
    """Decide whether a fast-path decision should be audited."""
    if getattr(decision, "audit_recommended", False):
        return True
    rate = safe_float(config.AGENT_FASTPATH_AUDIT_RATE, 0.0)
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return random.random() < rate


def _mark_agent_fastpath(result: dict):
    """Mark a result as having used the agent fast-path."""
    result["agent_skipped"] = True
    if result.get("match_source") == "search":
        result["match_source"] = "agent_fastpath"
    note = "Agent快速通道: 高置信候选，跳过LLM"
    explanation = (result.get("explanation") or "").strip()
    result["explanation"] = f"{explanation} | {note}" if explanation else note
    _append_trace_step(result, "agent_fastpath", skipped_llm=True)
    _append_trace_step(result, "agent_fastpath", skipped_llm=True)






