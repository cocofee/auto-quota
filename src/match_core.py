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

import json
import random

from loguru import logger

import config
from src.text_parser import parser as text_parser
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator


# ============================================================
# 常量
# ============================================================

# 级联搜索最少要返回的候选数量（少于此值则扩大搜索范围）
CASCADE_MIN_CANDIDATES = 3
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
    "脚手架搭拆", "脚手架费",
    "系统调整费", "系统调试费",
    "大型机械进出场", "大型机械安拆",
    "高层施工增加", "高层增加费",
    "材料补差", "材料价差", "价差调整",
    "垃圾清运", "垃圾弃置",
    "安全文明施工", "文明施工费",
    "夜间施工", "冬雨季施工",
]


# ============================================================
# 统一打分函数
# ============================================================

def calculate_confidence(param_score: float, param_match: bool = True,
                         name_bonus: float = 0.0,
                         score_gap: float = 1.0,
                         rerank_score: float = 0.0,
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

    if not param_match:
        # 参数不匹配：封顶50，确保不会进绿灯区
        return max(int(ps * 50), 15)

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
    nb_normalized = min(nb / 0.3, 1.0)
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
    gap_normalized = min(gap / 0.15, 1.0)
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

def _safe_float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
            "param_score": _safe_float_value(c.get("param_score"), 0.0),
            "rerank_score": _safe_float_value(
                c.get("rerank_score", c.get("hybrid_score", 0)), 0.0
            ),
        })
    return summary


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
    trace["final_confidence"] = _safe_float_value(result.get("confidence"), 0.0)
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
    trace["final_confidence"] = _safe_float_value(result.get("confidence"), 0.0)
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


def _normalize_classification(classification: dict) -> dict:
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


# ============================================================
# 经验库匹配
# ============================================================

