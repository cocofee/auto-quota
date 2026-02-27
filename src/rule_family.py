"""
定额规则 — 家族匹配与关键词逻辑

从 rule_validator.py 拆分出来，负责：
1. 行业常量（材质映射、连接方式、介质上下文等）
2. tokenize() 文本分词函数
3. 文本型家族匹配（电梯、起重机等）
4. 关键词驱动匹配
5. 多维属性兼容性检查（连接方式/材质/保温/人防/防爆/室外）

使用方式（方法重绑定，调用方无需感知拆分）：
    from src.rule_validator import RuleValidator
    rv = RuleValidator()
    rv.match_by_rules(...)  # 内部自动调用本模块的方法
"""

import re
import jieba
from loguru import logger


# ================================================================
# 行业常量
# ================================================================

# 关键词提取时忽略的词（太常见，没有区分度）
STOP_WORDS = {"以内", "以下", "以上", "以", "内", "其他", "及"}

# 通用修饰词（安装方式、连接方式等）
# 这些词太泛，不能单独作为设备匹配的依据
# 例如"嵌入安装"同时出现在灯具和配电箱描述中，不能仅凭这些词确定设备类型
GENERIC_MODIFIERS = {
    "安装", "明装", "暗装", "嵌入", "落地", "壁装", "吸顶", "挂装",
    "敷设", "制作", "连接", "螺纹", "法兰", "沟槽", "焊接", "卡压",
    "埋地", "穿管", "架空", "直埋", "桥架",
    # 设备大类词：太泛，不能单独确认匹配（"开关"同时出现在跷板开关和快速自动开关中）
    "开关", "插座", "灯具", "电缆", "管道", "母线", "接地",
}

# === 行业知识：材质商品名 → 定额中使用的标准名称 ===
# 造价人员看到"PPR"就知道是给水塑料管(热熔连接)，但系统不知道
# 这个映射表就是告诉系统这些行业常识
MATERIAL_TRADE_TO_QUOTA = {
    # 热塑性塑料管（热熔连接）
    "PPR":  ["塑料管", "给水塑料管"],     # 聚丙烯管，最常见的给水塑料管
    "PE":   ["塑料管"],                   # 聚乙烯管
    "HDPE": ["塑料管"],                   # 高密度聚乙烯管
    "PB":   ["塑料管"],                   # 聚丁烯管
    "PERT": ["塑料管"],                   # 耐热聚乙烯管（地暖用）
    # 热固性塑料管（粘接）
    "PVC":   ["塑料管"],                  # 聚氯乙烯管
    "UPVC":  ["塑料管", "排水塑料管"],     # 硬聚氯乙烯管，常用于排水
    "PVC-U": ["塑料管", "排水塑料管"],     # 同UPVC
    "CPVC":  ["塑料管"],                  # 氯化聚氯乙烯管（消防用）
    "ABS":   ["塑料管"],                  # 丙烯腈-丁二烯-苯乙烯管
}

# === 行业知识：材质 → 默认连接方式 ===
# PPR/PE等热塑性塑料 → 热熔连接
# PVC/UPVC等 → 粘接
MATERIAL_DEFAULT_CONNECTION = {
    "PPR": "热熔", "PE": "热熔", "HDPE": "热熔", "PB": "热熔", "PERT": "热熔",
    "PVC": "粘接", "UPVC": "粘接", "PVC-U": "粘接", "CPVC": "粘接", "ABS": "粘接",
}

# === 介质/用途上下文词组 ===
# 清单描述中包含这些词时，可以区分"给水"vs"排水"等同参数家族
# 每组词互相冲突：清单说"排水"则"给水"家族应降分
MEDIUM_CONTEXT = {
    "排水": {"match": {"排水", "污水", "雨水", "废水"}, "conflict": {"给水"}},
    "给水": {"match": {"给水", "冷水", "热水", "生活用水"}, "conflict": {"排水"}},
}

# text-family parameter hint -> parsed field key
TEXT_HINT_TO_PARAM = {
    "站数": "elevator_stops",
    "层数": "elevator_stops",
    "重量": "weight_t",
    "起重量": "weight_t",
    "功率": "kw",
    "容量": "kva",
    "电流": "ampere",
    "截面": "cable_section",
    "口径": "dn",
    "直径": "dn",
    "管径": "dn",
    "回路": "circuits",
    "周长": "perimeter",
    "大边": "large_side",
}


