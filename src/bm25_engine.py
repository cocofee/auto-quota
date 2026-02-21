"""
BM25关键词搜索引擎
功能：
1. 用jieba对定额文本进行中文分词（加载工程造价专业词典）
2. 用rank_bm25建立BM25索引
3. 支持关键词搜索，返回Top K结果

BM25是一种经典的关键词搜索算法，按词频和文档频率计算相关度。
与向量搜索互补：BM25擅长精确关键词匹配，向量搜索擅长语义相似匹配。
"""

import json
import os
import tempfile
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi
from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from db.sqlite import connect as _db_connect


class BM25Engine:
    """BM25关键词搜索引擎"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置
        """
        self.province = province or config.get_current_province()
        self.db_path = config.get_quota_db_path(self.province)

        # BM25索引缓存路径（安全JSON格式；旧pkl自动弃用重建）
        province_dir = config.get_province_db_dir(self.province)
        self.index_path = province_dir / "bm25_index.json"
        self.legacy_pickle_path = province_dir / "bm25_index.pkl"

        # BM25索引和数据
        self.bm25 = None               # BM25Okapi对象
        self.quota_ids = []             # 与索引对应的数据库记录ID列表
        self.tokenized_corpus = []      # 分词后的文档列表
        self.quota_books = {}           # 每条定额的所属册号 {db_id: "C10", ...}
        self.quota_specialties = {}     # 每条定额的专业 {db_id: "安装", ...}

        # 加载jieba专业词典
        self._load_custom_dict()

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=row_factory)

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

        # 从数据库读取数据（包含book和specialty字段，用于过滤）
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            # 检测列是否存在
            col_info = {row[1] for row in cursor.execute("PRAGMA table_info(quotas)").fetchall()}
            has_book_col = "book" in col_info
            has_specialty_col = "specialty" in col_info

            select_cols = "id, search_text"
            if has_book_col:
                select_cols += ", book"
            if has_specialty_col:
                select_cols += ", specialty"
            cursor.execute(f"SELECT {select_cols} FROM quotas WHERE search_text IS NOT NULL")
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            logger.error("数据库中没有定额数据，请先运行定额导入")
            return

        # jieba分词
        self.quota_ids = []
        self.tokenized_corpus = []
        self.quota_books = {}
        self.quota_specialties = {}

        for row in rows:
            self.quota_ids.append(row["id"])
            self.quota_books[row["id"]] = row["book"] or "" if has_book_col else ""
            self.quota_specialties[row["id"]] = row["specialty"] or "" if has_specialty_col else ""
            # 分词，去除单字符的词（通常是标点或无意义字符）
            tokens = [w for w in jieba.cut(row["search_text"]) if len(w.strip()) > 1]
            self.tokenized_corpus.append(tokens)

        # 构建BM25索引
        self.bm25 = BM25Okapi(self.tokenized_corpus)

        # 保存索引到磁盘
        self._save_index()

        logger.info(f"BM25索引构建完成: {len(self.quota_ids)}条文档")

    def _save_index(self):
        """将BM25索引保存到磁盘（JSON安全格式）"""
        data = {
            "quota_ids": self.quota_ids,
            "tokenized_corpus": self.tokenized_corpus,
            "quota_books": self.quota_books,
            "quota_specialties": self.quota_specialties,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix=f"{self.index_path.stem}_tmp_",
                dir=str(self.index_path.parent),
                encoding="utf-8",
                delete=False,
            ) as f:
                tmp_path = f.name
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self.index_path)
        finally:
            if tmp_path and Path(tmp_path).exists():
                try:
                    os.remove(tmp_path)
                except OSError as e:
                    logger.debug(f"BM25索引临时文件清理失败: {tmp_path} ({e})")
        logger.info(f"BM25索引已保存: {self.index_path}")

    def load_index(self) -> bool:
        """
        从磁盘加载BM25索引

        返回:
            True=加载成功, False=需要重新构建
        """
        if not self.index_path.exists():
            # 遇到旧版pickle索引，直接弃用并重建，避免不安全反序列化
            if self.legacy_pickle_path.exists():
                logger.warning("检测到旧版bm25_index.pkl，出于安全原因将重建为JSON索引")
            logger.info("BM25索引文件不存在，需要构建")
            return False

        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("索引根节点不是对象")

            self.quota_ids = [int(x) for x in data.get("quota_ids", [])]
            self.tokenized_corpus = data.get("tokenized_corpus", [])
            if not isinstance(self.tokenized_corpus, list):
                raise ValueError("tokenized_corpus 不是数组")

            raw_books = data.get("quota_books", {})
            if not isinstance(raw_books, dict):
                raw_books = {}
            # JSON键是字符串，恢复为int键
            self.quota_books = {int(k): v for k, v in raw_books.items()}

            raw_specialties = data.get("quota_specialties", {})
            if not isinstance(raw_specialties, dict):
                raw_specialties = {}
            self.quota_specialties = {int(k): v for k, v in raw_specialties.items()}

            if not self.quota_ids or not self.tokenized_corpus:
                raise ValueError("索引内容为空")
            if len(self.quota_ids) != len(self.tokenized_corpus):
                raise ValueError("索引数据长度不一致")

            self.bm25 = BM25Okapi(self.tokenized_corpus)
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

    def search(self, query: str, top_k: int = None, books: list[str] = None,
               specialty: str = None) -> list[dict]:
        """
        BM25关键词搜索

        参数:
            query: 搜索文本（清单描述）
            top_k: 返回前K条结果
            books: 限定搜索的册号列表（如["C10", "C8"]），为None时搜索全库
            specialty: 限定搜索的专业（如"安装"、"土建"），为None时不按专业过滤

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

        # 取候选结果（按分数降序）
        scored_indices = [(i, scores[i]) for i in range(len(scores)) if scores[i] > 0]
        scored_indices.sort(key=lambda x: x[1], reverse=True)

        # 按specialty和book联合过滤
        need_filter = bool(specialty) or bool(books)
        if need_filter and (self.quota_books or self.quota_specialties):
            books_set = set(books) if books else None
            top_indices = []
            for i, s in scored_indices:
                db_id = self.quota_ids[i]
                # specialty过滤
                if specialty and self.quota_specialties:
                    if self.quota_specialties.get(db_id, "") != specialty:
                        continue
                # book过滤
                # P1修复：book为空的定额不排除（可能属于任何册，如补充定额、措施费）
                # 原逻辑：book="" 不在 books_set 中会被跳过 → 这些定额永远搜不到
                # 新逻辑：有明确book但不匹配时才跳过，无book的定额放行
                if books_set and self.quota_books:
                    item_book = self.quota_books.get(db_id, "")
                    if item_book and item_book not in books_set:
                        continue
                top_indices.append((i, s))
                if len(top_indices) >= top_k:
                    break

            # 旧索引兼容：如果过滤后为空且metadata全为空，降级为全库搜索
            if not top_indices and scored_indices:
                all_specialties_empty = not self.quota_specialties or all(
                    v == "" for v in self.quota_specialties.values())
                all_books_empty = not self.quota_books or all(
                    v == "" for v in self.quota_books.values())
                if all_specialties_empty and all_books_empty:
                    logger.warning("旧索引缺少metadata，降级为全库搜索")
                    top_indices = scored_indices[:top_k]
        else:
            top_indices = scored_indices[:top_k]

        if not top_indices:
            return []

        # 查询数据库获取完整定额信息
        db_ids = [self.quota_ids[i] for i, _ in top_indices]
        score_map = {self.quota_ids[i]: s for i, s in top_indices}

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(db_ids))
            cursor.execute(f"SELECT * FROM quotas WHERE id IN ({placeholders})", db_ids)
            rows = {row["id"]: dict(row) for row in cursor.fetchall()}
        finally:
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
