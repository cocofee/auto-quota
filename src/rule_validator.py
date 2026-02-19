"""
定额规则校验器（方案B：规则辅助搜索）

功能：
1. 规则预匹配（搜索前）：用关键词匹配家族 → 按参数选档位 → 直接出结果
2. 规则校验（搜索后）：校验档位是否正确，错了自动纠正

设计原则：
- 规则预匹配能命中的直接返回，省掉搜索和大模型
- 命中不了的不干预，交给搜索兜底
- 宁可漏匹配，也不错匹配（阈值宁高勿低）
"""

import json
import re
import jieba
from pathlib import Path
from loguru import logger

from src.text_parser import parser as text_parser

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
    import jieba  # tokenize中仍需要，因为可能在其他模块调用

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


class RuleValidator:
    """定额规则校验器"""

    def __init__(self, rules_path: str = None):
        """
        参数:
            rules_path: 规则JSON文件路径，默认自动查找
        """
        self.rules = None
        self.family_index = {}
        self.all_families = []  # 所有家族（带预计算的关键词）

        if rules_path is None:
            # 自动查找规则文件
            project_root = Path(__file__).parent.parent
            rules_dir = project_root / "data" / "quota_rules"
            # 找第一个JSON文件
            json_files = list(rules_dir.glob("*_安装定额规则.json"))
            if json_files:
                rules_path = str(sorted(json_files)[0])
            else:
                logger.warning("未找到定额规则文件，规则校验功能禁用")
                return

        rules_file = Path(rules_path)
        try:
            with open(rules_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except FileNotFoundError:
            logger.warning(f"规则文件不存在，规则校验功能禁用: {rules_file}")
            return
        except json.JSONDecodeError as e:
            logger.warning(f"规则文件JSON损坏，规则校验功能禁用: {rules_file} ({e})")
            return
        except OSError as e:
            logger.warning(f"读取规则文件失败，规则校验功能禁用: {rules_file} ({e})")
            return
        except Exception as e:
            logger.warning(f"加载规则文件异常，规则校验功能禁用: {rules_file} ({e})")
            return

        if not isinstance(loaded, dict):
            logger.warning(f"规则文件格式错误（根节点非对象），规则校验功能禁用: {rules_file}")
            return
        chapters = loaded.get("chapters")
        if not isinstance(chapters, dict):
            logger.warning(f"规则文件格式错误（缺少chapters对象），规则校验功能禁用: {rules_file}")
            return

        self.rules = loaded

        # 构建两个索引：
        # 1. quota_id → 家族（用于档位校验）
        # 2. 关键词 → 家族列表（用于规则预匹配）
        self._build_index()

        family_count = len(set(id(v) for v in self.family_index.values()))
        if self.family_index:
            logger.info(f"规则校验器已加载: {len(self.family_index)} 条定额, "
                        f"{family_count} 个家族")
        else:
            logger.warning(f"规则文件已加载但未解析到可用家族，规则校验功能实际不可用: {rules_file}")

    def _build_index(self):
        """构建索引：quota_id→家族 + 关键词→家族"""
        if not self.rules:
            return

        for chapter_name, chapter_data in self.rules.get("chapters", {}).items():
            for family in chapter_data.get("families", []):
                if family.get("type") == "family":
                    # 索引1：quota_id → 家族
                    for quota in family.get("quotas", []):
                        qid = quota.get("id")
                        if qid:
                            self.family_index[qid] = family

                    # 索引2：预计算家族关键词（用于规则预匹配）
                    prefix = family.get("prefix", "")
                    name = family.get("name", "")
                    # 从名称和前缀中提取关键词
                    keywords = tokenize(f"{name} {prefix}")
                    # 去重
                    keywords = list(dict.fromkeys(keywords))

                    self.all_families.append({
                        "family": family,
                        "keywords": keywords,
                        "chapter": chapter_name,
                        "book": chapter_data.get("book", ""),  # 所属大册（C1~C12）
                        "attrs": family.get("attrs", {}),  # 结构化属性（连接方式/材质/保温/人防/防爆/室外）
                    })

                elif family.get("type") == "standalone":
                    # standalone只索引到family_index（用于validate_result校验）
                    # 不加入all_families（暂不参与规则预匹配，避免误匹配）
                    qid = family.get("quota_id")
                    if qid:
                        self.family_index[qid] = family

    def match_by_rules(self, bill_text: str, item: dict = None,
                       clean_query: str = None, books: list = None) -> dict:
        """
        规则预匹配：尝试用规则文件直接匹配定额

        两种匹配策略（自动选择最优）：
        策略A - 参数驱动：先从清单提取参数 → 找参数类型匹配的家族 → 关键词辅助确认
        策略B - 关键词驱动：纯关键词匹配找家族 → 再提取参数选档

        策略A更通用，适合有明确参数的清单（配电箱回路、变压器kVA、管道DN等）
        策略B兜底，适合关键词特征明显的清单

        参数:
            bill_text: 清单完整文本（名称+描述），用于参数提取
            item: 清单项字典（可选，用于构建返回结果）
            clean_query: 清洗后的搜索文本，用于关键词匹配。不传则用 bill_text。
            books: 允许匹配的大册列表（如 ["C10", "C8", "C12"]），None表示不限制

        返回:
            匹配结果字典，或 None（未命中）
        """
        if not self.rules or not self.all_families:
            return None

        keyword_text = clean_query if clean_query else bill_text

        # 从清单文本提取关键词
        bill_keywords = tokenize(keyword_text)
        if not bill_keywords:
            return None

        # 从清单文本提取结构化参数（连接方式、材质等，用于多维属性过滤）
        bill_params = text_parser.parse(bill_text)

        # === 行业知识推导：根据材质代号推断默认连接方式 ===
        # 清单没写连接方式但写了PPR/UPVC等材质时，自动推导
        # 例如：PPR → 热熔连接，UPVC → 粘接
        if not bill_params.get("connection"):
            # 从清单文本中查找材质代号
            text_upper = bill_text.upper()
            for mat_code, default_conn in MATERIAL_DEFAULT_CONNECTION.items():
                if mat_code in text_upper:
                    bill_params["connection"] = default_conn
                    logger.debug(f"连接方式推导: {mat_code} → {default_conn}")
                    break

        # ===== 策略A：参数驱动匹配（通用、不依赖修饰词如"明装"） =====
        # 思路：如果能从清单中提取出参数值，且参数类型匹配某个家族，
        #       那只需要核心名词对上就行，不要求"明装/落地/嵌入"等修饰词
        param_result = self._match_by_param_driven(
            bill_text, bill_keywords, bill_params, item, books=books)
        if param_result:
            return param_result

        # ===== 策略B：纯关键词驱动匹配（兜底） =====
        return self._match_by_keyword_driven(
            bill_text, bill_keywords, bill_params, item, books=books)

    def _match_by_param_driven(self, bill_text: str, bill_keywords: list,
                               bill_params: dict = None,
                               item: dict = None, books: list = None) -> dict:
        """
        策略A：参数驱动匹配

        逻辑：
        1. 对每个有数值档位的家族，尝试从清单中提取该家族的参数值
        2. 如果能提取到 → 说明参数类型匹配（清单有回路数 → 家族也是回路参数）
        3. 再检查核心名词是否匹配（至少1个关键词命中）
        4. 综合评分：参数匹配(高权重) + 关键词匹配(低权重)
        5. 取最高分的家族，用参数选档

        这样"成套配电箱 12回路"可以匹配"配电箱墙上明装 回路以内"家族，
        因为"回路"参数匹配 + "配电箱"关键词命中，不需要"明装"出现在清单中
        """
        best_score = 0
        best_entry = None
        best_param_value = None

        for entry in self.all_families:
            if not isinstance(entry, dict):
                continue
            family = entry.get("family")
            if not isinstance(family, dict):
                continue
            tiers = family.get("tiers")
            if not tiers:
                continue  # 只处理有数值档位的家族

            # 专业过滤：只在指定的大册范围内匹配（如给排水清单只匹配C10+C8+C12）
            if books and entry.get("book") and entry["book"] not in books:
                continue

            # 多维属性过滤：连接方式/材质/保温/人防/防爆/室外 不兼容的家族直接跳过
            family_attrs = entry.get("attrs", {})
            if family_attrs and not self._family_compatible(
                    bill_text, bill_params or {}, family_attrs):
                continue

            family_kws = entry.get("keywords") or []
            if not family_kws:
                continue

            # 第1步：尝试从清单中提取该家族类型的参数值
            param_value = self._extract_param_value(bill_text, family)
            if param_value is None:
                continue  # 提取不到参数 → 参数类型不匹配，跳过

            # 第2步：检查参数值是否在档位范围内
            correct_tier = self._find_correct_tier(param_value, tiers)
            if correct_tier is None:
                continue  # 参数超出范围

            # 第3步：关键词辅助确认（宽松匹配，只需核心名词命中）
            forward_hits = 0
            has_specific_keyword = False  # 是否命中了"有区分度"的关键词
            for fkw in family_kws:
                matched = False
                matched_via_generic = False  # 是否靠通用词子串匹配
                for bkw in bill_keywords:
                    if fkw == bkw or fkw in bkw:
                        # 精确匹配 或 家族词是清单词的子串 → 可靠匹配
                        matched = True
                        break
                    elif bkw in fkw:
                        # 清单词是家族词的子串（如"开关"在"快速自动开关"中）
                        matched = True
                        if bkw in GENERIC_MODIFIERS:
                            matched_via_generic = True  # 靠通用词匹配的
                        break

                # 补充匹配：如果家族关键词是复合词（如"给水塑料管"），
                # 检查清单tokens是否包含所有构成部分（如"给水"+"塑料管"都在tokens里）
                # 这解决了清单描述把"给水"和"塑料管"分开写的问题
                if not matched and len(fkw) >= 4:
                    fkw_parts = list(jieba.cut(fkw))
                    fkw_parts = [p for p in fkw_parts if len(p) >= 2 and p not in STOP_WORDS]
                    if len(fkw_parts) >= 2:
                        # 检查清单tokens是否覆盖家族关键词的所有子词
                        all_covered = all(
                            any(fp == bkw or fp in bkw or bkw in fp
                                for bkw in bill_keywords)
                            for fp in fkw_parts
                        )
                        if all_covered:
                            matched = True

                if matched:
                    forward_hits += 1
                # 检查命中的家族关键词是否是具体名词（非通用修饰词）
                # 例如"配电箱"是具体名词，"安装"、"嵌入"是通用修饰词
                # 额外排除：靠通用词子串匹配的也不算（如"开关"在"快速自动开关"中）
                if matched and fkw not in GENERIC_MODIFIERS and not matched_via_generic:
                    has_specific_keyword = True

            if forward_hits == 0:
                continue  # 一个关键词都没命中 → 不是同类设备

            # 如果所有命中的关键词都是通用修饰词（如"嵌入"+"安装"），
            # 说明没有匹配到核心名词（如"配电箱"），跳过
            # 例如："嵌装射灯"的"嵌入安装"不应匹配到"配电箱嵌入式安装"家族
            if not has_specific_keyword:
                continue

            # 第3.5步：验证家族基础名称匹配（防止参数描述中的关键词导致误匹配）
            # 例如："油浸频敏变阻器安装"的prefix包含"启动电动机功率"，
            # 导致"电动机检查接线"错误匹配到这个家族（"电动机"来自参数描述）
            # 修复：要求家族基础名称（不含参数）至少有一个核心词与清单匹配
            # 匹配方式：精确/子串匹配 + 共同前缀匹配（≥2字符）
            # 共同前缀：解决"接闪网"vs"接闪带"这类同族不同后缀的术语
            base_name = family.get("name", "")
            base_name_kws = tokenize(base_name)
            base_name_kws = [kw for kw in base_name_kws
                             if kw not in GENERIC_MODIFIERS and len(kw) >= 2]
            if base_name_kws:
                has_base_match = False
                for bkw in bill_keywords:
                    for bnkw in base_name_kws:
                        # 精确匹配或子串匹配
                        if bnkw == bkw or bnkw in bkw or bkw in bnkw:
                            has_base_match = True
                            break
                        # 共同前缀匹配（≥2个字符）
                        # 解决"接闪网"vs"接闪带"等同类术语的不同后缀问题
                        common_prefix_len = 0
                        for ca, cb in zip(bnkw, bkw):
                            if ca == cb:
                                common_prefix_len += 1
                            else:
                                break
                        if common_prefix_len >= 2:
                            has_base_match = True
                            break
                    if has_base_match:
                        break
                if not has_base_match:
                    continue  # 家族核心名称无匹配 → 跳过（参数描述关键词不算）

            reverse_hits = 0
            for bkw in bill_keywords:
                for fkw in family_kws:
                    if bkw in fkw or fkw in bkw:
                        reverse_hits += 1
                        break

            # 第4步：综合评分
            # 参数匹配本身就是很强的信号（回路数对上了回路家族），给高基础分
            # 关键词匹配作为辅助，权重较低
            kw_coverage = forward_hits / len(family_kws)
            bill_kw_coverage = reverse_hits / len(bill_keywords) if bill_keywords else 0

            # 参数匹配基础分 0.5，关键词额外加分
            # 介质上下文加减分（区分"给水塑料管"vs"排水塑料管"等同参数家族）
            medium_bonus = self._compute_medium_bonus(
                bill_text, family.get("name", ""))
            score = 0.5 + kw_coverage * 0.3 + bill_kw_coverage * 0.2 + medium_bonus

            if score > best_score:
                best_score = score
                best_entry = entry
                best_param_value = param_value

        # 阈值：参数驱动模式只需要 0.55 分（0.5基础分 + 至少一点关键词命中）
        if not best_entry or best_score < 0.55:
            return None

        return self._build_rule_result(
            best_entry, best_param_value, best_score, bill_text, item)

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

            forward_hits = 0
            has_specific_keyword = False  # 是否命中了有区分度的关键词
            for fkw in family_kws:
                matched = False
                matched_via_generic = False  # 是否靠通用词子串匹配
                for bkw in bill_keywords:
                    if fkw == bkw or fkw in bkw:
                        # 精确匹配 或 家族词是清单词的子串 → 可靠匹配
                        matched = True
                        break
                    elif bkw in fkw:
                        # 清单词是家族词的子串（如"开关"在"快速自动开关"中）
                        matched = True
                        if bkw in GENERIC_MODIFIERS:
                            matched_via_generic = True  # 靠通用词匹配的，不算具体
                        break

                # 补充匹配：复合关键词拆分后检查（和param_driven中相同逻辑）
                if not matched and len(fkw) >= 4:
                    fkw_parts = list(jieba.cut(fkw))
                    fkw_parts = [p for p in fkw_parts if len(p) >= 2 and p not in STOP_WORDS]
                    if len(fkw_parts) >= 2:
                        all_covered = all(
                            any(fp == bkw or fp in bkw or bkw in fp
                                for bkw in bill_keywords)
                            for fp in fkw_parts
                        )
                        if all_covered:
                            matched = True

                if matched:
                    forward_hits += 1
                # 检查是否命中了具体名词（非通用修饰词，且非靠通用词子串匹配）
                if matched and fkw not in GENERIC_MODIFIERS and not matched_via_generic:
                    has_specific_keyword = True

            reverse_hits = 0
            for bkw in bill_keywords:
                for fkw in family_kws:
                    if bkw in fkw or fkw in bkw:
                        reverse_hits += 1
                        break

            if forward_hits == 0:
                continue

            # 关键词驱动模式必须命中至少一个具体名词
            # 防止"开关"匹配到"快速自动开关"家族（只靠通用词子串命中）
            if not has_specific_keyword:
                continue

            coverage = forward_hits / len(family_kws)
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

    # ============ 多维属性兼容性检查 ============
    # 在关键词评分之前，先用家族的结构化属性（attrs）过滤掉明显不兼容的家族
    # === 介质/用途上下文评分 ===
    # 同参数类型的家族（如"给水塑料管"和"排水塑料管"都是粘接+DN），
    # 用清单中的介质信息（排水/污水/雨水 vs 给水/冷水）来区分

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

    # 解决6大P0问题：阀门连接方式、通风管道→保温、套管→人防、材质冲突等

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

    def _build_rule_result(self, entry: dict, param_value: float,
                           score: float, bill_text: str,
                           item: dict = None) -> dict:
        """构建规则匹配结果（策略A和B共用）"""
        family = entry["family"]
        tiers = family.get("tiers", [])

        correct_tier = self._find_correct_tier(param_value, tiers)
        if correct_tier is None:
            return None

        quota_id = self._find_quota_by_tier(family, correct_tier)
        if not quota_id:
            return None

        quota_name = self._find_quota_name(family, quota_id)
        confidence = 80 if score >= 0.7 else 70

        result = {
            "bill_item": item or {},
            "quotas": [{
                "quota_id": quota_id,
                "name": quota_name or f"{family.get('prefix', '')} {correct_tier}",
                "unit": family.get("unit", ""),
                "reason": (f"规则直接匹配: 「{family.get('name', '')}」"
                           f"参数{param_value}→档位{correct_tier}"),
            }],
            "confidence": confidence,
            "explanation": (f"规则匹配(得分{score:.2f}): "
                            f"「{family.get('name', '')}」"
                            f"参数{param_value}→{correct_tier}档"),
            "match_source": "rule",
            "rule_family": family.get("name", ""),
            "rule_score": round(score, 3),
        }

        logger.debug(f"规则预匹配成功: '{bill_text[:30]}' → {quota_id} "
                    f"(家族={family.get('name', '')}, 得分={score:.2f})")

        return result

    def validate_result(self, result: dict, bill_text: str) -> dict:
        """
        校验一条匹配结果，必要时纠正档位

        参数:
            result: 匹配结果字典，包含 quotas, confidence 等
            bill_text: 清单完整文本（名称+描述），用于提取参数值

        返回:
            修改后的 result（原地修改并返回）
        """
        if not self.rules or not self.family_index:
            return result

        quotas = result.get("quotas", [])
        if not quotas:
            return result

        # 取主定额（第一条）
        main_quota = quotas[0]
        quota_id = main_quota.get("quota_id", "")

        # 查找该定额所在的家族
        family = self.family_index.get(quota_id)
        if not family:
            # 规则文件里没有这个定额（可能是独立定额）→ 不干预
            return result

        # 有档位信息才做校验（纯文字类型的家族不校验）
        tiers = family.get("tiers")
        if not tiers:
            # 没有数值档位 → 加小幅置信度（至少家族对了）
            result["confidence"] = min(result.get("confidence", 0) + 3, 100)
            result["rule_validated"] = True
            result["rule_note"] = f"属于家族「{family.get('name', '')}」"
            return result

        # 从清单文本中提取数值参数
        bill_value = self._extract_param_value(bill_text, family)

        if bill_value is None:
            # 清单里提取不到参数值 → 不干预
            result["rule_validated"] = True
            result["rule_note"] = f"属于家族「{family.get('name', '')}」，清单未提供参数值"
            return result

        # 计算正确的档位（向上取档：选≥bill_value的最小档）
        correct_tier = self._find_correct_tier(bill_value, tiers)
        if correct_tier is None:
            # 参数值超出所有档位范围
            result["rule_note"] = (f"属于家族「{family.get('name', '')}」，"
                                   f"参数值{bill_value}超出最大档{tiers[-1]}")
            return result

        # 找到正确档位对应的定额编号
        correct_quota_id = self._find_quota_by_tier(family, correct_tier)

        # 比较：当前选的定额和正确定额是否一致？
        if quota_id == correct_quota_id:
            # 档位正确 → 置信度加分
            bonus = 8
            new_conf = min(result.get("confidence", 0) + bonus, 100)
            result["confidence"] = new_conf
            result["rule_validated"] = True
            result["rule_note"] = (f"规则校验通过: 「{family.get('name', '')}」"
                                   f"参数{bill_value}→档位{correct_tier}✓")
            return result
        else:
            # 档位错误 → 纠正
            if correct_quota_id:
                # 找到了正确的定额编号
                old_id = quota_id
                old_name = main_quota.get("name", "")

                # 从家族中找到正确定额的名称
                correct_name = self._find_quota_name(family, correct_quota_id)

                # 纠正主定额
                main_quota["quota_id"] = correct_quota_id
                main_quota["name"] = correct_name or main_quota.get("name", "")

                # 置信度：纠正后给一个合理分数
                # 但如果原始结果是"回退候选"（参数不匹配），不应强制拉高
                if "回退候选" in result.get("explanation", ""):
                    # 回退候选：纠正档位有帮助，但定额本身可能不对，小幅加分
                    result["confidence"] = min(result.get("confidence", 0) + 10, 55)
                else:
                    result["confidence"] = max(result.get("confidence", 0), 75)
                result["rule_validated"] = True
                result["rule_corrected"] = True
                result["rule_note"] = (
                    f"规则纠正档位: 「{family.get('name', '')}」"
                    f"参数{bill_value}→档位{correct_tier}, "
                    f"原{old_id}→改为{correct_quota_id}")

                logger.debug(f"规则纠正: {old_id}({old_name}) → "
                            f"{correct_quota_id}({correct_name}), "
                            f"参数值={bill_value}, 正确档={correct_tier}")
            else:
                result["rule_note"] = (f"属于家族「{family.get('name', '')}」，"
                                       f"参数{bill_value}→档位{correct_tier}，"
                                       f"但未找到对应编号")

            return result

    def validate_results(self, results: list[dict]) -> list[dict]:
        """
        批量校验所有匹配结果

        参数:
            results: 匹配结果列表

        返回:
            校验后的结果列表（原地修改）
        """
        if not self.rules:
            return results

        validated = 0
        corrected = 0

        for result in results:
            # 跳过经验库直通的结果（已在经验库匹配阶段做过参数校验）
            if result.get("match_source", "").startswith("experience"):
                continue

            # 组合清单文本
            item = result.get("bill_item", {})
            bill_text = f"{item.get('name', '')} {item.get('description', '')}".strip()

            self.validate_result(result, bill_text)

            if result.get("rule_validated"):
                validated += 1
            if result.get("rule_corrected"):
                corrected += 1

        if validated > 0:
            logger.info(f"规则校验: {validated} 条命中规则, {corrected} 条档位被纠正")

        return results

    def _extract_param_value(self, bill_text: str, family: dict) -> float:
        """
        从清单文本中提取参数值

        根据家族的参数类型（kVA、mm、mm²等）在清单文本中找到对应数值

        参数:
            bill_text: 清单完整文本
            family: 家族信息字典

        返回:
            提取到的数值，如果提取不到则返回 None
        """
        if not bill_text:
            return None

        param_unit = family.get("param_unit", "")
        param_name = family.get("param_name", "")

        # 策略1：根据参数单位，用对应的正则提取
        # 参数单位 → 提取正则 的映射
        unit_patterns = {
            "kVA": [r'(\d+(?:\.\d+)?)\s*[kK][vV][aA]'],
            "kV": [r'(\d+(?:\.\d+)?)\s*[kK][vV](?![aA])'],
            "kW": [r'(\d+(?:\.\d+)?)\s*[kK][wW]'],
            "A": [r'(\d+(?:\.\d+)?)\s*[aA](?![m²])'],
            "mm2": [
                r'截面[：:\s]*(\d+(?:\.\d+)?)\s*mm[²2]',  # 优先："截面50mm2"
                r'截面[：:\s]*(\d+(?:\.\d+)?)',            # 其次："截面50"
                r'(\d+(?:\.\d+)?)\s*mm[²2]',              # 兜底：直接找"XXmm2"
                r'(\d+(?:\.\d+)?)\s*平方',
            ],
            "mm": [
                r'[dD][nN]\s*(\d+(?:\.\d+)?)',
                r'[dD][eE]\s*(\d+(?:\.\d+)?)',
                r'[φΦ]\s*(\d+(?:\.\d+)?)',
                r'管径\s*(\d+(?:\.\d+)?)',
                r'公称直径\s*(\d+(?:\.\d+)?)',
            ],
            "m2": [r'(\d+(?:\.\d+)?)\s*[mM][²2]'],
            "t": [r'(\d+(?:\.\d+)?)\s*[tT吨]'],
        }

        # 先尝试按参数单位匹配
        if param_unit and param_unit in unit_patterns:
            for pat in unit_patterns[param_unit]:
                m = re.search(pat, bill_text)
                if m:
                    return float(m.group(1))

        # 策略2：如果参数名称是"回路"、"火"等中文单位
        chinese_units = {
            "回路": [r'(\d+)\s*回路', r'(\d+)\s*路'],
            "火": [r'(\d+)\s*火'],
            "芯": [r'(\d+)\s*芯'],
        }
        if param_unit in chinese_units:
            for pat in chinese_units[param_unit]:
                m = re.search(pat, bill_text)
                if m:
                    return float(m.group(1))

        # 策略3：按参数名称匹配
        if param_name:
            # "容量" → 找 容量XXX 或 XXXkVA
            pat = rf'{param_name}\s*[:：]?\s*(\d+(?:\.\d+)?)'
            m = re.search(pat, bill_text)
            if m:
                return float(m.group(1))

        # 策略4：DE外径转DN公称直径（PPR管常见）
        if param_unit == "mm":
            de_match = re.search(r'[dD][eE]\s*(\d+)', bill_text)
            if de_match:
                de_val = int(de_match.group(1))
                # DE→DN近似换算表（常见PPR/PE管）
                de_to_dn = {
                    20: 15, 25: 20, 32: 25, 40: 32, 50: 40,
                    63: 50, 75: 65, 90: 80, 110: 100, 125: 100,
                    140: 125, 160: 150, 200: 200, 250: 250,
                    315: 300, 400: 400, 500: 500, 630: 600,
                }
                if de_val in de_to_dn:
                    return float(de_to_dn[de_val])
                # 没有精确映射，近似取0.8倍（外径→内径近似）
                return float(int(de_val * 0.8))

        return None

    def _find_correct_tier(self, value: float, tiers: list) -> float:
        """
        向上取档：找到 ≥ value 的最小档位

        参数:
            value: 清单参数值
            tiers: 档位列表（已排序）

        返回:
            正确的档位值，如果超出范围返回 None
        """
        for tier in sorted(tiers):
            if tier >= value:
                return tier
        return None  # 超出最大档

    def _find_quota_by_tier(self, family: dict, tier_value: float) -> str:
        """
        在家族中找到指定档位对应的定额编号

        参数:
            family: 家族信息字典
            tier_value: 目标档位值

        返回:
            定额编号，找不到返回 None
        """
        for quota in family.get("quotas", []):
            try:
                quota_val = float(quota.get("value", ""))
                if abs(quota_val - tier_value) < 0.01:
                    return quota.get("id")
            except (ValueError, TypeError):
                continue
        return None

    def _find_quota_name(self, family: dict, quota_id: str) -> str:
        """根据定额编号在家族中查找名称"""
        # 家族的quotas只存了id和value，名称需要从前缀+value拼接
        prefix = family.get("prefix", "")
        for quota in family.get("quotas", []):
            if quota.get("id") == quota_id:
                val = quota.get("value", "")
                return f"{prefix} {val}".strip()
        return None