# ================================================================
# 文本分词
# ================================================================

def tokenize(text: str) -> list:
    """
    把文本拆分为关键词列表

    规则：
    - 去掉括号内的参数说明（如 "(kVA以内)"）
    - 去掉纯数字、纯符号
    - 去掉 "N.标签:" 格式的前缀标签（如 "1.名称:", "2.回路数:"）
    - 保留 ≥2 个字符的中文/英文词
    - 用jieba分词处理长token，确保"室内给水PPR冷水管"能拆成"室内"+"给水"+"PPR"+"冷水管"
    """
    # 去掉括号和括号内的内容
    text = re.sub(r'[\(（][^\)）]*[\)）]', ' ', text)
    # 去掉 "N.标签:" 格式（清单描述常见，如 "2.回路数:", "4.安装方式:"）
    text = re.sub(r'\d+[.、．]\s*[^:：\n]{1,6}[：:]', ' ', text)
    # 去掉无序号的 "标签:" 格式（如 "回路数:", "安装方式:", "名称:"）
    text = re.sub(r'[\u4e00-\u9fff]{2,4}[：:]', ' ', text)
    # 去掉数字和常见单位
    text = re.sub(r'[\d.]+\s*(?:mm[²2]?|kVA?|kW|[mM]|[tT]|A|cm|kg)?', ' ', text)
    # 按分隔符切分
    raw_tokens = re.split(r'[\s,，、/×xX\-~]+', text)

    # 第一轮：对每个原始token，如果太长（≥5字符）就用jieba再拆
    # 短token直接保留（避免过度拆分）
    result = []
    for t in raw_tokens:
        t = t.strip()
        if not t or len(t) < 2:
            continue
        if re.match(r'^[\d.]+$', t):
            continue
        if t in STOP_WORDS:
            continue

        # 长token用jieba分词（处理"室内给水PPR冷水管"这类混合词）
        if len(t) >= 5:
            jieba_tokens = list(jieba.cut(t))
            for jt in jieba_tokens:
                jt = jt.strip()
                if len(jt) >= 2 and jt not in STOP_WORDS and not re.match(r'^[\d.]+$', jt):
                    if jt not in result:
                        result.append(jt)
            # 同时保留原始长token（某些规则关键词可能就是长词）
            if t not in result:
                result.append(t)
        else:
            if t not in result:
                result.append(t)

    # 材质代号→定额标准名称展开
    # 例如清单写"PPR"，自动补充"塑料管"、"给水塑料管"到token列表
    # 这样家族关键词"给水塑料管"就能匹配到PPR清单
    for t in list(result):  # 遍历副本，避免修改迭代对象
        t_upper = t.upper()
        if t_upper in MATERIAL_TRADE_TO_QUOTA:
            for quota_name in MATERIAL_TRADE_TO_QUOTA[t_upper]:
                if quota_name not in result:
                    result.append(quota_name)

    # 生成相邻token的拼接词（二元组合）
    # 例如 tokens=['给水', '塑料管'] → 也添加 '给水塑料管'
    # 这样规则家族关键词"给水塑料管"就能被匹配到
    # 只拼接纯中文的相邻token（避免产生无意义组合）
    bigrams = []
    for i in range(len(result) - 1):
        t1, t2 = result[i], result[i + 1]
        # 只拼接短token（每个≤5字符），避免生成过长的无意义组合
        if len(t1) <= 5 and len(t2) <= 5:
            combined = t1 + t2
            if combined not in result and combined not in bigrams:
                bigrams.append(combined)
    result.extend(bigrams)

    return result


# ================================================================
# 以下为 RuleValidator 类的方法（通过重绑定挂回类上）
# ================================================================

