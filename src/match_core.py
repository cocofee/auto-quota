# -*- coding: utf-8 -*-
"""
匹配核心组件 — 从 match_engine.py 拆分出的底层函数

包含：
1. 工具函数（trace、fallback标准化等）
2. 经验库匹配（try_experience_match）
3. 级联搜索（cascade_search）
4. 候选准备（_prepare_candidates）
5. Agent快速通道判定

这些函数不依赖 match_pipeline 或 match_engine，只依赖外部模块。
"""

import inspect
import json
import random
import re
from contextlib import nullcontext

from loguru import logger

from src.utils import safe_float

import config
from src.ambiguity_gate import analyze_ambiguity
from src.text_parser import parser as text_parser
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.performance_monitor import PerformanceMonitor, measure_call
from src.policy_engine import PolicyEngine
from src.province_book_mapper import map_db_book_to_route_book, normalize_route_book_code
from src.specialty_classifier import detect_db_type
from src.candidate_scoring import sort_candidates_with_stage_priority


# ============================================================
# 常量
# ============================================================

# 级联搜索最少要返回的候选数量（少于此值则扩大搜索范围）
CASCADE_MIN_CANDIDATES = 5
# 规则预匹配直通阈值（低于该值时仅作为备选，不提前截断后续流程）
RULE_DIRECT_CONFIDENCE = 80
# 措施项弱关键词（需要同时无单位无工程量才跳过，防止误伤正常清单）
# 强关键词在STRONG_MEASURE_KEYWORDS中，不管有没有单位/工程量都跳过
MEASURE_KEYWORDS = [
    "操作高度增加", "超高增加",
    "特殊地区施工", "干扰增加",
]
# 强措施费关键词——名称完全是费用类，不管有没有单位/工程量都跳过
# （区别于普通关键词：普通关键词仍要求无单位无工程量才跳过）
STRONG_MEASURE_KEYWORDS = [
    "施工费", "增加费", "复测费", "措施费",
    "临时设施费", "工程保险费", "进出场费",
    "赶工费", "疫情防控",
    # 费用类条目——管理费、利润、税金等不是实体工程量，不应套定额
    "管理费", "利润", "税金", "规费",
    "企业管理费", "附加费",
    # 汇总行/分节行——"小计"、"合计"是Excel汇总行，不应套定额
    "小计", "合计",
    # 措施费具体项——脚手架/机械进出场/系统调整等统一计取，不套具体定额
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


# ============================================================
# 统一打分函数
# ============================================================

# 绮剧‘鎺掗櫎鐨勮垂鐢ㄧ被/鏆傚垪绫绘潯鐩紙闃叉琚濂楀畾棰濓級
_MEASURE_EXACT_NAMES = {
    "\u6682\u5217\u91d1\u989d",  # 暂列金额
    "\u6682\u4f30\u4ef7",  # 暂估价
    "\u4e13\u4e1a\u5de5\u7a0b\u6682\u4f30\u4ef7",  # 专业工程暂估价
    "\u4e8c\u6b21\u642c\u8fd0\u8d39",  # 二次搬运费
    "\u5df2\u5b8c\u5de5\u7a0b\u53ca\u8bbe\u5907\u4fdd\u62a4\u8d39",  # 已完工程及设备保护费
    "\u603b\u627f\u5305\u670d\u52a1\u8d39",
    "\u9884\u7b97\u5305\u5e72\u8d39",
    "\u5de5\u7a0b\u4f18\u8d28\u8d39",
    "\u73b0\u573a\u7b7e\u8bc1\u8d39\u7528",
    "\u7a0e\u524d\u5de5\u7a0b\u9020\u4ef7",
    "\u603b\u9020\u4ef7",
    "\u4eba\u5de5\u8d39",
    "\u6982\u7b97\u5e45\u5ea6\u5dee",
    "\u7d22\u8d54\u8d39\u7528",
    "\u5176\u4ed6\u9879\u76ee",
    "\u5176\u4ed6\u8d39\u7528",
    "\u8ba1\u65e5\u5de5",
    "\u5730\u4e0b\u7ba1\u7ebf\u4ea4\u53c9\u964d\u6548\u8d39",
    "\u589e\u503c\u7a0e\u9500\u9879\u7a0e\u989d",
}

# 妯＄硦鍖归厤鍏抽敭璇嶏紙涓婇潰 exact 鍏堟嫤锛岃繖閲屽仛鍙樹綋鍏滃簳锛?
_MEASURE_CONTAINS_KEYWORDS = (
    "\u4e8c\u6b21\u642c\u8fd0",
    "\u6682\u5217\u91d1\u989d",
    "\u6682\u4f30\u4ef7",
    "\u5df2\u5b8c\u5de5\u7a0b\u53ca\u8bbe\u5907\u4fdd\u62a4",
)


def calculate_confidence(param_score: float, param_match: bool = True,
                         name_bonus: float = 0.0,
                         score_gap: float = 1.0,
                         rerank_score: float = 0.0,
                         family_aligned: bool = False,
                         family_hard_conflict: bool = False,
                         candidates_count: int = 20,
                         is_ambiguous_short: bool = False) -> int:
    """
    多信号置信度计算（v2，2026-03-06 校准版）

    综合 param_score、品类核心词匹配度、top1/top2差距、搜索相关性
    来计算置信度，解决旧版 param_score*95 严重虚高的问题。

    旧版问题：自评95%的实际准确率只有35%。
    新版目标：置信度分段应与实际准确率单调递增对齐。

    参数:
        param_score: 参数匹配分数（0.0~1.0）
        param_match: 参数是否匹配通过
        name_bonus: 品类核心词匹配度（0.0~1.0，来自param_validator）
        score_gap: top1与top2的综合分差距（0.0~1.0，越大越确定）
        rerank_score: 搜索语义相关性（0.0~1.0，来自reranker/hybrid_score）
        candidates_count: 有效候选数量

    返回:
        置信度（0~95的整数）
    """
    try:
        ps = float(param_score)
    except (TypeError, ValueError):
        ps = 0.5 if param_match else 0.0

    try:
        nb = float(name_bonus)
    except (TypeError, ValueError):
        nb = 0.0
    nb = min(max(nb, 0.0), 1.0)

    try:
        rr = float(rerank_score)
    except (TypeError, ValueError):
        rr = 0.0
    rr = min(max(rr, 0.0), 1.0)

    if not param_match:
        # 参数不匹配：封顶50，确保不会进绿灯区
        mismatch_base = max(int(ps * 50), 15)
        if family_hard_conflict:
            return mismatch_base
        if not family_aligned:
            return min(mismatch_base, 58)

        name_part = min(nb / 0.3, 1.0) * 10.0
        rerank_part = rr * 8.0
        confidence = mismatch_base + name_part + rerank_part + 8.0
        return max(min(int(confidence), 82), 15)

    # === 基础分（param_score贡献，满分40分） ===
    base = ps * 40.0

    # === 品类核心词匹配（满分20分） ===
    # name_bonus是关键区分信号：品类对了才可能选对
    # 实际数据中name_bonus范围约0~0.5，用非线性映射放大
    try:
        nb = float(name_bonus)
    except (TypeError, ValueError):
        nb = 0.0
    nb = min(nb, 1.0)
    # nb>=0.3视为品类完全命中，给满分
    nb_normalized = min(nb / 0.15, 1.0)
    name_part = nb_normalized * 20.0

    # === top1/top2差距（满分15分） ===
    # score_gap实际范围很小(0~0.2)，用非线性映射放大区分度
    # gap>=0.15视为"明显领先"给满分，gap=0给基础3分（不再给0）
    # 修复#3：多候选同分时gap=0→虚低问题，基础3分反映"至少通过了品类审核"
    try:
        gap = float(score_gap)
    except (TypeError, ValueError):
        gap = 0.0
    gap = min(max(gap, 0.0), 1.0)
    # 基础3分 + 差距最多12分 = 仍是满分15
    gap_normalized = min(gap / 0.05, 1.0)
    gap_part = gap_normalized * 12.0 + 3.0

    # === 搜索语义相关性（满分10分） ===
    # rerank_score范围通常在0~1，但实际高质量结果一般0.3~0.8
    try:
        rr = float(rerank_score)
    except (TypeError, ValueError):
        rr = 0.0
    rerank_part = min(rr, 1.0) * 10.0

    # 基础得分（满分85）
    raw = base + name_part + gap_part + rerank_part

    # === 奖励项：多信号一致性加分 ===
    # 当品类词命中+参数满分+有差距时，说明匹配很可靠，额外加分
    bonus = 0.0
    good_signals = 0
    if nb_normalized >= 0.5:
        good_signals += 1
    if ps >= 0.9:
        good_signals += 1
    if gap_normalized >= 0.5:
        good_signals += 1
    if rr >= 0.3:
        good_signals += 1
    # 3个以上好信号一致：加10分（可到95）
    if good_signals >= 3:
        bonus += 10.0

    # === 惩罚项 ===
    penalty = 0.0

    # 品类核心词完全没命中：强惩罚（最可疑的虚高信号）
    if nb < 0.01:
        penalty += 12.0

    # 候选太少：召回可能有问题
    if candidates_count < 3:
        penalty += 8.0
    elif candidates_count < 5:
        penalty += 4.0

    # 短名称歧义项：封顶黄灯（最高75），防止信息不足时误给绿灯
    if is_ambiguous_short:
        penalty += 5.0
        result = int(max(raw + bonus - penalty, 10))
        return min(result, 75)

    result = int(max(raw + bonus - penalty, 10))
    return min(result, 95)


# ============================================================
# 工具函数
# ============================================================

def infer_confidence_family_alignment(candidate: dict) -> bool:
    """Infer whether a param-mismatch fallback is still same-family enough for yellow confidence."""
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
    """把主材字段收敛为 list[dict]，兼容 JSON 字符串和异常值。"""
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


def _summarize_candidates_for_trace(candidates: list[dict], top_n: int = 3) -> list[dict]:
    """抽取候选摘要，避免 trace 过大。"""
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
    """抽取候选解释，供 trace/结果输出复用。"""
    candidate = candidate or {}
    reasoning = {
        "param_match": bool(candidate.get("param_match", True)),
        "param_score": safe_float(candidate.get("param_score"), 0.0),
        "param_tier": int(candidate.get("param_tier", 1) or 1),
        "name_bonus": safe_float(candidate.get("name_bonus"), 0.0),
        "rerank_score": safe_float(
            candidate.get("rerank_score", candidate.get("hybrid_score", 0)), 0.0
        ),
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


def _append_trace_step(result: dict, stage: str, **fields):
    """向结果追加统一 trace 步骤。"""
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
    """保证每条结果都带统一 trace 结构。"""
    if not isinstance(result, dict):
        return
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
    """把fallback输入统一为去重后的字符串列表。"""
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
    """标准化专业分类结果，避免fallback类型异常导致后续崩溃。"""
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
                                      context_prior: dict = None) -> list[dict]:
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
    if "reorder_candidates" in param_names:
        kwargs["reorder_candidates"] = False
    return validator.validate_candidates(full_query, candidates, **kwargs)


# ============================================================
# 经验库匹配
# ============================================================

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

    # 组合清单完整文本（名称+特征描述）
    bill_text = f"{item.get('name', '')} {item.get('description', '')}".strip()

    main_quota = quotas[0]  # 只验证主定额（第一条）
    main_quota_id = main_quota.get("quota_id", "")
    main_quota_name = main_quota.get("name", "")

    # ===== 方法1：用规则校验器检查档位（处理回路/容量/截面等家族参数） =====
    # 这能发现"7回路"不应该套"4回路以内"这类错误
    rule_validated = False  # 标记规则校验器是否已验证通过
    if rule_validator and rule_validator.rules and main_quota_id:
        family = rule_validator.family_index.get(main_quota_id)
        if family:
            tiers = family.get("tiers")
            if tiers:
                # 从清单文本中提取参数值（如"7回路" → 7）
                bill_value = rule_validator._extract_param_value(bill_text, family)
                if bill_value is not None:
                    # 计算正确的档位（向上取档：≥7的最小档 → 8）
                    correct_tier = rule_validator._find_correct_tier(bill_value, tiers)
                    if correct_tier is not None:
                        correct_quota_id = rule_validator._find_quota_by_tier(
                            family, correct_tier)
                        if correct_quota_id and correct_quota_id != main_quota_id:
                            # 档位不对！经验库给的定额参数范围不覆盖当前清单
                            logger.info(
                                f"经验库参数校验失败: '{bill_text[:40]}' "
                                f"参数值{bill_value}→应套档位{correct_tier}, "
                                f"但经验库给的是{main_quota_id}, 拒绝经验库结果")
                            return None
                        else:
                            # 规则校验器确认档位正确（包括向上取档的情况）
                            rule_validated = True

    # ===== 方法2：用参数提取器对比基本参数（DN/截面/材质等） =====
    # 这能发现"DN150"不应该套"DN100以内"这类错误
    # 方法1只验证"家族参数"（如回路数），方法2验证DN/截面/材质等基础参数。
    # 即使方法1已通过，方法2仍应执行（防止回路对但DN不对的情况）。
    # 精确匹配(is_exact=True)且方法1已验证时，用宽松模式：
    #   只拦截硬参数超档（DN/截面/kVA，score=0.0），不因材质名称差异误杀。
    #   原因：用户确认的文本→定额映射中，材质名称可能不同（如"射频同轴电缆"≠"同轴电缆"），
    #   这类差异不应导致拒绝，但DN/截面/kVA超档是真正的错误。
    if main_quota_name:
        bill_params = text_parser.parse(bill_text)
        quota_params = text_parser.parse(main_quota_name)
        if bill_params and quota_params:
            is_match, score = text_parser.params_match(bill_params, quota_params)
            if not is_match:
                if rule_validated and is_exact and score > 0.0:
                    # 宽松模式：精确匹配+方法1确认+非硬参数超档 → 放行
                    # score>0.0 说明不是DN/截面/kVA硬超档（硬超档直接返回0.0），
                    # 而是材质等软参数差异，用户确认的映射不应被误杀
                    logger.debug(
                        f"经验库精确匹配参数软差异(放行): '{bill_text[:40]}' "
                        f"score={score:.2f}, 方法1已确认档位正确")
                else:
                    logger.info(
                        f"经验库参数校验失败(方法2): '{bill_text[:40]}' "
                        f"清单参数{bill_params} vs 定额'{main_quota_name[:30]}'参数{quota_params}, "
                        f"拒绝经验库结果")
                    return None

    return exp_result  # 参数验证通过，接受经验库结果


def _validate_experience_params(exp_result: dict, item: dict,
                                rule_validator=None, is_exact=False) -> dict:
    """Validate every quota attached to an experience hit."""
    _ = is_exact
    quotas = exp_result.get("quotas", [])
    if not quotas:
        return exp_result

    bill_text = f"{item.get('name', '')} {item.get('description', '')}".strip()
    bill_params = text_parser.parse(bill_text)

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

    return exp_result


def try_experience_match(query: str, item: dict, experience_db,
                         rule_validator=None, province: str = None) -> dict:
    """
    尝试从经验库匹配

    参数:
        query: 清单搜索文本
        item: 清单项目字典
        experience_db: 经验库实例
        rule_validator: 规则校验器实例（用于验证经验库结果的参数是否正确）
        province: 省份（用于限定经验库查询范围）

    返回:
        匹配结果字典，如果经验库未命中则返回 None
    """
    if experience_db is None:
        return None

    # 在经验库中搜索相似历史记录
    similar = experience_db.search_similar(
        query, top_k=3,
        min_confidence=config.EXPERIENCE_DIRECT_THRESHOLD,
        province=province,
    )

    if not similar:
        # L5跨省预热：本省无经验时，查其他省份的经验作为搜索参考
        if getattr(config, "CROSS_PROVINCE_WARMUP_ENABLED", False) and experience_db:
            try:
                cross_refs = experience_db.search_cross_province(
                    query, current_province=province or "")
                if cross_refs:
                    hint_keywords = []
                    for ref in cross_refs:
                        names = ref.get("quota_names", [])
                        # 防御：确保是字符串列表，避免字符串被拆成单字符
                        if isinstance(names, str):
                            names = [names]
                        elif isinstance(names, list):
                            names = [str(n) for n in names if n]
                        else:
                            names = []
                        hint_keywords.extend(names)
                    sanitized_hints = _sanitize_cross_province_hints(hint_keywords)
                    if sanitized_hints:
                        # 存到item上，供后续搜索使用（不直通）
                        item["_cross_province_hints"] = sanitized_hints
                        logger.debug(
                            f"L5跨省预热: {query[:30]} → "
                            f"提示={sanitized_hints[:3]}")
            except (KeyError, TypeError, ValueError, AttributeError,
                    OSError, RuntimeError, ImportError) as e:
                logger.debug(f"L5跨省搜索跳过: {e}")
        return None

    # 取第一条可直通（非stale、非候选层）的经验，避免"top1过期就整体失效"
    best = None
    for candidate in similar:
        if candidate.get("match_type") not in ("stale", "candidate"):
            best = candidate
            break

    # 全部是过期经验时，不直通
    if best is None:
        logger.debug(f"经验库命中但版本均过期，不直通: {query[:50]}")
        return None
    similarity = safe_float(best.get("similarity"), 0.0)
    exp_materials = _safe_json_materials(best.get("materials"))

    # 精确匹配（完全相同的清单文本）→ 构建结果
    if best.get("match_type") == "exact":
        quota_ids = best.get("quota_ids", [])
        quota_names = best.get("quota_names", [])
        if not quota_ids:
            logger.debug(f"经验库精确命中但定额列表为空，跳过: {query[:50]}")
            return None
        confidence = min(best.get("confidence", 80), 98)  # 经验库最高98分

        # 构建定额列表（一条清单可能对应多条定额）
        quotas = []
        for i, qid in enumerate(quota_ids):
            quotas.append({
                "quota_id": qid,
                "name": quota_names[i] if i < len(quota_names) else "",
                "unit": "",
                "reason": f"经验库精确匹配 (置信度{confidence}%, 确认{best.get('confirm_count', 1)}次)",
            })

        result = {
            "bill_item": item,
            "quotas": quotas,
            "materials": exp_materials,
            "confidence": confidence,
            "explanation": f"经验库精确匹配 (确认{best.get('confirm_count', 1)}次)",
            "match_source": "experience_exact",  # 标记匹配来源
        }
        _append_trace_step(
            result,
            "experience_exact",
            record_id=best.get("id"),
            similarity=1.0,
            confirm_count=best.get("confirm_count", 0),
            quota_ids=[q.get("quota_id", "") for q in quotas],
            materials_count=len(exp_materials),
        )

        # 参数验证：即使经验库文本完全匹配，参数也必须对
        # （同名清单不同参数的情况，如"配电箱"7回路 vs 4回路）
        # 精确匹配时方法2用宽松模式（只拦截硬参数超档，不因材质差异误杀）
        validated = _validate_experience_params(result, item, rule_validator, is_exact=True)
        if validated is None:
            return None  # 参数不匹配，拒绝经验库结果
        return validated

    # 向量相似匹配 → 相似度≥0.80才采纳
    # 原阈值0.75偏低：0.75-0.80区间可能混入用途不同但名称相似的定额
    # （如"给水管道DN100"和"排水管道DN100"相似度可能>0.78）
    # 提高到0.80后，这类不确定匹配会走搜索兜底，更安全
    if similarity >= 0.80:
        quota_ids = best.get("quota_ids", [])
        quota_names = best.get("quota_names", [])
        if not quota_ids:
            logger.debug(f"经验库相似命中但定额列表为空，跳过: {query[:50]}")
            return None
        # 相似匹配置信度稍低
        confidence = min(int(similarity * best.get("confidence", 80)), 90)

        quotas = []
        for i, qid in enumerate(quota_ids):
            quotas.append({
                "quota_id": qid,
                "name": quota_names[i] if i < len(quota_names) else "",
                "unit": "",
                "reason": f"经验库相似匹配 (相似度{similarity:.2f}, 原文: {best.get('bill_text', '')[:50]})",
            })

        result = {
            "bill_item": item,
            "quotas": quotas,
            "materials": exp_materials,
            "confidence": confidence,
            "explanation": f"经验库相似匹配 (相似度{similarity:.2f})",
            "match_source": "experience_similar",
        }
        _append_trace_step(
            result,
            "experience_similar",
            record_id=best.get("id"),
            similarity=similarity,
            confirm_count=best.get("confirm_count", 0),
            quota_ids=[q.get("quota_id", "") for q in quotas],
            materials_count=len(exp_materials),
        )

        # 参数验证：相似匹配更需要校验（文本相似但参数可能不同）
        validated = _validate_experience_params(result, item, rule_validator)
        if validated is None:
            return None
        return validated

    # 相似度不够高，不采纳
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
    """轻量经验库命中：只做 exact/normalized exact，不跑完整相似检索链。"""
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

    best = dict(best)
    normalizer = getattr(experience_db, "_normalize_record_quota_fields", None)
    if callable(normalizer):
        best = normalizer(best)

    quota_ids = best.get("quota_ids", [])
    quota_names = best.get("quota_names", [])
    if not quota_ids:
        logger.debug(f"经验库轻量精确命中但定额列表为空，跳过: {query[:50]}")
        return None

    confidence = min(best.get("confidence", 80), 98)
    exp_materials = _safe_json_materials(best.get("materials"))
    quotas = []
    for i, qid in enumerate(quota_ids):
        quotas.append({
            "quota_id": qid,
            "name": quota_names[i] if i < len(quota_names) else "",
            "unit": "",
            "reason": f"经验库精确匹配 (置信度{confidence}%, 确认{best.get('confirm_count', 1)}次)",
        })

    result = {
        "bill_item": item,
        "quotas": quotas,
        "materials": exp_materials,
        "confidence": confidence,
        "explanation": f"经验库精确匹配 (确认{best.get('confirm_count', 1)}次)",
        "match_source": "experience_exact",
    }
    _append_trace_step(
        result,
        "experience_exact_lightweight",
        record_id=best.get("id"),
        similarity=1.0,
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
# 搜索与级联
# ============================================================

def _translate_books_for_industry(c_books: list[str],
                                  quota_books: dict) -> list[str]:
    """把C1-C12翻译成行业定额实际使用的book值

    行业定额（石油/电力等）不使用C1-C12前缀，book字段是纯数字"1"-"9"。
    翻译规则：C4 → "4"，C10 → "10"，非C开头的保持原样。
    最后只保留定额库中实际存在的book值（不乱搜）。
    """
    # 收集定额库中实际存在的book值
    actual_books = set(quota_books.values()) if quota_books else set()

    translated = set()
    for book in c_books:
        if not book:
            continue
        if book.startswith("C") and book[1:].isdigit():
            # C4 → "4", C10 → "10"
            translated.add(book[1:])
        else:
            # 非C开头（如"A","D","E"等）保持原样
            translated.add(book)

    # 只保留定额库中实际存在的book值（避免乱搜不存在的册号）
    valid = [b for b in translated if b in actual_books]

    # 如果翻译后完全没有匹配的册号，说明映射失效，
    # 退化到搜全库（books=None），由后续Reranker和参数验证过滤噪音
    if not valid:
        return None

    return valid


def _merge_with_aux(main_candidates: list[dict], aux_candidates: list[dict],
                    top_k: int) -> list[dict]:
    """合并主库和辅助库搜索结果，按 hybrid_score 统一排序。

    如果辅助库无结果，直接返回主库结果（零开销）。
    去重逻辑：主库和辅助库是不同定额库，编号体系不同，不做跨库去重。
    同一辅助库内的同 quota_id 保留分数最高的那条。
    """
    if not aux_candidates:
        return main_candidates

    # 主库结果直接保留（不去重，主库内部已由 HybridSearcher 去重）
    merged = list(main_candidates)

    # 辅助库结果按"quota_id@来源库"去重（同一辅助库内可能有重复）
    aux_seen = {}
    for r in aux_candidates:
        qid = r.get("quota_id") or id(r)  # 无quota_id时用对象id，避免误合并
        source = r.get("_source_province", "aux")
        key = f"{qid}@{source}"
        score = r.get("hybrid_score", 0)
        if key not in aux_seen or score > aux_seen[key][0]:
            aux_seen[key] = (score, r)

    merged.extend(v[1] for v in aux_seen.values())
    merged.sort(key=HybridSearcher._stable_result_identity)
    merged.sort(key=HybridSearcher._hybrid_result_sort_key)
    return merged[:top_k]


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


def _cascade_search_legacy(searcher: HybridSearcher, search_query: str,
                           classification: dict, top_k: int = None,
                           item: dict | None = None,
                           context_prior: dict | None = None,
                           adaptive_strategy: str | None = None) -> list[dict]:
    """Staged search that honors book-routing constraints before escaping."""
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

    def _search_is_good_enough(found: list[dict]) -> bool:
        if len(found) < CASCADE_MIN_CANDIDATES:
            return False
        top_score = found[0].get("hybrid_score", 0)
        # 绝对分数太低时，不管分差多大都继续搜（防止主册搜到弱结果就停止）
        ABSOLUTE_QUALITY_FLOOR = 0.6
        if top_score < ABSOLUTE_QUALITY_FLOOR:
            return False
        if len(found) >= CASCADE_MIN_CANDIDATES + 2:
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
                for result in aux_results:
                    result["_source_province"] = aux.province
                aux_candidates.extend(aux_results)
            except (KeyError, TypeError, ValueError, AttributeError,
                    OSError, RuntimeError, ImportError) as e:
                logger.warning(f"aux search failed: {getattr(aux, 'province', '')}: {e}")
        return aux_candidates

    if not primary_stage_books and not expanded_stage_books:
        _record_retrieval_resolution_call(
            retrieval_resolution,
            target="main",
            stage="open",
            requested_books=[],
            resolved_books=[],
            source_province=str(getattr(searcher, "province", "") or ""),
            uses_standard_books=getattr(searcher, "uses_standard_books", None),
        )
        candidates = _search_with_optional_context(
            searcher,
            search_query,
            top_k=top_k,
            books=None,
            item=item,
            context_prior=context_prior,
        )
        if _search_is_good_enough(candidates) and bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
            return candidates[:top_k]
        return _merge_with_aux(candidates, _collect_aux_candidates(), top_k)

    best_candidates: list[dict] = []
    if primary_stage_books:
        if not _should_search_target_for_books(searcher, primary_stage_books):
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage="primary_skipped",
                requested_books=primary_stage_books,
                resolved_books=[],
                source_province=str(getattr(searcher, "province", "") or ""),
                uses_standard_books=getattr(searcher, "uses_standard_books", None),
            )
        else:
            resolved_primary_books = _resolve_search_books_for_target(
                searcher,
                search_query,
                primary_stage_books,
                allow_classifier_fallback=False,
            ) or []
            primary_open_search = (
                not bool(resolved_primary_books)
                and _allows_unresolved_open_search(searcher, primary_stage_books)
            )
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage="primary",
                requested_books=primary_stage_books,
                resolved_books=resolved_primary_books,
                source_province=str(getattr(searcher, "province", "") or ""),
                uses_standard_books=getattr(searcher, "uses_standard_books", None),
                open_search=primary_open_search,
            )
            if resolved_primary_books or primary_open_search:
                best_candidates = _search_with_optional_context(
                    searcher,
                    search_query,
                    top_k=top_k * 2,
                    books=resolved_primary_books or None,
                    item=item,
                    context_prior=context_prior,
                )
            if strategy == "standard" and len(best_candidates) >= top_k:
                if bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
                    return best_candidates[:top_k * 2]
                return _merge_with_aux(best_candidates, _collect_aux_candidates(), top_k * 2)
            if (
                resolved_primary_books
                and len(best_candidates) >= top_k
                and not getattr(searcher, "uses_standard_books", True)
            ):
                if bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
                    return best_candidates[:top_k * 2]
                return _merge_with_aux(best_candidates, _collect_aux_candidates(), top_k * 2)
            if _search_is_good_enough(best_candidates):
                if bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
                    return best_candidates[:top_k * 2]
                return _merge_with_aux(best_candidates, _collect_aux_candidates(), top_k * 2)

    if expanded_stage_books and expanded_stage_books != primary_stage_books:
        if not _should_search_target_for_books(searcher, expanded_stage_books):
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage="expanded_skipped",
                requested_books=expanded_stage_books,
                resolved_books=[],
                source_province=str(getattr(searcher, "province", "") or ""),
                uses_standard_books=getattr(searcher, "uses_standard_books", None),
            )
        else:
            resolved_expanded_books = _resolve_search_books_for_target(
                searcher,
                search_query,
                expanded_stage_books,
                allow_classifier_fallback=False,
            ) or []
            expanded_open_search = (
                not bool(resolved_expanded_books)
                and _allows_unresolved_open_search(searcher, expanded_stage_books)
            )
            _record_retrieval_resolution_call(
                retrieval_resolution,
                target="main",
                stage="expanded",
                requested_books=expanded_stage_books,
                resolved_books=resolved_expanded_books,
                source_province=str(getattr(searcher, "province", "") or ""),
                uses_standard_books=getattr(searcher, "uses_standard_books", None),
                open_search=expanded_open_search,
            )
            expanded_candidates = []
            if resolved_expanded_books or expanded_open_search:
                expanded_candidates = _search_with_optional_context(
                    searcher,
                    search_query,
                    top_k=top_k * 2,
                    books=resolved_expanded_books or None,
                    item=item,
                    context_prior=context_prior,
                )
            if expanded_candidates:
                best_candidates = expanded_candidates
            if _search_is_good_enough(expanded_candidates):
                if bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
                    return expanded_candidates[:top_k * 2]
                return _merge_with_aux(expanded_candidates, _collect_aux_candidates(), top_k * 2)

    if not allow_cross_book_escape:
        return _merge_with_aux(best_candidates, _collect_aux_candidates(), top_k)

    _record_retrieval_resolution_call(
        retrieval_resolution,
        target="main",
        stage="escape",
        requested_books=[],
        resolved_books=[],
        source_province=str(getattr(searcher, "province", "") or ""),
        uses_standard_books=getattr(searcher, "uses_standard_books", None),
    )
    candidates = _search_with_optional_context(
        searcher,
        search_query,
        top_k=top_k,
        books=None,
        item=item,
        context_prior=context_prior,
    )
    if candidates:
        best_candidates = candidates
    if _search_is_good_enough(best_candidates) and bool(getattr(config, "HYBRID_DEFER_AUX_SEARCH", True)):
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
    """判断是否为措施项/章节分隔行，这类行不应套安装定额。

    两层判断：
    1. 强关键词（施工费/增加费/措施费等）：只要名称命中就跳过，不管有没有单位/工程量
       因为措施费清单里经常填"项"作单位、"1"作工程量
    2. 弱关键词（操作高度/超高等）：需要同时满足无单位无工程量才跳过
       防止误伤正常清单（比如"超高层钢结构"不应被跳过）
    """
    # 强关键词：直接跳过
    clean_name = name.replace("\n", "").replace("\r", "").replace(" ", "").strip()
    if clean_name in _MEASURE_EXACT_NAMES:
        return True
    if any(kw in clean_name for kw in _MEASURE_CONTAINS_KEYWORDS):
        return True

    if any(kw in name for kw in STRONG_MEASURE_KEYWORDS):
        return True
    # 弱关键词：需要无单位无工程量
    if any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity:
        return True
    # 章节分隔行（如"其他"空行）
    if name.strip() == "其他" and not unit and not quantity and not desc.strip():
        return True
    # 专业标题行（如"电气工程"、"给排水工程"）——纯分组标题，不是实体清单
    # 有些Excel给标题行也填了"项"和"1"作为单位/工程量，所以不能要求无单位无工程量
    # 用明确的专业标题名单做强过滤，避免误伤真实清单（如"钢结构工程"子项）
    # clean_name already normalized above
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
    # 附属计量条目——广东/江西等省的清单常把防腐保温面积、管件数量、超高部分
    # 作为独立行列出，这些不是独立工程量清单，不应单独套定额
    _SUBSIDIARY_PATTERNS = [
        "外表面积",      # 防腐保温的管道外表面积
        "保护层面积",    # 保温保护层面积
        "超高外表面积",  # 超高部分的外表面积
        "超高保护层面积",  # 超高部分的保护层面积
        "超高长度",      # 超高部分管道长度
        "超高数量",      # 超高部分管件数量
    ]
    if clean_name in _SUBSIDIARY_PATTERNS or any(
        clean_name.startswith(p) for p in _SUBSIDIARY_PATTERNS
    ):
        return True
    # "数量(个)" / "数量（个）" — 纯管件/阀门计数行
    if clean_name.startswith("数量") and ("个" in clean_name or "只" in clean_name):
        return True
    return False


