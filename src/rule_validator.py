"""
定额规则校验器（方案B：规则辅助搜索）

功能：
1. 规则预匹配（搜索前）：用关键词匹配家族 → 按参数选档位 → 直接出结果
2. 规则校验（搜索后）：校验档位是否正确，错了自动纠正

设计原则：
- 规则预匹配能命中的直接返回，省掉搜索和大模型
- 命中不了的不干预，交给搜索兜底
- 宁可漏匹配，也不错匹配（阈值宁高勿低）

代码组织（拆分为三个文件）：
- rule_validator.py（本文件）：核心类、初始化、参数驱动匹配、参数提取、档位逻辑
- rule_family.py：常量、分词、文本型匹配、关键词匹配、属性兼容性检查
- rule_post_validator.py：后置校验（validate_result/validate_results）
"""

import json
import re
from pathlib import Path
from loguru import logger

from src.text_parser import parser as text_parser
import config

# 常量和分词函数从 rule_family 导入（避免重复定义）
from src.rule_family import tokenize, MATERIAL_DEFAULT_CONNECTION


class RuleValidator:
    """定额规则校验器"""

    def __init__(self, rules_path: str = None, province: str = None):
        """
        参数:
            rules_path: 规则JSON文件路径，默认自动查找
            province: 省份名称，用于定位规则文件目录
        """
        self.rules = None
        self.family_index = {}
        self.all_families = []  # 所有家族（带预计算的关键词）

        if rules_path is None:
            # 自动查找并加载所有专业的规则文件
            project_root = Path(__file__).parent.parent
            rules_dir = project_root / "data" / "quota_rules"

            # 优先从省份子目录加载（新结构）
            province = province or config.get_current_province()
            province_rules_dir = rules_dir / province
            if province_rules_dir.exists():
                json_files = sorted(province_rules_dir.glob("*定额规则.json"))
            else:
                # 兼容旧结构：从根目录按前缀匹配
                json_files = sorted(rules_dir.glob(f"{province}_*定额规则.json"))
            if not json_files:
                logger.warning("未找到定额规则文件，规则校验功能禁用")
                return

            # 合并所有规则文件的chapters
            merged_chapters = {}
            for jf in json_files:
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict) and isinstance(loaded.get("chapters"), dict):
                        merged_chapters.update(loaded["chapters"])
                        logger.info(f"已加载规则文件: {jf.name} ({len(loaded['chapters'])}章节)")
                except Exception as e:
                    logger.warning(f"跳过损坏的规则文件: {jf.name} ({e})")

            if not merged_chapters:
                logger.warning("所有规则文件均无有效章节，规则校验功能禁用")
                return

            self.rules = {"chapters": merged_chapters}
            self._build_index()

            family_count = len(set(id(v) for v in self.family_index.values()))
            if self.family_index:
                logger.info(f"规则校验器已加载: {len(self.family_index)} 条定额, "
                            f"{family_count} 个家族 (来自{len(json_files)}个规则文件)")
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

        # ===== 策略A：参数驱动匹配（数值型档位，通用、不依赖修饰词如"明装"） =====
        # 思路：如果能从清单中提取出参数值，且参数类型匹配某个家族，
        #       那只需要核心名词对上就行，不要求"明装/落地/嵌入"等修饰词
        param_result = self._match_by_param_driven(
            bill_text, bill_keywords, bill_params, item, books=books)
        if param_result:
            return param_result

        # ===== 策略A2：文本型家族匹配（电梯/起重机等 value_type="text" 的家族） =====
        # 通用逻辑：自动分析家族数据推断参数类型，不需要为每种设备写专用代码
        text_result = self._match_text_family(
            bill_text, bill_keywords, bill_params, item, books=books)
        if text_result:
            return text_result

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
            forward_hits, has_specific_keyword = self._compute_keyword_hits(
                family_kws, bill_keywords)

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
            if not self._has_base_name_match(family.get("name", ""), bill_keywords):
                continue  # 家族核心名称无匹配 → 跳过（参数描述关键词不算）

            reverse_hits = self._count_reverse_hits(family_kws, bill_keywords)

            # 第4步：综合评分
            # 参数匹配本身就是很强的信号（回路数对上了回路家族），给高基础分
            # 关键词匹配作为辅助，权重较低
            kw_coverage = forward_hits / len(family_kws) if family_kws else 0
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

    # _match_text_family — 已拆分到 rule_family.py
    # _match_by_keyword_driven — 已拆分到 rule_family.py
    # _compute_keyword_hits / _count_reverse_hits / _has_base_name_match — 已拆分到 rule_family.py
    # _compute_medium_bonus / _family_compatible / _connections_compatible / _materials_compatible — 已拆分到 rule_family.py
    # validate_result / validate_results — 已拆分到 rule_post_validator.py

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
        reason = (f"规则直接匹配: 「{family.get('name', '')}」"
                  f"参数{param_value}→档位{correct_tier}")
        explanation = (f"规则匹配(得分{score:.2f}): "
                       f"「{family.get('name', '')}」"
                       f"参数{param_value}→{correct_tier}档")

        result = self._build_rule_match_result(
            item=item,
            quota_id=quota_id,
            quota_name=quota_name or f"{family.get('prefix', '')} {correct_tier}",
            unit=family.get("unit", ""),
            reason=reason,
            confidence=confidence,
            family_name=family.get("name", ""),
            score=score,
            explanation=explanation,
        )

        logger.debug(f"规则预匹配成功: '{bill_text[:30]}' → {quota_id} "
                    f"(家族={family.get('name', '')}, 得分={score:.2f})")

        return result

    @staticmethod
    def _first_match_float(text: str, patterns: list[str]) -> float:
        """按顺序匹配正则并返回首个数值。"""
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return float(m.group(1))
        return None

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
            value = self._first_match_float(bill_text, unit_patterns[param_unit])
            if value is not None:
                return value

        # 策略2：如果参数名称是"回路"、"火"等中文单位
        chinese_units = {
            "回路": [r'(\d+)\s*回路', r'(\d+)\s*路'],
            "火": [r'(\d+)\s*火'],
            "芯": [r'(\d+)\s*芯'],
        }
        if param_unit in chinese_units:
            value = self._first_match_float(bill_text, chinese_units[param_unit])
            if value is not None:
                return value

        # 策略3：按参数名称匹配
        if param_name:
            # "容量" → 找 容量XXX 或 XXXkVA
            # 用re.escape转义参数名中的特殊字符（如括号）
            safe_name = re.escape(param_name)
            value = self._first_match_float(
                bill_text, [rf'{safe_name}\s*[:：]?\s*(\d+(?:\.\d+)?)'])
            if value is not None:
                return value

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