def _match_text_family(self, bill_text: str, bill_keywords: list,
                       bill_params: dict = None,
                       item: dict = None, books: list = None) -> dict:
    """
    通用文本型家族匹配

    适用于所有 value_type="text" 的家族（电梯、起重机、轨道等）。
    不需要为每种设备写专用代码。

    算法：
    1. 关键词匹配找候选文本型家族
    2. 自动分析家族 quota values 中的数字模式
    3. 从家族名称/前缀/值中提取参数线索
    4. 用线索从清单中提取对应参数值
    5. 向上取档匹配
    """
    best_score = 0
    best_match = None

    for entry in self.all_families:
        if not isinstance(entry, dict):
            continue
        family = entry.get("family")
        if not isinstance(family, dict):
            continue

        # 只处理文本型家族（有 tiers 的走 param_driven）
        if family.get("tiers"):
            continue
        quotas = family.get("quotas", [])
        if not quotas:
            continue

        # 册号过滤
        if books and entry.get("book") and entry["book"] not in books:
            continue

        # 属性兼容性检查
        family_attrs = entry.get("attrs", {})
        if family_attrs and not self._family_compatible(
                bill_text, bill_params or {}, family_attrs):
            continue

        family_kws = entry.get("keywords") or []
        if not family_kws:
            continue

        # ---- 关键词匹配评分 ----
        forward_hits, has_specific_keyword = self._compute_keyword_hits(
            family_kws, bill_keywords)

        type_fallback = False
        if forward_hits == 0 or not has_specific_keyword:
            # 补充定位：关键词没命中，但清单提取的分类参数直接匹配家族名称
            # 例如 text_parser 提取了 elevator_type="载货电梯"，
            # 而家族名称是"载货电梯 层数"→ 直接定位成功
            # 这是通用逻辑：不管什么设备，分类参数匹配家族名就视为有效
            type_matched = False
            family_name = family.get("name", "")
            for key, val in (bill_params or {}).items():
                if isinstance(val, str) and len(val) >= 3:
                    if val in family_name:
                        type_matched = True
                        break
            if not type_matched:
                continue
            # 分类参数本身就是强信号，视同命中了具体关键词
            has_specific_keyword = True
            forward_hits = max(forward_hits, 1)
            type_fallback = True  # 标记：通过分类参数匹配的

        # 验证家族基础名称匹配（防止参数描述关键词导致误匹配）
        # 但如果已通过分类参数直接定位家族，跳过此检查（分类参数是更强的信号）
        if not type_fallback:
            if not self._has_base_name_match(family.get("name", ""), bill_keywords):
                continue

        # ---- 解析 quota values 中的数字（提取可比较的档位） ----
        value_tiers = self._parse_text_values(quotas)
        if not value_tiers:
            continue  # 纯文本值（如"门型吊钩式"），暂不处理

        # ---- 从清单中提取对应参数 ----
        bill_value = self._extract_text_param(
            bill_text, bill_params or {}, family)
        if bill_value is None:
            continue

        # ---- 向上取档匹配 ----
        matched_quota, exceeded = self._find_text_tier(
            value_tiers, bill_value)
        if not matched_quota:
            continue

        # ---- 综合评分 ----
        reverse_hits = self._count_reverse_hits(family_kws, bill_keywords)
        coverage = forward_hits / len(family_kws) if family_kws else 0
        bill_coverage = (reverse_hits / len(bill_keywords)
                         if bill_keywords else 0)
        medium_bonus = self._compute_medium_bonus(
            bill_text, family.get("name", ""))
        score = 0.5 + coverage * 0.3 + bill_coverage * 0.2 + medium_bonus

        # 文本型参数与家族名称的关联加分
        # 例如 elevator_type="载货电梯" 出现在家族名 "载货电梯 层数" 中 → 加分
        # 这是通用逻辑：不管什么设备类型，只要提取到的文本参数匹配家族名就加分
        family_name = family.get("name", "")
        for key, val in (bill_params or {}).items():
            if isinstance(val, str) and len(val) >= 2:
                if val in family_name:
                    score += 0.15
                    break

        if score > best_score:
            best_score = score
            best_match = {
                "entry": entry,
                "quota": matched_quota,
                "bill_value": bill_value,
                "exceeded": exceeded,
            }

    if not best_match or best_score < 0.55:
        return None

    return self._build_text_result(
        best_match["entry"], best_match["quota"],
        best_match["bill_value"], best_match["exceeded"],
        best_score, bill_text, item)


