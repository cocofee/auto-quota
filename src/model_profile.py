# -*- coding: utf-8 -*-
"""
向量模型配置文件（VectorModelProfile）

集中管理不同向量模型的差异（编码方式、加载参数、维度、索引目录），
业务代码只调 encode_queries() / encode_documents()，不感知BGE/Qwen3细节。

支持的模型：
- bge: BAAI/bge-large-zh-v1.5（768维，查询需加中文前缀）
- qwen3: Qwen3-Embedding-0.6B 微调版（1024维，无前缀）

切换方法：.env 里设 VECTOR_MODEL_KEY=qwen3（或bge）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger


@dataclass
class VectorModelProfile:
    """一个向量模型的完整配置"""
    key: str                    # 稳定标识符，用于索引目录命名（如"bge"、"qwen3"）
    model_name: str             # HuggingFace模型名或本地路径
    embedding_dim: int          # 向量维度（768 or 1024）
    query_prefix: str           # 查询端前缀（BGE需要，Qwen3为空）
    doc_prefix: str = ""        # 文档端前缀（目前都为空）
    load_kwargs: dict = field(default_factory=dict)  # GPU加载额外参数
    cpu_load_kwargs: dict = field(default_factory=dict)  # CPU回退时的参数


# ============================================================
# 预定义的模型配置
# ============================================================

# BGE基线模型
_BGE_PROFILE = VectorModelProfile(
    key="bge",
    model_name="BAAI/bge-large-zh-v1.5",
    embedding_dim=768,
    query_prefix="为这个句子生成表示以用于检索中文文档: ",
    load_kwargs={},
    cpu_load_kwargs={},
)

# Qwen3微调模型（v3，分层采样20万条训练，含清单库/行业数据）
_QWEN3_PROFILE = VectorModelProfile(
    key="qwen3",
    model_name="models/qwen3-embedding-quota-v3",
    embedding_dim=1024,
    query_prefix="",  # Qwen3不需要查询前缀
    load_kwargs={
        "model_kwargs": {"torch_dtype": "bfloat16"},
    },
    cpu_load_kwargs={},  # CPU回退时不强制bf16
)

# 所有支持的模型注册表
_PROFILES = {
    "bge": _BGE_PROFILE,
    "qwen3": _QWEN3_PROFILE,
}


def get_active_profile() -> VectorModelProfile:
    """获取当前激活的向量模型配置

    读取环境变量 VECTOR_MODEL_KEY（默认"bge"），返回对应的Profile。
    也支持通过 VECTOR_MODEL_NAME 覆盖模型路径（高级用法）。
    """
    key = os.getenv("VECTOR_MODEL_KEY", "bge").lower()

    if key not in _PROFILES:
        # 未知的key，回退到BGE
        logger.warning(
            f"未知的VECTOR_MODEL_KEY={key}，回退到bge"
        )
        key = "bge"

    profile = _PROFILES[key]

    # 允许环境变量覆盖模型路径（比如指定不同版本的微调模型）
    custom_name = os.getenv("VECTOR_MODEL_NAME")
    if custom_name:
        profile = VectorModelProfile(
            key=profile.key,
            model_name=custom_name,
            embedding_dim=profile.embedding_dim,
            query_prefix=profile.query_prefix,
            doc_prefix=profile.doc_prefix,
            load_kwargs=profile.load_kwargs,
            cpu_load_kwargs=profile.cpu_load_kwargs,
        )

    return profile


def encode_queries(model, texts: list[str], profile: Optional[VectorModelProfile] = None) -> np.ndarray:
    """编码查询文本（统一入口，自动处理不同模型的前缀差异）

    Args:
        model: SentenceTransformer模型实例
        texts: 查询文本列表
        profile: 模型配置（None则自动获取当前激活配置）

    Returns:
        归一化后的向量矩阵 (n, dim)
    """
    if profile is None:
        profile = get_active_profile()

    # 加前缀（BGE需要，Qwen3为空所以等于不加）
    if profile.query_prefix:
        texts = [profile.query_prefix + t for t in texts]

    return model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def encode_documents(model, texts: list[str], profile: Optional[VectorModelProfile] = None,
                     batch_size: int = 64, show_progress: bool = False) -> np.ndarray:
    """编码文档文本（统一入口）

    Args:
        model: SentenceTransformer模型实例
        texts: 文档文本列表
        profile: 模型配置
        batch_size: 批大小
        show_progress: 是否显示进度条

    Returns:
        归一化后的向量矩阵 (n, dim)
    """
    if profile is None:
        profile = get_active_profile()

    # 加前缀（目前文档端两个模型都不加）
    if profile.doc_prefix:
        texts = [profile.doc_prefix + t for t in texts]

    return model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
    )
