"""
自动套定额系统 - 命令行入口
功能：
1. 读取清单Excel → 逐条匹配定额 → 输出结果Excel
2. 支持三种模式：
   - search: 纯搜索模式（不调API，免费但精度低一些）
   - full: 完整模式（搜索+大模型精选，需要API Key）
   - agent: Agent模式（造价员贾维斯，搜索+Agent分析+自动学习进化）
3. 整合经验库：先查经验库→命中直通→未命中走搜索→结果自动存入经验库

使用方法：
    # 纯搜索模式（不需要API Key，免费）
    python main.py 清单文件.xlsx --mode search

    # 完整模式（需要配置API Key）
    python main.py 清单文件.xlsx --mode full

    # Agent模式（造价员贾维斯，自动学习进化）
    python main.py 清单文件.xlsx --mode agent

    # 不使用经验库（纯搜索，不查也不存经验）
    python main.py 清单文件.xlsx --no-experience

    # 查看帮助
    python main.py --help
"""

import argparse
import sys
import time
from pathlib import Path

from loguru import logger

import config
from src.bill_reader import BillReader
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.output_writer import OutputWriter
from src.text_parser import parser as text_parser, normalize_bill_text
from src.specialty_classifier import classify as classify_specialty
from src.bill_cleaner import clean_bill_items
from src.rule_validator import RuleValidator


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
                         rule_validator=None) -> dict:
    """
    尝试从经验库匹配

    参数:
        query: 清单搜索文本
        item: 清单项目字典
        experience_db: 经验库实例
        rule_validator: 规则校验器实例（用于验证经验库结果的参数是否正确）

    返回:
        匹配结果字典，如果经验库未命中则返回 None
    """
    if experience_db is None:
        return None

    # 在经验库中搜索相似历史记录
    similar = experience_db.search_similar(
        query, top_k=1,
        min_confidence=config.EXPERIENCE_DIRECT_THRESHOLD,
    )

    if not similar:
        return None

    best = similar[0]
    similarity = best.get("similarity", 0)

    # 版本不一致的经验 → 不允许直通，降级为不采纳
    # （stale 表示这条经验基于旧版定额库，定额编号可能已失效或含义变化）
    if best.get("match_type") == "stale":
        logger.debug(f"经验库命中但版本过期，不直通: {best.get('bill_text', '')[:50]}")
        return None

    # 精确匹配（完全相同的清单文本）→ 构建结果
    if best.get("match_type") == "exact":
        quota_ids = best.get("quota_ids", [])
        quota_names = best.get("quota_names", [])
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


def cascade_search(searcher: HybridSearcher, search_query: str,
                   classification: dict, top_k: int = None) -> list[dict]:
    """
    级联搜索：主专业+借用专业一起搜 → 不够则全库搜索

    设计思想：不要只搜主专业（太窄，容易漏掉正确答案），
    直接在"主专业+借用专业"范围内搜索，让Reranker和参数验证来挑最好的。
    比如弱电(C5)的双绞线在C5能找到，而同一清单中的配管/穿线通过
    借用C4也能找到。

    参数:
        searcher: 混合搜索引擎实例
        search_query: 搜索文本
        classification: specialty_classifier.classify() 的返回值
        top_k: 返回候选数量

    返回:
        候选定额列表
    """
    top_k = top_k or config.HYBRID_TOP_K
    primary = classification.get("primary")
    fallbacks = classification.get("fallbacks", [])

    # 如果无法判断专业（primary=None），直接全库搜索
    if not primary:
        return searcher.search(search_query, top_k=top_k, books=None)

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