def _parse_text_values(self, quotas: list) -> list:
    """
    从文本型家族的 quota values 中提取数字档位

    例如 "26 站数26" → 提取首部数字 26
    例如 "5 跨距(m以内) 19.5" → 提取首部数字 5

    返回: [(quota_dict, primary_number), ...] 按数字升序排列
    """
    results = []
    for quota in quotas:
        value_str = quota.get("value", "")
        # 提取值的首部数字（支持小数和分数如"15/3"）
        m = re.match(r'^(\d+(?:\.\d+)?)\s', value_str)
        if m:
            results.append((quota, float(m.group(1))))
    results.sort(key=lambda x: x[1])
    return results


def _extract_text_param(self, bill_text: str, bill_params: dict,
                        family: dict) -> float:
    """
    通用文本型参数提取：从清单中提取与家族对应的参数值

    不需要知道具体是什么设备。而是：
    1. 从家族 name/prefix/values 中收集参数名称线索（如"层数"、"起重量"）
    2. 将线索映射到 bill_params 中已提取的参数
    3. 如果映射失败，直接在清单文本中搜索 "线索词+数字"
    """
    hints = self._collect_param_hints(family)
    if not hints:
        return None

    # 策略1：通过参数名映射到 bill_params
    for hint in hints:
        for map_key, param_key in TEXT_HINT_TO_PARAM.items():
            if map_key in hint:
                val = bill_params.get(param_key)
                if val is not None:
                    return float(val)

    # 策略2：在清单文本中直接搜索 "参数名:数字" 或 "参数名 数字"
    for hint in hints:
        if len(hint) < 2:
            continue
        pattern = rf'{re.escape(hint)}[：:\s]*(\d+(?:\.\d+)?)'
        m = re.search(pattern, bill_text)
        if m:
            return float(m.group(1))

    return None


def _collect_param_hints(self, family: dict) -> list:
    """
    从家族数据中收集参数名称线索

    数据来源（不需要知道设备类型）：
    - 家族名称尾部词：如 "曳引式电梯 层数" → "层数"
    - 家族前缀尾部词：如 "载货电梯 层数" → "层数"
    - quota value 中的中文词：如 "26 站数26" → "站数"
    """
    hints = []

    # 从 family name 提取尾部参数词
    name = family.get("name", "")
    name_parts = name.split()
    if len(name_parts) >= 2:
        last_part = name_parts[-1]
        # 去掉括号内容（如"起重量(t以内)"→"起重量"）
        last_part = re.sub(r'[（(].*[)）]', '', last_part).strip()
        if last_part and len(last_part) >= 2:
            hints.append(last_part)

    # 从 prefix 提取尾部参数词
    prefix = family.get("prefix", "")
    prefix_parts = prefix.split()
    if len(prefix_parts) >= 2:
        last_part = prefix_parts[-1]
        last_part = re.sub(r'[（(].*[)）]', '', last_part).strip()
        if last_part and len(last_part) >= 2 and last_part not in hints:
            hints.append(last_part)

    # 从 quota values 中提取中文参数名
    for quota in family.get("quotas", [])[:3]:
        value_str = quota.get("value", "")
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,}', value_str)
        for word in chinese_words:
            # 过滤"以内"等无意义词
            if word not in STOP_WORDS and word not in hints:
                hints.append(word)

    return hints


def _find_text_tier(self, value_tiers: list,
                    bill_value: float) -> tuple:
    """
    向上取档：在文本型家族的数字档位中找 ≥ bill_value 的最小值

    返回: (matched_quota_dict, exceeded_max)
    - matched_quota_dict: 匹配到的 quota 字典，None 表示没找到
    - exceeded_max: 是否超出了最大档（取了最大档兜底）
    """
    # 向上取档
    for quota, number in value_tiers:
        if number >= bill_value:
            return quota, False

    # 超出最大档 → 取最大档
    if value_tiers:
        max_quota, max_num = value_tiers[-1]
        logger.warning(
            f"参数值{bill_value}超出最大档{max_num}，取最大档定额"
            f"{max_quota.get('id')}，超出部分需另行计算")
        return max_quota, True

    return None, False


def _extract_tier_display(self, value_str: str) -> str:
    """从档位值字符串中提取用于展示的数字部分。"""
    tier_match = re.match(r'^(\d+(?:\.\d+)?)', value_str or "")
    return tier_match.group(1) if tier_match else (value_str or "?")