def _validate_experience_params(exp_result: dict, item: dict,
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
                    if hint_keywords:
                        # 存到item上，供后续搜索使用（不直通）
                        item["_cross_province_hints"] = hint_keywords[:5]
                        logger.debug(
                            f"L5跨省预热: {query[:30]} → "
                            f"提示={hint_keywords[:3]}")
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
    similarity = _safe_float_value(best.get("similarity"), 0.0)
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
    merged.sort(key=lambda x: x.get("hybrid_score", 0), reverse=True)
    return merged[:top_k]


def cascade_search(searcher: HybridSearcher, search_query: str,
                   classification: dict, top_k: int = None) -> list[dict]:
    """
    级联搜索：主专业+借用专业一起搜 → 不够则全库搜索

    设计思想：不要只搜主专业（太窄，容易漏掉正确答案），
    直接在"主专业+借用专业"范围内搜索，让Reranker和参数验证来挑最好的。

    辅助定额库：
    无论清单专业是什么（C/A/D/E），都同时搜索所有辅助库，
    结果与主库合并后按 hybrid_score 统一排序。

    参数:
        searcher: 混合搜索引擎实例（可能挂载了 aux_searchers）
        search_query: 搜索文本
        classification: specialty_classifier.classify() 的返回值
        top_k: 返回候选数量

    返回:
        候选定额列表
    """
    top_k = top_k or config.HYBRID_TOP_K
    classification = _normalize_classification(classification)
    primary = classification.get("primary")
    fallbacks = classification.get("fallbacks", [])

    # 如果无法判断专业（primary=None），直接全库搜索
    if not primary:
        return searcher.search(search_query, top_k=top_k, books=None)

    # ---- 辅助定额库搜索（与主库并行，不互斥） ----
    # 无论清单是什么专业（C/A/D/E），都搜索所有辅助库
    # 辅助库结果在最后与主库结果合并排序，不提前return
    aux_searchers = getattr(searcher, 'aux_searchers', [])
    aux_candidates = []
    if aux_searchers:
        for aux in aux_searchers:
            try:
                results = aux.search(search_query, top_k=top_k, books=None)
                # 给每条结果打上来源库标记，用于后续去重时区分不同库的同编号定额
                for r in results:
                    r["_source_province"] = aux.province
                aux_candidates.extend(results)
            except (KeyError, TypeError, ValueError, AttributeError,
                    OSError, RuntimeError, ImportError) as e:
                logger.warning(f"辅助库 {aux.province} 搜索失败: {e}")
    # 多取一些候选（top_k*2），让借用册有机会出现在结果中
    # 场景：搜"镀锌钢管沟槽连接"时，C10的"镀锌钢管螺纹连接"得分高会挤掉C9的"钢管沟槽连接"
    # 扩大搜索范围能让C9结果有机会进入候选池，由Reranker和参数验证挑最好的
    #
    # 统一逻辑：classify()已通过BookClassifier（数据驱动）给出了primary+fallbacks，
    # 不再区分标准/行业定额。行业定额仅需做册号翻译（C10→"10"等）
    search_books = [primary] + fallbacks
    if not searcher.uses_standard_books and search_books:
        # 行业定额：C-book编号翻译为实际book值（如C10→"10"）
        translated = _translate_books_for_industry(
            search_books, searcher.bm25_engine.quota_books
        )
        if translated:
            search_books = translated
        else:
            # 翻译失败：降级用词频统计判断册号
            search_books = searcher.bm25_engine.classify_to_books(search_query, top_k=3)
    candidates = searcher.search(search_query, top_k=top_k * 2, books=search_books)

    # 结果足够就返回（加质量门槛检查）
    if len(candidates) >= CASCADE_MIN_CANDIDATES:
        # 候选较多（>= 5个）：直接返回，不需要额外质量检查
        if len(candidates) >= CASCADE_MIN_CANDIDATES + 2:
            return _merge_with_aux(candidates, aux_candidates, top_k * 2)
        # 候选较少（3-4个）：检查top候选是否有明确的分差优势
        # 如果top1和top3分数太接近，说明搜索结果不确定，需要扩大搜索
        top_score = candidates[0].get("hybrid_score", 0)
        third_idx = min(2, len(candidates) - 1)
        third_score = candidates[third_idx].get("hybrid_score", 0)
        quality_threshold = getattr(config, "CASCADE_QUALITY_THRESHOLD", 0.3)
        if top_score > 0 and (top_score - third_score) / top_score >= quality_threshold:
            return _merge_with_aux(candidates, aux_candidates, top_k * 2)
        # 分差不够，继续全库搜索补充候选
        logger.debug(
            f"主搜{len(candidates)}条但质量不足"
            f"(分差比{(top_score - third_score) / max(top_score, 1e-9):.2f}"
            f"<{quality_threshold})，触发全库补充搜索"
        )

    # 第2步：兜底全库搜索
    candidates = searcher.search(search_query, top_k=top_k, books=None)
    return _merge_with_aux(candidates, aux_candidates, top_k)


def _is_measure_item(name: str, desc: str, unit, quantity) -> bool:
    """判断是否为措施项/章节分隔行，这类行不应套安装定额。

    两层判断：
    1. 强关键词（施工费/增加费/措施费等）：只要名称命中就跳过，不管有没有单位/工程量
       因为措施费清单里经常填"项"作单位、"1"作工程量
    2. 弱关键词（操作高度/超高等）：需要同时满足无单位无工程量才跳过
       防止误伤正常清单（比如"超高层钢结构"不应被跳过）
    """
    # 强关键词：直接跳过
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
    clean_name = name.replace("\n", "").replace("\r", "").replace(" ", "").strip()
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
    return False


# ============================================================
# 候选准备
# ============================================================

def _prepare_candidates(searcher: HybridSearcher, reranker, validator: ParamValidator,
                        search_query: str, full_query: str,
                        classification: dict,
                        bill_params: dict = None) -> list[dict]:
    """统一执行：级联搜索 → 去重 → Reranker重排 → 参数验证。"""
    candidates = cascade_search(searcher, search_query, classification)

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
        candidates.sort(key=lambda x: x.get("hybrid_score", 0), reverse=True)

    # 单候选时重排无意义，可直接跳过提升速度
    if candidates and len(candidates) > 1:
        candidates = reranker.rerank(search_query, candidates)
    if candidates:
        # 从classification中提取search_books（用于v3 LTR特征book_match）
        search_books = classification.get("search_books", []) if classification else []
        candidates = validator.validate_candidates(
            full_query, candidates, supplement_query=search_query,
            bill_params=bill_params, search_books=search_books)
    return candidates


def _prepare_candidates_from_prepared(prepared: dict, searcher: HybridSearcher,
                                      reranker, validator: ParamValidator):
    """从统一 prepared 上下文中取字段并执行候选流水线。"""
    ctx = prepared["ctx"]
    full_query = ctx["full_query"]
    search_query = ctx["search_query"]
    classification = prepared["classification"]

    # L5跨省预热：如果经验库miss时留下了跨省提示，增强搜索查询
    item = ctx.get("item", {})
    cross_hints = item.get("_cross_province_hints", []) if isinstance(item, dict) else []
    if cross_hints:
        # 取前3个提示关键词追加到搜索查询（不影响原始query）
        # 防御：确保每个元素都是字符串
        hint_text = " ".join(str(h) for h in cross_hints[:3] if h)
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
        bill_params=item_params)
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
        backup_conf = _safe_float_value(backup.get("confidence"), 0.0)
        if backup_conf < config.CONFIDENCE_YELLOW:
            continue
        backup_sig = _result_quota_signature(backup)
        if backup_sig and backup_sig[0] != top_id:
            return True
    return False