def match_search_only(bill_items: list[dict], searcher: HybridSearcher,
                      validator: ParamValidator,
                      experience_db=None) -> list[dict]:
    """
    纯搜索模式：经验库 → 混合搜索 + 参数验证（不调用大模型API）

    优点：完全免费，速度快
    缺点：没有大模型精选，可能不够精确

    逻辑：
    1. 先查经验库，命中则直接返回
    2. 未命中则走混合搜索+参数验证
    3. 取参数验证后排名第1的候选作为主定额
    4. 高置信度结果自动存入经验库
    """
    results = []
    exp_hits = 0  # 经验库命中计数
    rule_hits = 0  # 规则预匹配命中计数

    # 创建规则校验器（用于预匹配和后置校验）
    rule_validator = RuleValidator()

    # 创建Reranker（延迟加载，第一次走搜索时才实际加载模型）
    from src.reranker import Reranker
    reranker = Reranker()

    # 措施项关键词（这些是工程管理费用，不套安装定额）
    MEASURE_KEYWORDS = ["施工费", "增加费", "复测费", "措施费"]
    measure_skip = 0  # 措施项跳过计数

    for idx, item in enumerate(bill_items, start=1):
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        section = item.get("section", "") or ""
        unit = item.get("unit")
        quantity = item.get("quantity")

        # 第-1层：措施项自动跳过（不套定额）
        # 判断条件：名称含措施费关键词 + 无单位无数量
        # 或者：名称是"其他"且无单位无数量无描述（章节分隔行）
        is_measure = (
            (any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity)
            or (name.strip() == "其他" and not unit and not quantity and not desc.strip())
        )
        if is_measure:
            results.append({
                "bill_item": item,
                "quotas": [],
                "alternatives": [],
                "confidence": 0,
                "match_source": "skip_measure",
                "explanation": "措施项（管理费用），不套安装定额",
            })
            measure_skip += 1
            continue

        # 原始名称（清洗前的，用于经验库匹配——因为经验库是用原始名称导入的）
        original_name = item.get("original_name", name)
        # 完整文本（用于参数验证）
        full_query = f"{name} {desc}".strip()
        # 规范化文本（用于经验库匹配，用原始名称+去掉序号和换行，和导入时格式一致）
        normalized_query = normalize_bill_text(original_name, desc)
        # 精简query（用于搜索引擎，用清洗后的名称，模仿定额命名风格）
        search_query = text_parser.build_quota_query(name, desc)

        # 第0层：专业分类（bill_cleaner已预处理，直接读取标签）
        classification = {
            "primary": item.get("specialty"),
            "fallbacks": item.get("specialty_fallbacks", []),
        }
        # 如果清洗时没有标签（备用），现场判断
        if not classification["primary"]:
            classification = classify_specialty(name, desc, section_title=section)

        # 第1层：查经验库（用规范化文本匹配，和导入时格式一致）
        # 传入rule_validator用于参数校验（防止经验库的档位不匹配当前清单参数）
        exp_result = try_experience_match(
            normalized_query, item, experience_db, rule_validator)
        if exp_result:
            # 精确匹配（文本完全一致）：直接用，不走搜索
            if exp_result.get("match_source") == "experience_exact":
                results.append(exp_result)
                exp_hits += 1
                if idx % 50 == 0 or idx == len(bill_items):
                    logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                               f"(经验库命中{exp_hits}条)")
                continue
            # 相似匹配（文本相似但不完全一致）：暂存，继续走搜索取更好的结果
            # 原因：相似匹配的参数可能不对（如同名管道不同DN），搜索可能更准
            exp_backup = exp_result
        else:
            exp_backup = None

        # 第1.5层：规则预匹配（用关键词匹配家族→按参数选档位→直接出结果）
        # clean_query用清洗后的搜索文本（如"成套配电箱 明装 7回路"），关键词更准确
        # full_query保留完整文本，用于参数提取（如从"底距地1.3m安装"中提取数值）
        # books限定在清单所属专业册号范围内匹配（如给排水只匹配C10+C8+C12）
        rule_books = [classification["primary"]] + classification.get("fallbacks", [])
        rule_books = [b for b in rule_books if b]  # 过滤掉空值
        rule_result = rule_validator.match_by_rules(
            full_query, item, clean_query=search_query,
            books=rule_books if rule_books else None)
        if rule_result:
            results.append(rule_result)
            rule_hits += 1
            if idx % 50 == 0 or idx == len(bill_items):
                logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                           f"(经验库{exp_hits}, 规则{rule_hits})")
            continue

        # 第2层：级联搜索（主专业 → 借用专业 → 全库）
        candidates = cascade_search(searcher, search_query, classification)

        # 第2.5层：Reranker重排（交叉编码器语义精排，比向量搜索更准确）
        if candidates:
            candidates = reranker.rerank(search_query, candidates)

        # 参数验证（full_query提供完整信息，search_query补充规范化参数）
        # 典型场景：full_query中"BV4"提取不到截面，但search_query中"导线截面 4"可以
        if candidates:
            candidates = validator.validate_candidates(
                full_query, candidates, supplement_query=search_query)

        # 取参数匹配的第1名作为结果
        # 软降权策略：优先用param_match=True的候选，没有的话回退到第一个候选（降低置信度）
        best = None
        confidence = 0
        explanation = ""

        if candidates:
            matched_candidates = [c for c in candidates if c.get("param_match", True)]
            if matched_candidates:
                # 有参数匹配的候选 → 正常使用
                best = matched_candidates[0]
                param_score = best.get("param_score", 0.5)
                confidence = int(param_score * 85)
                explanation = best.get("param_detail", "")
            else:
                # 所有候选都参数不匹配 → 回退到第一个候选，但大幅降低置信度
                # （不直接丢弃，让用户能看到"最接近"的候选，方便手动选择）
                best = candidates[0]
                param_score = best.get("param_score", 0.0)
                confidence = max(int(param_score * 40), 15)  # 最低15分，最高40分
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

        # 提取 top-3 备选候选（排除已选中的 best）
        if best and candidates:
            alt_list = [c for c in candidates if c is not best][:3]
            alternatives = []
            for alt in alt_list:
                alt_ps = alt.get("param_score", 0.5)
                alt_conf = int(alt_ps * 85) if alt.get("param_match", True) else max(int(alt_ps * 40), 15)
                alternatives.append({
                    "quota_id": alt["quota_id"],
                    "name": alt["name"],
                    "unit": alt.get("unit", ""),
                    "confidence": alt_conf,
                    "reason": alt.get("param_detail", ""),
                })
            result["alternatives"] = alternatives

        if not best:
            result["no_match_reason"] = "搜索无匹配结果"

        # 和经验库相似匹配结果对比，取置信度更高的
        # 场景：搜索给85分（绿），经验库相似匹配只给80分（黄）→ 选搜索结果
        # 场景：搜索给40分（红），经验库相似匹配给80分（黄）→ 选经验库结果
        if exp_backup:
            if exp_backup.get("confidence", 0) >= result.get("confidence", 0):
                result = exp_backup
                exp_hits += 1
            else:
                logger.debug(
                    f"搜索结果优于经验库相似匹配: "
                    f"搜索{result.get('confidence', 0)}分 > "
                    f"经验库{exp_backup.get('confidence', 0)}分, "
                    f"使用搜索结果")

        results.append(result)

        # 每50条打印一次进度
        if idx % 50 == 0 or idx == len(bill_items):
            logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                       f"({idx * 100 // len(bill_items)}%, "
                       f"经验库{exp_hits}, 规则{rule_hits})")

    if exp_hits > 0 or rule_hits > 0:
        logger.info(f"经验库命中 {exp_hits}/{len(bill_items)} 条, "
                   f"规则命中 {rule_hits}/{len(bill_items)} 条")

    # 规则后置校验：对搜索出来的结果校验档位，纠正选错的档位
    rule_validator.validate_results(results)

    return results


