"""
全局模型缓存

功能：
1. 向量模型（BGE）和Reranker模型在整个进程中只加载一次
2. VectorEngine、ExperienceDB、Reranker 都从这里获取模型
3. 避免重复加载导致的15-20秒额外耗时

使用方式：
    from src.model_cache import ModelCache
    model = ModelCache.get_vector_model()      # 获取BGE向量模型
    reranker = ModelCache.get_reranker_model()  # 获取Reranker模型
"""

import threading

from loguru import logger

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class ModelCache:
    """全局模型单例缓存，避免同一个模型在不同模块中重复加载"""

    _vector_model = None
    _reranker_model = None
    _lock = threading.Lock()  # 线程安全（防止并发场景下重复加载）

    @classmethod
    def get_vector_model(cls):
        """获取BGE向量模型（全局单例，首次调用时加载）"""
        if cls._vector_model is None:
            with cls._lock:
                # 双重检查（进入锁之后再确认一次）
                if cls._vector_model is None:
                    logger.info(f"[ModelCache] 加载向量模型: {config.VECTOR_MODEL_NAME}")
                    try:
                        from sentence_transformers import SentenceTransformer
                        cls._vector_model = SentenceTransformer(
                            config.VECTOR_MODEL_NAME,
                            device="cuda"
                        )
                        logger.info("[ModelCache] 向量模型加载成功（GPU模式）")
                    except Exception as e:
                        logger.warning(f"[ModelCache] GPU加载失败({e})，切换到CPU模式")
                        from sentence_transformers import SentenceTransformer
                        cls._vector_model = SentenceTransformer(
                            config.VECTOR_MODEL_NAME,
                            device="cpu"
                        )
                        logger.info("[ModelCache] 向量模型加载成功（CPU模式）")
        return cls._vector_model

    @classmethod
    def get_reranker_model(cls):
        """获取Reranker交叉编码器模型（全局单例，首次调用时加载）"""
        if cls._reranker_model is None:
            with cls._lock:
                if cls._reranker_model is None:
                    model_name = config.RERANKER_MODEL_NAME
                    logger.info(f"[ModelCache] 加载Reranker模型: {model_name}")
                    try:
                        from sentence_transformers import CrossEncoder
                        cls._reranker_model = CrossEncoder(
                            model_name,
                            max_length=512,
                            device="cuda",
                        )
                        logger.info("[ModelCache] Reranker模型加载成功（GPU模式）")
                    except Exception as e:
                        logger.warning(f"[ModelCache] Reranker GPU加载失败({e})，尝试CPU")
                        from sentence_transformers import CrossEncoder
                        cls._reranker_model = CrossEncoder(
                            model_name,
                            max_length=512,
                            device="cpu",
                        )
                        logger.info("[ModelCache] Reranker模型加载成功（CPU模式）")
        return cls._reranker_model

    @classmethod
    def preload_all(cls):
        """预加载所有模型（在初始化阶段调用，避免第一条清单等待）"""
        logger.info("[ModelCache] 开始预加载模型...")
        cls.get_vector_model()
        cls.get_reranker_model()
        logger.info("[ModelCache] 所有模型预加载完成")