# ============================================================
# 候选准备
# ============================================================

def _prepare_candidates(searcher: HybridSearcher, reranker, validator: ParamValidator,
                        search_query: str, full_query: str,
                        classification: dict,
                        bill_params: dict = None,
                        canonical_features: dict = None,
                        context_prior: dict = None,
                        route_profile=None,
                        item: dict | None = None,
                        include_prior_candidates: bool = True,
                        adaptive_strategy: str = "standard",
                        performance_monitor: PerformanceMonitor | None = None) -> list[dict]:
    """统一执行：级联搜索 → 去重 → Reranker重排 → 参数验证。"""
    with (
        performance_monitor.measure("混合搜索")
        if performance_monitor is not None else nullcontext()
    ):
        search_top_k = _resolve_adaptive_search_top_k(adaptive_strategy)
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
        include_prior_candidates = bool(include_prior_candidates) and adaptive_strategy != "fast"
        if (
            include_prior_candidates
            and str(adaptive_strategy or "").strip().lower() == "deep"
            and len(candidates) >= max(3, search_top_k)
        ):
            include_prior_candidates = False
        if include_prior_candidates:
            prior_candidates = _collect_all_prior_candidates(
                searcher,
                search_query=search_query,
                full_query=full_query,
                classification=classification,
                item=item,
            )
            if prior_candidates:
                candidates = _merge_prior_candidates(candidates, prior_candidates)
        candidates, route_scope_filter = _filter_candidates_to_route_scope(candidates, classification)
        if isinstance(classification, dict):
            classification["route_scope_filter"] = route_scope_filter
        candidates, candidate_scope_guard = _filter_candidates_to_effective_guard_scope(candidates, classification)
        if isinstance(classification, dict):
            classification["candidate_scope_guard"] = candidate_scope_guard

    # 按 (quota_id + 来源库) 去重：RRF融合后同一定额可能因不同查询变体出现多次
    # 保留hybrid_score最高的那条，避免浪费reranker和LLM的处理资源
    # 注意：多辅助库场景下不同库可能有相同quota_id，需要用来源库区分
    if candidates:
        seen_ids = {}
        for c in candidates:
            qid = c.get("quota_id", "")
            if not qid:
                # 无quota_id的候选（异常数据），用唯一递增key保留不去重
                seen_ids[f"_no_id_{len(seen_ids)}"] = c
                continue
            # 去重键 = quota_id + 来源库省份（没有来源标记的视为主库）
            dedup_key = (qid, c.get("_source_province", ""))
            existing = seen_ids.get(dedup_key)
            if existing is None or c.get("hybrid_score", 0) > existing.get("hybrid_score", 0):
                seen_ids[dedup_key] = c
        candidates = list(seen_ids.values())
        # 去重后重新按 hybrid_score 排序（防御性：覆盖替换可能打乱插入顺序）
        candidates.sort(key=HybridSearcher._stable_result_identity)
        candidates.sort(key=HybridSearcher._hybrid_result_sort_key)

    # 单候选时重排无意义，可直接跳过提升速度
    if candidates and len(candidates) > 1:
        with (
            performance_monitor.measure("候选打分")
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
    if candidates:
        # 从classification中提取search_books（用于v3 LTR特征book_match）
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
        )
        if len(candidates) > 1:
            candidates = sort_candidates_with_stage_priority(candidates)
    return candidates


