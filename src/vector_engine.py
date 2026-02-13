"""
向量搜索引擎
功能：
1. 加载BGE-large-zh-v1.5模型（中文向量模型，本地GPU运行）
2. 将所有定额文本向量化并存入ChromaDB
3. 支持语义搜索查询，返回Top K相似定额

向量搜索的优势：能理解语义（"水泵"和"离心泵"是相关的），
而不只是匹配关键词。与BM25互补使用效果最好。
"""

import sqlite3
from pathlib import Path

from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class VectorEngine:
    """BGE向量搜索引擎（基于ChromaDB）"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置
        """
        self.province = province or config.CURRENT_PROVINCE
        self.db_path = config.get_quota_db_path(self.province)
        self.chroma_dir = config.get_chroma_quota_dir(self.province)

        # 模型和数据库对象（延迟加载）
        self._model = None
        self._collection = None
        self._chroma_client = None

    @property
    def model(self):
        """延迟加载BGE向量模型（首次调用时加载，占用约2GB显存）"""
        if self._model is None:
            logger.info(f"正在加载向量模型: {config.VECTOR_MODEL_NAME}")
            logger.info("首次加载需要下载模型文件（约1.3GB），请耐心等待...")
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    config.VECTOR_MODEL_NAME,
                    device="cuda"  # 使用GPU加速
                )
                logger.info("向量模型加载成功（GPU模式）")
            except Exception as e:
                logger.warning(f"GPU加载失败({e})，切换到CPU模式")
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    config.VECTOR_MODEL_NAME,
                    device="cpu"
                )
                logger.info("向量模型加载成功（CPU模式，速度较慢）")
        return self._model

    @property
    def collection(self):
        """延迟初始化ChromaDB collection"""
        if self._collection is None:
            import chromadb
            self.chroma_dir.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=str(self.chroma_dir))
            self._collection = self._chroma_client.get_or_create_collection(
                name="quotas",
                metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
            )
        return self._collection

    def build_index(self, batch_size: int = 256):
        """
        构建向量索引：读取所有定额 → BGE向量化 → 存入ChromaDB

        参数:
            batch_size: 批量向量化的大小（越大越快，但更耗显存）
        """
        logger.info("开始构建向量索引...")

        # 从数据库读取数据
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, search_text FROM quotas WHERE search_text IS NOT NULL")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            logger.error("数据库中没有定额数据，请先运行定额导入")
            return

        total = len(rows)
        logger.info(f"共{total}条定额需要向量化")

        # 清空旧索引（重建时）
        import chromadb
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=str(self.chroma_dir))
        # 删除并重建collection
        try:
            self._chroma_client.delete_collection("quotas")
        except Exception:
            pass
        self._collection = self._chroma_client.create_collection(
            name="quotas",
            metadata={"hnsw:space": "cosine"}
        )

        # 分批向量化并存入ChromaDB
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_rows = rows[start:end]

            ids = [str(row["id"]) for row in batch_rows]
            texts = [row["search_text"] for row in batch_rows]

            # BGE模型向量化
            # BGE模型建议在查询文本前加"为这个句子生成表示以用于检索"
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=True  # L2归一化，配合余弦相似度
            )

            # 存入ChromaDB
            self.collection.add(
                ids=ids,
                documents=texts,
                embeddings=embeddings.tolist(),
            )

            logger.info(f"  向量化进度: {end}/{total} ({end * 100 // total}%)")

        logger.info(f"向量索引构建完成: {total}条 → {self.chroma_dir}")

    def search(self, query: str, top_k: int = None) -> list[dict]:
        """
        向量语义搜索

        参数:
            query: 搜索文本（清单描述）
            top_k: 返回前K条结果

        返回:
            匹配结果列表，每条包含 {id, quota_id, name, unit, vector_score, ...}
        """
        top_k = top_k or config.VECTOR_TOP_K

        # 检查索引是否存在
        if self.collection.count() == 0:
            logger.error("向量索引为空，请先运行 build_index()")
            return []

        # 对查询文本进行向量化
        # BGE模型官方建议：检索时在query前加提示词
        query_prefix = "为这个句子生成表示以用于检索中文文档: "
        query_embedding = self.model.encode(
            [query_prefix + query],
            normalize_embeddings=True
        )

        # ChromaDB查询
        results = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=top_k,
        )

        if not results or not results["ids"] or not results["ids"][0]:
            return []

        # 获取匹配的数据库ID和相似度分数
        matched_ids = results["ids"][0]
        # ChromaDB返回的distances是距离（越小越相似），转为相似度分数
        distances = results["distances"][0] if results["distances"] else [0] * len(matched_ids)
        # 余弦距离 → 相似度分数（1-distance，因为用的cosine space）
        scores = [1 - d for d in distances]

        # 查询数据库获取完整定额信息
        db_ids = [int(mid) for mid in matched_ids]
        score_map = {int(mid): s for mid, s in zip(matched_ids, scores)}

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        placeholders = ",".join(["?"] * len(db_ids))
        cursor.execute(f"SELECT * FROM quotas WHERE id IN ({placeholders})", db_ids)
        rows = {row["id"]: dict(row) for row in cursor.fetchall()}
        conn.close()

        # 组装结果，保持分数排序
        results = []
        for db_id in db_ids:
            if db_id in rows:
                result = rows[db_id]
                result["vector_score"] = score_map[db_id]
                results.append(result)

        # 按相似度降序排序
        results.sort(key=lambda x: x["vector_score"], reverse=True)

        return results

    def get_index_count(self) -> int:
        """获取向量索引中的文档数量"""
        try:
            return self.collection.count()
        except Exception:
            return 0


# ================================================================
# 命令行入口：构建向量索引并测试搜索
# ================================================================

if __name__ == "__main__":
    import json

    engine = VectorEngine()

    # 构建索引
    engine.build_index()

    # 测试搜索
    test_queries = [
        "镀锌钢管DN150沟槽连接管道安装",
        "干式变压器800kva安装",
        "电力电缆截面185敷设",
        "柔性防水套管DN125制作",
        "离心泵消防泵安装",
    ]

    for query in test_queries:
        results = engine.search(query, top_k=3)
        logger.info(f"\n搜索: '{query}'")
        for r in results:
            logger.info(f"  [{r['vector_score']:.4f}] {r['quota_id']} | {r['name'][:60]} | {r['unit']}")
