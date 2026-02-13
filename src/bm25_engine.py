"""
BM25关键词搜索引擎
功能：
1. 用jieba对定额文本进行中文分词（加载工程造价专业词典）
2. 用rank_bm25建立BM25索引
3. 支持关键词搜索，返回Top K结果

BM25是一种经典的关键词搜索算法，按词频和文档频率计算相关度。
与向量搜索互补：BM25擅长精确关键词匹配，向量搜索擅长语义相似匹配。
"""

import sqlite3
import pickle
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi
from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class BM25Engine:
    """BM25关键词搜索引擎"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置
        """
        self.province = province or config.CURRENT_PROVINCE
        self.db_path = config.get_quota_db_path(self.province)

        # BM25索引缓存路径（构建一次后保存，下次直接加载）
        self.index_path = config.get_province_db_dir(self.province) / "bm25_index.pkl"

        # BM25索引和数据
        self.bm25 = None               # BM25Okapi对象
        self.quota_ids = []             # 与索引对应的数据库记录ID列表
        self.tokenized_corpus = []      # 分词后的文档列表

        # 加载jieba专业词典
        self._load_custom_dict()

    def _load_custom_dict(self):
        """加载工程造价专业词典到jieba"""
        dict_path = config.ENGINEERING_DICT_PATH
        if dict_path.exists():
            jieba.load_userdict(str(dict_path))
            logger.info(f"加载工程造价词典: {dict_path}")
        else:
            logger.warning(f"专业词典不存在: {dict_path}")

    def build_index(self):
        """
        从SQLite数据库读取所有定额，构建BM25索引

        构建过程：
        1. 读取所有定额的search_text字段
        2. 用jieba分词
        3. 建立BM25索引
        4. 保存索引到磁盘（下次直接加载）
        """
        logger.info("开始构建BM25索引...")

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

        # jieba分词
        self.quota_ids = []
        self.tokenized_corpus = []

        for row in rows:
            self.quota_ids.append(row["id"])
            # 分词，去除单字符的词（通常是标点或无意义字符）
            tokens = [w for w in jieba.cut(row["search_text"]) if len(w.strip()) > 1]
            self.tokenized_corpus.append(tokens)

        # 构建BM25索引
        self.bm25 = BM25Okapi(self.tokenized_corpus)

        # 保存索引到磁盘
        self._save_index()

        logger.info(f"BM25索引构建完成: {len(self.quota_ids)}条文档")

    def _save_index(self):
        """将BM25索引保存到磁盘"""
        data = {
            "bm25": self.bm25,
            "quota_ids": self.quota_ids,
            "tokenized_corpus": self.tokenized_corpus,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "wb") as f:
            pickle.dump(data, f)
        logger.info(f"BM25索引已保存: {self.index_path}")

    def load_index(self) -> bool:
        """
        从磁盘加载BM25索引

        返回:
            True=加载成功, False=需要重新构建
        """
        if not self.index_path.exists():
            logger.info("BM25索引文件不存在，需要构建")
            return False

        try:
            with open(self.index_path, "rb") as f:
                data = pickle.load(f)
            self.bm25 = data["bm25"]
            self.quota_ids = data["quota_ids"]
            self.tokenized_corpus = data["tokenized_corpus"]
            logger.info(f"BM25索引加载成功: {len(self.quota_ids)}条文档")
            return True
        except Exception as e:
            logger.warning(f"BM25索引加载失败({e})，需要重新构建")
            return False

    def ensure_index(self):
        """确保索引可用（先尝试加载，加载失败则构建）"""
        if self.bm25 is not None:
            return  # 已经在内存中
        if not self.load_index():
            self.build_index()

    def search(self, query: str, top_k: int = None) -> list[dict]:
        """
        BM25关键词搜索

        参数:
            query: 搜索文本（清单描述）
            top_k: 返回前K条结果

        返回:
            匹配结果列表，每条包含 {id, quota_id, name, unit, score, ...}
        """
        top_k = top_k or config.BM25_TOP_K
        self.ensure_index()

        if self.bm25 is None:
            logger.error("BM25索引未就绪")
            return []

        # 对查询文本分词
        query_tokens = [w for w in jieba.cut(query) if len(w.strip()) > 1]

        if not query_tokens:
            return []

        # BM25搜索
        scores = self.bm25.get_scores(query_tokens)

        # 取Top K（按分数降序）
        scored_indices = [(i, scores[i]) for i in range(len(scores)) if scores[i] > 0]
        scored_indices.sort(key=lambda x: x[1], reverse=True)
        top_indices = scored_indices[:top_k]

        if not top_indices:
            return []

        # 查询数据库获取完整定额信息
        db_ids = [self.quota_ids[i] for i, _ in top_indices]
        score_map = {self.quota_ids[i]: s for i, s in top_indices}

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
                result["bm25_score"] = score_map[db_id]
                results.append(result)

        return results


# ================================================================
# 命令行入口：构建索引并测试搜索
# ================================================================

if __name__ == "__main__":
    import json

    engine = BM25Engine()

    # 构建索引
    engine.build_index()

    # 测试搜索
    test_queries = [
        "镀锌钢管DN150沟槽连接",
        "干式变压器800kva",
        "电力电缆YJV-4*185",
        "柔性防水套管DN125",
        "室内消火栓泵",
    ]

    for query in test_queries:
        results = engine.search(query, top_k=3)
        logger.info(f"\n搜索: '{query}'")
        for r in results:
            logger.info(f"  [{r['bm25_score']:.2f}] {r['quota_id']} | {r['name'][:60]} | {r['unit']}")