def _collect_all_prior_candidates(searcher: HybridSearcher, *,
                                  search_query: str,
                                  full_query: str,
                                  classification: dict | None,
                                  item: dict | None) -> list[dict]:
    prior_candidates: list[dict] = []
    classification = dict(classification or {})
    base_books = list(classification.get("search_books", []) or [])

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
        for row in rows or []:
            candidate = dict(row)
            if source_province and not candidate.get("_source_province"):
                candidate["_source_province"] = source_province
            prior_candidates.append(candidate)

    if _should_search_target_for_books(searcher, base_books):
        _collect_from(searcher, base_books)

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


def _parse_chinese_count_token(token: str) -> int:
    token = str(token or "").strip()
    if not token:
        return 0
    if token.isdigit():
        return int(token)
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2:
        return 10 + mapping.get(token[1], 0)
    if token.endswith("十") and len(token) == 2:
        return mapping.get(token[0], 0) * 10
    if len(token) == 2 and "十" in token:
        left, _, right = token.partition("十")
        return mapping.get(left, 0) * 10 + mapping.get(right, 0)
    return mapping.get(token, 0)


def _extract_surface_process_count(text: str, token: str) -> int:
    if not text or not token:
        return 0
    patterns = [
        rf"{re.escape(token)}[^，。；;\n]*?([0-9一二两三四五六七八九十]+)\s*道",
        rf"{re.escape(token)}[^，。；;\n]*?([0-9一二两三四五六七八九十]+)\s*遍",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        count = _parse_chinese_count_token(match.group(1))
        if count > 0:
            return count
    return 0


def _search_surface_process_candidate(searcher: HybridSearcher, reranker, *,
                                      query: str, books: list[str],
                                      matcher) -> dict | None:
    try:
        candidates = searcher.search(
            query,
            top_k=max(int(config.HYBRID_TOP_K), 8),
            books=books or None,
        )
    except Exception as e:
        logger.debug(f"表面处理附加搜索失败: query={query!r} error={e}")
        return None

    if candidates and len(candidates) > 1 and reranker is not None:
        try:
            candidates = reranker.rerank(query, candidates)
        except Exception as e:
            logger.debug(f"表面处理附加重排失败: query={query!r} error={e}")

    for candidate in candidates or []:
        name = str(candidate.get("name") or "")
        if name and matcher(name):
            return candidate
    return None


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
    if support_scope != "管道支架":
        return []

    if not any(token in full_text for token in ("除锈", "刷油", "油漆", "防锈漆", "调和漆", "调合漆")):
        return []

    search_books = [
        str(book).strip()
        for book in ((classification or {}).get("search_books") or [])
        if str(book).strip() == "C12"
    ] or ["C12"]

    specs: list[tuple[str, str, object]] = []
    if "除锈" in full_text:
        specs.append((
            "surface_rust_remove",
            "手工除锈 一般钢结构 轻锈",
            lambda name: "除锈" in name,
        ))

    primer_token = "红丹防锈漆" if "红丹防锈漆" in full_text else ("防锈漆" if "防锈漆" in full_text else "")
    primer_count = _extract_surface_process_count(full_text, primer_token) if primer_token else 0
    if primer_token:
        specs.append((
            "surface_primer_first",
            f"一般钢结构 {primer_token} 第一遍",
            lambda name, token=primer_token: token in name and any(flag in name for flag in ("第一遍", "一遍")),
        ))
        if primer_count >= 2:
            specs.append((
                "surface_primer_extra",
                f"一般钢结构 {primer_token} 增一遍",
                lambda name, token=primer_token: token in name and any(flag in name for flag in ("增一遍", "增加一遍")),
            ))

    finish_count = max(
        _extract_surface_process_count(full_text, "调和漆"),
        _extract_surface_process_count(full_text, "调合漆"),
    )
    if any(token in full_text for token in ("调和漆", "调合漆")):
        specs.append((
            "surface_finish_first",
            "一般钢结构 调和漆 第一遍",
            lambda name: ("调和漆" in name or "调合漆" in name) and any(flag in name for flag in ("第一遍", "一遍")),
        ))
        if finish_count >= 2:
            specs.append((
                "surface_finish_extra",
                "一般钢结构 调和漆 增一遍",
                lambda name: ("调和漆" in name or "调合漆" in name) and any(flag in name for flag in ("增一遍", "增加一遍")),
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
            "reason": f"附加定额:{query}",
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
    """从统一 prepared 上下文中取字段并执行候选流水线。"""
    ctx = prepared["ctx"]
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

    # L5跨省预热：如果经验库miss时留下了跨省提示，增强搜索查询
    item = ctx.get("item", {})
    cross_hints = []
    if isinstance(item, dict):
        cross_hints = _sanitize_cross_province_hints(item.get("_cross_province_hints", []))
        if cross_hints:
            item["_cross_province_hints"] = cross_hints
        else:
            item.pop("_cross_province_hints", None)
    if cross_hints:
        # 取前3个提示关键词追加到搜索查询（不影响原始query）
        appended_hints = [hint for hint in cross_hints[:3] if hint and hint not in search_query]
        if appended_hints:
            hint_text = " ".join(appended_hints)
            search_query = f"{search_query} {hint_text}"

    # L6局部上下文提示：短名称歧义项从邻居获取的提示词
    context_hints = item.get("_context_hints", []) if isinstance(item, dict) else []
    if context_hints:
        context_text = " ".join(str(h) for h in context_hints[:3] if h)
        search_query = f"{search_query} {context_text}"

    # 频率先验兜底：没有邻居上下文时，用历史统计的定额族名作为搜索提示
    prior_family = item.get("_prior_family", "") if isinstance(item, dict) else ""
    if prior_family and prior_family not in search_query:
        search_query = f"{search_query} {prior_family}"

    # 从清单项获取已清洗的参数，传给参数验证（避免重新提取）
    item_params = item.get("params") if isinstance(item, dict) else None
    candidates = _prepare_candidates(
        searcher, reranker, validator, search_query, full_query, classification,
        bill_params=item_params,
        canonical_features=ctx.get("canonical_features"),
        context_prior=ctx.get("context_prior"),
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
    """返回结果中的定额编号签名，用于一致性比对。"""
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
    # 参数不匹配 → 需要LLM判断
    if not top.get("param_match", True):
        return False
    # Reranker失败 → 候选排序不可信，必须走LLM
    if any(c.get("reranker_failed") for c in candidates[:3]):
        return False
    # 经验库/规则与搜索结果冲突 → 需要LLM仲裁
    if _has_fastpath_conflict(candidates, exp_backup=exp_backup, rule_backup=rule_backup):
        return False

    policy = PolicyEngine.get_route_policy(route_profile)
    top_score = safe_float(top.get("param_score"), 0.0)
    if top_score < policy.agent_fastpath_score:
        return False

    # ===== 无参数候选盲区检查 =====
    # 清单有参数（DN/截面/回路等）但top1的param_score较低（定额无参数或参数不确定）
    # → 强制走LLM，避免无参数候选因语义得分高而盲通
    if policy.require_param_match:
        top_detail = str(top.get("param_detail", ""))
        if ("定额无" in top_detail or "未指定" in top_detail) and top_score < 0.7:
            return False

    # ===== 单候选拦截 =====
    # 只搜到1条结果时，搜索质量不可靠（没有对比对象），强制走LLM仲裁
    if len(candidates) < policy.agent_fastpath_min_candidates:
        logger.debug("FastPath拦截: 单候选，强制走LLM")
        return False

    # ===== 搜索排名分差检查 =====
    # 当top1和top2的reranker分数太接近时，搜索结果不确定，需要LLM仲裁
    # 这解决了"无参数可验证"时FastPath盲目信任搜索排序的问题
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
            return False  # 分差不够，让LLM来选

    return True


def get_fastpath_decision(candidates: list[dict],
                          exp_backup: dict = None,
                          rule_backup: dict = None,
                          route_profile=None,
                          adaptive_strategy: str | None = None):
    """返回快通道判定详情，供主流程决定是否跳过LLM以及是否优先抽检。"""
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
    """显式歧义门控：高置信候选走快通道，歧义候选交给 Agent。"""
    decision = get_fastpath_decision(
        candidates,
        exp_backup=exp_backup,
        rule_backup=rule_backup,
        route_profile=route_profile,
        adaptive_strategy=adaptive_strategy,
    )
    return bool(decision and decision.can_fastpath)


def _should_audit_fastpath(decision=None) -> bool:
    """按风险优先、其余按配置比例抽检快速通道结果。"""
    if getattr(decision, "audit_recommended", False):
        return True
    rate = safe_float(config.AGENT_FASTPATH_AUDIT_RATE, 0.0)
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    return random.random() < rate


def _mark_agent_fastpath(result: dict):
    """为快速通道结果打标，便于统计与审计。"""
    result["agent_skipped"] = True
    if result.get("match_source") == "search":
        result["match_source"] = "agent_fastpath"
    note = "Agent快速通道: 高置信候选，跳过LLM"
    explanation = (result.get("explanation") or "").strip()
    result["explanation"] = f"{explanation} | {note}" if explanation else note
    _append_trace_step(result, "agent_fastpath", skipped_llm=True)
