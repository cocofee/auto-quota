"""
Reranker 重排模块

功能：对混合搜索返回的候选定额进行语义重排
原理：交叉编码器（CrossEncoder）把查询和候选拼在一起逐字对比，
      比向量搜索的"双塔模型"理解更深入

模型：BAAI/bge-reranker-v2-m3（568M参数，FP16约2GB显存）
位置：插在混合搜索和参数验证之间

注意：Reranker只负责语义类型匹配（"电缆穿导管敷设"vs"预制分支电缆敷设"），
      数字参数的匹配交给 param_validator 处理。
      因此传给Reranker的query会自动去掉数字参数，避免数字匹配干扰排序。

使用方式：
    reranker = Reranker()
    candidates = reranker.rerank(query_text, candidates, top_k=10)
"""

import re

from loguru import logger

import config


class Reranker:
    """语义重排器 - 用交叉编码器对候选定额重新排序"""

    def __init__(self, model_name: str = None):
        """
        参数:
            model_name: 模型名称或本地路径，默认用 config 配置
        """
        self.model_name = model_name or config.RERANKER_MODEL_NAME
        # 延迟加载（和 vector_engine 一样，不用时不占显存）
        self._model = None

    @property
    def model(self):
        """从全局 ModelCache 获取 Reranker 模型（避免重复加载）"""
        if self._model is None:
            from src.model_cache import ModelCache
            self._model = ModelCache.get_reranker_model()
        return self._model

    def rerank(self, query: str, candidates: list[dict],
               top_k: int = None) -> list[dict]:
        """
        对候选定额列表进行语义重排

        参数:
            query: 查询文本（清单名称+描述的搜索文本）
            candidates: 混合搜索返回的候选列表，每条是字典
                        必须包含 "name" 字段（定额名称）
            top_k: 重排后保留的候选数，None 表示全部保留

        返回:
            重排后的候选列表，每条增加 rerank_score 字段
            如果Reranker失败，候选会被标记 reranker_failed=True
        """
        if not candidates:
            return candidates

        if top_k is None:
            top_k = config.RERANKER_TOP_K

        # 向量搜索关闭时跳过重排（模型不可用）
        if not getattr(config, "VECTOR_ENABLED", True):
            return candidates[:top_k] if top_k else candidates

        # 检查模型是否可用（冷却期内返回None）
        model = self.model
        if model is None:
            logger.warning("Reranker模型不可用（加载失败或冷却中），跳过重排")
            for c in candidates:
                c["reranker_failed"] = True
            return candidates[:top_k] if top_k else candidates

        # 构造 [查询, 候选名称] 对
        # 用定额名称做重排（最有区分度的字段）
        # 关键：去掉query中的数字参数，让Reranker专注于语义类型匹配
        # 例如："电缆穿导管敷设 电缆截面 25" → "电缆穿导管敷设 电缆截面"
        # 避免"25"导致Reranker把"预制分支电缆25"排到"电缆穿导管敷设35"前面
        # 注意：只去query中的数字，候选名称保留原样（数字是有意义的档位信息）
        rerank_query = self._strip_numbers(query)

        pairs = []
        for c in candidates:
            doc_text = c.get("name", "")
            pairs.append([rerank_query, doc_text])

        # 用交叉编码器打分
        try:
            scores = model.predict(pairs)
        except Exception as e:
            logger.error(f"Reranker打分失败: {e}")
            # 打分失败：标记候选为"未重排"（下游FastPath不应信任排序）
            for c in candidates:
                c["reranker_failed"] = True
            return candidates[:top_k] if top_k else candidates

        # 给每条候选加上 rerank_score
        for i, c in enumerate(candidates):
            c["rerank_score"] = float(scores[i])

        # 按 rerank_score 降序排列
        ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)

        # 截取 top_k
        if top_k and len(ranked) > top_k:
            ranked = ranked[:top_k]

        return ranked

    @staticmethod
    def _strip_numbers(text: str) -> str:
        """
        去掉文本中的独立数字和数字参数，只保留中文和英文描述

        目的：让 Reranker 专注于语义类型匹配，不受数字干扰
        例如：
          "电缆穿导管敷设 电缆截面 25" → "电缆穿导管敷设 电缆截面"
          "配管 刚性难燃线管 公称直径 20 暗配" → "配管 刚性难燃线管 公称直径 暗配"
          "配电箱墙上(柱上)明装 规格(回路以内) 48" → "配电箱墙上(柱上)明装 规格(回路以内)"
        """
        # 去掉独立的数字（包括小数和乘号组合，如 "25", "2.5", "4×70"）
        text = re.sub(r'\b[\d.]+[×/][\d.]+\b', '', text)  # 复合参数：4×70, 50/57
        text = re.sub(r'\b[\d.]+\b', '', text)              # 纯数字：25, 2.5, 630
        # 清理多余空格
        text = re.sub(r'\s+', ' ', text).strip()
        return text
