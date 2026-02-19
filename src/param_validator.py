"""
参数验证器
功能：
1. 对比清单描述中提取的参数和候选定额的参数
2. 过滤掉数值参数明显不匹配的候选（如DN不对、截面不对）
3. 对匹配程度打分，调整候选排序

为什么需要参数验证？
- 向量搜索和BM25可能召回语义相似但参数不同的定额
  比如搜"DN150"可能也返回"DN100"的定额（名称很相似）
- 结构化参数（管径、截面、容量等）必须精确匹配或向上取档
- 参数验证是精度提升的关键环节
"""

import math
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.text_parser import parser as text_parser


class ParamValidator:
    """候选定额的参数验证器"""

    def validate_candidates(self, query_text: str, candidates: list[dict],
                            supplement_query: str = None) -> list[dict]:
        """
        对候选定额进行参数验证和重排序

        参数:
            query_text: 清单项目名称+特征描述（完整原文）
            candidates: 混合搜索返回的候选定额列表
            supplement_query: 补充查询文本（如 search_query），从中提取参数填补 query_text 的空缺
                             典型场景：原文"BV4"提取不到截面，但 search_query"管内穿铜芯线 导线截面 4"可以

        返回:
            验证后的候选列表，每条增加 param_score 和 param_detail 字段
        """
        if not candidates:
            return []

        # 从清单文本中提取参数
        bill_params = text_parser.parse(query_text)

        # 如果有补充query，从中提取参数填补空缺
        # （search_query 经过 build_quota_query 规范化，参数提取更可靠）
        if supplement_query:
            supplement_params = text_parser.parse(supplement_query)
            for key, value in supplement_params.items():
                if key not in bill_params:
                    bill_params[key] = value

        # 没有可比较的清单参数时，仍然检查定额侧是否有档位参数
        # 有档位参数说明存在"不确定选对了哪个档"的风险，降低置信度
        if not bill_params:
            TIER_PARAMS = ["dn", "cable_section", "kva", "circuits", "ampere", "weight_t", "perimeter", "large_side", "elevator_stops"]
            for c in candidates:
                # 从定额名称提取参数
                quota_params = text_parser.parse(c.get("name", ""))
                # 从数据库字段补充
                db_params = self._get_db_params(c)
                merged = {**quota_params, **{k: v for k, v in db_params.items() if v is not None}}

                has_tier = any(p in merged for p in TIER_PARAMS)
                if has_tier:
                    c["param_score"] = 0.6  # 定额有档位但清单没指定，不确定
                    c["param_detail"] = "定额有档位参数但清单未指定"
                else:
                    c["param_score"] = 1.0  # 无参数可验证，默认通过
                    c["param_detail"] = "无参数可验证"
            return candidates

        # 逐个验证候选定额
        validated = []
        for candidate in candidates:
            # 从定额名称中提取参数
            quota_params = text_parser.parse(candidate.get("name", ""))

            # 同时使用数据库中已提取的结构化参数（更可靠）
            db_params = self._get_db_params(candidate)
            # 合并：数据库字段优先，文本提取作为补充
            merged_quota_params = {**quota_params, **{k: v for k, v in db_params.items() if v is not None}}

            # 执行参数匹配
            is_match, score, detail = self._check_params(bill_params, merged_quota_params)

            # 负向关键词检查：清单没提到"防爆"/"铜制"，定额却包含 → 降分
            neg_penalty, neg_detail = self._check_negative_keywords(
                query_text, candidate.get("name", ""))
            if neg_penalty > 0:
                score = max(0.0, score - neg_penalty)
                detail += f"; {neg_detail}"
                if neg_penalty >= 0.3:
                    is_match = False  # 重罚时直接标记不匹配

            candidate["param_score"] = score
            candidate["param_detail"] = detail
            candidate["param_match"] = is_match

            validated.append(candidate)

        # 按参数匹配分数排序（不匹配的排到后面，但不删除——让大模型做最终判断）
        # 平局排序：优先用 rerank_score（Reranker的语义精排比hybrid_score更准确）
        # 没有Reranker时回退到 hybrid_score
        validated.sort(
            key=lambda x: (
                x["param_match"],     # 匹配的排前面
                x["param_score"],     # 分数高的排前面
                x.get("rerank_score", x.get("hybrid_score", 0)),  # Reranker分优先，否则用搜索分
            ),
            reverse=True,
        )

        return validated

    def _get_db_params(self, candidate: dict) -> dict:
        """从候选定额的数据库字段中提取参数"""
        params = {}
        if candidate.get("dn") is not None:
            params["dn"] = candidate["dn"]
        if candidate.get("cable_section") is not None:
            params["cable_section"] = candidate["cable_section"]
        if candidate.get("kva") is not None:
            params["kva"] = candidate["kva"]
        if candidate.get("kv") is not None:
            params["kv"] = candidate["kv"]
        if candidate.get("ampere") is not None:
            params["ampere"] = candidate["ampere"]
        if candidate.get("weight_t") is not None:
            params["weight_t"] = candidate["weight_t"]
        if candidate.get("material"):
            params["material"] = candidate["material"]
        if candidate.get("connection"):
            params["connection"] = candidate["connection"]
        return params

    # 材质族谱：同族内的材质视为"近似匹配"，跨族则为"不匹配"
    MATERIAL_FAMILIES = {
        "钢塑族": ["钢塑", "钢塑复合管", "衬塑钢管", "涂塑钢管", "衬塑", "涂塑",
                   "涂塑碳钢管", "热浸塑钢管",
                   "涂覆碳钢管", "涂覆钢管", "PSP钢塑复合管"],  # 涂覆=涂塑, PSP=钢塑复合管品牌
        "铝塑族": ["铝塑", "铝塑复合管", "塑铝稳态管", "铝合金衬塑管"],
        "镀锌钢族": ["镀锌钢管", "镀锌"],
        "焊接钢族": ["焊接钢管", "碳钢", "碳钢管"],
        "不锈钢族": ["不锈钢管", "薄壁不锈钢管", "不锈钢"],
        "铸铁族": ["铸铁管", "球墨铸铁管", "柔性铸铁管", "铸铁"],
        "PPR族": ["PPR管", "PP管", "PPR复合管", "PPR冷水管", "PPR热水管"],
        "PE族": ["PE管", "HDPE管"],
        "PVC族": ["PVC管", "UPVC管", "CPVC管"],
        "铜族": ["铜", "铜管", "铜制", "紫铜管", "黄铜管"],  # 加入"铜"和具体铜管类型
        "铜芯族": ["铜芯", "铜芯电缆", "铜导线"],
        "铝芯族": ["铝芯", "铝芯电缆", "铝导线", "高压铝芯电缆"],
        "碳钢板族": ["碳钢", "薄钢板", "钢板", "钢板制", "镀锌钢板"],  # 碳钢板材系列，含镀锌钢板
        "玻璃钢族": ["玻璃钢", "玻璃钢管", "FRP管", "FRP"],  # 玻璃钢系列
    }

    # 泛称→具体材质映射：泛称是一大类的统称，和该类下的所有具体材质都兼容
    # 例如清单写"塑料管"，定额可能是"PPR管"或"PE管"——都应视为兼容
    GENERIC_MATERIALS = {
        "塑料管": ["PPR管", "PE管", "PVC管", "UPVC管", "HDPE管", "PP管",
                   "ABS管", "CPVC管", "PPR复合管", "PPR冷水管", "PPR热水管"],
        "复合管": ["钢塑复合管", "铝塑复合管", "PPR复合管", "衬塑钢管", "涂塑钢管",
                   "钢丝网骨架管", "孔网钢带管", "塑铝稳态管", "铝合金衬塑管"],
        "钢管": ["镀锌钢管", "焊接钢管", "无缝钢管", "不锈钢管", "薄壁不锈钢管"],
        "钢板": ["薄钢板", "镀锌钢板", "不锈钢板", "碳钢板"],  # 钢板类泛称
        "金属软管": ["不锈钢软管", "碳钢软管", "不锈钢"],  # 金属软管是泛称，包含不锈钢等
        # 涂塑/涂覆管道在实际造价中借用镀锌钢管定额换主材（行业通用做法）
        "涂塑钢管": ["镀锌钢管", "焊接钢管"],
        "涂塑碳钢管": ["镀锌钢管", "焊接钢管"],
        "涂覆碳钢管": ["镀锌钢管", "焊接钢管"],
        "涂覆钢管": ["镀锌钢管", "焊接钢管"],
        "涂塑": ["镀锌钢管", "镀锌"],
        "涂覆": ["镀锌钢管", "镀锌"],
    }

    def _materials_compatible(self, mat1: str, mat2: str) -> bool:
        """
        判断两种材质是否兼容（近似匹配）

        兼容规则（按优先级）：
        1. 完全相同 → 在外层已处理，这里不会走到
        2. 同族材质（如"钢塑"和"钢塑复合管"都在钢塑族）→ 兼容
        3. 泛称兼容（如"塑料管"和"PPR管"，"复合管"和"钢塑复合管"）→ 兼容
        4. 子串包含（如"复合管"包含在"钢塑复合管"中）→ 兼容
        5. 以上都不满足 → 不兼容
        """
        # 规则2：同族检查
        for family_members in self.MATERIAL_FAMILIES.values():
            if mat1 in family_members and mat2 in family_members:
                return True

        # 规则3：泛称兼容（清单常用泛称，定额用具体名称）
        for generic, specifics in self.GENERIC_MATERIALS.items():
            if mat1 == generic and mat2 in specifics:
                return True
            if mat2 == generic and mat1 in specifics:
                return True

        # 规则4：子串包含（如"复合管"⊂"钢塑复合管"等）
        if mat1 in mat2 or mat2 in mat1:
            return True

        return False

    @staticmethod
    def _check_negative_keywords(bill_text: str, quota_name: str) -> tuple[float, str]:
        """
        负向关键词检查：清单没提到某关键词，但定额名称包含 → 降分

        典型场景：
          - 清单"普通插座" → 定额"防爆插座" → 不应该匹配
          - 清单"接地母线" → 定额"铜接地母线" → 没说铜不该套铜的

        返回: (惩罚分数, 说明文本)
        """
        bill_lower = bill_text.lower()
        quota_lower = quota_name.lower()

        # 排除性关键词列表：(关键词, 惩罚分, 豁免词, 同义词)
        # 豁免词：如果清单包含豁免词，则不惩罚
        # 同义词：清单包含同义词也视为已提及，不惩罚（如"保温"≈"绝热"）
        NEGATIVE_RULES = [
            # 清单没写"防爆"，定额是防爆 → 重罚
            {"keyword": "防爆", "penalty": 0.3, "exempt": [], "alt_keywords": []},
            # 清单没写"铜"，定额含"铜制/铜接地/铜母线" → 中罚
            # 豁免"铜芯"（BV线默认铜芯）
            {"keyword": "铜", "penalty": 0.2, "exempt": ["铜芯"], "alt_keywords": []},
            # 清单没说保温/绝热，定额是绝热类 → 重罚（通风管道≠通风管道绝热）
            {"keyword": "绝热", "penalty": 0.3, "exempt": [], "alt_keywords": ["保温"]},
            {"keyword": "保温", "penalty": 0.3, "exempt": [], "alt_keywords": ["绝热"]},
            # 清单没说人防，定额是人防类 → 重罚（普通套管≠人防密闭套管）
            {"keyword": "人防", "penalty": 0.3, "exempt": [], "alt_keywords": ["密闭"]},
        ]

        max_penalty = 0.0
        details = []

        for rule in NEGATIVE_RULES:
            kw = rule["keyword"]
            # 定额名称包含该关键词
            if kw not in quota_lower:
                continue
            # 清单已经提到该关键词 → 不惩罚
            if kw in bill_lower:
                continue
            # 清单包含同义词也不惩罚（如清单说"保温"则"绝热"定额不罚）
            alt_keywords = rule.get("alt_keywords", [])
            if alt_keywords and any(alt in bill_lower for alt in alt_keywords):
                continue
            # 检查豁免词：如果定额中该关键词只出现在豁免词中，不惩罚
            if rule["exempt"]:
                # 把豁免词从定额名称中去掉后，还有没有该关键词
                temp = quota_lower
                for ex in rule["exempt"]:
                    temp = temp.replace(ex, "")
                if kw not in temp:
                    continue  # 只在豁免词中出现，不惩罚

            penalty = rule["penalty"]
            if penalty > max_penalty:
                max_penalty = penalty
                details = [f"清单无'{kw}'但定额含'{kw}' 罚分-{penalty}"]

        return max_penalty, "; ".join(details)

    @staticmethod
    def _connections_compatible_pv(bill_conn: str, quota_conn: str) -> bool:
        """
        判断两个连接方式是否兼容（用于参数验证层）

        兼容规则：
        1. 子串匹配 → 兼容（如"卡压"包含在"卡压、环压连接"中）
        2. 都含"法兰" → 兼容（焊接法兰/螺纹法兰都是法兰子类型）
        3. 行业同义词 → 兼容（"承插"≈"粘接"、"双热熔"≈"热熔"）
        4. 其他 → 不兼容
        """
        # 子串匹配：如"卡压"在"卡压、环压连接"中、"热熔"在"双热熔"中
        if bill_conn in quota_conn or quota_conn in bill_conn:
            return True
        # 法兰系列互相兼容
        if "法兰" in bill_conn and "法兰" in quota_conn:
            return True
        # 行业同义词（每组内的词互相兼容）
        conn_synonyms = [
            {"承插", "粘接"},      # PVC排水管：承插连接≈粘接
            {"双热熔", "热熔"},    # 双热熔是热熔的变体（PSP钢塑管用双热熔）
        ]
        for syn_group in conn_synonyms:
            bill_in = any(s in bill_conn for s in syn_group)
            quota_in = any(s in quota_conn for s in syn_group)
            if bill_in and quota_in:
                return True
        return False

    @staticmethod
    def _tier_up_score(bill_value: float, quota_value: float) -> float:
        """
        向上取档的评分函数

        定额参数标注为"XX以内"（阶梯制），清单参数25需要匹配到35以内的档位。
        这是正确且唯一的匹配方式，紧邻的下一档应接近满分。

        评分逻辑：根据 quota_value / bill_value 的比值（偏差比例）计算
          - 比值 1.0（精确匹配）→ 1.0分
          - 比值 1.4（25→35）  → ~0.95分（紧邻下一档，几乎完美）
          - 比值 2.0（25→50）  → ~0.90分（跳一档，还行）
          - 比值 4.0（25→100） → ~0.80分（跳了几档，偏远）
          - 比值 10+（25→300） → ~0.65分（差很远）
          - 最低不低于 0.55

        为什么用对数：因为定额档位通常是等比数列（如截面 2.5,4,6,10,16,25,35,50,70,95...）
        """
        if bill_value <= 0:
            return 0.6  # 异常值，保守评分

        ratio = quota_value / bill_value
        if ratio <= 1.0:
            return 1.0  # 不应发生（已在外层判断），但保险起见

        # score = 1.0 - 0.1 * log2(ratio)，限制在 [0.55, 1.0]
        score = 1.0 - 0.1 * math.log2(ratio)
        return max(0.55, min(1.0, score))

    def _check_params(self, bill_params: dict, quota_params: dict) -> tuple[bool, float, str]:
        """
        对比清单参数和定额参数

        参数:
            bill_params: 清单提取的参数
            quota_params: 定额的参数

        返回:
            (是否匹配, 分数0-1, 详情说明)
        """
        if not quota_params:
            return True, 0.8, "定额无参数可验证"

        details = []  # 记录每项检查结果
        score_sum = 0.0
        check_count = 0
        has_hard_fail = False  # 是否有硬性不匹配

        # === 1. DN管径（硬性参数：必须精确匹配或向上取档） ===
        if "dn" in bill_params:
            check_count += 1
            if "dn" in quota_params:
                bill_dn = bill_params["dn"]
                quota_dn = quota_params["dn"]
                if bill_dn == quota_dn:
                    score_sum += 1.0
                    details.append(f"DN{bill_dn}=DN{quota_dn} 精确匹配")
                elif bill_dn < quota_dn:
                    # 向上取档：定额用"以内"标注，取紧邻的下一档是正确行为
                    # 根据偏差比例评分：越接近满分越高
                    tier_score = self._tier_up_score(bill_dn, quota_dn)
                    score_sum += tier_score
                    details.append(f"DN{bill_dn}→DN{quota_dn} 向上取档")
                else:
                    # 清单DN大于定额DN，不可能匹配
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"DN{bill_dn}≠DN{quota_dn} 不匹配(清单>定额)")
            else:
                score_sum += 0.5
                details.append(f"定额无DN参数")

        # === 2. 电缆截面（硬性参数） ===
        if "cable_section" in bill_params:
            check_count += 1
            if "cable_section" in quota_params:
                bill_sec = bill_params["cable_section"]
                quota_sec = quota_params["cable_section"]
                if bill_sec == quota_sec:
                    score_sum += 1.0
                    details.append(f"截面{bill_sec}={quota_sec} 精确匹配")
                elif bill_sec < quota_sec:
                    tier_score = self._tier_up_score(bill_sec, quota_sec)
                    score_sum += tier_score
                    details.append(f"截面{bill_sec}→{quota_sec} 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"截面{bill_sec}≠{quota_sec} 不匹配(清单>定额)")
            else:
                score_sum += 0.5
                details.append(f"定额无截面参数")

        # === 3. 容量kVA（硬性参数） ===
        if "kva" in bill_params:
            check_count += 1
            if "kva" in quota_params:
                bill_kva = bill_params["kva"]
                quota_kva = quota_params["kva"]
                if bill_kva == quota_kva:
                    score_sum += 1.0
                    details.append(f"容量{bill_kva}kVA={quota_kva}kVA 精确匹配")
                elif bill_kva < quota_kva:
                    tier_score = self._tier_up_score(bill_kva, quota_kva)
                    score_sum += tier_score
                    details.append(f"容量{bill_kva}→{quota_kva}kVA 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"容量{bill_kva}kVA≠{quota_kva}kVA 不匹配")
            else:
                score_sum += 0.5
                details.append(f"定额无容量参数")

        # === 4. 回路数（硬性参数） ===
        if "circuits" in bill_params:
            check_count += 1
            if "circuits" in quota_params:
                bill_cir = bill_params["circuits"]
                quota_cir = quota_params["circuits"]
                if bill_cir == quota_cir:
                    score_sum += 1.0
                    details.append(f"回路{bill_cir}={quota_cir} 精确匹配")
                elif bill_cir < quota_cir:
                    tier_score = self._tier_up_score(bill_cir, quota_cir)
                    score_sum += tier_score
                    details.append(f"回路{bill_cir}→{quota_cir} 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"回路{bill_cir}>{quota_cir} 不匹配(清单>定额)")
            else:
                score_sum += 0.5
                details.append("定额无回路参数")

        # === 5. 电流A（硬性参数） ===
        if "ampere" in bill_params:
            check_count += 1
            if "ampere" in quota_params:
                bill_amp = bill_params["ampere"]
                quota_amp = quota_params["ampere"]
                if bill_amp == quota_amp:
                    score_sum += 1.0
                    details.append(f"电流{bill_amp}A={quota_amp}A 精确匹配")
                elif bill_amp < quota_amp:
                    tier_score = self._tier_up_score(bill_amp, quota_amp)
                    score_sum += tier_score
                    details.append(f"电流{bill_amp}A→{quota_amp}A 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"电流{bill_amp}A>{quota_amp}A 不匹配(清单>定额)")
            else:
                score_sum += 0.5
                details.append("定额无电流参数")

        # === 6. 电压等级kV（软性参数） ===
        if "kv" in bill_params and "kv" in quota_params:
            check_count += 1
            if bill_params["kv"] == quota_params["kv"]:
                score_sum += 1.0
                details.append(f"电压{bill_params['kv']}kV匹配")
            else:
                score_sum += 0.3
                details.append(f"电压{bill_params['kv']}kV≠{quota_params['kv']}kV")

        # === 7. 材质（硬性参数：钢塑≠铝塑，材质错了直接降权） ===
        if "material" in bill_params and "material" in quota_params:
            check_count += 1
            bill_mat = bill_params["material"]
            quota_mat = quota_params["material"]
            if bill_mat == quota_mat:
                score_sum += 1.0
                details.append(f"材质'{bill_mat}'匹配")
            elif self._materials_compatible(bill_mat, quota_mat):
                # 同族材质（如"钢塑"和"钢塑复合管"），给部分分
                score_sum += 0.7
                details.append(f"材质'{bill_mat}'≈'{quota_mat}' 近似匹配")
            else:
                # 不同材质（如"钢塑"和"铝塑"），硬性不匹配
                has_hard_fail = True
                score_sum += 0.0
                details.append(f"材质'{bill_mat}'≠'{quota_mat}' 不匹配")

        # === 8. 连接方式（硬性参数：螺纹≠沟槽、热熔≠粘接 必须匹配） ===
        if "connection" in bill_params and "connection" in quota_params:
            check_count += 1
            bill_c = bill_params["connection"]
            quota_c = quota_params["connection"]
            if bill_c == quota_c:
                score_sum += 1.0
                details.append(f"连接方式'{bill_c}'匹配")
            elif self._connections_compatible_pv(bill_c, quota_c):
                # 兼容的连接方式（子串包含、法兰系、行业同义词）
                score_sum += 0.8
                details.append(f"连接方式'{bill_c}'≈'{quota_c}' 兼容")
            else:
                # 连接方式不同（如螺纹≠沟槽）→ 重度降权但非硬性失败
                # 理由：定额库可能没有清单指定的连接方式（如PSP双热熔→只有螺纹），
                #       此时最接近的定额（材质+DN都对）仍是最佳选择，不应完全排除
                score_sum += 0.2
                details.append(f"连接方式'{bill_c}'≠'{quota_c}' 不匹配(降权)")
        # === 9. 风管形状（硬性参数：矩形≠圆形，形状错了定额完全不同） ===
        if "shape" in bill_params:
            quota_shape = quota_params.get("shape", "")
            if quota_shape:
                check_count += 1
                if bill_params["shape"] == quota_shape:
                    score_sum += 1.0
                    details.append(f"形状'{bill_params['shape']}'匹配")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"形状'{bill_params['shape']}'≠'{quota_shape}' 不匹配")

        # === 10. 周长（硬性参数：风口/阀门/散流器/消声器按周长取档） ===
        if "perimeter" in bill_params and "perimeter" in quota_params:
            check_count += 1
            bill_p = bill_params["perimeter"]
            quota_p = quota_params["perimeter"]
            if bill_p == quota_p:
                score_sum += 1.0
                details.append(f"周长{bill_p}={quota_p} 精确匹配")
            elif bill_p <= quota_p:
                tier_score = self._tier_up_score(bill_p, quota_p)
                score_sum += tier_score
                details.append(f"周长{bill_p}→{quota_p} 向上取档")
            else:
                has_hard_fail = True
                score_sum += 0.0
                details.append(f"周长{bill_p}>{quota_p} 不匹配(清单>定额)")

        # === 11. 大边长（硬性参数：弯头导流叶片等按大边长取档） ===
        if "large_side" in bill_params and "large_side" in quota_params:
            check_count += 1
            bill_ls = bill_params["large_side"]
            quota_ls = quota_params["large_side"]
            if bill_ls == quota_ls:
                score_sum += 1.0
                details.append(f"大边长{bill_ls}={quota_ls} 精确匹配")
            elif bill_ls <= quota_ls:
                tier_score = self._tier_up_score(bill_ls, quota_ls)
                score_sum += tier_score
                details.append(f"大边长{bill_ls}→{quota_ls} 向上取档")
            else:
                has_hard_fail = True
                score_sum += 0.0
                details.append(f"大边长{bill_ls}>{quota_ls} 不匹配(清单>定额)")

        # === 12. 重量（软性参数） ===
        if "weight_t" in bill_params and "weight_t" in quota_params:
            check_count += 1
            bill_w = bill_params["weight_t"]
            quota_w = quota_params["weight_t"]
            if bill_w == quota_w:
                score_sum += 1.0
                details.append(f"重量{bill_w}t匹配")
            elif bill_w <= quota_w:
                tier_score = self._tier_up_score(bill_w, quota_w)
                score_sum += tier_score
                details.append(f"重量{bill_w}→{quota_w}t 向上取档")
            else:
                score_sum += 0.3
                details.append(f"重量{bill_w}t≠{quota_w}t")

        # === 13. 电梯站数（硬性参数） ===
        if "elevator_stops" in bill_params and "elevator_stops" in quota_params:
            check_count += 1
            bill_stops = bill_params["elevator_stops"]
            quota_stops = quota_params["elevator_stops"]
            if bill_stops == quota_stops:
                score_sum += 1.0
                details.append(f"站数{bill_stops}={quota_stops} 精确匹配")
            elif bill_stops < quota_stops:
                tier_score = self._tier_up_score(bill_stops, quota_stops)
                score_sum += tier_score
                details.append(f"站数{bill_stops}→{quota_stops} 向上取档")
            else:
                has_hard_fail = True
                score_sum += 0.0
                details.append(f"站数{bill_stops}>{quota_stops} 不匹配(清单>定额)")

        # 计算最终分数
        if check_count == 0:
            # 反向检查：定额有档位参数但清单没提供 → 无法确认档位
            # 例如配电箱定额写"48回路"但清单只写了尺寸没写回路数
            TIER_PARAMS = ["dn", "cable_section", "kva", "circuits", "ampere", "weight_t", "perimeter", "large_side", "elevator_stops"]
            quota_has_tier = any(p in quota_params for p in TIER_PARAMS)
            if quota_has_tier:
                return True, 0.6, "定额有档位参数但清单未指定"
            return True, 0.8, "无共同参数可对比"

        final_score = score_sum / check_count
        is_match = not has_hard_fail and final_score >= 0.5
        detail_str = "; ".join(details)

        return is_match, final_score, detail_str


# 模块级单例
validator = ParamValidator()


# ================================================================
# 命令行入口：测试参数验证
# ================================================================

if __name__ == "__main__":
    # 测试参数验证
    v = ParamValidator()

    # 模拟清单+候选
    query = "镀锌钢管DN150沟槽连接管道安装"
    candidates = [
        {"id": 1, "name": "放水管安装 DN150", "quota_id": "C3-3-71", "unit": "个",
         "dn": 150, "material": None, "connection": None,
         "cable_section": None, "kva": None, "kv": None, "ampere": None, "weight_t": None},
        {"id": 2, "name": "放水管安装 DN50", "quota_id": "C3-3-68", "unit": "个",
         "dn": 50, "material": None, "connection": None,
         "cable_section": None, "kva": None, "kv": None, "ampere": None, "weight_t": None},
        {"id": 3, "name": "罐顶接合管 DN250", "quota_id": "C3-3-77", "unit": "个",
         "dn": 250, "material": None, "connection": None,
         "cable_section": None, "kva": None, "kv": None, "ampere": None, "weight_t": None},
    ]

    results = v.validate_candidates(query, candidates)
    print(f"清单: {query}")
    bill_params = text_parser.parse(query)
    print(f"提取参数: {bill_params}")
    print()
    for r in results:
        print(f"  [{r['param_score']:.2f}] {r['param_match']} | {r['quota_id']} {r['name']} | {r['param_detail']}")