def match_full(bill_items: list[dict], searcher: HybridSearcher,
               validator: ParamValidator,
               experience_db=None) -> list[dict]:
    """
    完整模式：经验库 → 混合搜索 + 参数验证 + 大模型精选

    需要配置API Key，每条清单消耗约几分钱API费用
    经验库命中的条目不消耗API费用
    """
    from src.llm_matcher import LLMMatcher

    matcher = LLMMatcher()

    results = []
    exp_hits = 0
    rule_hits = 0  # 规则预匹配命中计数

    # 创建规则校验器（用于预匹配和后置校验）
    rule_validator = RuleValidator()

    # 创建Reranker（延迟加载）
    from src.reranker import Reranker
    reranker = Reranker()

    # 措施项关键词（这些是工程管理费用，不套安装定额）
    MEASURE_KEYWORDS = ["施工费", "增加费", "复测费", "措施费"]
    measure_skip = 0

    for idx, item in enumerate(bill_items, start=1):
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        section = item.get("section", "") or ""
        # 原始名称（清洗前的，用于经验库匹配）
        original_name = item.get("original_name", name)
        # 完整文本（用于参数验证）
        full_query = f"{name} {desc}".strip()
        # 规范化文本（用于经验库匹配，用原始名称+去掉序号和换行）
        normalized_query = normalize_bill_text(original_name, desc)
        # 精简query（用于搜索引擎）
        search_query = text_parser.build_quota_query(name, desc)

        unit = item.get("unit")
        quantity = item.get("quantity")

        # 第-1层：措施项自动跳过（不套定额）
        is_measure = (
            (any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity)
            or (name.strip() == "其他" and not unit and not quantity and not desc.strip())
        )
        if is_measure:
            results.append({
                "bill_item": item,
                "quotas": [],
                "alternatives": [],
                "confidence": 0,
                "match_source": "skip_measure",
                "explanation": "措施项（管理费用），不套安装定额",
            })
            measure_skip += 1
            continue

        # 第0层：专业分类（bill_cleaner已预处理，直接读取标签）
        classification = {
            "primary": item.get("specialty"),
            "fallbacks": item.get("specialty_fallbacks", []),
        }
        if not classification["primary"]:
            classification = classify_specialty(name, desc, section_title=section)

        # 第1层：查经验库（用规范化文本匹配）
        # 传入rule_validator用于参数校验（防止经验库的档位不匹配当前清单参数）
        exp_result = try_experience_match(
            normalized_query, item, experience_db, rule_validator)
        if exp_result:
            results.append(exp_result)
            exp_hits += 1
            if idx % 10 == 0 or idx == len(bill_items):
                logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                           f"(经验库{exp_hits}, 规则{rule_hits})")
            continue

        # 第1.5层：规则预匹配（用关键词匹配家族→按参数选档位→直接出结果）
        # clean_query用清洗后的搜索文本，关键词更准确
        # books限定在清单所属专业册号范围内匹配
        rule_books = [classification["primary"]] + classification.get("fallbacks", [])
        rule_books = [b for b in rule_books if b]
        rule_result = rule_validator.match_by_rules(
            full_query, item, clean_query=search_query,
            books=rule_books if rule_books else None)
        if rule_result:
            results.append(rule_result)
            rule_hits += 1
            if idx % 10 == 0 or idx == len(bill_items):
                logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                           f"(经验库{exp_hits}, 规则{rule_hits})")
            continue

        # 第2层：级联搜索（主专业 → 借用专业 → 全库）
        candidates = cascade_search(searcher, search_query, classification)

        # 第2.5层：Reranker重排（交叉编码器语义精排）
        if candidates:
            candidates = reranker.rerank(search_query, candidates)

        # 参数验证（full_query提供完整信息，search_query补充规范化参数）
        if candidates:
            candidates = validator.validate_candidates(
                full_query, candidates, supplement_query=search_query)

        if not candidates:
            results.append({
                "bill_item": item,
                "quotas": [],
                "confidence": 0,
                "explanation": "搜索无结果",
                "no_match_reason": "搜索无结果",
                "match_source": "search",
            })
            continue

        # 获取经验库参考案例（给大模型做 few-shot 参考）
        reference_cases = []
        if experience_db:
            reference_cases = experience_db.get_reference_cases(full_query, top_k=3)

        # 第2.5层：大模型精选（带参考案例）
        result = matcher.match(item, candidates, reference_cases=reference_cases)
        result["bill_item"] = item
        result["candidates_count"] = len(candidates)
        result["match_source"] = "llm"

        # 提取 top-3 备选候选（排除大模型选中的主定额）
        if candidates:
            selected_ids = {q.get("quota_id") for q in result.get("quotas", [])}
            alt_list = [c for c in candidates if c["quota_id"] not in selected_ids][:3]
            alternatives = []
            for alt in alt_list:
                alt_ps = alt.get("param_score", 0.5)
                alt_conf = int(alt_ps * 85) if alt.get("param_match", True) else max(int(alt_ps * 40), 15)
                alternatives.append({
                    "quota_id": alt["quota_id"],
                    "name": alt["name"],
                    "unit": alt.get("unit", ""),
                    "confidence": alt_conf,
                    "reason": alt.get("param_detail", ""),
                })
            result["alternatives"] = alternatives

        results.append(result)

        # 注意：不再自动存入经验库
        # 经验库只在用户通过Web界面确认/修正后才存入

        # 打印进度
        if idx % 10 == 0 or idx == len(bill_items):
            logger.info(f"匹配进度: {idx}/{len(bill_items)} "
                       f"({idx * 100 // len(bill_items)}%, "
                       f"经验库{exp_hits}, 规则{rule_hits})")

    if exp_hits > 0 or rule_hits > 0:
        logger.info(f"经验库命中 {exp_hits}/{len(bill_items)} 条, "
                   f"规则命中 {rule_hits}/{len(bill_items)} 条")

    # 规则后置校验：对搜索/大模型的结果校验档位
    rule_validator.validate_results(results)

    return results