def _should_skip_agent_llm(candidates: list[dict],
                           exp_backup: dict = None,
                           rule_backup: dict = None) -> bool:
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

    top_score = _safe_float_value(top.get("param_score"), 0.0)
    if top_score < config.AGENT_FASTPATH_SCORE:
        return False

    # ===== 无参数候选盲区检查 =====
    # 清单有参数（DN/截面/回路等）但top1的param_score较低（定额无参数或参数不确定）
    # → 强制走LLM，避免无参数候选因语义得分高而盲通
    if getattr(config, "AGENT_FASTPATH_REQUIRE_PARAM_MATCH", True):
        top_detail = str(top.get("param_detail", ""))
        if ("定额无" in top_detail or "未指定" in top_detail) and top_score < 0.7:
            return False

    # ===== 单候选拦截 =====
    # 只搜到1条结果时，搜索质量不可靠（没有对比对象），强制走LLM仲裁
    if len(candidates) < 2:
        logger.debug("FastPath拦截: 单候选，强制走LLM")
        return False

    # ===== 搜索排名分差检查 =====
    # 当top1和top2的reranker分数太接近时，搜索结果不确定，需要LLM仲裁
    # 这解决了"无参数可验证"时FastPath盲目信任搜索排序的问题
    score_gap_threshold = _safe_float_value(config.AGENT_FASTPATH_SCORE_GAP, 1.0)
    if score_gap_threshold > 0:
        top1_rs = _safe_float_value(
            candidates[0].get("rerank_score", candidates[0].get("hybrid_score", 0)), 0.0)
        top2_rs = _safe_float_value(
            candidates[1].get("rerank_score", candidates[1].get("hybrid_score", 0)), 0.0)
        gap = top1_rs - top2_rs
        logger.debug(f"FastPath分差: top1={top1_rs:.3f} top2={top2_rs:.3f} gap={gap:.3f} "
                     f"阈值={score_gap_threshold} → {'放行' if gap >= score_gap_threshold else '拦截'}")
        if gap < score_gap_threshold:
            return False  # 分差不够，让LLM来选

    return True


def _should_audit_fastpath() -> bool:
    """按配置比例抽检快速通道结果。"""
    rate = _safe_float_value(config.AGENT_FASTPATH_AUDIT_RATE, 0.0)
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
