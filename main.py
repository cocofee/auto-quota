"""
自动套定额系统 - 命令行入口
功能：
1. 读取清单Excel → 逐条匹配定额 → 输出结果Excel
2. 支持两种模式：
   - agent: Agent模式（造价员贾维斯，搜索+Agent分析+自动学习进化）
   - search: 纯搜索模式（不调API，免费）
3. 整合经验库：先查经验库→命中直通→未命中走搜索（经验只在人工审核后入库）

使用方法：
    # Agent模式（造价员贾维斯，自动学习进化）
    python main.py 清单文件.xlsx --mode agent

    # 纯搜索模式（不需要API Key，免费）
    python main.py 清单文件.xlsx --mode search

    # 不使用经验库（不查也不存经验）
    python main.py 清单文件.xlsx --no-experience

    # 查看帮助
    python main.py --help
"""

import argparse
import sys
import time
import os
import json
import random
import tempfile
from pathlib import Path

from loguru import logger

# 日志写入文件（logs/目录下，按天轮转，保留30天）
config_module = __import__("config")
logger.add(
    str(config_module.LOG_DIR / "auto_quota_{time:YYYY-MM-DD}.log"),
    rotation="00:00",     # 每天零点新建一个日志文件
    retention="30 days",  # 保留30天
    encoding="utf-8",
    level="INFO",
)

import config
from src.bill_reader import BillReader
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.output_writer import OutputWriter
from src.text_parser import parser as text_parser, normalize_bill_text
from src.specialty_classifier import classify as classify_specialty
from src.bill_cleaner import clean_bill_items
from src.rule_validator import RuleValidator


def _atomic_write_json(output_path: str, payload: dict):
    """原子写JSON，避免中断时留下损坏文件。"""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"{target.stem}_tmp_",
            dir=str(target.parent),
            encoding="utf-8",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            json.dump(payload, tf, ensure_ascii=False)
        os.replace(tmp_path, target)
    finally:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass


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
       注意：精确匹配(is_exact=True)时跳过方法2，因为用户已确认该文本→定额的映射，
       简单参数对比可能因材质名称差异（如"射频同轴电缆"≠"同轴电缆"）误杀正确结果。

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
    # 注意：如果方法1已验证通过（rule_validated=True），跳过方法2
    # 注意：如果是精确匹配（is_exact=True），跳过方法2
    # 原因：方法1理解"向上取档"（如DN75→DN100），方法2只做简单数值对比会误杀
    #       精确匹配是用户确认过的映射，材质名差异（如"射频同轴电缆"≠"同轴电缆"）不应否决
    if main_quota_name and not rule_validated and not is_exact:
        bill_params = text_parser.parse(bill_text)
        quota_params = text_parser.parse(main_quota_name)
        if bill_params and quota_params:
            is_match, score = text_parser.params_match(bill_params, quota_params)
            if not is_match:
                logger.info(
                    f"经验库参数校验失败: '{bill_text[:40]}' "
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
        return None

    # 取第一条可直通（非stale）的经验，避免“top1过期就整体失效”
    best = None
    for candidate in similar:
        if candidate.get("match_type") != "stale":
            best = candidate
            break

    # 全部是过期经验时，不直通
    if best is None:
        logger.debug(f"经验库命中但版本均过期，不直通: {query[:50]}")
        return None
    similarity = best.get("similarity", 0)

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
            "confidence": confidence,
            "explanation": f"经验库精确匹配 (确认{best.get('confirm_count', 1)}次)",
            "match_source": "experience_exact",  # 标记匹配来源
        }

        # 参数验证：即使经验库文本完全匹配，参数也必须对
        # （同名清单不同参数的情况，如"配电箱"7回路 vs 4回路）
        # 精确匹配时跳过方法2（简单参数对比），只做方法1（规则校验器档位检查）
        validated = _validate_experience_params(result, item, rule_validator, is_exact=True)
        if validated is None:
            return None  # 参数不匹配，拒绝经验库结果
        return validated

    # 向量相似匹配 → 相似度≥0.75即可采纳（项目导入的正规数据可信度高）
    if similarity >= 0.75:
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
                "reason": f"经验库相似匹配 (相似度{similarity:.2f}, 原文: {best['bill_text'][:50]})",
            })

        result = {
            "bill_item": item,
            "quotas": quotas,
            "confidence": confidence,
            "explanation": f"经验库相似匹配 (相似度{similarity:.2f})",
            "match_source": "experience_similar",
        }

        # 参数验证：相似匹配更需要校验（文本相似但参数可能不同）
        validated = _validate_experience_params(result, item, rule_validator)
        if validated is None:
            return None
        return validated

    # 相似度不够高，不采纳
    return None