def _build_rule_match_result(self, item: dict, quota_id: str, quota_name: str,
                             unit: str, reason: str, confidence: int,
                             family_name: str, score: float,
                             explanation: str) -> dict:
    """统一构建规则匹配结果字典。"""
    return {
        "bill_item": item or {},
        "quotas": [{
            "quota_id": quota_id,
            "name": quota_name,
            "unit": unit,
            "reason": reason,
        }],
        "confidence": confidence,
        "explanation": explanation,
        "match_source": "rule",
        "rule_family": family_name,
        "rule_score": round(score, 3),
    }


def _build_text_result(self, entry: dict, matched_quota: dict,
                       bill_value: float, exceeded: bool,
                       score: float, bill_text: str,
                       item: dict = None) -> dict:
    """构建文本型家族匹配结果"""
    family = entry["family"]
    quota_id = matched_quota.get("id")
    prefix = family.get("prefix", "")
    value_str = matched_quota.get("value", "")
    quota_name = f"{prefix} {value_str}".strip()

    # 从 value 中提取档位显示数字
    tier_display = self._extract_tier_display(value_str)

    # 置信度：精确匹配85，超档80（需≥80才能跳过搜索）
    confidence = 80 if exceeded else 85

    explanation_parts = [
        f"规则匹配: 「{family.get('name', '')}」",
        f"参数{bill_value}→档位{tier_display}",
    ]
    if exceeded:
        explanation_parts.append(
            f"(超出最大档{tier_display}，需另行计算)")

    explanation = " | ".join(explanation_parts)
    result = self._build_rule_match_result(
        item=item,
        quota_id=quota_id,
        quota_name=quota_name,
        unit=family.get("unit", ""),
        reason=explanation,
        confidence=confidence,
        family_name=family.get("name", ""),
        score=score,
        explanation=explanation,
    )

    logger.info(f"文本型规则匹配: '{bill_text[:40]}' → {quota_id} "
                 f"(「{family.get('name', '')}」参数{bill_value}→{tier_display})")

    return result


def _match_by_keyword_driven(self, bill_text: str, bill_keywords: list,
                             bill_params: dict = None,
                             item: dict = None,
                             books: list = None) -> dict:
    """
    策略B：纯关键词驱动匹配（和之前逻辑一致，作为兜底）

    适合没有数值参数、但关键词特征明显的清单
    """
    best_score = 0
    best_entry = None

    for entry in self.all_families:
        if not isinstance(entry, dict):
            continue
        # 按册过滤：只在指定的册号范围内匹配
        if books and entry.get("book") and entry["book"] not in books:
            continue
        # 多维属性过滤：连接方式/材质/保温/人防/防爆不兼容的家族直接跳过
        family_attrs = entry.get("attrs", {})
        if family_attrs and not self._family_compatible(
                bill_text, bill_params or {}, family_attrs):
            continue

        family_kws = entry.get("keywords") or []
        if not family_kws:
            continue

        forward_hits, has_specific_keyword = self._compute_keyword_hits(
            family_kws, bill_keywords)

        reverse_hits = self._count_reverse_hits(family_kws, bill_keywords)

        if forward_hits == 0:
            continue

        # 关键词驱动模式必须命中至少一个具体名词
        # 防止"开关"匹配到"快速自动开关"家族（只靠通用词子串命中）
        if not has_specific_keyword:
            continue

        coverage = forward_hits / len(family_kws) if family_kws else 0
        bill_coverage = reverse_hits / len(bill_keywords) if bill_keywords else 0
        # 介质上下文加减分（区分"给水"vs"排水"等同参数家族）
        family = entry.get("family")
        if not isinstance(family, dict):
            continue
        medium_bonus = self._compute_medium_bonus(
            bill_text, family.get("name", ""))
        score = coverage * 0.6 + bill_coverage * 0.4 + medium_bonus

        # 关键词驱动需要更严格的门槛（没有参数做确认，纯靠关键词）
        if forward_hits >= 2 and coverage >= 0.5 and score > best_score:
            best_score = score
            best_entry = entry

    if not best_entry or best_score < 0.45:
        return None

    family = best_entry.get("family")
    if not isinstance(family, dict):
        return None
    tiers = family.get("tiers")

    if tiers:
        param_value = self._extract_param_value(bill_text, family)
        if param_value is None:
            return None
        return self._build_rule_result(
            best_entry, param_value, best_score, bill_text, item)
    else:
        return None


