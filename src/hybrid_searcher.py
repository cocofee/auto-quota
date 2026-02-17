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

import sqlite3
from pathlib import Path

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.bm25_engine import BM25Engine
from src.vector_engine import VectorEngine


class HybridSearcher:
    """混合搜索引擎：BM25 + 向量搜索，RRF融合"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置
        """
        self.province = province or config.CURRENT_PROVINCE

        # 两个搜索引擎（延迟初始化）
        self._bm25_engine = None
        self._vector_engine = None

        # 通用知识库（延迟初始化）
        self._universal_kb = None

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
        bm25_weight = bm25_weight or config.BM25_WEIGHT
        vector_weight = vector_weight or config.VECTOR_WEIGHT

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
        # 第1步：分别执行两路搜索
        # ============================================================

        # 构建增强搜索词：原始query + 通用知识库提供的定额名称关键词
        # BM25对精确关键词敏感，增强词能帮它找到更准确的候选
        enhanced_query = query
        if kb_hints:
            # 取第一个定额名称模式作为补充（避免关键词过多反而稀释）
            enhanced_query = query + " " + kb_hints[0]

        # BM25关键词搜索（使用增强搜索词，带册号过滤）
        bm25_results = []
        try:
            bm25_results = self.bm25_engine.search(enhanced_query, top_k=config.BM25_TOP_K, books=books)
            logger.debug(f"BM25搜索返回 {len(bm25_results)} 条结果")
        except Exception as e:
            logger.warning(f"BM25搜索失败: {e}")

        # 向量语义搜索（使用原始query，带册号过滤）
        vector_results = []
        try:
            vector_results = self.vector_engine.search(query, top_k=config.VECTOR_TOP_K, books=books)
            logger.debug(f"向量搜索返回 {len(vector_results)} 条结果")
        except Exception as e:
            logger.warning(f"向量搜索失败: {e}")

        # 如果两路都没有结果，返回空
        if not bm25_results and not vector_results:
            logger.warning(f"两路搜索均无结果: '{query}'")
            return []

        # 如果只有一路有结果，直接返回那一路的结果
        if not bm25_results:
            for r in vector_results[:top_k]:
                r["hybrid_score"] = r.get("vector_score", 0)
                r["bm25_rank"] = None
                r["vector_rank"] = vector_results.index(r) + 1
            return vector_results[:top_k]

        if not vector_results:
            for r in bm25_results[:top_k]:
                r["hybrid_score"] = r.get("bm25_score", 0)
                r["bm25_rank"] = bm25_results.index(r) + 1
                r["vector_rank"] = None
            return bm25_results[:top_k]

        # ============================================================
        # 第2步：RRF融合排序
        # ============================================================

        merged = self._rrf_fusion(
            bm25_results=bm25_results,
            vector_results=vector_results,
            bm25_weight=bm25_weight,
            vector_weight=vector_weight,
            k=config.RRF_K,
        )

        # 取Top K
        top_results = merged[:top_k]

        logger.debug(
            f"混合搜索: BM25({len(bm25_results)}条) + "
            f"向量({len(vector_results)}条) → "
            f"融合后 {len(top_results)} 条"
        )

        return top_results

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
        except Exception:
            pass

        try:
            status["vector_count"] = self.vector_engine.get_index_count()
            status["vector_ready"] = status["vector_count"] > 0
        except Exception:
            pass

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