def match_agent(bill_items: list[dict], searcher: HybridSearcher,
                validator: ParamValidator,
                experience_db=None, llm_type: str = None) -> list[dict]:
    """
    Agent模式（造价员贾维斯）：经验库 → 规则 → 搜索+Agent分析

    流程：
    1. 先查经验库，命中则直通（和search模式一样）
    2. 再查规则预匹配（和search模式一样）
    3. 未命中 → 代码自动执行搜索+参数验证
    4. 把候选结果喂给Agent大模型分析判断
    5. 自动记录学习笔记（为后续进化积累数据）

    和search模式的区别：第3步不是直接取参数验证第1名，而是让大模型分析选择
    和full模式的区别：Prompt更强（造价员角色）、自动记录学习笔记
    """
    from src.agent_matcher import AgentMatcher

    # 初始化Agent（使用指定的或config中配置的大模型）
    agent_llm = llm_type or config.AGENT_LLM
    agent = AgentMatcher(llm_type=agent_llm)

    results = []
    exp_hits = 0
    rule_hits = 0
    agent_hits = 0

    # 创建规则校验器和Reranker
    rule_validator = RuleValidator()
    from src.reranker import Reranker
    reranker = Reranker()

    # 查规则知识库（Agent需要规则上下文）
    rule_kb = None
    try:
        from src.rule_knowledge import RuleKnowledge
        rule_kb = RuleKnowledge()
        if rule_kb.get_stats()["total"] == 0:
            rule_kb = None
    except Exception:
        pass

    logger.info(f"Agent模式启动，大模型: {agent_llm}")

    # 措施项关键词（这些是工程管理费用，不套安装定额）
    MEASURE_KEYWORDS = ["施工费", "增加费", "复测费", "措施费"]
    measure_skip = 0

    for idx, item in enumerate(bill_items, start=1):
        name = item.get("name", "")
        desc = item.get("description", "") or ""
        section = item.get("section", "") or ""
        original_name = item.get("original_name", name)
        full_query = f"{name} {desc}".strip()
        normalized_query = normalize_bill_text(original_name, desc)
        search_query = text_parser.build_quota_query(name, desc)

        unit = item.get("unit")
        quantity = item.get("quantity")

        # 第-1层：措施项自动跳过（不套定额）
        is_measure = (
            (any(kw in name for kw in MEASURE_KEYWORDS) and not unit and not quantity)
            or (name.strip() == "其他" and not unit and not quantity and not desc.strip())
        )
        if is_measure:
            results.append({
                "bill_item": item,
                "quotas": [],
                "alternatives": [],
                "confidence": 0,
                "match_source": "skip_measure",
                "explanation": "措施项（管理费用），不套安装定额",
            })
            measure_skip += 1
            continue

        # 专业分类
        classification = {
            "primary": item.get("specialty"),
            "fallbacks": item.get("specialty_fallbacks", []),
        }
        if not classification["primary"]:
            classification = classify_specialty(name, desc, section_title=section)

        # 第1层：查经验库（和search模式完全一样）
        exp_result = try_experience_match(
            normalized_query, item, experience_db, rule_validator)
        if exp_result:
            results.append(exp_result)
            exp_hits += 1
            if idx % 50 == 0 or idx == len(bill_items):
                logger.info(f"Agent进度: {idx}/{len(bill_items)} "
                           f"(经验库{exp_hits}, 规则{rule_hits}, Agent{agent_hits})")
            continue

        # 第1.5层：规则预匹配（和search模式完全一样）
        rule_books = [classification["primary"]] + classification.get("fallbacks", [])
        rule_books = [b for b in rule_books if b]
        rule_result = rule_validator.match_by_rules(
            full_query, item, clean_query=search_query,
            books=rule_books if rule_books else None)
        if rule_result:
            results.append(rule_result)
            rule_hits += 1
            if idx % 50 == 0 or idx == len(bill_items):
                logger.info(f"Agent进度: {idx}/{len(bill_items)} "
                           f"(经验库{exp_hits}, 规则{rule_hits}, Agent{agent_hits})")
            continue

        # 第2层：搜索+参数验证（代码自动执行，和search模式一样）
        candidates = cascade_search(searcher, search_query, classification)
        if candidates:
            candidates = reranker.rerank(search_query, candidates)
        if candidates:
            candidates = validator.validate_candidates(
                full_query, candidates, supplement_query=search_query)

        # 第3层：Agent大模型分析（这是和search模式的关键区别）
        # 准备额外上下文
        reference_cases = None
        if experience_db:
            try:
                reference_cases = experience_db.get_reference_cases(full_query, top_k=3)
            except Exception:
                pass

        rules_context = None
        if rule_kb:
            try:
                rules_context = rule_kb.search_rules(f"{name} {desc}", top_k=3)
            except Exception:
                pass

        result = agent.match_single(
            bill_item=item,
            candidates=candidates,
            reference_cases=reference_cases,
            rules_context=rules_context,
            search_query=search_query,
        )
        results.append(result)
        agent_hits += 1

        # 打印进度
        if idx % 10 == 0 or idx == len(bill_items):
            logger.info(f"Agent进度: {idx}/{len(bill_items)} "
                       f"(经验库{exp_hits}, 规则{rule_hits}, Agent{agent_hits})")

    logger.info(f"Agent匹配完成: 经验库{exp_hits}, 规则{rule_hits}, "
               f"Agent分析{agent_hits}/{len(bill_items)}条")

    # 规则后置校验
    rule_validator.validate_results(results)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="自动套定额系统 - 命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 纯搜索模式（免费，不需要API Key）
  python main.py 清单文件.xlsx --mode search

  # 完整模式（需要API Key，精度更高）
  python main.py 清单文件.xlsx --mode full

  # 指定输出路径
  python main.py 清单文件.xlsx --output 结果.xlsx

  # 只处理安装相关的清单项（编码以03开头）
  python main.py 清单文件.xlsx --filter-code 03

  # 不使用经验库
  python main.py 清单文件.xlsx --no-experience
        """,
    )
    parser.add_argument("input_file", help="清单Excel文件路径")
    parser.add_argument("--mode", choices=["search", "full", "agent"], default="search",
                        help="匹配模式: search=纯搜索(免费) full=搜索+大模型(需API) agent=造价员贾维斯(自动学习)")
    parser.add_argument("--output", "-o", help="输出文件路径（默认自动生成）")
    parser.add_argument("--filter-code", help="只处理指定编码前缀的清单项（如03=安装）")
    parser.add_argument("--limit", type=int, help="只处理前N条清单项（调试用）")
    parser.add_argument("--province", default=None, help=f"省份（默认: {config.CURRENT_PROVINCE}）")
    parser.add_argument("--no-experience", action="store_true",
                        help="不使用经验库（不查询也不存储经验）")
    parser.add_argument("--sheet", help="指定只读取的Sheet名称（默认读取所有Sheet）")
    parser.add_argument("--json-output", help="将匹配结果输出为JSON文件（供Web界面读取）")
    parser.add_argument("--agent-llm", help="Agent模式使用的大模型（覆盖config中的AGENT_LLM）")

    args = parser.parse_args()

    # 验证输入文件
    input_path = Path(args.input_file)
    if not input_path.exists():
        logger.error(f"文件不存在: {input_path}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("自动套定额系统")
    logger.info(f"  输入文件: {input_path}")
    logger.info(f"  匹配模式: {args.mode}")
    logger.info(f"  省份: {args.province or config.CURRENT_PROVINCE}")
    logger.info(f"  经验库: {'关闭' if args.no_experience else '开启'}")
    logger.info("=" * 60)

    start_time = time.time()

    # 1. 读取清单
    logger.info("第1步：读取清单文件...")
    reader = BillReader()
    bill_items = reader.read_excel(str(input_path), sheet_name=args.sheet)

    if not bill_items:
        logger.error("未读取到任何清单项目，请检查文件格式")
        sys.exit(1)

    # 清单数据清洗（名称修正+专业分类+参数提取）
    bill_items = clean_bill_items(bill_items)

    # 过滤编码前缀
    if args.filter_code:
        bill_items = [i for i in bill_items if i.get("code", "").startswith(args.filter_code)]
        logger.info(f"按编码前缀'{args.filter_code}'过滤后: {len(bill_items)} 条")

    # 限制数量（调试用）
    if args.limit:
        bill_items = bill_items[:args.limit]
        logger.info(f"限制处理前 {args.limit} 条")

    # 2. 初始化搜索引擎
    logger.info("第2步：初始化搜索引擎...")
    province = args.province or config.CURRENT_PROVINCE
    searcher = HybridSearcher(province)
    validator = ParamValidator()

    # 检查引擎状态
    status = searcher.get_status()
    logger.info(f"  BM25索引: {status['bm25_count']} 条定额")
    logger.info(f"  向量索引: {status['vector_count']} 条定额")

    if not status["bm25_ready"]:
        logger.error("BM25索引未就绪，请先运行: python -m src.bm25_engine")
        sys.exit(1)

    # 初始化经验库（可选）
    experience_db = None
    if not args.no_experience:
        try:
            from src.experience_db import ExperienceDB
            experience_db = ExperienceDB()
            exp_stats = experience_db.get_stats()
            logger.info(f"  经验库: {exp_stats['total']} 条历史记录")
        except Exception as e:
            logger.warning(f"经验库加载失败，将跳过经验库: {e}")
            experience_db = None

    # 3. 执行匹配
    logger.info(f"第3步：开始匹配 ({args.mode} 模式)...")

    if args.mode == "search":
        results = match_search_only(bill_items, searcher, validator, experience_db)
    elif args.mode == "agent":
        results = match_agent(bill_items, searcher, validator, experience_db,
                              llm_type=args.agent_llm)
    else:
        results = match_full(bill_items, searcher, validator, experience_db)

    # 4. 输出结果
    elapsed = time.time() - start_time
    total = len(results)
    matched = sum(1 for r in results if r.get("quotas"))
    high_conf = sum(1 for r in results if r.get("confidence", 0) >= config.CONFIDENCE_GREEN)
    mid_conf = sum(1 for r in results if config.CONFIDENCE_YELLOW <= r.get("confidence", 0) < config.CONFIDENCE_GREEN)
    exp_matched = sum(1 for r in results if r.get("match_source", "").startswith("experience"))

    stats = {
        "total": total,
        "matched": matched,
        "high_conf": high_conf,
        "mid_conf": mid_conf,
        "low_conf": total - high_conf - mid_conf,
        "exp_hits": exp_matched,
        "elapsed": elapsed,
    }

    if args.json_output:
        # JSON模式：输出结果给Web界面读取，不生成Excel
        import json
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({"results": results, "stats": stats}, f,
                      ensure_ascii=False)
        logger.info(f"JSON结果已保存: {args.json_output}")
    else:
        # 常规模式：生成Excel（基于原始文件结构，保留分部小节标题）
        logger.info("第4步：生成结果Excel...")
        writer = OutputWriter()
        output_path = writer.write_results(
            results, args.output, original_file=str(input_path))
        logger.info(f"  输出文件: {output_path}")

    # 5. 打印统计
    logger.info("=" * 60)
    logger.info("匹配完成")
    logger.info(f"  总清单项: {total}")
    logger.info(f"  已匹配: {matched} ({matched * 100 // max(total, 1)}%)")
    logger.info(f"  高置信度(绿): {high_conf}")
    logger.info(f"  中置信度(黄): {mid_conf}")
    logger.info(f"  未匹配/低置信度(红): {total - high_conf - mid_conf}")
    if experience_db:
        logger.info(f"  经验库命中: {exp_matched} ({exp_matched * 100 // max(total, 1)}%)")
    logger.info(f"  耗时: {elapsed:.1f}秒")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