# ================================================================
# 关键词匹配辅助方法
# ================================================================

def _compute_keyword_hits(self, family_kws: list, bill_keywords: list) -> tuple:
    """计算关键词正向命中数和是否命中了具体名词。"""
    forward_hits = 0
    has_specific_keyword = False

    for fkw in family_kws:
        matched = False
        matched_via_generic = False
        for bkw in bill_keywords:
            if fkw == bkw or fkw in bkw:
                matched = True
                break
            if bkw in fkw:
                matched = True
                if bkw in GENERIC_MODIFIERS:
                    matched_via_generic = True
                break

        if not matched and len(fkw) >= 4:
            fkw_parts = list(jieba.cut(fkw))
            fkw_parts = [p for p in fkw_parts if len(p) >= 2 and p not in STOP_WORDS]
            if len(fkw_parts) >= 2:
                all_covered = all(
                    any(fp == bkw or fp in bkw or bkw in fp for bkw in bill_keywords)
                    for fp in fkw_parts
                )
                if all_covered:
                    matched = True

        if matched:
            forward_hits += 1
        if matched and fkw not in GENERIC_MODIFIERS and not matched_via_generic:
            has_specific_keyword = True

    return forward_hits, has_specific_keyword


def _count_reverse_hits(self, family_kws: list, bill_keywords: list) -> int:
    """计算清单关键词在家族关键词中的反向命中数。"""
    reverse_hits = 0
    for bkw in bill_keywords:
        for fkw in family_kws:
            if bkw in fkw or fkw in bkw:
                reverse_hits += 1
                break
    return reverse_hits


def _has_common_prefix(self, left: str, right: str, min_len: int = 2) -> bool:
    """检查两个字符串是否有共同前缀（≥min_len字符）。"""
    common_prefix_len = 0
    for c1, c2 in zip(left, right):
        if c1 != c2:
            break
        common_prefix_len += 1
        if common_prefix_len >= min_len:
            return True
    return False


def _has_base_name_match(self, base_name: str, bill_keywords: list) -> bool:
    """验证家族基础名称是否与清单关键词有匹配。"""
    base_name_kws = tokenize(base_name)
    base_name_kws = [
        kw for kw in base_name_kws
        if kw not in GENERIC_MODIFIERS and len(kw) >= 2
    ]
    if not base_name_kws:
        return True

    for bkw in bill_keywords:
        for bnkw in base_name_kws:
            if bnkw == bkw or bnkw in bkw or bkw in bnkw:
                return True
            if self._has_common_prefix(bnkw, bkw, min_len=2):
                return True
    return False


# ================================================================
# 多维属性兼容性检查
# ================================================================

def _compute_medium_bonus(self, bill_text: str, family_name: str) -> float:
    """
    根据清单中的介质/用途信息，给家族名称匹配或冲突的加减分

    例如：清单说"介质:污水"+"UPVC排水DN100"
      → "排水塑料管(粘接)" 加分 +0.1
      → "给水塑料管(粘接)" 减分 -0.15
    """
    bonus = 0.0
    bill_lower = bill_text.lower()
    for context_key, rules in MEDIUM_CONTEXT.items():
        # 家族名称包含该上下文关键词（如家族名含"排水"或"给水"）
        if context_key not in family_name:
            continue
        # 检查清单是否包含匹配的介质词
        bill_has_match = any(kw in bill_lower for kw in rules["match"])
        # 检查清单是否包含冲突的介质词
        bill_has_conflict = any(kw in bill_lower for kw in rules["conflict"])

        if bill_has_match:
            bonus += 0.1   # 清单说"排水/污水"，家族也是"排水" → 奖励
        if bill_has_conflict:
            bonus -= 0.15  # 清单说"给水"，家族却是"排水" → 惩罚
    return bonus


