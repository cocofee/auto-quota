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
import re
from difflib import SequenceMatcher
from pathlib import Path

import jieba
import numpy as np
from loguru import logger

import config
from src.text_parser import parser as text_parser
from src.compat_primitives import (
    MATERIAL_FAMILIES,
    GENERIC_MATERIALS,
    materials_compatible as _compat_materials_compatible,
    connections_compatible as _compat_connections_compatible,
)


class ParamValidator:
    """候选定额的参数验证器"""
    TIER_PARAMS = [
        "dn", "cable_section", "kva", "kw", "circuits", "ampere",
        "weight_t", "perimeter", "large_side", "elevator_stops",
        "ground_bar_width",  # 接地扁钢宽度（如40×4中的40mm）
        "half_perimeter",  # 配电箱半周长（悬挂/嵌入式按半周长取档）
        "switch_gangs",  # 开关联数（单联/双联/三联/四联）
    ]

    # LTR模型特征列名（必须和训练时一致）
    # v1: 16维原始特征
    # v2: 21维（+5个参数距离特征，解决57%的"选错档位"问题）
    # v3: 23维（+book_match, token_overlap）
    _LTR_FEATURES = [
        "bm25_score", "vector_score", "hybrid_score", "rerank_score",
        "param_score", "param_match",
        "param_tier_0", "param_tier_1", "param_tier_2",
        "name_bonus", "candidates_count",
        "bm25_rank_score", "vector_rank_score",
        "name_edit_dist", "score_gap_to_top1", "dual_recall",
        # v2新增：参数距离特征（让模型学会"DN精确匹配远比语义相似更重要"）
        "param_main_exact",       # 17. 主参数(DN/截面等)是否精确匹配(0/1)
        "param_main_rel_dist",    # 18. 主参数相对距离(0=精确, 1=最远)
        "param_main_direction",   # 19. 向上取(+1)/向下取(-1)/精确(0)
        "param_material_match",   # 20. 材质匹配度(1.0精确/0.7兼容/0.0冲突/-1无信息)
        "param_n_checks",         # 21. 参数检查项数(越多越可信)
        # v3新增：册号匹配+词级重叠（Claude+Codex联合确认）
        "book_match",             # 22. 候选册号vs清单分类目标册号匹配度(1/0.5/0)
        "token_overlap",          # 23. 同义词归一化后词级Jaccard重叠度
    ]

    # LTR模型（类级别单例，所有实例共享）
    _ltr_model = None
    _ltr_model_loaded = False

    @classmethod
    def _load_ltr_model(cls):
        """加载LTR排序模型（只加载一次）"""
        if cls._ltr_model_loaded:
            return
        cls._ltr_model_loaded = True
        model_path = Path(__file__).parent.parent / "data" / "ltr_model.txt"
        if model_path.exists():
            try:
                import lightgbm as lgb
                cls._ltr_model = lgb.Booster(model_file=str(model_path))
                logger.info(f"LTR排序模型已加载: {model_path}")
            except Exception as e:
                logger.warning(f"LTR模型加载失败，回退到手工公式: {e}")
                cls._ltr_model = None
        else:
            logger.debug("LTR模型文件不存在，使用手工排序公式")

    def validate_candidates(self, query_text: str, candidates: list[dict],
                            supplement_query: str = None,
                            bill_params: dict = None,
                            search_books: list[str] = None) -> list[dict]:
        """
        对候选定额进行参数验证和重排序

        参数:
            query_text: 清单项目名称+特征描述（完整原文）
            candidates: 混合搜索返回的候选定额列表
            supplement_query: 补充查询文本（如 search_query），从中提取参数填补 query_text 的空缺
                             典型场景：原文"BV4"提取不到截面，但 search_query"管内穿铜芯线 导线截面 4"可以
            bill_params: 清单已清洗的参数字典（来自bill_cleaner）。
                         如果提供，优先使用；否则从文本重新提取。
            search_books: 清单分类的搜索册号列表（用于v3 book_match特征）

        返回:
            验证后的候选列表，每条增加 param_score 和 param_detail 字段
        """
        if not candidates:
            return []

        # 优先使用清单清洗阶段已清洗的参数（如卫生器具已剔除DN）
        if bill_params is not None:
            bill_params = dict(bill_params)  # 复制一份，避免修改原dict
        else:
            # 从清单文本中提取参数（兼容未经bill_cleaner的调用）
            bill_params = text_parser.parse(query_text)

        # 如果有补充query，从中提取参数填补空缺
        # （search_query 经过 build_quota_query 规范化，参数提取更可靠）
        if supplement_query:
            supplement_params = text_parser.parse(supplement_query)
            for key, value in supplement_params.items():
                if key not in bill_params:
                    bill_params[key] = value

        # 电气配管管径(conduit_dn)映射为dn参与参数验证
        # 场景：清单"配管SC20"提取了conduit_dn=20，定额"公称直径(mm以内) 20"有dn分档
        if "conduit_dn" in bill_params and "dn" not in bill_params:
            bill_params["dn"] = bill_params.pop("conduit_dn")

        # 没有可比较的清单参数时，仍然检查定额侧是否有档位参数
        # 有档位参数说明存在"不确定选对了哪个档"的风险，降低置信度
        if not bill_params:
            for c in candidates:
                # 从定额名称提取参数
                quota_params = text_parser.parse(c.get("name", ""))
                # 从数据库字段补充
                db_params = self._get_db_params(c)
                merged = {**quota_params, **{k: v for k, v in db_params.items() if v is not None}}

                has_tier = any(p in merged for p in self.TIER_PARAMS)
                if has_tier:
                    c["param_score"] = 0.6  # 定额有档位但清单没指定，不确定
                    c["param_detail"] = "定额有档位参数但清单未指定"
                else:
                    c["param_score"] = 1.0  # 无参数可验证，默认通过
                    c["param_detail"] = "无参数可验证"
                c["param_match"] = True  # 默认为匹配
                c["param_tier"] = 1  # 无清单参数，归为部分匹配层

                # 即使无参数，仍需检查品类冲突（如"喷淋泵"不应配"喷头"定额）
                cat_penalty, cat_detail = self._check_category_conflict(
                    query_text, c.get("name", ""))
                if cat_penalty > 0:
                    c["param_score"] = max(0.0, c["param_score"] - cat_penalty)
                    c["param_detail"] += f"; {cat_detail}"
                    if cat_penalty >= 0.3:
                        c["param_match"] = False
                        c["param_tier"] = 0  # 品类冲突降为硬失败层（如"普通插座"不应配"防爆插座"）
                neg_penalty, neg_detail = self._check_negative_keywords(
                    query_text, c.get("name", ""))
                if neg_penalty > 0:
                    c["param_score"] = max(0.0, c["param_score"] - neg_penalty)
                    c["param_detail"] += f"; {neg_detail}"
                    if neg_penalty >= 0.3:
                        c["param_match"] = False
                        c["param_tier"] = 0  # 负向关键词降为硬失败层
                # 无参数分支：LTR参数特征设为默认值（无参数可比较）
                c["_ltr_param"] = {
                    "param_main_exact": 0,
                    "param_main_rel_dist": 1.0,
                    "param_main_direction": 0,
                    "param_material_match": -1.0,
                    "param_n_checks": 0,
                }
            for c in candidates:
                c["name_bonus"] = self._bill_keyword_bonus(
                    query_text, c.get("name", ""))

            # 无参数分支：用LTR模型或品类词主导排序
            self._ltr_sort(candidates, query_text, search_books=search_books)
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

            # 传入定额名称，供速度分类等校验使用
            merged_quota_params["_quota_name"] = candidate.get("name", "")

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

            # 品类互斥检查：清单核心品类和定额核心品类冲突 → 降分
            cat_penalty, cat_detail = self._check_category_conflict(
                query_text, candidate.get("name", ""))
            if cat_penalty > 0:
                score = max(0.0, score - cat_penalty)
                detail += f"; {cat_detail}"
                if cat_penalty >= 0.3:
                    is_match = False

            candidate["param_score"] = score
            candidate["param_detail"] = detail
            candidate["param_match"] = is_match
            candidate["param_tier"] = self._determine_param_tier(is_match, score, detail)

            # 存LTR参数距离特征（供排序模型学习"参数精确匹配比语义相似更重要"）
            candidate["_ltr_param"] = self._compute_param_ltr_features(
                bill_params, merged_quota_params)

            # 清单核心词匹配加分：清单关键词在候选名称中出现越多，排序越靠前
            candidate["name_bonus"] = self._bill_keyword_bonus(
                query_text, candidate.get("name", ""))

            validated.append(candidate)

        # 有参数分支：用LTR模型或三项融合排序
        self._ltr_sort(validated, query_text, search_books=search_books)

        return validated

    def _compute_param_ltr_features(self, bill_params: dict,
                                     quota_params: dict) -> dict:
        """
        计算LTR参数距离特征（5维）

        用途：让排序模型学会区分"DN精确匹配"和"DN差一档"，
        解决57%的"选错档位"错误。

        返回:
            dict，包含5个特征值
        """
        features = {
            "param_main_exact": 0,        # 主参数是否精确匹配
            "param_main_rel_dist": 1.0,   # 主参数相对距离(默认最大)
            "param_main_direction": 0,    # 方向(+1向上/-1向下/0精确)
            "param_material_match": -1.0, # 材质(-1=无信息)
            "param_n_checks": 0,          # 参数检查项数
        }

        # 找主参数：清单中第一个出现的数值型取档参数
        main_param = None
        for p in self.TIER_PARAMS:
            if p in bill_params:
                main_param = p
                break

        # 计算主参数距离
        if main_param:
            bill_val = bill_params[main_param]
            if main_param in quota_params:
                quota_val = quota_params[main_param]
                if bill_val == quota_val:
                    features["param_main_exact"] = 1
                    features["param_main_rel_dist"] = 0.0
                    features["param_main_direction"] = 0
                else:
                    max_val = max(abs(bill_val), abs(quota_val), 1)
                    features["param_main_rel_dist"] = abs(
                        bill_val - quota_val) / max_val
                    features["param_main_direction"] = (
                        1 if quota_val > bill_val else -1)
            # else: 定额无此参数（通用定额），保持默认值

        # 材质匹配度
        if "material" in bill_params and "material" in quota_params:
            bill_mat = bill_params["material"]
            quota_mat = quota_params["material"]
            if bill_mat == quota_mat:
                features["param_material_match"] = 1.0
            elif self._materials_compatible(bill_mat, quota_mat):
                features["param_material_match"] = 0.7
            else:
                features["param_material_match"] = 0.0

        # 参数检查项数（清单提供了几个可比较的参数）
        count = 0
        for p in self.TIER_PARAMS:
            if p in bill_params:
                count += 1
        if "material" in bill_params:
            count += 1
        if "connection" in bill_params:
            count += 1
        features["param_n_checks"] = count

        return features

    def _ltr_sort(self, candidates: list[dict], query_text: str,
                  search_books: list[str] = None):
        """
        用LTR模型排序候选（如果模型可用），否则回退到手工公式。

        两阶段排序：
        1. param_tier=0（硬失败）永远排最后（业务规则不让模型越界）
        2. tier>0的候选用LTR模型打分排序
        """
        self._load_ltr_model()

        if self._ltr_model is not None:
            try:
                # 计算v4同族排名特征（共享函数，训练/推理一致）
                from src.ltr_features import compute_within_tier_features
                compute_within_tier_features(candidates)

                features = self._extract_ltr_features(
                    candidates, query_text, search_books=search_books)
                scores = self._ltr_model.predict(features)
                for i, c in enumerate(candidates):
                    # 硬失败候选不让模型翻身
                    if c.get("param_tier", 1) == 0:
                        c["ltr_score"] = -1e9
                    else:
                        c["ltr_score"] = float(scores[i])
                candidates.sort(key=lambda x: x.get("ltr_score", 0), reverse=True)
                return
            except Exception as e:
                logger.warning(f"LTR模型预测失败，回退手工公式: {e}")

        # 回退：手工公式排序（有参数分支权重）
        candidates.sort(
            key=lambda x: (
                x.get("param_tier", 1),
                x.get("param_score", 0) * 0.55
                + x.get("name_bonus", 0) * 0.30
                + (x.get("rerank_score") or x.get("hybrid_score") or 0) * 0.15,
            ),
            reverse=True,
        )

    def _extract_ltr_features(self, candidates: list[dict],
                              query_text: str,
                              search_books: list[str] = None) -> np.ndarray:
        """
        从候选列表中提取LTR特征矩阵。
        v1: 16维（兼容旧模型）
        v2: 21维（+5个参数距离特征）
        v3: 23维（+book_match, token_overlap）
        v4: 28维（+5个同族排名特征）

        Args:
            search_books: 清单分类的搜索册号列表（用于book_match特征）
        """
        n = len(candidates)
        # 版本检测：用特征名校验（Codex P0修复，不再靠维度数硬判断）
        has_v2, has_v3, has_v4 = False, False, False
        n_features = 16  # v1基线
        if self._ltr_model is not None:
            try:
                model_feature_names = set(self._ltr_model.feature_name())
                has_v2 = "param_main_exact" in model_feature_names
                has_v3 = "book_match" in model_feature_names
                has_v4 = "param_tier_rank" in model_feature_names
                n_features = self._ltr_model.num_feature()
            except Exception:
                n_features = 16
        # 提取清单名称（取query_text的第一段）
        bill_name = query_text.split()[0] if query_text else ""

        # 构建bm25/vector排名映射
        bm25_sorted = sorted(range(n),
                             key=lambda i: candidates[i].get("bm25_score") or 0,
                             reverse=True)
        vector_sorted = sorted(range(n),
                               key=lambda i: candidates[i].get("vector_score") or 0,
                               reverse=True)

        bm25_rank = {candidates[idx].get("quota_id", ""): i / max(n, 1)
                     for i, idx in enumerate(bm25_sorted)}
        vector_rank = {candidates[idx].get("quota_id", ""): i / max(n, 1)
                       for i, idx in enumerate(vector_sorted)}

        bm25_ids = {candidates[i].get("quota_id", "") for i in range(n)
                    if (candidates[i].get("bm25_score") or 0) > 0}
        vector_ids = {candidates[i].get("quota_id", "") for i in range(n)
                      if (candidates[i].get("vector_score") or 0) > 0}

        # top1的composite分（用于score_gap_to_top1）
        top1 = candidates[0] if candidates else {}
        top1_composite = (
            (top1.get("param_score") or 0) * 0.55
            + (top1.get("name_bonus") or 0) * 0.30
            + (top1.get("rerank_score") or top1.get("hybrid_score") or 0) * 0.15
        )

        features = np.zeros((n, n_features), dtype=np.float64)
        for i, c in enumerate(candidates):
            qid = str(c.get("quota_id", ""))
            tier = c.get("param_tier", 1)
            ps = c.get("param_score") or 0
            nb = c.get("name_bonus") or 0
            rr = c.get("rerank_score") or c.get("hybrid_score") or 0
            composite = ps * 0.55 + nb * 0.30 + rr * 0.15
            cand_name = c.get("name", "")

            # 基础16维特征
            row = [
                c.get("bm25_score") or 0,            # 1
                c.get("vector_score") or 0,           # 2
                c.get("hybrid_score") or 0,           # 3
                c.get("rerank_score") or 0,           # 4
                ps,                                    # 5
                1 if c.get("param_match", True) else 0,  # 6
                1 if tier == 0 else 0,                 # 7
                1 if tier == 1 else 0,                 # 8
                1 if tier == 2 else 0,                 # 9
                nb,                                    # 10
                n,                                     # 11
                1.0 - bm25_rank.get(qid, 1.0),        # 12
                1.0 - vector_rank.get(qid, 1.0),      # 13
                SequenceMatcher(None, bill_name, cand_name).ratio()
                if bill_name and cand_name else 0.0,   # 14
                top1_composite - composite,            # 15
                1 if (qid in bm25_ids and qid in vector_ids) else 0,  # 16
            ]

            # v2新增：5个参数距离特征
            if has_v2:
                ltr_param = c.get("_ltr_param", {})
                row.extend([
                    ltr_param.get("param_main_exact", 0),       # 17
                    ltr_param.get("param_main_rel_dist", 1.0),  # 18
                    ltr_param.get("param_main_direction", 0),   # 19
                    ltr_param.get("param_material_match", -1.0),  # 20
                    ltr_param.get("param_n_checks", 0),         # 21
                ])

            # v3新增：册号匹配+词级重叠
            if has_v3:
                row.extend([
                    self._compute_book_match(c, search_books or []),  # 22
                    self._compute_token_overlap(bill_name, cand_name),  # 23
                ])

            # v4新增：同族排名特征（从共享函数计算结果读取）
            if has_v4:
                row.extend([
                    c.get("_v4_param_tier_rank", 0.5),       # 24
                    c.get("_v4_family_size", 0.0),            # 25
                    c.get("_v4_param_score_rank", 0.5),       # 26
                    c.get("_v4_rerank_within_tier", 0.5),     # 27
                    c.get("_v4_dist_to_tier_best", 0.0),      # 28
                ])

            features[i] = row

        return features

    @staticmethod
    def _compute_book_match(candidate: dict, search_books: list[str]) -> float:
        """计算候选册号与搜索册号的匹配度（1=主专业/0.5=借用/0=不在范围）"""
        from src.specialty_classifier import get_book_from_quota_id
        qid = str(candidate.get("quota_id", ""))
        if not qid or not search_books:
            return 0.0
        cand_book = get_book_from_quota_id(qid)
        if not cand_book:
            return 0.0
        cand_book_upper = cand_book.upper()
        if search_books[0].upper() == cand_book_upper:
            return 1.0
        for book in search_books[1:]:
            if book.upper() == cand_book_upper:
                return 0.5
        return 0.0

    @staticmethod
    def _compute_token_overlap(bill_name: str, cand_name: str) -> float:
        """计算词级Jaccard重叠度（jieba分词后）"""
        if not bill_name or not cand_name:
            return 0.0

        def _tokenize(text: str) -> set[str]:
            tokens = set()
            for w in jieba.cut(text):
                w = w.strip()
                if len(w) >= 2 and not re.match(r'^[\d.]+$', w):
                    tokens.add(w)
            return tokens

        bill_tokens = _tokenize(bill_name)
        cand_tokens = _tokenize(cand_name)
        if not bill_tokens or not cand_tokens:
            return 0.0
        intersection = bill_tokens & cand_tokens
        union = bill_tokens | cand_tokens
        return len(intersection) / len(union) if union else 0.0

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
        if candidate.get("install_method"):
            params["install_method"] = candidate["install_method"]
        if candidate.get("circuits") is not None:
            params["circuits"] = candidate["circuits"]
        if candidate.get("shape"):
            params["shape"] = candidate["shape"]
        if candidate.get("perimeter") is not None:
            params["perimeter"] = candidate["perimeter"]
        if candidate.get("large_side") is not None:
            params["large_side"] = candidate["large_side"]
        if candidate.get("elevator_stops") is not None:
            params["elevator_stops"] = candidate["elevator_stops"]
        if candidate.get("elevator_speed") is not None:
            params["elevator_speed"] = candidate["elevator_speed"]
        return params

    # 清单核心词匹配：停用词表（太常见、无区分度的词）
    _KEYWORD_STOPWORDS = {
        '安装', '制作', '设备', '编号', '名称', '型号', '规格', '以内', '以下',
        '以上', '及其', '工程', '项目', '系统', '配套', '其他', '一般',
    }

    # jieba分词缓存：同一query_text在候选循环中只分词一次
    _keyword_cache_text = ""
    _keyword_cache_result = frozenset()

    def _bill_keyword_bonus(self, query_text: str, candidate_name: str) -> float:
        """
        清单核心词与候选名称的匹配加分（0~1.0）

        用jieba分词从清单原文提取有意义的中文关键词（≥2字），
        检查候选定额名称是否包含这些词。
        匹配越多说明品类越吻合，给予加分。
        """
        if not query_text or not candidate_name:
            return 0.0

        # 缓存：同一query_text只分词一次（候选循环中反复调用）
        if query_text != self._keyword_cache_text:
            clean = re.sub(r'[0-9a-zA-Z×φΦ≤≥<>*.\-~]+', ' ', query_text)
            clean = re.sub(r'[()（）\[\]【】{}、，。：；""''·/\\,.:;]', ' ', clean)
            words = jieba.lcut(clean)
            self._keyword_cache_result = frozenset(
                w for w in words if len(w) >= 2 and w not in self._KEYWORD_STOPWORDS
            )
            self._keyword_cache_text = query_text

        keywords = self._keyword_cache_result
        if not keywords:
            return 0.0

        # 计算匹配比例：候选名称包含多少个清单关键词
        matches = sum(1 for kw in keywords if kw in candidate_name)
        return matches / len(keywords)

    # 材质族谱和泛称映射：从 compat_primitives 导入（单一事实来源）
    MATERIAL_FAMILIES = MATERIAL_FAMILIES
    GENERIC_MATERIALS = GENERIC_MATERIALS

    # 品类互斥表：同组内的品类不应相互匹配
    # 例如清单"阀门"不应匹配到"弯头"定额，清单"水泵"不应匹配到"风机"定额
    # 按专业大类组织（C=安装, A=土建, D=市政, E=园林），方便多专业扩展
    #
    # 注意事项：
    # - "法兰"不在此表中：它既是产品（法兰盘）又是连接方式修饰语（法兰蝶阀），
    #   放在互斥组中会导致"法兰蝶阀"与"阀门安装"误报冲突
    # - "管件"不在此表中：它是泛称（包含弯头/三通/异径管），与子类型不互斥
    CATEGORY_CONFLICTS_BY_SPECIALTY = {
        "install": [  # 安装工程（C1~C12）
            {"阀门", "弯头", "三通", "异径管"},
            {"泵", "风机", "风口"},  # "水泵"改为"泵"，覆盖所有泵类（喷淋泵/消防泵/加压泵等）
            {"桥架", "穿线管", "配管"},
            {"配电箱", "控制柜", "端子箱"},
            {"灯具", "开关", "插座"},
            {"消火栓", "灭火器", "喷头"},
            {"散热器", "地暖", "风机盘管"},
        ],
        # 以下专业暂时为空，待扩展时添加
        # "civil": [],      # 土建工程
        # "municipal": [],  # 市政工程
        # "landscape": [],  # 园林绿化
    }

    # 兼容旧代码：不指定专业时默认用安装工程的品类表
    CATEGORY_CONFLICTS = CATEGORY_CONFLICTS_BY_SPECIALTY["install"]

    # 跨品类硬排斥表：清单含某关键词 → 定额名称不能含这些词
    # 与 CATEGORY_CONFLICTS（组内冲突）不同，这里检测的是跨组的品类错配
    # 例如："泵"在组2，"喷头"在组6，它们属于不同组，组内检查拦不住
    CATEGORY_HARD_REJECTS = {
        "泵": ["喷头", "消火栓", "灭火器", "散流器", "风口"],
        "喷头": ["泵", "消火栓箱", "灭火器"],
        "消火栓": ["喷头", "泵", "灭火器"],
        "灭火器": ["消火栓", "喷头", "泵"],
        "电缆": ["导线", "双绞线"],
        "导线": ["电缆"],
        "桥架": ["穿线管"],
        "穿线管": ["桥架"],
        # 暖通品类：风机盘管和散热器是完全不同的末端设备
        "风机盘管": ["散热器", "地暖"],
        "散热器": ["风机盘管", "风口"],
        # 电气品类：电缆头（终端半成品）不应配电缆敷设定额
        "电缆头": ["电缆敷设", "导线"],
        # 消防品类：防火阀和普通风阀是不同专业
        # 注意：不排斥"调节阀"，因为定额名就叫"防火调节阀安装"
        "防火阀": ["蝶阀", "球阀"],
    }

    def _materials_compatible(self, mat1: str, mat2: str) -> bool:
        """判断两种材质是否兼容（委托给 compat_primitives 统一实现）"""
        return _compat_materials_compatible(mat1, mat2)

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

    @classmethod
    def _check_category_conflict(cls, bill_text: str, quota_name: str) -> tuple[float, str]:
        """
        品类互斥检查：清单核心品类和定额核心品类冲突时降分

        例如：
          - 清单"闸阀DN100"  → 定额"弯头DN100"  → 品类冲突（阀门≠弯头）
          - 清单"消防泵"     → 定额"风机安装"    → 品类冲突（水泵≠风机）

        返回: (惩罚分数, 说明文本)
        """
        # 找清单命中的品类
        bill_category = None
        for conflict_group in cls.CATEGORY_CONFLICTS:
            for cat in conflict_group:
                if cat in bill_text:
                    bill_category = (cat, conflict_group)
                    break
            if bill_category:
                break

        if not bill_category:
            # 组内没命中，继续检查跨品类硬排斥
            for bill_kw, reject_list in cls.CATEGORY_HARD_REJECTS.items():
                if bill_kw in bill_text:
                    for reject_kw in reject_list:
                        if reject_kw in quota_name:
                            return 0.3, f"品类硬排斥: 清单含'{bill_kw}' ≠ 定额含'{reject_kw}'"
            return 0.0, ""

        bill_cat_name, bill_group = bill_category

        # 找定额命中的品类（组内冲突检查）
        for cat in bill_group:
            if cat == bill_cat_name:
                continue  # 同品类不算冲突
            if cat in quota_name:
                return 0.3, f"品类冲突: 清单'{bill_cat_name}' vs 定额'{cat}'"

        # 组内没冲突，继续检查跨品类硬排斥
        # 例如："泵"在组2匹配到了，但定额含"喷头"（组6），组内检查拦不住
        for bill_kw, reject_list in cls.CATEGORY_HARD_REJECTS.items():
            if bill_kw in bill_text:
                for reject_kw in reject_list:
                    if reject_kw in quota_name:
                        return 0.3, f"品类硬排斥: 清单含'{bill_kw}' ≠ 定额含'{reject_kw}'"

        return 0.0, ""

    @staticmethod
    def _connections_compatible_pv(bill_conn: str, quota_conn: str) -> bool:
        """判断两种连接方式是否兼容（委托给 compat_primitives 统一实现）"""
        return _compat_connections_compatible(bill_conn, quota_conn)

    # 安装方式兼容组：同组内的表达方式互相兼容（一个方式可出现在多组中）
    _INSTALL_COMPAT_GROUPS = [
        {"挂墙", "壁挂", "悬挂"},  # 明装系：悬挂/挂墙/壁挂（外露安装）
        {"明装", "明敷"},  # 电气设备用"明装"，母线/线缆用"明敷"
        {"暗装", "暗敷", "嵌入", "嵌墙"},  # 暗装系：暗装/嵌入/嵌墙（墙体内安装）
    ]

    @classmethod
    def _install_methods_compatible(cls, m1: str, m2: str) -> bool:
        """判断两种安装方式是否兼容（同义表达视为兼容）"""
        if m1 == m2:
            return True
        for group in cls._INSTALL_COMPAT_GROUPS:
            if m1 in group and m2 in group:
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
            # 清单参数 >= 定额参数，不应走升档逻辑（外层已判断 bill < quota）
            # 如果意外进入，返回0分（不匹配），而不是1分（满分）
            return 0.0

        # score = 1.0 - 0.1 * log2(ratio)，限制在 [0.55, 1.0]
        score = 1.0 - 0.1 * math.log2(ratio)
        return max(0.55, min(1.0, score))

    @staticmethod
    def _determine_param_tier(is_match: bool, score: float, detail: str) -> int:
        """
        根据参数匹配结果判定排序层级（三层分级）

        param_tier决定排序优先级，tier高的永远排在tier低的前面，
        不受BM25/name_bonus等软分数影响。

        判定依据：不看分数高低，而是看定额是否**缺少**清单要求的参数。
        - 定额有参数且匹配（精确/向上取档）→ tier=2
        - 定额缺少清单要求的参数（通用定额）→ tier=1
        - 参数明显不匹配（硬失败）→ tier=0

        返回:
            2 = 精确匹配（定额有清单要求的参数，且匹配通过）
            1 = 部分匹配/无参数（定额缺少关键参数，属于"万金油"通用定额）
            0 = 硬失败（参数明显不匹配，param_match=False）
        """
        if not is_match:
            return 0
        # 检查detail中是否有"通用定额降权"标记
        # 这些标记由_check_params在定额缺少清单参数时写入
        if "通用定额降权" in detail or "定额无参数可验证" in detail or "无共同参数可对比" in detail:
            return 1
        # 定额有参数且匹配通过 → 精确匹配层
        return 2

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
                # 定额无DN参数（通用定额），降权到0.64
                # L8用0.55导致confidence=52（红灯），但通用定额不是"匹配错了"而是"无法验证"
                # 0.64 → confidence=60（黄灯），标记"需人工确认"而非"很可能错"
                # 注意：向上取档下界仍是0.55（那是真的参数偏差大，应该更低）
                score_sum += 0.64
                details.append(f"定额无DN参数(通用定额降权)")

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
                # 通用定额降权：0.64 → confidence=60（黄灯）
                score_sum += 0.64
                details.append(f"定额无截面参数(通用定额降权)")

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
                # 通用定额降权：0.64 → confidence=60（黄灯）
                score_sum += 0.64
                details.append(f"定额无容量参数(通用定额降权)")

        # === 3b. 功率kW（硬性参数，电动机/水泵等按功率分档） ===
        if "kw" in bill_params:
            check_count += 1
            if "kw" in quota_params:
                bill_kw = bill_params["kw"]
                quota_kw = quota_params["kw"]
                if bill_kw == quota_kw:
                    score_sum += 1.0
                    details.append(f"功率{bill_kw}kW={quota_kw}kW 精确匹配")
                elif bill_kw < quota_kw:
                    tier_score = self._tier_up_score(bill_kw, quota_kw)
                    score_sum += tier_score
                    details.append(f"功率{bill_kw}→{quota_kw}kW 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"功率{bill_kw}kW>{quota_kw}kW 不匹配(清单>定额)")
            else:
                score_sum += 0.64
                details.append(f"定额无功率参数(通用定额降权)")

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
                # 通用定额降权：0.64 → confidence=60（黄灯）
                score_sum += 0.64
                details.append("定额无回路参数(通用定额降权)")

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
                # 通用定额降权：0.64 → confidence=60（黄灯）
                score_sum += 0.64
                details.append("定额无电流参数(通用定额降权)")

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
        if "material" in bill_params:
            if "material" in quota_params:
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
            else:
                # 清单有材质但定额无材质信息 → 信息缺失微惩罚
                # 通用定额不分材质是正常的，不算hard_fail，但排序应低于精确匹配
                check_count += 1
                score_sum += 0.7
                details.append(f"清单有材质'{bill_params['material']}'但定额无材质信息")

        # === 8. 连接方式（硬性参数：螺纹≠沟槽、热熔≠粘接 必须匹配） ===
        if "connection" in bill_params:
            if "connection" in quota_params:
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
                    # L8：连接方式不匹配升级为硬性失败
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"连接方式'{bill_c}'≠'{quota_c}' 不匹配")
            else:
                # 清单有连接方式但定额无 → 信息缺失微惩罚
                check_count += 1
                score_sum += 0.7
                details.append(f"清单有连接方式'{bill_params['connection']}'但定额无连接方式信息")

        # === 8.5 安装/敷设方式（只加分不扣分：匹配时boost，不匹配时跳过） ===
        # 为什么只加分不扣分？因为各省定额命名差异大：
        # - 北京"弱电箱"清单说暗装但正确定额叫"挂墙安装"
        # - 浙江"插座箱"清单说挂墙但定额叫"悬挂式"
        # 扣分会误杀这些正确匹配，只加分则安全：匹配的候选拉高，不匹配的不变
        if "install_method" in bill_params:
            bill_im = bill_params["install_method"]
            quota_im = quota_params.get("install_method", "")
            if quota_im and (bill_im == quota_im
                             or self._install_methods_compatible(bill_im, quota_im)):
                check_count += 1
                score_sum += 1.0
                details.append(f"安装方式'{bill_im}'匹配")

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
        if "perimeter" in bill_params:
            if "perimeter" in quota_params:
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
            else:
                # 清单有周长但定额无 → 通用定额降权
                check_count += 1
                score_sum += 0.64
                details.append("定额无周长参数(通用定额降权)")

        # === 10.5 半周长（硬性参数：配电箱悬挂/嵌入式按半周长取档） ===
        if "half_perimeter" in bill_params:
            if "half_perimeter" in quota_params:
                check_count += 1
                bill_hp = bill_params["half_perimeter"]
                quota_hp = quota_params["half_perimeter"]
                if bill_hp == quota_hp:
                    score_sum += 1.0
                    details.append(f"半周长{bill_hp}={quota_hp} 精确匹配")
                elif bill_hp <= quota_hp:
                    tier_score = self._tier_up_score(bill_hp, quota_hp)
                    score_sum += tier_score
                    details.append(f"半周长{bill_hp}→{quota_hp} 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"半周长{bill_hp}>{quota_hp} 不匹配(清单>定额)")
            else:
                # 清单有半周长但定额无 → 通用定额降权
                check_count += 1
                score_sum += 0.64
                details.append("定额无半周长参数(通用定额降权)")

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

        # === 14. 电梯运行速度（硬性参数） ===
        # 只有当定额名称明确标注了速度分类（"2m/s以上"或"2m/s以下"）时才校验
        # 没有速度标注的定额（如"增加厅门""电气安装"）不参与速度评分，避免误加分
        if "elevator_speed" in bill_params:
            bill_speed = bill_params["elevator_speed"]
            quota_name = quota_params.get("_quota_name", "")
            has_speed_class = "2m/s以下" in quota_name or "2m/s以上" in quota_name
            if has_speed_class:
                check_count += 1
                if bill_speed > 2.0 and "2m/s以下" in quota_name:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"速度{bill_speed}m/s>2 但定额是2m/s以下")
                elif bill_speed <= 2.0 and "2m/s以上" in quota_name:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"速度{bill_speed}m/s≤2 但定额是2m/s以上")
                else:
                    score_sum += 1.0
                    details.append(f"电梯速度{bill_speed}m/s匹配")

        # === 15. 接地扁钢宽度（硬性参数：按宽度取档，如40×4中的40mm） ===
        if "ground_bar_width" in bill_params:
            if "ground_bar_width" in quota_params:
                check_count += 1
                bill_gbw = bill_params["ground_bar_width"]
                quota_gbw = quota_params["ground_bar_width"]
                if bill_gbw == quota_gbw:
                    score_sum += 1.0
                    details.append(f"扁钢宽{bill_gbw}={quota_gbw} 精确匹配")
                elif bill_gbw < quota_gbw:
                    tier_score = self._tier_up_score(bill_gbw, quota_gbw)
                    score_sum += tier_score
                    details.append(f"扁钢宽{bill_gbw}→{quota_gbw} 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"扁钢宽{bill_gbw}>{quota_gbw} 不匹配(清单>定额)")
            else:
                # 清单有扁钢宽度但定额无 → 通用定额降权
                check_count += 1
                score_sum += 0.64
                details.append("定额无扁钢宽度参数(通用定额降权)")

        # === 16. 开关联数（硬性参数：单联≠双联，按联数分档） ===
        if "switch_gangs" in bill_params:
            check_count += 1
            if "switch_gangs" in quota_params:
                bill_sg = bill_params["switch_gangs"]
                quota_sg = quota_params["switch_gangs"]
                if bill_sg == quota_sg:
                    score_sum += 1.0
                    details.append(f"联数{bill_sg}={quota_sg} 精确匹配")
                elif bill_sg < quota_sg:
                    # 向上取档（如单联清单匹配≤3联定额）
                    tier_score = self._tier_up_score(bill_sg, quota_sg)
                    score_sum += tier_score
                    details.append(f"联数{bill_sg}→{quota_sg} 向上取档")
                else:
                    has_hard_fail = True
                    score_sum += 0.0
                    details.append(f"联数{bill_sg}>{quota_sg} 不匹配(清单>定额)")
            else:
                # 定额无联数参数（通用定额降权）
                score_sum += 0.64
                details.append("定额无联数参数(通用定额降权)")

        # 计算最终分数
        if check_count == 0:
            # 反向检查：定额有档位参数但清单没提供 → 无法确认档位
            # 例如配电箱定额写"48回路"但清单只写了尺寸没写回路数
            quota_has_tier = any(p in quota_params for p in self.TIER_PARAMS)
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
