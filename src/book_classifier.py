"""
数据驱动册号分类器

功能：
从定额库SQLite学习"词→册"概率，用TF-IDF自动判断清单属于哪册定额。
覆盖全专业（安装C1-C12 + 土建A + 市政D + 园林E），换省自适应。

原理：
和老造价师一样——看过几万条定额后，脑子里自然知道"过滤器"属于给排水册、
"配电箱"属于电气册。系统把定额库"读"一遍，统计每个词出现在哪些册中，
查询时用TF-IDF加权判断最可能的册号。

使用位置：被 specialty_classifier.py 的 classify() 调用
独立于 BM25Engine：直接读SQLite，不依赖搜索引擎（清洗阶段搜索引擎还没初始化）
"""

import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import jieba
from loguru import logger

import config

# 工程词典加载标记（全局只加载一次，jieba是进程级单例）
_dict_loaded = False


def _ensure_engineering_dict():
    """加载工程造价专业词典到jieba（只加载一次）"""
    global _dict_loaded
    if _dict_loaded:
        return
    dict_path = config.ENGINEERING_DICT_PATH
    if dict_path.exists():
        jieba.load_userdict(str(dict_path))
    _dict_loaded = True


def _db_connect(db_path, row_factory=False):
    """统一的SQLite连接"""
    conn = sqlite3.connect(str(db_path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


class BookClassifier:
    """数据驱动的册号分类器

    从定额库SQLite直接读取数据，用TF-IDF统计"词→册"概率。
    独立于BM25Engine，可在清洗阶段（BM25未初始化时）使用。

    用法：
        classifier = BookClassifier.get_instance("北京2024")
        result = classifier.classify("Y型过滤器法兰安装 DN50")
        # result = {"primary": "C10", "confidence": "high", ...}
    """

    # 类级缓存：同一省份只构建一次（切换省份时用不同的key）
    _instances: dict = {}

    def __init__(self, province: str = None):
        self.province = province or config.get_current_province()
        self.db_path = config.get_quota_db_path(self.province)
        self._cache_path = config.get_province_db_dir(self.province) / "book_classifier_index.json"

        # TF-IDF索引数据
        self._token_book_counts: dict[str, dict[str, int]] = {}  # {词: {册号: 出现条数}}
        self._book_total_counts: dict[str, int] = {}  # {册号: 该册总定额条数}
        self._ready = False

    @classmethod
    def get_instance(cls, province: str = None) -> "BookClassifier":
        """获取指定省份的分类器实例（单例缓存）"""
        province = province or config.get_current_province()
        if province not in cls._instances:
            instance = cls(province)
            instance._ensure_index()
            cls._instances[province] = instance
        return cls._instances[province]

    @classmethod
    def invalidate_cache(cls, province: str = None):
        """失效指定省份的缓存（定额库重新导入后调用）"""
        province = province or config.get_current_province()
        # 删除内存缓存
        cls._instances.pop(province, None)
        # 删除文件缓存
        cache_path = config.get_province_db_dir(province) / "book_classifier_index.json"
        if cache_path.exists():
            try:
                os.remove(cache_path)
                logger.info(f"BookClassifier缓存已删除: {cache_path}")
            except OSError as e:
                logger.warning(f"BookClassifier缓存删除失败: {e}")

    def _ensure_index(self):
        """确保索引可用（先读缓存，缓存没有则从SQLite构建）"""
        if self._ready:
            return

        # 尝试从JSON缓存加载
        if self._load_cache():
            return

        # 从SQLite构建
        self._build_from_db()

    def _load_cache(self) -> bool:
        """从JSON缓存文件加载索引"""
        if not self._cache_path.exists():
            return False
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._token_book_counts = data.get("token_book_counts", {})
            self._book_total_counts = data.get("book_total_counts", {})
            if self._token_book_counts and self._book_total_counts:
                self._ready = True
                total_tokens = len(self._token_book_counts)
                total_books = len(self._book_total_counts)
                logger.debug(f"BookClassifier从缓存加载: {total_books}个册号, {total_tokens}个特征词")
                return True
        except Exception as e:
            logger.debug(f"BookClassifier缓存加载失败({e})，将重建索引")
        return False

    def _save_cache(self):
        """保存索引到JSON缓存文件"""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="book_cls_tmp_",
                dir=str(self._cache_path.parent), encoding="utf-8", delete=False,
            ) as f:
                tmp_path = f.name
                json.dump({
                    "token_book_counts": self._token_book_counts,
                    "book_total_counts": self._book_total_counts,
                }, f, ensure_ascii=False)
            os.replace(tmp_path, self._cache_path)
            logger.debug(f"BookClassifier索引已缓存: {self._cache_path}")
        except Exception as e:
            logger.warning(f"BookClassifier缓存保存失败: {e}")
        finally:
            if tmp_path and Path(tmp_path).exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _build_from_db(self):
        """从SQLite读取全部定额，构建词→册号倒排索引

        逻辑和 bm25_engine.py 的 _build_token_book_index() 完全一样：
        - 遍历所有定额的 search_text 和 book 字段
        - jieba 分词，统计每个词在各册中出现的定额条数
        - 同一定额中重复的词只计一次（避免频率爆炸）
        """
        if not self.db_path.exists():
            logger.debug(f"定额库不存在({self.db_path})，数据驱动分类不可用")
            return

        # 加载工程词典
        _ensure_engineering_dict()

        conn = _db_connect(self.db_path, row_factory=True)
        try:
            # 检测必要的列是否存在
            col_info = {row[1] for row in conn.execute("PRAGMA table_info(quotas)").fetchall()}
            if "book" not in col_info or "search_text" not in col_info:
                logger.debug("quotas表缺少book或search_text列，数据驱动分类不可用")
                return

            rows = conn.execute(
                "SELECT search_text, book FROM quotas "
                "WHERE search_text IS NOT NULL AND book IS NOT NULL AND book != ''"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            logger.debug("定额库无有效数据，数据驱动分类不可用")
            return

        # 支持任意专业：不按硬编码列表过滤册号，数据库有什么册就学什么册
        # 这样新增专业（光伏、高标准农田、轨道交通等）时自动适应，无需改代码
        #
        # 唯一的标准化：D1-D5（市政分册）合并为"D"（行业惯例，搜索时按前缀匹配）
        def _normalize_book(book: str) -> str:
            """标准化册号（目前只做D分册合并，其余原样保留）"""
            if book.startswith("D") and len(book) > 1 and book[1:].isdigit():
                return "D"
            return book  # 所有册号原样保留（C1-C12、A、E、G系列、新专业等）

        # 构建倒排索引（和 bm25_engine._build_token_book_index 相同逻辑）
        token_book_counts = defaultdict(lambda: defaultdict(int))
        book_total_counts = Counter()

        for row in rows:
            book = _normalize_book(row["book"])
            if not book:
                continue  # 安全保护：跳过空册号（正常不会出现）
            text = row["search_text"]
            book_total_counts[book] += 1

            # 分词用 cut_for_search：同时产出复合词和子词，覆盖面更广
            # 例："给水球墨铸铁管道安装" → ["给水","管道","铸铁","给水球墨铸铁管道","安装"]
            # 这样查询"给水管道"时，"给水"和"管道"都能在索引中命中
            seen_tokens = set()  # 同一定额中重复的词只计一次
            for token in jieba.cut_for_search(text):
                if len(token.strip()) > 1 and token not in seen_tokens:
                    token_book_counts[token][book] += 1
                    seen_tokens.add(token)

        # defaultdict 转 dict（JSON序列化需要）
        self._token_book_counts = {k: dict(v) for k, v in token_book_counts.items()}
        self._book_total_counts = dict(book_total_counts)
        self._ready = True

        # 保存缓存
        self._save_cache()

        total_tokens = len(self._token_book_counts)
        total_books = len(self._book_total_counts)
        total_quotas = sum(self._book_total_counts.values())
        logger.info(
            f"BookClassifier索引构建完成: {total_quotas}条定额, "
            f"{total_books}个册号, {total_tokens}个特征词"
        )

    def classify(self, bill_text: str, top_k: int = 3) -> dict | None:
        """判断清单文本最可能属于哪个册号

        算法和 bm25_engine.classify_to_books() 完全一样：
        1. 分词 → 过滤停用词（出现在70%以上册中的词）
        2. TF-IDF 加权：只出现在少数册的词得分更高
        3. 按得分排序，返回最可能的册号

        参数:
            bill_text: 清单名称+特征描述
            top_k: 返回的备选册号数量

        返回:
            和 specialty_classifier.classify() 兼容的字典格式，
            不可用时返回 None（让调用方降级到其他分类方式）
        """
        if not self._ready:
            return None

        total_books = len(self._book_total_counts)
        if total_books == 0:
            return None

        # 加载工程词典（确保分词一致）
        _ensure_engineering_dict()

        # 分词用 cut_for_search（和建索引一致）
        tokens = [w for w in jieba.cut_for_search(bill_text) if len(w.strip()) > 1]
        if not tokens:
            return None

        # 第一步：过滤停用词（出现在70%以上册中的词没有区分度）
        stopword_threshold = total_books * 0.7
        discriminative_tokens = []
        for token in tokens:
            if token not in self._token_book_counts:
                continue
            df = len(self._token_book_counts[token])  # 出现在多少个册中
            if df < stopword_threshold:
                discriminative_tokens.append(token)

        # 如果所有词都是停用词（如"安装"），降级用全部词
        scoring_tokens = discriminative_tokens if discriminative_tokens else [
            t for t in tokens if t in self._token_book_counts
        ]

        if not scoring_tokens:
            return None

        # 复合词去重：jieba.cut_for_search 会同时产出子词和复合词
        # 例："电力电缆" → ["电力", "电缆", "电力电缆"]
        # 复合词若只在少数册中出现，其高IDF会导致那些册得分偏高
        # 当子词已在评分集中时，跳过包含它们的复合词（信息已被子词覆盖）
        deduped = []
        for token in scoring_tokens:
            is_compound = any(
                other != token and len(other) < len(token) and other in token
                for other in scoring_tokens
            )
            if not is_compound:
                deduped.append(token)
        scoring_tokens = deduped if deduped else scoring_tokens

        # 第二步：TF-IDF计分
        book_scores = defaultdict(float)
        for token in scoring_tokens:
            book_counts = self._token_book_counts[token]
            df = len(book_counts)  # 这个词出现在多少个册中
            idf = math.log(total_books / df + 1)  # IDF加权，+1平滑

            for book, count in book_counts.items():
                total = self._book_total_counts.get(book, 1)
                tf = count / total  # 这个词在该册中出现的频率
                book_scores[book] += tf * idf

        if not book_scores:
            return None

        # 册规模先验：防止小型专业册的高TF抢走大册的分类结果
        # 原理：TF = count/total，小册total小所以TF偏高（例如G5轨道交通电气 vs C4标准电气）
        # 加温和的规模权重：大册最多+50%，小册接近+0%
        # 对纯光伏/农田等同规模库无影响（所有册权重接近，加成差异可忽略）
        if self._book_total_counts:
            max_total = max(self._book_total_counts.values())
            if max_total > 0:
                for book in book_scores:
                    size_prior = self._book_total_counts.get(book, 1) / max_total
                    book_scores[book] *= (1 + size_prior * 0.5)

        sorted_books = sorted(book_scores.items(), key=lambda x: x[1], reverse=True)

        if not sorted_books:
            return None

        # 第三步：置信度判定
        top_book, top_score = sorted_books[0]
        second_score = sorted_books[1][1] if len(sorted_books) > 1 else 0

        if second_score > 0 and top_score > second_score * 2:
            confidence = "high"
        elif second_score > 0 and top_score > second_score * 1.3:
            confidence = "medium"
        else:
            confidence = "low"

        # 评分词太少时强制降为low（只靠1-2个词给24个册打分不靠谱）
        # 例："管道 DN25"只有2个评分词，在C12/C8/C10/C7都有，判断不可信
        if len(scoring_tokens) <= 2:
            confidence = "low"

        # 第四步：构建兼容格式的返回值
        from src.specialty_classifier import BOOKS, BORROW_PRIORITY

        primary = top_book
        primary_name = BOOKS.get(primary, {}).get("name", primary)

        # 借用优先级：优先用已配置的BORROW_PRIORITY，再补充数据驱动发现的相关册
        fallbacks = list(BORROW_PRIORITY.get(primary, []))
        for b, _s in sorted_books[1:top_k]:
            if b not in fallbacks and b != primary:
                fallbacks.append(b)

        # 构建得分说明（调试用）
        scores_str = ", ".join(f"{b}={s:.3f}" for b, s in sorted_books[:3])

        return {
            "primary": primary,
            "primary_name": primary_name,
            "fallbacks": fallbacks,
            "confidence": confidence,
            "reason": f"数据驱动(TF-IDF): {scores_str}",
        }