# 级联搜索最少要返回的候选数量（少于此值则扩大搜索范围）
CASCADE_MIN_CANDIDATES = 3
# 规则预匹配直通阈值（低于该值时仅作为备选，不提前截断后续流程）
RULE_DIRECT_CONFIDENCE = 80
# 措施项关键词（这些是工程管理费用，不套安装定额）
MEASURE_KEYWORDS = ["施工费", "增加费", "复测费", "措施费"]


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


def cascade_search(searcher: HybridSearcher, search_query: str,
                   classification: dict, top_k: int = None) -> list[dict]:
    """
    级联搜索：主专业+借用专业一起搜 → 不够则全库搜索

    设计思想：不要只搜主专业（太窄，容易漏掉正确答案），
    直接在"主专业+借用专业"范围内搜索，让Reranker和参数验证来挑最好的。

    辅助定额库：
    当清单项被分类为非安装专业（如A=土建、D=市政、E=园林），
    搜索用户选择的所有辅助定额库，取最佳结果。

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

    # ---- 辅助定额库路由 ----
    # 清单项分类为非安装（A/D/E）时，搜索所有辅助定额库
    aux_searchers = getattr(searcher, 'aux_searchers', [])
    if aux_searchers and not primary.startswith("C"):
        # 搜索每个辅助库，合并结果取最好的
        all_candidates = []
        for aux in aux_searchers:
            try:
                results = aux.search(search_query, top_k=top_k, books=None)
                all_candidates.extend(results)
            except Exception as e:
                logger.warning(f"辅助库 {aux.province} 搜索失败: {e}")
        if all_candidates:
            # 按分数降序排序，取 top_k
            all_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            return all_candidates[:top_k]

    # 第1步：在主专业+借用专业范围内搜索（比只搜主专业更灵活）
    # 多取一些候选（top_k*2），让借用册有机会出现在结果中
    # 场景：搜"镀锌钢管沟槽连接"时，C10的"镀锌钢管螺纹连接"得分高会挤掉C9的"钢管沟槽连接"
    # 扩大搜索范围能让C9结果有机会进入候选池，由Reranker和参数验证挑最好的
    search_books = [primary] + fallbacks
    candidates = searcher.search(search_query, top_k=top_k * 2, books=search_books)

    # 结果足够就返回
    if len(candidates) >= CASCADE_MIN_CANDIDATES:
        return candidates

    # 第2步：兜底全库搜索
    candidates = searcher.search(search_query, top_k=top_k, books=None)
    return candidates


def _is_measure_item(name: str, desc: str, unit, quantity) -> bool:
    """判断是否为措施项/章节分隔行，这类行不应套安装定额。"""
    return (
        (any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity)
        or (name.strip() == "其他" and not unit and not quantity and not desc.strip())
    )


def _build_classification(item: dict, name: str, desc: str, section: str) -> dict:
    """获取并标准化专业分类结果。"""
    classification = {
        "primary": item.get("specialty"),
        "fallbacks": item.get("specialty_fallbacks", []),
    }
    if not classification["primary"]:
        classification = classify_specialty(name, desc, section_title=section)
    return _normalize_classification(classification)


def _build_item_context(item: dict) -> dict:
    """构建匹配所需的清单上下文（名称/查询文本/单位/工程量等）。"""
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    original_name = item.get("original_name", name)
    return {
        "name": name,
        "desc": desc,
        "section": section,
        "unit": item.get("unit"),
        "quantity": item.get("quantity"),
        "full_query": f"{name} {desc}".strip(),
        "normalized_query": normalize_bill_text(original_name, desc),
        "search_query": text_parser.build_quota_query(name, desc),
    }


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
    if rule_result.get("confidence", 0) >= RULE_DIRECT_CONFIDENCE:
        return rule_result, None
    return None, rule_result


def _prepare_candidates(searcher: HybridSearcher, reranker, validator: ParamValidator,
                        search_query: str, full_query: str,
                        classification: dict) -> list[dict]:
    """统一执行：级联搜索 → Reranker重排 → 参数验证。"""
    candidates = cascade_search(searcher, search_query, classification)
    # 单候选时重排无意义，可直接跳过提升速度
    if candidates and len(candidates) > 1:
        candidates = reranker.rerank(search_query, candidates)
    if candidates:
        candidates = validator.validate_candidates(
            full_query, candidates, supplement_query=search_query)
    return candidates


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
        alt_ps = alt.get("param_score", 0.5)
        alt_conf = int(alt_ps * 95) if alt.get("param_match", True) else max(int(alt_ps * 45), 15)
        alternatives.append({
            "quota_id": alt["quota_id"],
            "name": alt["name"],
            "unit": alt.get("unit", ""),
            "confidence": alt_conf,
            "reason": alt.get("param_detail", ""),
        })
    return alternatives


def _build_skip_measure_result(item: dict) -> dict:
    """构建措施项跳过结果。"""
    return {
        "bill_item": item,
        "quotas": [],
        "alternatives": [],
        "confidence": 0,
        "match_source": "skip_measure",
        "explanation": "措施项（管理费用），不套安装定额",
    }


def _apply_rule_backup(result: dict, rule_backup: dict, rule_hits: int,
                       prefer_label: str) -> tuple[dict, int]:
    """
    低置信规则结果兜底比较：置信度更高则替换当前结果。

    prefer_label 用于日志前缀，如“搜索/经验”“LLM/经验”“Agent/经验”。
    """
    if not rule_backup:
        return result, rule_hits
    if rule_backup.get("confidence", 0) > result.get("confidence", 0):
        return rule_backup, rule_hits + 1
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
    if exp_backup.get("confidence", 0) >= result.get("confidence", 0):
        return exp_backup, exp_hits + 1
    logger.debug(
        f"{prefer_label}结果优于经验库相似匹配: "
        f"当前{result.get('confidence', 0)}分 > "
        f"经验库{exp_backup.get('confidence', 0)}分, "
        f"保持{prefer_label}结果")
    return result, exp_hits


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

    classification = _build_classification(item, name, desc, ctx["section"])
    exp_result = try_experience_match(
        normalized_query, item, experience_db, rule_validator, province=province)
    exp_backup = exp_result if exp_result else None

    if exact_exp_direct and exp_result and exp_result.get("match_source") == "experience_exact":
        return {
            "early_result": exp_result,
            "early_type": "experience_exact",
        }

    rule_direct, rule_backup = _prepare_rule_match(
        rule_validator, full_query, item, search_query, classification)
    if rule_direct:
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


def _consume_early_result(results: list[dict], early_result: dict, early_type: str,
                          idx: int, total: int, interval: int,
                          exp_hits: int, rule_hits: int,
                          log_types: set[str], is_agent: bool = False,
                          agent_hits: int = 0) -> tuple[bool, int, int]:
    """统一处理前置阶段提前命中的结果。"""
    if early_result is None:
        return False, exp_hits, rule_hits

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
        return result, exp_hits + 1

    if exp_source == "experience_exact":
        exp_conf = min(exp_backup.get("confidence", 0), 88)
        search_conf = result.get("confidence", 0)
        if exp_conf >= search_conf:
            exp_backup["confidence"] = exp_conf
            logger.debug(
                f"经验库精确匹配(降级) vs 搜索: "
                f"经验{exp_conf}分 >= 搜索{search_conf}分")
            return exp_backup, exp_hits + 1
        logger.debug(
            f"搜索优于经验库精确匹配: "
            f"搜索{search_conf}分 > 经验{exp_conf}分(降级)")
        return result, exp_hits

    if exp_backup.get("confidence", 0) >= result.get("confidence", 0):
        return exp_backup, exp_hits + 1

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

    if candidates:
        matched_candidates = [c for c in candidates if c.get("param_match", True)]
        if matched_candidates:
            best = matched_candidates[0]
            param_score = best.get("param_score", 0.5)
            confidence = int(param_score * 95)
            explanation = best.get("param_detail", "")
        else:
            best = candidates[0]
            param_score = best.get("param_score", 0.0)
            confidence = max(int(param_score * 45), 15)
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

    if best and candidates:
        result["alternatives"] = _build_alternatives(
            candidates, skip_obj=best, top_n=3)
    if not best:
        result["no_match_reason"] = "搜索无匹配结果"
    return result


def _build_empty_match_result(item: dict, reason: str, source: str = "search") -> dict:
    """构建空匹配结果（用于无候选时兜底）。"""
    return {
        "bill_item": item,
        "quotas": [],
        "confidence": 0,
        "explanation": reason,
        "no_match_reason": reason,
        "match_source": source,
    }


def _apply_mode_backups(result: dict, exp_backup: dict, rule_backup: dict,
                        exp_hits: int, rule_hits: int,
                        exp_label: str, rule_label: str) -> tuple[dict, int, int]:
    """full/agent 模式统一后处理：经验库相似兜底 + 低置信规则兜底。"""
    result, exp_hits = _apply_similar_exp_backup(
        result, exp_backup, exp_hits, prefer_label=exp_label)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label=rule_label)
    return result, exp_hits, rule_hits


def _prepare_candidates_from_prepared(prepared: dict, searcher: HybridSearcher,
                                      reranker, validator: ParamValidator):
    """从统一 prepared 上下文中取字段并执行候选流水线。"""
    ctx = prepared["ctx"]
    full_query = ctx["full_query"]
    search_query = ctx["search_query"]
    classification = prepared["classification"]
    candidates = _prepare_candidates(
        searcher, reranker, validator, search_query, full_query, classification)
    return (
        ctx,
        full_query,
        search_query,
        candidates,
        prepared["exp_backup"],
        prepared["rule_backup"],
    )


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


def _resolve_search_mode_result(item: dict, candidates: list[dict],
                                exp_backup: dict, rule_backup: dict,
                                exp_hits: int, rule_hits: int):
    """search模式统一结果决策：搜索结果 + 经验/规则兜底。"""
    result = _build_search_result_from_candidates(item, candidates)
    result, exp_hits = _reconcile_search_and_experience(result, exp_backup, exp_hits)
    result, rule_hits = _apply_rule_backup(
        result, rule_backup, rule_hits, prefer_label="搜索/经验")
    return result, exp_hits, rule_hits


def _safe_float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _result_quota_signature(result: dict) -> tuple:
    """返回结果中的定额编号签名，用于一致性比对。"""
    quotas = result.get("quotas") or []
    return tuple(str(q.get("quota_id", "")).strip() for q in quotas if q.get("quota_id"))


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
    7. top1和top2的reranker分差足够大（≥SCORE_GAP）
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

    # ===== 新增：搜索排名分差检查 =====
    # 当top1和top2的reranker分数太接近时，搜索结果不确定，需要LLM仲裁
    # 这解决了"无参数可验证"时FastPath盲目信任搜索排序的问题
    score_gap_threshold = _safe_float_value(config.AGENT_FASTPATH_SCORE_GAP, 1.0)
    if score_gap_threshold > 0 and len(candidates) >= 2:
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


def _resolve_agent_mode_result(agent, item: dict, candidates: list[dict],
                               experience_db, full_query: str, search_query: str,
                               rule_kb, name: str, desc: str,
                               exp_backup: dict, rule_backup: dict,
                               exp_hits: int, rule_hits: int,
                               province: str = None,
                               reference_cases_cache: dict = None,
                               rules_context_cache: dict = None):
    """agent模式统一结果决策：Agent分析 + 经验/规则兜底。"""
    if reference_cases_cache is None:
        reference_cases_cache = {}
    if rules_context_cache is None:
        rules_context_cache = {}

    reference_cases = _get_reference_cases_cached(
        reference_cases_cache, experience_db, full_query, province=province,
        top_k=3, tolerate_error=True, default=None,
        error_prefix="参考案例获取失败（不影响Agent主流程）")
    rules_context = _get_agent_rules_context_cached(
        rules_context_cache, rule_kb, name, desc, province=province, top_k=3)
    result = agent.match_single(
        bill_item=item,
        candidates=candidates,
        reference_cases=reference_cases,
        rules_context=rules_context,
        search_query=search_query,
    )
    result, exp_hits, rule_hits = _apply_mode_backups(
        result, exp_backup, rule_backup,
        exp_hits, rule_hits,
        exp_label="Agent", rule_label="Agent/经验")
    return result, exp_hits, rule_hits


def _append_search_result_and_log(results: list[dict], result: dict,
                                  idx: int, total: int,
                                  exp_hits: int, rule_hits: int):
    """search模式统一结果入列与进度日志。"""
    results.append(result)
    _log_standard_progress(
        idx, total, exp_hits, rule_hits, interval=50, show_percent=True)


def _append_agent_result_and_log(results: list[dict], result: dict,
                                 idx: int, total: int,
                                 exp_hits: int, rule_hits: int,
                                 agent_hits: int) -> int:
    """agent模式统一结果入列、命中计数与进度日志。"""
    results.append(result)
    if result.get("match_source", "").startswith("agent"):
        agent_hits += 1
    _log_agent_progress(
        idx, total, exp_hits, rule_hits, agent_hits, interval=10)
    return agent_hits


def _match_by_mode(mode: str, bill_items: list[dict], searcher: HybridSearcher,
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


def _build_run_stats(results: list[dict], elapsed: float) -> dict:
    """构建运行统计信息。"""
    total = len(results)
    matched = sum(1 for r in results if r.get("quotas"))
    high_conf = sum(
        1 for r in results if r.get("confidence", 0) >= config.CONFIDENCE_GREEN)
    mid_conf = sum(
        1 for r in results
        if config.CONFIDENCE_YELLOW <= r.get("confidence", 0) < config.CONFIDENCE_GREEN)
    exp_matched = sum(
        1 for r in results if r.get("match_source", "").startswith("experience"))
    return {
        "total": total,
        "matched": matched,
        "high_conf": high_conf,
        "mid_conf": mid_conf,
        "low_conf": total - high_conf - mid_conf,
        "exp_hits": exp_matched,
        "elapsed": elapsed,
    }


def _log_run_summary(stats: dict, has_experience_db: bool):
    """打印运行汇总日志。"""
    total = stats["total"]
    matched = stats["matched"]
    high_conf = stats["high_conf"]
    mid_conf = stats["mid_conf"]
    exp_matched = stats["exp_hits"]
    elapsed = stats["elapsed"]

    logger.info("=" * 60)
    logger.info("匹配完成")
    logger.info(f"  总清单项: {total}")
    logger.info(f"  已匹配: {matched} ({matched * 100 // max(total, 1)}%)")
    logger.info(f"  高置信度(绿): {high_conf}")
    logger.info(f"  中置信度(黄): {mid_conf}")
    logger.info(f"  未匹配/低置信度(红): {total - high_conf - mid_conf}")
    if has_experience_db:
        logger.info(f"  经验库命中: {exp_matched} ({exp_matched * 100 // max(total, 1)}%)")
    logger.info(f"  耗时: {elapsed:.1f}秒")
    per_item = elapsed / max(total, 1)
    logger.info(f"  平均每条: {per_item:.2f}秒/条（含初始化）")
    if total > 0:
        init_overhead = 23  # 模型加载固定开销（秒）
        match_time = max(elapsed - init_overhead, 0)
        match_per_item = match_time / total
        logger.info(f"  纯匹配速度: {match_per_item:.2f}秒/条")
        logger.info(f"  预估: 100条≈{(init_overhead + match_per_item * 100) / 60:.1f}分钟 | "
                   f"500条≈{(init_overhead + match_per_item * 500) / 60:.1f}分钟 | "
                   f"1000条≈{(init_overhead + match_per_item * 1000) / 60:.1f}分钟")
    logger.info("=" * 60)


def _log_run_banner(input_path: Path, mode: str, province: str,
                    no_experience: bool):
    """打印启动横幅信息。"""
    logger.info("=" * 60)
    logger.info("自动套定额系统")
    logger.info(f"  输入文件: {input_path}")
    logger.info(f"  匹配模式: {mode}")
    logger.info(f"  省份: {province}")
    logger.info(f"  经验库: {'关闭' if no_experience else '开启'}")
    logger.info("=" * 60)


def _init_search_components(resolved_province: str, aux_provinces: list = None):
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


def _init_experience_db(no_experience: bool):
    """按配置初始化经验库（可选）。"""
    experience_db = None
    if no_experience:
        return experience_db
    try:
        from src.experience_db import ExperienceDB
        experience_db = ExperienceDB()
        exp_stats = experience_db.get_stats()
        logger.info(f"  经验库: {exp_stats['total']} 条历史记录")
    except Exception as e:
        logger.warning(f"经验库加载失败，将跳过经验库: {e}")
        experience_db = None
    return experience_db


def _resolve_run_province(province: str, interactive, json_output):
    """解析并设置当前省份。"""
    if interactive is None:
        interactive = not json_output
    resolved_province = config.resolve_province(
        province,
        interactive=interactive
    )
    config.set_current_province(resolved_province)
    return resolved_province


def _load_bill_items_for_run(input_path: Path, sheet=None, limit=None):
    """读取并清洗清单数据，按需截断数量。"""
    logger.info("第1步：读取清单文件...")
    reader = BillReader()
    bill_items = reader.read_excel(str(input_path), sheet_name=sheet)

    if not bill_items:
        raise RuntimeError("未读取到任何清单项目，请检查文件格式")

    # 清单数据清洗（名称修正+专业分类+参数提取）
    bill_items = clean_bill_items(bill_items)

    # 限制数量（调试用）
    if limit:
        bill_items = bill_items[:limit]
        logger.info(f"限制处理前 {limit} 条")

    return bill_items


def _get_reference_cases(experience_db, full_query: str, province: str = None,
                         top_k: int = 3, tolerate_error: bool = False,
                         default=None, error_prefix: str = "参考案例获取失败（不影响主流程）"):
    """统一获取经验库参考案例。"""
    if not experience_db:
        return default
    if not tolerate_error:
        return experience_db.get_reference_cases(
            full_query, top_k=top_k, province=province)
    try:
        return experience_db.get_reference_cases(
            full_query, top_k=top_k, province=province)
    except Exception as e:
        logger.debug(f"{error_prefix}: {e}")
        return default


def _get_reference_cases_cached(cache: dict, experience_db, full_query: str,
                                province: str = None, top_k: int = 3,
                                tolerate_error: bool = False, default=None,
                                error_prefix: str = "参考案例获取失败（不影响主流程）"):
    """带缓存获取经验案例，减少重复查询。"""
    key = (province or "", full_query, top_k, tolerate_error)
    if key not in cache:
        cache[key] = _get_reference_cases(
            experience_db, full_query, province=province, top_k=top_k,
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

    # 初始化Agent（使用指定的或config中配置的大模型）
    agent_llm = llm_type or config.AGENT_LLM
    agent = AgentMatcher(llm_type=agent_llm, province=province)

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
            )
            return idx, result, task_exp_hits, task_rule_hits, is_audit

        # 并发执行LLM任务
        concurrent = max(1, config.LLM_CONCURRENT)
        logger.info(f"第2阶段: {len(llm_tasks)}条LLM任务，{concurrent}路并发")

        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            futures = {pool.submit(_process_llm_task, task): task for task in llm_tasks}
            completed = 0
            for future in as_completed(futures):
                idx, result, task_exp_hits, task_rule_hits, is_audit = future.result()
                completed += 1

                if is_audit:
                    # 审计模式：对比快通道结果
                    fast_result = results_by_idx.get(idx)
                    if fast_result and _result_quota_signature(result) != _result_quota_signature(fast_result):
                        fastpath_audit_mismatch += 1
                        result["agent_fastpath_overruled"] = True
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

    return results


def run(input_file, mode="agent", output=None,
        limit=None, province=None, aux_provinces=None,
        no_experience=False, sheet=None,
        json_output=None, agent_llm=None, interactive=None):
    """执行匹配的核心逻辑（供命令行和其他模块直接调用）

    参数:
        input_file: 清单Excel文件路径
        mode: 匹配模式 (search/agent)
        output: 输出Excel路径（默认自动生成）
        limit: 只处理前N条（调试用）
        province: 主定额库省份名称
        aux_provinces: 辅助定额库列表（如 ["广东土建", "广东市政"]）
        no_experience: 是否禁用经验库
        sheet: 指定只读取的Sheet名称
        json_output: JSON结果输出路径（可选）
        agent_llm: Agent模式使用的大模型
        interactive: 是否允许交互式提示（如省份选择）。
                     默认None=自动判断（命令行调用时True，程序调用建议传False）

    返回: {"results": [...], "stats": {...}}
    """
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")

    # 解析省份（支持简称模糊匹配）
    resolved_province = _resolve_run_province(
        province, interactive=interactive, json_output=json_output)
    _log_run_banner(input_path, mode, resolved_province, no_experience)

    start_time = time.time()

    # 1. 读取清单
    bill_items = _load_bill_items_for_run(input_path, sheet=sheet, limit=limit)

    # 2. 初始化搜索引擎
    searcher, validator = _init_search_components(resolved_province, aux_provinces)

    # 初始化经验库（可选）
    experience_db = _init_experience_db(no_experience)

    # 3. 执行匹配
    logger.info(f"第3步：开始匹配 ({mode} 模式)...")
    results = _match_by_mode(
        mode, bill_items, searcher, validator, experience_db,
        resolved_province, agent_llm=agent_llm)

    # 4. 输出结果
    elapsed = time.time() - start_time
    stats = _build_run_stats(results, elapsed)

    # 生成Excel（基于原始文件结构，保留分部小节标题）
    logger.info("第4步：生成结果Excel...")
    writer = OutputWriter()
    output_path = writer.write_results(
        results, output, original_file=str(input_path))
    logger.info(f"  输出文件: {output_path}")

    # 如果指定了JSON输出，也保存一份JSON（供审核工具读取）
    if json_output:
        _atomic_write_json(json_output, {"results": results, "stats": stats})
        logger.info(f"  JSON结果已保存: {json_output}")

    # 5. 打印统计
    _log_run_summary(stats, has_experience_db=bool(experience_db))

    return {"results": results, "stats": stats}


def main():
    """命令行入口：解析参数后调用 run()"""
    parser = argparse.ArgumentParser(
        description="自动套定额系统 - 命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # Agent模式（造价员贾维斯，需要API Key）
  python main.py 清单文件.xlsx --mode agent

  # 纯搜索模式（免费，不需要API Key）
  python main.py 清单文件.xlsx --mode search

  # 指定输出路径
  python main.py 清单文件.xlsx --output 结果.xlsx

  # 不使用经验库
  python main.py 清单文件.xlsx --no-experience
        """,
    )
    parser.add_argument("input_file", help="清单Excel文件路径")
    parser.add_argument("--mode", choices=["search", "agent"], default="agent",
                        help="匹配模式: agent=造价员贾维斯(默认) search=纯搜索(免费)")
    parser.add_argument("--output", "-o", help="输出文件路径（默认自动生成）")
    parser.add_argument("--limit", type=int, help="只处理前N条清单项（调试用）")
    parser.add_argument("--province", default=None, help=f"主定额库（默认: {config.CURRENT_PROVINCE}）")
    parser.add_argument("--aux-province", default=None,
                        help="辅助定额库（逗号分隔，用于安装清单中的土建/市政项目）")
    parser.add_argument("--no-experience", action="store_true",
                        help="不使用经验库（不查询也不存储经验）")
    parser.add_argument("--sheet", help="指定只读取的Sheet名称（默认读取所有Sheet）")
    parser.add_argument("--json-output", help="将匹配结果输出为JSON文件（供Web界面读取）")
    parser.add_argument("--agent-llm", help="Agent模式使用的大模型（覆盖config中的AGENT_LLM）")

    args = parser.parse_args()

    # 解析辅助定额库
    aux_provinces = None
    if args.aux_province:
        aux_provinces = [p.strip() for p in args.aux_province.split(",") if p.strip()]

    try:
        run(
            input_file=args.input_file,
            mode=args.mode,
            output=args.output,
            limit=args.limit,
            province=args.province,
            aux_provinces=aux_provinces,
            no_experience=args.no_experience,
            sheet=args.sheet,
            json_output=args.json_output,
            agent_llm=args.agent_llm,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
