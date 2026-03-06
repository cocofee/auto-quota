"""
全局模型和连接缓存

功能：
1. 向量模型（BGE）和Reranker模型在整个进程中只加载一次
2. ChromaDB客户端按路径缓存，避免多模块各自创建导致连接冲突
3. VectorEngine、ExperienceDB、UniversalKB 都从这里获取模型和客户端

使用方式：
    from src.model_cache import ModelCache
    model = ModelCache.get_vector_model()           # 获取BGE向量模型
    reranker = ModelCache.get_reranker_model()       # 获取Reranker模型
    client = ModelCache.get_chroma_client(path)      # 获取ChromaDB客户端（按路径缓存）
"""

import threading
from pathlib import Path

from loguru import logger

import config


class ModelCache:
    """全局模型和连接单例缓存

    防御机制：模型加载失败后进入冷却期（60秒），避免每条清单都重试加载
    （GPU瞬时故障时，每次重试加载要13秒，548条清单会浪费2小时）
    """

    _vector_model = None
    _reranker_model = None
    _chroma_clients = {}  # {路径字符串: chromadb.PersistentClient}
    _lock = threading.Lock()  # 线程安全

    # 失败冷却机制：连续失败后暂停重试
    _vector_fail_count = 0       # 连续加载失败次数
    _vector_fail_time = 0.0      # 上次失败时间戳
    _reranker_fail_count = 0
    _reranker_fail_time = 0.0
    _FAIL_COOLDOWN = 60.0        # 冷却期60秒
    _FAIL_MAX_RETRIES = 3        # 连续失败3次后进入冷却

    @classmethod
    def _in_cooldown(cls, fail_count: int, fail_time: float) -> bool:
        """判断是否处于加载失败冷却期"""
        if fail_count < cls._FAIL_MAX_RETRIES:
            return False
        import time
        elapsed = time.time() - fail_time
        if elapsed < cls._FAIL_COOLDOWN:
            return True  # 还在冷却中
        return False  # 冷却结束，可以重试

    @classmethod
    def get_vector_model(cls):
        """获取BGE向量模型（全局单例，首次调用时加载）

        加载失败3次后进入60秒冷却期，期间返回None避免反复重试
        """
        if cls._vector_model is not None:
            return cls._vector_model

        # 冷却期内直接返回None（不再浪费时间重试）
        if cls._in_cooldown(cls._vector_fail_count, cls._vector_fail_time):
            return None

        with cls._lock:
            # 双重检查（进入锁之后再确认一次）
            if cls._vector_model is not None:
                return cls._vector_model
            if cls._in_cooldown(cls._vector_fail_count, cls._vector_fail_time):
                return None

            logger.info(f"[ModelCache] 加载向量模型: {config.VECTOR_MODEL_NAME}")
            try:
                from sentence_transformers import SentenceTransformer
                cls._vector_model = SentenceTransformer(
                    config.VECTOR_MODEL_NAME,
                    device="cuda"
                )
                logger.info("[ModelCache] 向量模型加载成功（GPU模式）")
                cls._vector_fail_count = 0  # 成功则重置计数
            except Exception as e:
                logger.warning(f"[ModelCache] GPU加载失败({e})，切换到CPU模式")
                # GPU失败后清理CUDA状态再加载CPU版本
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                try:
                    from sentence_transformers import SentenceTransformer
                    cls._vector_model = SentenceTransformer(
                        config.VECTOR_MODEL_NAME,
                        device="cpu"
                    )
                    logger.info("[ModelCache] 向量模型加载成功（CPU模式）")
                    cls._vector_fail_count = 0
                except Exception as e2:
                    # GPU和CPU都失败，记录失败并进入冷却
                    import time
                    cls._vector_fail_count += 1
                    cls._vector_fail_time = time.time()
                    logger.error(f"[ModelCache] 向量模型加载完全失败（第{cls._vector_fail_count}次）: {e2}")
                    if cls._vector_fail_count >= cls._FAIL_MAX_RETRIES:
                        logger.warning(f"[ModelCache] 向量模型连续失败{cls._vector_fail_count}次，"
                                       f"冷却{cls._FAIL_COOLDOWN}秒后重试")
        return cls._vector_model

    @classmethod
    def get_reranker_model(cls):
        """获取Reranker交叉编码器模型（全局单例，首次调用时加载）

        加载失败3次后进入60秒冷却期
        """
        if cls._reranker_model is not None:
            return cls._reranker_model

        if cls._in_cooldown(cls._reranker_fail_count, cls._reranker_fail_time):
            return None

        with cls._lock:
            if cls._reranker_model is not None:
                return cls._reranker_model
            if cls._in_cooldown(cls._reranker_fail_count, cls._reranker_fail_time):
                return None

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
                cls._reranker_fail_count = 0
            except Exception as e:
                logger.warning(f"[ModelCache] Reranker GPU加载失败({e})，尝试CPU")
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                try:
                    from sentence_transformers import CrossEncoder
                    cls._reranker_model = CrossEncoder(
                        model_name,
                        max_length=512,
                        device="cpu",
                    )
                    logger.info("[ModelCache] Reranker模型加载成功（CPU模式）")
                    cls._reranker_fail_count = 0
                except Exception as e2:
                    import time
                    cls._reranker_fail_count += 1
                    cls._reranker_fail_time = time.time()
                    logger.error(f"[ModelCache] Reranker加载完全失败（第{cls._reranker_fail_count}次）: {e2}")
                    if cls._reranker_fail_count >= cls._FAIL_MAX_RETRIES:
                        logger.warning(f"[ModelCache] Reranker连续失败{cls._reranker_fail_count}次，"
                                       f"冷却{cls._FAIL_COOLDOWN}秒后重试")
        return cls._reranker_model

    @classmethod
    def get_chroma_client(cls, path: str):
        """获取ChromaDB客户端（按路径缓存，同路径共用一个客户端）

        解决的问题：之前VectorEngine/ExperienceDB/UniversalKB各自创建PersistentClient，
        GPU崩溃时一个client关闭会导致其他client也连带失败（级联崩溃）。
        现在全局缓存，同一路径只创建一次。如果client失效，自动重建。
        """
        path_str = str(path)

        # 防御：拒绝 None 或空路径，避免在当前目录创建 "None/" 等垃圾目录
        if not path_str or path_str == "None":
            raise ValueError(f"ChromaDB路径无效: {path!r}，请检查调用方是否正确传入了路径")

        # 快速路径：已缓存且可用
        if path_str in cls._chroma_clients:
            client = cls._chroma_clients[path_str]
            try:
                # 检查client是否还活着（轻量操作）
                client.heartbeat()
                return client
            except Exception:
                # client已失效，移除缓存，下面重建
                logger.warning(f"[ModelCache] ChromaDB客户端失效，重建: {path_str}")
                del cls._chroma_clients[path_str]

        # 加锁创建
        with cls._lock:
            # 双重检查
            if path_str in cls._chroma_clients:
                return cls._chroma_clients[path_str]

            import chromadb
            Path(path_str).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=path_str)
            cls._chroma_clients[path_str] = client
            logger.debug(f"[ModelCache] ChromaDB客户端创建: {path_str}")
            return client

    @classmethod
    def preload_all(cls):
        """预加载所有模型（在初始化阶段调用，避免第一条清单等待）"""
        import config
        if not getattr(config, "VECTOR_ENABLED", True):
            logger.info("[ModelCache] VECTOR_ENABLED=false，跳过向量模型和Reranker预加载（纯BM25模式）")
            return
        logger.info("[ModelCache] 开始预加载模型...")
        cls.get_vector_model()
        cls.get_reranker_model()
        # 启动自检提示：如果模型加载失败，提前告知用户
        if cls._vector_model is None:
            logger.warning("[ModelCache] 向量模型不可用，本轮将仅使用BM25关键词搜索（精度有所下降）")
        if cls._reranker_model is None:
            logger.warning("[ModelCache] Reranker模型不可用，本轮将跳过语义重排（排序精度有所下降）")
        logger.info("[ModelCache] 所有模型预加载完成")

    @classmethod
    def get_degradation_summary(cls, agent_matcher=None) -> dict:
        """获取本轮运行的降级统计摘要（供日志汇总用）"""
        from src.vector_engine import VectorEngine
        return {
            "vector_available": cls._vector_model is not None,
            "reranker_available": cls._reranker_model is not None,
            "vector_skip_count": VectorEngine._model_skip_count,
            "llm_circuit_open": agent_matcher._llm_circuit_open if agent_matcher else False,
            "llm_consecutive_fails": agent_matcher._llm_consecutive_fails if agent_matcher else 0,
        }
