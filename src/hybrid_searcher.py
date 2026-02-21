"""
混合搜索引擎
功能：
1. 同时调用BM25关键词搜索和BGE向量语义搜索
2. 用RRF（Reciprocal Rank Fusion，倒数排名融合）算法合并结果
3. 返回融合排序后的Top K候选定额

为什么要混合搜索？
- BM25擅长：精确关键词匹配（"DN150"、"YJV-4*185"等型号规格）
- 向量搜索擅长：语义相似匹配（"水泵"和"离心泵"是相关的）
- 两者互补，混合后效果最好

RRF算法原理：
- 不直接比较BM25分数和向量分数（它们量纲不同，不可直接相加）
- 而是根据各自的排名来融合：排名越靠前，贡献越大
- 公式：RRF_score = Σ (weight / (k + rank))
- k=60是标准常数，防止排名第1的权重过大
"""

import re
import time
from pathlib import Path

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.bm25_engine import BM25Engine
from src.vector_engine import VectorEngine
from db.sqlite import connect as _db_connect


class HybridSearcher:
    """混合搜索引擎：BM25 + 向量搜索，RRF融合"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置
        """
        self.province = province or config.get_current_province()

        # 两个搜索引擎（延迟初始化）
        self._bm25_engine = None
        self._vector_engine = None

        # 通用知识库（延迟初始化）
        self._universal_kb = None

        # 反馈偏置缓存（用用户修正/确认数据动态校准检索权重）
        self._feedback_bias_value = 0.0
        self._feedback_bias_ts = 0.0

    @property
    def bm25_engine(self):
        """延迟加载BM25引擎"""
        if self._bm25_engine is None:
            self._bm25_engine = BM25Engine(self.province)
        return self._bm25_engine

    @property
    def vector_engine(self):
        """延迟加载向量引擎"""
        if self._vector_engine is None:
            self._vector_engine = VectorEngine(self.province)
        return self._vector_engine

    @property
    def universal_kb(self):
        """延迟加载通用知识库"""
        if self._universal_kb is None:
            try:
                from src.universal_kb import UniversalKB
                self._universal_kb = UniversalKB()
                # 检查是否有数据（没数据就不用查了）
                if self._universal_kb.get_stats()["authority"] == 0:
                    logger.debug("通用知识库权威层为空，跳过知识库增强")
                    self._universal_kb = False  # 标记为不可用，避免反复初始化
            except Exception as e:
                logger.debug(f"通用知识库加载失败（不影响基础搜索）: {e}")
                self._universal_kb = False
        return self._universal_kb if self._universal_kb is not False else None

    def search(self, query: str, top_k: int = None,
               bm25_weight: float = None, vector_weight: float = None,
               books: list[str] = None) -> list[dict]:
        """
        混合搜索：同时执行BM25和向量搜索，用RRF融合结果

        流程：
        1. 查通用知识库，获取搜索增强关键词（如果有权威层数据）
        2. 用原始query + 增强关键词分别执行BM25和向量搜索
        3. RRF融合排序

        参数:
            query: 搜索文本（清单项目名称+特征描述）
            top_k: 最终返回的候选数量
            bm25_weight: BM25搜索权重（默认0.3）
            vector_weight: 向量搜索权重（默认0.7）
            books: 限定搜索的册号列表（如["C10", "C8"]），为None时搜索全库

        返回:
            融合排序后的候选定额列表，每条包含:
            {id, quota_id, name, unit, hybrid_score, bm25_rank, vector_rank, ...}
        """
        top_k = top_k or config.HYBRID_TOP_K
        base_bm25_weight = config.BM25_WEIGHT if bm25_weight is None else bm25_weight
        base_vector_weight = config.VECTOR_WEIGHT if vector_weight is None else vector_weight
        bm25_weight, vector_weight, weight_reason = self._get_adaptive_weights(
            query=query,
            bm25_weight=base_bm25_weight,
            vector_weight=base_vector_weight,
        )

        # ============================================================
        # 第0步：查通用知识库获取搜索增强关键词
        # ============================================================
        kb_hints = []
        if self.universal_kb:
            try:
                kb_hints = self.universal_kb.get_search_keywords(query)
                if kb_hints:
                    logger.debug(f"通用知识库增强: {kb_hints[:3]}")
            except Exception as e:
                logger.debug(f"通用知识库查询失败（不影响搜索）: {e}")

        # ============================================================
        # 第1步：多查询变体检索（Query2doc / MuGI 思路的轻量落地）
        # ============================================================
        query_variants = self._build_query_variants(query, kb_hints)
        bm25_runs = []
        vector_runs = []
        total_bm25_hits = 0
        total_vector_hits = 0

        # 批量预编码所有查询变体的向量（一次GPU调用，比逐条快很多）
        variant_queries = [v["query"] for v in query_variants]
        try:
            all_embeddings = self.vector_engine.encode_queries(variant_queries)
        except Exception as e:
            logger.warning(f"批量向量编码失败: {e}")
            all_embeddings = [None] * len(variant_queries)

        for idx, variant in enumerate(query_variants, start=1):
            q_text = variant["query"]
            q_weight = variant["weight"]
            q_tag = variant["tag"]

            bm25_results = []
            try:
                bm25_results = self.bm25_engine.search(
                    q_text, top_k=config.BM25_TOP_K, books=books
                )
                total_bm25_hits += len(bm25_results)
            except Exception as e:
                logger.warning(f"BM25搜索失败[{q_tag}]: {e}")

            vector_results = []
            try:
                # 使用预计算的向量，跳过逐条编码
                embedding = all_embeddings[idx - 1] if all_embeddings[0] is not None else None
                vector_results = self.vector_engine.search(
                    q_text, top_k=config.VECTOR_TOP_K, books=books,
                    precomputed_embedding=embedding
                )
                total_vector_hits += len(vector_results)
            except Exception as e:
                logger.warning(f"向量搜索失败[{q_tag}]: {e}")

            bm25_runs.append({
                "tag": q_tag,
                "query": q_text,
                "weight": q_weight,
                "results": bm25_results,
            })
            vector_runs.append({
                "tag": q_tag,
                "query": q_text,
                "weight": q_weight,
                "results": vector_results,
            })

            logger.debug(
                f"变体#{idx} [{q_tag}] 检索完成: "
                f"BM25={len(bm25_results)} 向量={len(vector_results)}"
            )

        # 如果两路都没有结果，返回空
        if total_bm25_hits == 0 and total_vector_hits == 0:
            logger.warning(f"两路搜索均无结果: '{query}'")
            return []

        # 如果只有一路有结果，做该路的多查询融合排序后返回
        if total_bm25_hits == 0:
            vector_only = self._merge_single_engine_runs(
                vector_runs, engine="vector", k=config.RRF_K
            )
            top_results = vector_only[:top_k]
            for r in top_results:
                r["hybrid_score"] = r.get("vector_rrf_score", r.get("vector_score", 0))
                r["bm25_rank"] = None
                r["fusion_mode"] = "vector_only_rrf"
                r["effective_bm25_weight"] = bm25_weight
                r["effective_vector_weight"] = vector_weight
                r["fusion_weight_reason"] = weight_reason
            return top_results

        if total_vector_hits == 0:
            bm25_only = self._merge_single_engine_runs(
                bm25_runs, engine="bm25", k=config.RRF_K
            )
            top_results = bm25_only[:top_k]
            for r in top_results:
                r["hybrid_score"] = r.get("bm25_rrf_score", r.get("bm25_score", 0))
                r["vector_rank"] = None
                r["fusion_mode"] = "bm25_only_rrf"
                r["effective_bm25_weight"] = bm25_weight
                r["effective_vector_weight"] = vector_weight
                r["fusion_weight_reason"] = weight_reason
            return top_results

        # ============================================================
        # 第2步：RRF融合排序
        # ============================================================

        use_multi_query = bool(getattr(config, "HYBRID_MULTI_QUERY_FUSION", True))
        multi_query_effective = use_multi_query and len(query_variants) > 1
        if multi_query_effective:
            merged = self._rrf_fusion_multi_query(
                bm25_runs=bm25_runs,
                vector_runs=vector_runs,
                bm25_weight=bm25_weight,
                vector_weight=vector_weight,
                k=config.RRF_K,
            )
        else:
            merged = self._rrf_fusion(
                bm25_results=bm25_runs[0]["results"],
                vector_results=vector_runs[0]["results"],
                bm25_weight=bm25_weight,
                vector_weight=vector_weight,
                k=config.RRF_K,
            )

        # 取Top K
        top_results = merged[:top_k]

        for r in top_results:
            r["fusion_mode"] = "adaptive_multi_query_rrf" if multi_query_effective else "adaptive_rrf"
            r["effective_bm25_weight"] = bm25_weight
            r["effective_vector_weight"] = vector_weight
            r["fusion_weight_reason"] = weight_reason
            r["query_variants"] = [v["tag"] for v in query_variants]

        logger.debug(
            f"混合搜索: 变体={len(query_variants)} "
            f"BM25累计={total_bm25_hits} 向量累计={total_vector_hits} "
            f"权重(bm25={bm25_weight:.2f}, vector={vector_weight:.2f}, reason={weight_reason}) "
            f"→ 融合后 {len(top_results)} 条"
        )

        return top_results

    def _get_adaptive_weights(self, query: str, bm25_weight: float,
                              vector_weight: float) -> tuple[float, float, str]:
        """
        查询自适应权重：
        - 规格型号/数字参数密集：提高BM25占比
        - 纯语义描述为主：提高向量占比
        """
        if not bool(getattr(config, "HYBRID_ADAPTIVE_FUSION", True)):
            total = max(bm25_weight + vector_weight, 1e-9)
            return bm25_weight / total, vector_weight / total, "static"

        boost = float(getattr(config, "HYBRID_ADAPTIVE_BOOST", 0.18))
        boost = min(max(boost, 0.0), 0.4)

        pattern_hits = 0
        spec_patterns = [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]
        for p in spec_patterns:
            if re.search(p, query, flags=re.IGNORECASE):
                pattern_hits += 1

        chinese_len = len(re.findall(r"[\u4e00-\u9fff]", query))
        reason = "balanced"
        new_bm25 = bm25_weight
        new_vector = vector_weight

        if pattern_hits >= 2 or (pattern_hits >= 1 and chinese_len <= 20):
            new_bm25 = bm25_weight + boost
            new_vector = vector_weight - boost
            reason = "spec_heavy"
        elif pattern_hits == 0 and chinese_len >= 18:
            new_bm25 = bm25_weight - boost
            new_vector = vector_weight + boost
            reason = "semantic_heavy"

        # 叠加来自用户反馈的全局偏置（快速进化学习）
        feedback_bias = self._get_feedback_bias()
        if feedback_bias != 0:
            new_bm25 += feedback_bias
            new_vector -= feedback_bias
            reason = f"{reason}+feedback"

        # 防止某一路权重过低导致失去召回能力
        new_bm25 = max(new_bm25, 0.1)
        new_vector = max(new_vector, 0.1)
        total = new_bm25 + new_vector
        return new_bm25 / total, new_vector / total, reason

    def _get_feedback_bias(self) -> float:
        """
        基于经验库最近样本计算全局偏置：
        - 规格型清单纠错率更高 -> 往BM25偏
        - 语义型清单纠错率更高 -> 往向量偏
        """
        if not bool(getattr(config, "HYBRID_FEEDBACK_ADAPTIVE_BIAS", True)):
            return 0.0

        refresh_sec = max(int(getattr(config, "HYBRID_FEEDBACK_BIAS_REFRESH_SEC", 300)), 30)
        now = time.time()
        if now - self._feedback_bias_ts < refresh_sec:
            return self._feedback_bias_value

        min_samples = max(int(getattr(config, "HYBRID_FEEDBACK_MIN_SAMPLES", 60)), 20)
        max_bias = float(getattr(config, "HYBRID_FEEDBACK_BIAS_MAX", 0.08))
        max_bias = min(max(max_bias, 0.0), 0.2)

        bias = 0.0
        try:
            exp_db = config.get_experience_db_path()
            if not exp_db.exists():
                self._feedback_bias_value = 0.0
                self._feedback_bias_ts = now
                return 0.0

            conn = _db_connect(exp_db)
            try:
                rows = conn.execute(
                    """
                    SELECT source, bill_text
                    FROM experiences
                    WHERE bill_text IS NOT NULL
                      AND province = ?
                      AND source IN ('user_correction', 'user_confirmed')
                    ORDER BY updated_at DESC
                    LIMIT 2000
                    """
                    , (self.province,)
                ).fetchall()
            finally:
                conn.close()

            if len(rows) < min_samples:
                self._feedback_bias_value = 0.0
                self._feedback_bias_ts = now
                return 0.0

            spec_total = 0
            spec_corr = 0
            sem_total = 0
            sem_corr = 0

            for source, bill_text in rows:
                text = str(bill_text or "").strip()
                if not text:
                    continue
                is_spec = self._is_spec_heavy_text(text)
                is_corr = (source == "user_correction")
                if is_spec:
                    spec_total += 1
                    if is_corr:
                        spec_corr += 1
                else:
                    sem_total += 1
                    if is_corr:
                        sem_corr += 1

            if spec_total >= min_samples // 3 and sem_total >= min_samples // 3:
                spec_err = spec_corr / max(spec_total, 1)
                sem_err = sem_corr / max(sem_total, 1)
                delta = spec_err - sem_err
                # delta>0: 规格型更难，向BM25偏；delta<0: 语义型更难，向向量偏
                bias = max(-max_bias, min(max_bias, delta * 0.25))

        except Exception as e:
            logger.debug(f"反馈偏置计算失败（忽略，不影响主流程）: {e}")
            bias = 0.0

        self._feedback_bias_value = bias
        self._feedback_bias_ts = now
        return bias

    @staticmethod
    def _is_spec_heavy_text(text: str) -> bool:
        """判断文本是否为规格参数主导（DN/回路/型号/参数单位等）。"""
        patterns = [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]
        hits = 0
        for p in patterns:
            if re.search(p, text, flags=re.IGNORECASE):
                hits += 1
        chinese_len = len(re.findall(r"[\u4e00-\u9fff]", text))
        return hits >= 2 or (hits >= 1 and chinese_len <= 20)

    def _build_query_variants(self, query: str, kb_hints: list[str]) -> list[dict]:
        """
        构造少量高价值查询变体，避免纯原始query召回盲区。
        """
        max_variants = int(getattr(config, "HYBRID_QUERY_VARIANTS", 3))
        max_variants = max(max_variants, 1)
        raw_weights = getattr(config, "HYBRID_VARIANT_WEIGHTS", [1.0, 0.75, 0.60])
        if not isinstance(raw_weights, (list, tuple)) or not raw_weights:
            raw_weights = [1.0, 0.75, 0.60]
        variant_weights = [max(float(w), 0.05) for w in raw_weights]

        variants = []
        seen = set()

        def _add(q: str, tag: str):
            qn = re.sub(r"\s+", " ", q or "").strip()
            if not qn or qn in seen:
                return
            idx = len(variants)
            w = variant_weights[idx] if idx < len(variant_weights) else variant_weights[-1] * 0.85
            variants.append({"query": qn, "tag": tag, "weight": w})
            seen.add(qn)

        # V1: 原始query
        _add(query, "raw")

        # V2: 规范化query（去噪、统一分隔符）
        normalized = re.sub(r"[，,。；;、|/\\]+", " ", query)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        _add(normalized, "normalized")

        # V3: 参数强化query（把关键规格参数显式再强调一遍）
        param_tokens = []
        normalized_lower = normalized.lower()
        for p in [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]:
            for m in re.finditer(p, query, flags=re.IGNORECASE):
                token = m.group(0).strip()
                if token and token not in param_tokens and token.lower() not in normalized_lower:
                    param_tokens.append(token)
        if param_tokens:
            _add(f"{normalized} {' '.join(param_tokens[:4])}", "param_boost")

        # 额外兜底：若仍不够且有知识库提示，拼接一个知识变体
        if kb_hints and len(variants) < max_variants:
            _add(f"{query} {kb_hints[0]}", "kb_hint")

        return variants[:max_variants]

    def _merge_single_engine_runs(self, runs: list[dict], engine: str,
                                  k: int = 60) -> list[dict]:
        """
        单路搜索（仅BM25或仅向量）时的多查询RRF融合。
        """
        if engine not in ("bm25", "vector"):
            raise ValueError(f"unsupported engine: {engine}")

        score_key = "bm25_score" if engine == "bm25" else "vector_score"
        rank_key = "bm25_rank" if engine == "bm25" else "vector_rank"
        rrf_key = "bm25_rrf_score" if engine == "bm25" else "vector_rrf_score"

        score_map = {}
        best_rank_map = {}
        best_result_map = {}

        for run in runs:
            run_weight = max(float(run.get("weight", 1.0)), 0.05)
            for rank, result in enumerate(run.get("results", []), start=1):
                db_id = result["id"]
                score_map[db_id] = score_map.get(db_id, 0.0) + run_weight / (k + rank)
                if db_id not in best_rank_map or rank < best_rank_map[db_id]:
                    best_rank_map[db_id] = rank
                    best_result_map[db_id] = result

        merged = []
        for db_id, rrf_score in score_map.items():
            result = dict(best_result_map[db_id])
            result[rrf_key] = rrf_score
            result[rank_key] = best_rank_map[db_id]
            if score_key not in result:
                result[score_key] = None
            merged.append(result)

        merged.sort(key=lambda x: x[rrf_key], reverse=True)
        return merged

    def _rrf_fusion_multi_query(self, bm25_runs: list[dict], vector_runs: list[dict],
                                bm25_weight: float, vector_weight: float,
                                k: int = 60) -> list[dict]:
        """
        多查询 + 双路引擎的加权RRF融合。
        """
        bm25_rank_maps = []
        vector_rank_maps = []
        all_ids = set()

        for run in bm25_runs:
            rank_map = {}
            for rank, result in enumerate(run.get("results", []), start=1):
                db_id = result["id"]
                rank_map[db_id] = (rank, result)
                all_ids.add(db_id)
            bm25_rank_maps.append((max(float(run.get("weight", 1.0)), 0.05), rank_map))

        for run in vector_runs:
            rank_map = {}
            for rank, result in enumerate(run.get("results", []), start=1):
                db_id = result["id"]
                rank_map[db_id] = (rank, result)
                all_ids.add(db_id)
            vector_rank_maps.append((max(float(run.get("weight", 1.0)), 0.05), rank_map))

        scored_results = []
        for db_id in all_ids:
            bm25_rank = None
            vector_rank = None
            rrf_score = 0.0
            base_result = None
            bm25_score = None
            vector_score = None

            for run_weight, rank_map in bm25_rank_maps:
                if db_id in rank_map:
                    rank, result = rank_map[db_id]
                    rrf_score += (bm25_weight * run_weight) / (k + rank)
                    if bm25_rank is None or rank < bm25_rank:
                        bm25_rank = rank
                        bm25_score = result.get("bm25_score", 0)
                    if base_result is None:
                        base_result = result

            for run_weight, rank_map in vector_rank_maps:
                if db_id in rank_map:
                    rank, result = rank_map[db_id]
                    rrf_score += (vector_weight * run_weight) / (k + rank)
                    if vector_rank is None or rank < vector_rank:
                        vector_rank = rank
                        vector_score = result.get("vector_score", 0)
                    # 优先以向量结果作为基础，信息通常更完整
                    base_result = result

            if base_result is None:
                continue

            result = dict(base_result)
            result["hybrid_score"] = rrf_score
            result["bm25_rank"] = bm25_rank
            result["vector_rank"] = vector_rank
            result["bm25_score"] = bm25_score
            result["vector_score"] = vector_score
            scored_results.append(result)

        scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
        return scored_results

    def _rrf_fusion(self, bm25_results: list[dict], vector_results: list[dict],
                    bm25_weight: float, vector_weight: float,
                    k: int = 60) -> list[dict]:
        """
        RRF（Reciprocal Rank Fusion）融合算法

        原理：
        - 对于每条结果，根据它在BM25和向量搜索中的排名计算融合分数
        - RRF_score = bm25_weight / (k + bm25_rank) + vector_weight / (k + vector_rank)
        - k=60是标准值，作用是平滑排名差异，避免排名第1的结果权重过大

        参数:
            bm25_results: BM25搜索结果（已按分数降序排列）
            vector_results: 向量搜索结果（已按分数降序排列）
            bm25_weight: BM25权重（默认0.3）
            vector_weight: 向量权重（默认0.7）
            k: RRF常数（默认60，标准值）

        返回:
            融合排序后的结果列表
        """
        # 建立数据库ID → 排名的映射
        # rank从1开始（排名第1是最好的）
        bm25_rank_map = {}  # {db_id: (rank, result_dict)}
        for rank, result in enumerate(bm25_results, start=1):
            db_id = result["id"]
            bm25_rank_map[db_id] = (rank, result)

        vector_rank_map = {}  # {db_id: (rank, result_dict)}
        for rank, result in enumerate(vector_results, start=1):
            db_id = result["id"]
            vector_rank_map[db_id] = (rank, result)

        # 收集所有出现过的ID（去重合并）
        all_ids = set(bm25_rank_map.keys()) | set(vector_rank_map.keys())

        # 计算每条结果的RRF融合分数
        scored_results = []
        for db_id in all_ids:
            bm25_rank = None
            vector_rank = None
            rrf_score = 0.0

            # BM25排名贡献
            if db_id in bm25_rank_map:
                bm25_rank = bm25_rank_map[db_id][0]
                rrf_score += bm25_weight / (k + bm25_rank)

            # 向量排名贡献
            if db_id in vector_rank_map:
                vector_rank = vector_rank_map[db_id][0]
                rrf_score += vector_weight / (k + vector_rank)

            # 选一个完整的结果字典作为基础（优先用向量的，因为它有更多语义信息）
            if db_id in vector_rank_map:
                result = dict(vector_rank_map[db_id][1])  # 复制一份，避免修改原数据
            else:
                result = dict(bm25_rank_map[db_id][1])

            # 添加融合信息
            result["hybrid_score"] = rrf_score
            result["bm25_rank"] = bm25_rank        # 在BM25中的排名（None表示未出现）
            result["vector_rank"] = vector_rank     # 在向量搜索中的排名（None表示未出现）
            result["bm25_score"] = bm25_rank_map[db_id][1].get("bm25_score", 0) if db_id in bm25_rank_map else None
            result["vector_score"] = vector_rank_map[db_id][1].get("vector_score", 0) if db_id in vector_rank_map else None

            scored_results.append(result)

        # 按RRF分数降序排列
        scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)

        return scored_results

    def search_bm25_only(self, query: str, top_k: int = None) -> list[dict]:
        """仅使用BM25搜索（调试用）"""
        top_k = top_k or config.BM25_TOP_K
        return self.bm25_engine.search(query, top_k=top_k)

    def search_vector_only(self, query: str, top_k: int = None) -> list[dict]:
        """仅使用向量搜索（调试用）"""
        top_k = top_k or config.VECTOR_TOP_K
        return self.vector_engine.search(query, top_k=top_k)

    def get_status(self) -> dict:
        """获取搜索引擎状态"""
        status = {
            "province": self.province,
            "bm25_ready": False,
            "vector_ready": False,
            "bm25_count": 0,
            "vector_count": 0,
        }

        try:
            self.bm25_engine.ensure_index()
            status["bm25_ready"] = self.bm25_engine.bm25 is not None
            status["bm25_count"] = len(self.bm25_engine.quota_ids) if self.bm25_engine.bm25 else 0
        except Exception as e:
            logger.debug(f"HybridSearcher状态检查-BM25失败: {e}")

        try:
            status["vector_count"] = self.vector_engine.get_index_count()
            status["vector_ready"] = status["vector_count"] > 0
        except Exception as e:
            logger.debug(f"HybridSearcher状态检查-向量引擎失败: {e}")

        return status


# ================================================================
# 命令行入口：测试混合搜索
# ================================================================

if __name__ == "__main__":
    searcher = HybridSearcher()

    # 检查引擎状态
    status = searcher.get_status()
    logger.info(f"搜索引擎状态: BM25={status['bm25_count']}条, 向量={status['vector_count']}条")

    # 测试搜索
    test_queries = [
        "镀锌钢管DN150沟槽连接管道安装",
        "干式变压器800kva安装",
        "电力电缆截面185敷设",
        "柔性防水套管DN125制作",
        "离心泵消防泵安装",
    ]

    for query in test_queries:
        results = searcher.search(query, top_k=5)
        logger.info(f"\n混合搜索: '{query}'")
        for r in results:
            bm25_info = f"BM25#{r['bm25_rank']}" if r.get('bm25_rank') else "BM25:无"
            vec_info = f"向量#{r['vector_rank']}" if r.get('vector_rank') else "向量:无"
            logger.info(
                f"  [{r['hybrid_score']:.6f}] {r['quota_id']} | "
                f"{r['name'][:50]} | {r['unit']} | "
                f"{bm25_info} {vec_info}"
            )