# ================================================================
# 方法重绑定：把拆分出去的函数挂回 RuleValidator 类
# 调用方仍然用 rv.match_text_family(...) 等，无需感知拆分
# ================================================================
from src import rule_family as _rule_family
from src import rule_post_validator as _rule_pv

# 家族匹配相关方法（来自 rule_family.py）
RuleValidator._match_text_family = _rule_family._match_text_family
RuleValidator._parse_text_values = _rule_family._parse_text_values
RuleValidator._extract_text_param = _rule_family._extract_text_param
RuleValidator._collect_param_hints = _rule_family._collect_param_hints
RuleValidator._find_text_tier = _rule_family._find_text_tier
RuleValidator._extract_tier_display = _rule_family._extract_tier_display
RuleValidator._build_rule_match_result = _rule_family._build_rule_match_result
RuleValidator._build_text_result = _rule_family._build_text_result
RuleValidator._match_by_keyword_driven = _rule_family._match_by_keyword_driven
RuleValidator._compute_keyword_hits = _rule_family._compute_keyword_hits
RuleValidator._count_reverse_hits = _rule_family._count_reverse_hits
RuleValidator._has_common_prefix = _rule_family._has_common_prefix
RuleValidator._has_base_name_match = _rule_family._has_base_name_match
RuleValidator._compute_medium_bonus = _rule_family._compute_medium_bonus
RuleValidator._family_compatible = _rule_family._family_compatible
RuleValidator._connections_compatible = _rule_family._connections_compatible
RuleValidator._materials_compatible = _rule_family._materials_compatible

# 后置校验相关方法（来自 rule_post_validator.py）
RuleValidator.validate_result = _rule_pv.validate_result
RuleValidator.validate_results = _rule_pv.validate_results
RuleValidator._validate_non_tier_family = _rule_pv._validate_non_tier_family
RuleValidator._validate_text_family_result = _rule_pv._validate_text_family_result
RuleValidator._is_experience_source = _rule_pv._is_experience_source
RuleValidator._compose_bill_text = _rule_pv._compose_bill_text
RuleValidator._tally_validation_flags = _rule_pv._tally_validation_flags
RuleValidator._family_note = _rule_pv._family_note
RuleValidator._mark_rule_validation = _rule_pv._mark_rule_validation
RuleValidator._bump_confidence = _rule_pv._bump_confidence
RuleValidator._set_main_quota = _rule_pv._set_main_quota
