"""
定额规则知识库
功能：
1. 存储各省定额的章节说明、计算规则、换算规则、注意事项
2. 向量化后支持语义检索（给定清单描述，找到最相关的定额规则）
3. 匹配时自动检索相关规则，注入大模型Prompt上下文

数据来源：
- 从广联达中查看的定额说明文字
- 各省发布的补充通知、解释、调整系数
- 用户手动整理的规则笔记

存储结构：
- SQLite存规则文本和元信息
- ChromaDB存向量索引，支持语义检索

目录结构：
    knowledge/规则库/北京/安装工程说明.txt
    knowledge/规则库/山东/安装工程说明.txt
    ...

用户只需把规则文本文件放到对应省份文件夹，然后运行导入。
"""

import hashlib
import re
import sqlite3
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class RuleKnowledge:
    """定额规则知识库"""

    def __init__(self, province: str = None):
        """
        参数:
            province: 省份名称，默认用config配置。
                      传None时检索所有省份的规则。
        """
        self.province = province  # None表示全局检索

        # SQLite数据库路径（通用数据库，所有省份共用一个库）
        self.db_path = config.COMMON_DB_DIR / "rule_knowledge.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # ChromaDB向量数据库目录
        self.chroma_dir = config.DB_DIR / "chroma" / "rule_knowledge"
        self.chroma_dir.parent.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self._init_db()

        # 向量引擎（延迟初始化）
        self._collection = None

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数，降低并发场景下锁等待失败。"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        if row_factory:
            conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化SQLite数据库表结构"""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    province TEXT NOT NULL,          -- 省份名称（如"北京2024"）
                    specialty TEXT DEFAULT '',       -- 专业（安装/土建/市政）
                    chapter TEXT DEFAULT '',         -- 章节名称（如"第五章 给排水"）
                    section TEXT DEFAULT '',         -- 小节名称（如"管道安装"）
                    content TEXT NOT NULL,           -- 规则正文
                    content_hash TEXT UNIQUE,        -- 内容哈希（去重用）
                    source_file TEXT DEFAULT '',     -- 来源文件路径
                    keywords TEXT DEFAULT '',        -- 提取的关键词（空格分隔）
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 索引：按省份+专业查询
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rules_province
                ON rules(province)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rules_specialty
                ON rules(province, specialty)
            """)

            conn.commit()
        finally:
            conn.close()

    @property
    def collection(self):
        """延迟初始化ChromaDB集合"""
        if self._collection is None:
            try:
                import chromadb
                client = chromadb.PersistentClient(
                    path=str(self.chroma_dir)
                )
                self._collection = client.get_or_create_collection(
                    name="rule_knowledge",
                    metadata={"hnsw:space": "cosine"}
                )
            except Exception as e:
                logger.warning(f"ChromaDB初始化失败: {e}")
                self._collection = False  # 标记不可用
        return self._collection if self._collection is not False else None

    def import_file(self, file_path: str, province: str,
                    specialty: str = "", chapter: str = "") -> dict:
        """
        导入一个规则文本文件

        文件会被分段存储（按段落或固定长度分割），每段独立向量化。

        参数:
            file_path: 文本文件路径（.txt）
            province: 省份名称
            specialty: 专业名称（安装/土建/市政）
            chapter: 章节名称（可选，如果文件名有章节信息可以传入）

        返回:
            {"total": 总段数, "added": 新增段数, "skipped": 已存在段数}
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"文件不存在: {path}")
            return {"total": 0, "added": 0, "skipped": 0}

        # 读取文件内容
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gbk")
            except Exception as e:
                logger.error(f"文件编码读取失败: {e}")
                return {"total": 0, "added": 0, "skipped": 0}

        if not text.strip():
            logger.warning(f"文件内容为空: {path}")
            return {"total": 0, "added": 0, "skipped": 0}

        # 从文件名推断章节（如果没有手动指定）
        if not chapter:
            chapter = self._infer_chapter(path.stem)

        # 从文件名推断专业（如果没有手动指定）
        if not specialty:
            specialty = self._infer_specialty(path.stem, text[:200])

        # 分段：按段落分割，每段不超过500字
        segments = self._split_text(text, max_len=500)

        # 存入数据库
        stats = {"total": len(segments), "added": 0, "skipped": 0}
        conn = self._connect()
        try:
            cursor = conn.cursor()
            for seg in segments:
                content_hash = hashlib.md5(
                    f"{province}:{specialty}:{seg}".encode()
                ).hexdigest()

                # 检查是否已存在（去重）
                cursor.execute(
                    "SELECT id FROM rules WHERE content_hash = ?",
                    (content_hash,)
                )
                if cursor.fetchone():
                    stats["skipped"] += 1
                    continue

                # 提取关键词
                keywords = self._extract_keywords(seg)

                cursor.execute("""
                    INSERT INTO rules (province, specialty, chapter, section, content,
                                       content_hash, source_file, keywords)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (province, specialty, chapter, "", seg,
                      content_hash, str(path), " ".join(keywords)))

                stats["added"] += 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.info(
            f"导入规则: {path.name} → "
            f"{province}/{specialty}/{chapter} "
            f"({stats['added']}段新增, {stats['skipped']}段已存在)"
        )

        # 如果有新增，重建向量索引
        if stats["added"] > 0:
            self._update_vector_index()

        return stats

    def import_directory(self, dir_path: str = None) -> dict:
        """
        批量导入目录下的所有规则文件

        目录结构约定:
            knowledge/规则库/北京2024/安装工程说明.txt
            knowledge/规则库/北京2024/给排水章节.txt
            knowledge/规则库/山东2024/安装工程说明.txt

        省份名从文件夹名推断。

        参数:
            dir_path: 规则文件目录，默认 knowledge/规则库/

        返回:
            汇总统计
        """
        if dir_path:
            root = Path(dir_path)
        else:
            root = config.KNOWLEDGE_DIR / "规则库"

        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
            logger.info(f"已创建规则库目录: {root}")
            logger.info(f"请在此目录下按省份建文件夹，放入定额说明文本文件")
            return {"total": 0, "added": 0, "skipped": 0}

        total_stats = {"total": 0, "added": 0, "skipped": 0}

        # 遍历省份文件夹
        for province_dir in sorted(root.iterdir()):
            if not province_dir.is_dir():
                continue

            province = province_dir.name  # 文件夹名 = 省份名

            # 遍历该省份下的所有txt文件
            for txt_file in sorted(province_dir.glob("*.txt")):
                stats = self.import_file(str(txt_file), province)
                total_stats["total"] += stats["total"]
                total_stats["added"] += stats["added"]
                total_stats["skipped"] += stats["skipped"]

        logger.info(
            f"规则库批量导入完成: "
            f"{total_stats['added']}段新增, {total_stats['skipped']}段已存在"
        )
        return total_stats

    def search_rules(self, query: str, top_k: int = 5,
                     province: str = None) -> list[dict]:
        """
        检索相关定额规则（向量+关键词双路搜索，合并去重）

        因为ChromaDB默认用英文向量模型，中文搜索效果不稳定，
        所以同时做向量搜索和关键词搜索，合并结果确保不遗漏。

        参数:
            query: 搜索文本（清单名称+描述 或 定额名称）
            top_k: 返回最多几条规则
            province: 限定省份（None表示检索所有省份）

        返回:
            [{id, province, specialty, chapter, content, similarity}, ...]
        """
        province = province or self.province

        # 双路搜索：向量 + 关键词，合并去重
        results = []
        seen_ids = set()

        # 第1路：向量检索（可能漏掉一些中文词，但能捕捉语义相似）
        if self.collection:
            try:
                vector_results = self._vector_search(query, top_k, province)
                for r in vector_results:
                    rid = self._normalize_result_id(r.get("id", ""))
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        results.append(r)
            except Exception as e:
                logger.debug(f"向量检索失败: {e}")

        # 第2路：关键词检索（精确匹配中文词，弥补英文向量模型的不足）
        keyword_results = self._keyword_search(query, top_k, province)
        for r in keyword_results:
            rid = self._normalize_result_id(r.get("id", ""))
            if rid not in seen_ids:
                seen_ids.add(rid)
                results.append(r)

        # 截取top_k条返回
        return results[:top_k]

    def _vector_search(self, query: str, top_k: int,
                       province: str = None) -> list[dict]:
        """使用ChromaDB向量检索"""
        where_filter = None
        if province:
            # 同时搜索指定省份和"通用"规则（通用规则适用于所有省份）
            where_filter = {"$or": [
                {"province": province},
                {"province": "通用"},
            ]}

        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter,
        )

        rules = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 1.0
                similarity = 1 - distance  # cosine距离转相似度

                if similarity < 0.3:  # 相似度太低的不要
                    continue

                rules.append({
                    "id": results["ids"][0][i],
                    "province": metadata.get("province", ""),
                    "specialty": metadata.get("specialty", ""),
                    "chapter": metadata.get("chapter", ""),
                    "content": doc,
                    "similarity": similarity,
                })

        return rules

    def _keyword_search(self, query: str, top_k: int,
                        province: str = None) -> list[dict]:
        """关键词检索（向量检索的回退方案）"""
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()

            # 提取查询中的关键词
            keywords = self._extract_keywords(query)
            if not keywords:
                return []

            # 构建LIKE查询（匹配任意一个关键词）
            conditions = []
            params = []
            for kw in keywords[:5]:  # 最多用5个关键词
                conditions.append("(content LIKE ? OR keywords LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])

            where_clause = " OR ".join(conditions)
            if province:
                # 同时搜索指定省份和"通用"规则
                where_clause = f"province IN (?, '通用') AND ({where_clause})"
                params.insert(0, province)

            sql = f"""
                SELECT id, province, specialty, chapter, content
                FROM rules
                WHERE {where_clause}
                LIMIT ?
            """
            params.append(top_k)

            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _update_vector_index(self):
        """更新ChromaDB向量索引（增量：只添加没有索引过的规则）"""
        if not self.collection:
            return

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            # 获取所有规则
            cursor.execute("SELECT id, province, specialty, chapter, content FROM rules")
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return

        # 获取已有的ID
        try:
            existing = self.collection.get()
            existing_ids = set(existing["ids"]) if existing["ids"] else set()
        except Exception as e:
            logger.debug(f"读取规则向量索引现有ID失败，按全量补写处理: {e}")
            existing_ids = set()

        # 找出需要新增的
        new_docs = []
        new_ids = []
        new_metadatas = []

        for row in rows:
            doc_id = f"rule_{row['id']}"
            if doc_id in existing_ids:
                continue

            new_docs.append(row["content"])
            new_ids.append(doc_id)
            new_metadatas.append({
                "province": row["province"],
                "specialty": row["specialty"],
                "chapter": row["chapter"],
            })

        if new_docs:
            # 批量添加（ChromaDB内置的embedding会自动向量化）
            # 使用chunk方式避免单次请求过大
            batch_size = 100
            for i in range(0, len(new_docs), batch_size):
                self.collection.add(
                    documents=new_docs[i:i + batch_size],
                    ids=new_ids[i:i + batch_size],
                    metadatas=new_metadatas[i:i + batch_size],
                )
            logger.info(f"规则向量索引更新: 新增 {len(new_docs)} 条")

    def _split_text(self, text: str, max_len: int = 500) -> list[str]:
        """
        将长文本分段

        分段策略：
        1. 优先按段落分割（双换行\n\n）
        2. 段落太长则按句号分割
        3. 句子太长则按固定长度截断
        4. 去掉太短的段（<20字，通常是标题或序号）
        """
        # 按段落分割
        paragraphs = re.split(r'\n\s*\n', text)

        segments = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) <= max_len:
                current = f"{current}\n{para}" if current else para
            else:
                # 当前段满了，保存
                if current:
                    segments.append(current.strip())
                # 新段落可能本身很长，需要再细分
                if len(para) > max_len:
                    # 按句号分割
                    sentences = re.split(r'[。！？；\n]', para)
                    sub = ""
                    for sent in sentences:
                        sent = sent.strip()
                        if not sent:
                            continue
                        if len(sub) + len(sent) <= max_len:
                            sub = f"{sub}。{sent}" if sub else sent
                        else:
                            if sub:
                                segments.append(sub.strip())
                            sub = sent
                    if sub:
                        current = sub
                    else:
                        current = ""
                else:
                    current = para

        if current.strip():
            segments.append(current.strip())

        # 过滤掉太短的段
        segments = [s for s in segments if len(s) >= 20]

        return segments

    def _extract_keywords(self, text: str) -> list[str]:
        """从文本中提取关键词（用于辅助检索）"""
        # 工程造价常见关键词模式
        patterns = [
            r'DN\d+',                    # DN管径
            r'\d+[kK][vV][·.]?[aA]?',    # kVA/kV
            r'[A-Z]{2,}管?',             # PPR管、PE管等
            r'[\u4e00-\u9fff]{2,4}管',   # 镀锌钢管、不锈钢管等
            r'[\u4e00-\u9fff]{2,4}连接',  # 沟槽连接、焊接连接等
        ]

        keywords = set()
        for pattern in patterns:
            for match in re.findall(pattern, text):
                keywords.add(match)

        # 提取中文词组（简单的2-4字词切分）
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        # 过滤停用词
        stop_words = {"的", "了", "在", "是", "和", "或", "与", "按", "为", "其",
                      "不", "及", "有", "以", "内", "到", "可", "等", "一", "二",
                      "三", "四", "五", "六", "七", "八", "九", "十", "用于",
                      "包括", "如下", "下列", "规定", "要求", "应当"}
        for word in chinese_words:
            if word not in stop_words:
                keywords.add(word)

        return list(keywords)

    @staticmethod
    def _normalize_result_id(raw_id) -> str:
        """统一规则结果ID格式，避免向量路与关键词路去重失效。"""
        rid = str(raw_id or "").strip()
        if rid.startswith("rule_"):
            rid = rid[5:]
        return rid

    def _infer_chapter(self, filename: str) -> str:
        """从文件名推断章节名称"""
        # 常见模式：第X章_xxx, 第X册_xxx, C5_给排水
        patterns = [
            r'(第[一二三四五六七八九十\d]+[章册节篇].*)',
            r'([A-Z]\d+[_\-].*)',
        ]
        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                return match.group(1)
        return filename  # 找不到则用文件名

    def _infer_specialty(self, filename: str, text_head: str) -> str:
        """从文件名或文本开头推断专业"""
        combined = f"{filename} {text_head}"
        if any(kw in combined for kw in ["安装", "给排水", "电气", "暖通", "消防"]):
            return "安装"
        if any(kw in combined for kw in ["土建", "建筑", "结构", "装饰"]):
            return "土建"
        if any(kw in combined for kw in ["市政", "道路", "桥梁", "管网"]):
            return "市政"
        return ""

    def get_stats(self) -> dict:
        """获取规则库统计信息"""
        conn = self._connect()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM rules")
            total = cursor.fetchone()[0]

            cursor.execute(
                "SELECT province, COUNT(*) FROM rules GROUP BY province"
            )
            by_province = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute(
                "SELECT specialty, COUNT(*) FROM rules GROUP BY specialty"
            )
            by_specialty = {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

        return {
            "total": total,
            "by_province": by_province,
            "by_specialty": by_specialty,
        }


# ================================================================
# 命令行入口：导入/查询规则
# ================================================================

if __name__ == "__main__":
    import argparse

    arg_parser = argparse.ArgumentParser(
        description="定额规则知识库 - 导入和查询定额规则说明"
    )
    sub = arg_parser.add_subparsers(dest="command")

    # 导入命令
    import_cmd = sub.add_parser("import", help="导入规则文件")
    import_cmd.add_argument("file", nargs="?", help="规则文件路径（不指定则批量导入规则库目录）")
    import_cmd.add_argument("--province", default="", help="省份名称")
    import_cmd.add_argument("--specialty", default="", help="专业（安装/土建/市政）")

    # 搜索命令
    search_cmd = sub.add_parser("search", help="搜索规则")
    search_cmd.add_argument("query", help="搜索关键词")
    search_cmd.add_argument("--province", default=None, help="限定省份")
    search_cmd.add_argument("--top-k", type=int, default=5, help="返回数量")

    # 统计命令
    sub.add_parser("stats", help="查看统计")

    args = arg_parser.parse_args()
    kb = RuleKnowledge()

    if args.command == "import":
        if args.file:
            kb.import_file(args.file, args.province, args.specialty)
        else:
            kb.import_directory()
    elif args.command == "search":
        results = kb.search_rules(args.query, args.top_k, args.province)
        for r in results:
            print(f"\n[{r.get('province','')}|{r.get('chapter','')}] "
                  f"相似度:{r.get('similarity','N/A')}")
            print(f"  {r['content'][:200]}")
    elif args.command == "stats":
        stats = kb.get_stats()
        print(f"总规则段: {stats['total']}")
        print(f"按省份: {stats['by_province']}")
        print(f"按专业: {stats['by_specialty']}")
    else:
        arg_parser.print_help()