def _family_compatible(self, bill_text: str, bill_params: dict,
                       family_attrs: dict) -> bool:
    """
    检查家族结构化属性是否与清单兼容

    任何一条属性冲突就返回False（跳过该家族）。
    如果清单或家族没有对应属性，则不做该维度的检查（不跳过）。

    参数:
        bill_text: 清单完整文本（用于关键词检查：保温/人防/防爆）
        bill_params: text_parser.parse()提取的结构化参数
        family_attrs: 家族的attrs字典（connection/material/is_insulation等）
    """
    # === 规则1：连接方式冲突 → 跳过 ===
    # 清单写"沟槽连接"，家族是"螺纹" → 不兼容
    bill_conn = bill_params.get("connection", "")
    family_conn = family_attrs.get("connection", "")
    if bill_conn and family_conn:
        if not self._connections_compatible(bill_conn, family_conn):
            return False

    # === 规则2：材质冲突 → 跳过 ===
    # 清单写"镀锌钢管"，家族是"塑料管" → 不兼容
    bill_mat = bill_params.get("material", "")
    family_mat = family_attrs.get("material", "")
    if bill_mat and family_mat:
        if not self._materials_compatible(bill_mat, family_mat):
            return False

    # === 规则3：清单没说保温/绝热，定额是保温类 → 跳过 ===
    if family_attrs.get("is_insulation"):
        if "保温" not in bill_text and "绝热" not in bill_text:
            return False

    # === 规则4：清单没说人防，定额是人防类 → 跳过 ===
    if family_attrs.get("is_civil_defense"):
        if "人防" not in bill_text:
            return False

    # === 规则5：清单没说防爆，定额是防爆类 → 跳过 ===
    if family_attrs.get("is_explosion_proof"):
        if "防爆" not in bill_text:
            return False

    # === 规则6：清单没说室外，定额是室外类 → 跳过（默认室内） ===
    # 造价行业惯例：清单没写"室内"/"室外"时默认指室内
    if family_attrs.get("is_outdoor"):
        if "室外" not in bill_text:
            return False

    return True


def _connections_compatible(self, bill_conn: str,
                            family_conn: str) -> bool:
    """
    检查两个连接方式是否兼容

    兼容规则：
    - 完全相同 → 兼容
    - 一方包含另一方 → 兼容（如"热熔连接"包含"热熔"、"电熔连接"包含"电熔"）
    - 都包含"法兰" → 兼容（焊接法兰/螺纹法兰都是法兰的子类型）
    - 行业同义词 → 兼容（"承插"≈"粘接"：PVC-U排水管的承插连接就是粘接）
    - 其他 → 不兼容（螺纹≠沟槽、热熔≠粘接 等）
    """
    if bill_conn == family_conn:
        return True
    # 子串匹配：如"热熔连接"包含"热熔"，"电熔连接"包含"电熔"
    if bill_conn in family_conn or family_conn in bill_conn:
        return True
    # 法兰系列互相兼容：焊接法兰、螺纹法兰、对夹式法兰都是法兰的子类型
    if "法兰" in bill_conn and "法兰" in family_conn:
        return True
    # 行业同义词：PVC-U排水管的"承插连接"实际上就是"粘接"（管子插入承口用胶粘合）
    conn_synonyms = [
        {"承插", "粘接"},   # PVC-U排水管：承插连接≈粘接
    ]
    for syn_group in conn_synonyms:
        bill_in = any(s in bill_conn for s in syn_group)
        family_in = any(s in family_conn for s in syn_group)
        if bill_in and family_in:
            return True
    return False


def _materials_compatible(self, bill_mat: str, family_mat: str) -> bool:
    """
    检查两个材质是否兼容

    兼容规则：
    - 完全相同 → 兼容
    - 一方包含另一方 → 兼容（如"不锈钢"和"不锈钢管"）
    - 清单材质含已知代号（PPR/UPVC等）且代号的定额标准名包含家族材质 → 兼容
    - 其他 → 不兼容（镀锌钢管≠塑料管、钢塑≠铝塑 等）
    """
    if bill_mat == family_mat:
        return True
    # 一方包含另一方（如"不锈钢"包含在"不锈钢管"中）
    if bill_mat in family_mat or family_mat in bill_mat:
        return True
    # 材质代号兼容检查：PPR→塑料管、UPVC→塑料管 等
    bill_upper = bill_mat.upper()
    for trade_name, quota_names in MATERIAL_TRADE_TO_QUOTA.items():
        if trade_name in bill_upper:
            # 清单含该材质代号，检查家族材质是否在其定额标准名列表中
            for qn in quota_names:
                if qn == family_mat or qn in family_mat or family_mat in qn:
                    return True
    return False
