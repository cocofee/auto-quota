# -*- coding: utf-8 -*-
"""
方法论卡片模块 — Jarvis 自我进化的核心

功能：
1. 存储从经验数据中提炼出的"选定额方法论"
2. 根据清单名称和专业，快速查找相关的方法卡片
3. 供 Agent 在推理时作为 Prompt 上下文注入

每张方法卡片包含：
- 类别（如"管道安装"）和专业（如"C10"）
- 匹配的模式键列表（从 learning_notebook 的 pattern_key 来）
- 方法论正文（自然语言，大模型生成）
- 常见错误提示
- 基于多少条样本、确认率多高

使用位置：
- agent_matcher.py 的 _build_agent_prompt() 注入方法卡片
- tools/gen_method_cards.py 生成方法卡片
"""

import json
import sqlite3
import time
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def get_method_cards_db_path():
    """获取方法卡片数据库路径（存在公共数据目录下）"""
    return config.COMMON_DB_DIR / "method_cards.db"


class MethodCards:
    """
    方法论卡片 — 存储和查询从经验中提炼的选定额方法

    用途：
    1. 存储方法卡片（由 gen_method_cards.py 生成）
    2. 按清单名称+专业查找最相关的方法卡片
    3. 供 Agent Prompt 注入使用
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or get_method_cards_db_path()
        self.db_path = Path(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS method_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,       -- 类别（如"管道安装"、"电缆敷设"）
                specialty TEXT,               -- 专业分类（如"C10"、"C4"）
                pattern_keys TEXT DEFAULT '[]', -- 匹配的模式键列表(JSON数组)
                keywords TEXT DEFAULT '[]',   -- 关键词列表(JSON数组)，用于快速匹配
                method_text TEXT NOT NULL,     -- 方法论正文（自然语言）
                common_errors TEXT DEFAULT '', -- 常见错误提示
                sample_count INTEGER DEFAULT 0,  -- 基于多少条样本生成
                confirm_rate REAL DEFAULT 0,     -- 样本确认率
                source_province TEXT DEFAULT '', -- 生成时用的省份数据
                version INTEGER DEFAULT 1,       -- 版本号（每次更新+1）
                created_at REAL,
                updated_at REAL
            )
        """)
        # 索引：按专业快速查询
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_method_specialty
            ON method_cards(specialty)
        """)
        # 索引：按类别查询
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_method_category
            ON method_cards(category)
        """)
        conn.commit()
        conn.close()

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        if row_factory:
            conn.row_factory = sqlite3.Row
        return conn

    def add_card(self, category: str, specialty: str,
                 pattern_keys: list, keywords: list,
                 method_text: str, common_errors: str = "",
                 sample_count: int = 0, confirm_rate: float = 0,
                 source_province: str = "") -> int:
        """
        添加一张方法卡片

        如果同类别+同专业已有卡片，则更新（版本号+1）。
        否则新建。

        返回:
            卡片ID
        """
        now = time.time()
        pattern_keys_json = json.dumps(pattern_keys, ensure_ascii=False)
        keywords_json = json.dumps(keywords, ensure_ascii=False)

        conn = self._connect()
        try:
            # 检查是否已存在同类别+同专业的卡片
            existing = conn.execute(
                "SELECT id, version FROM method_cards WHERE category = ? AND specialty = ?",
                (category, specialty or "")
            ).fetchone()

            if existing:
                # 更新已有卡片
                card_id, old_version = existing
                conn.execute("""
                    UPDATE method_cards SET
                        pattern_keys = ?,
                        keywords = ?,
                        method_text = ?,
                        common_errors = ?,
                        sample_count = ?,
                        confirm_rate = ?,
                        source_province = ?,
                        version = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (pattern_keys_json, keywords_json,
                      method_text, common_errors,
                      sample_count, confirm_rate,
                      source_province, old_version + 1,
                      now, card_id))
                conn.commit()
                logger.debug(f"方法卡片已更新 #{card_id}: {category} (v{old_version + 1})")
                return card_id
            else:
                # 新建卡片
                cursor = conn.execute("""
                    INSERT INTO method_cards (
                        category, specialty, pattern_keys, keywords,
                        method_text, common_errors,
                        sample_count, confirm_rate, source_province,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (category, specialty or "", pattern_keys_json, keywords_json,
                      method_text, common_errors,
                      sample_count, confirm_rate, source_province,
                      now, now))
                conn.commit()
                card_id = cursor.lastrowid
                logger.debug(f"方法卡片已创建 #{card_id}: {category}")
                return card_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_relevant(self, bill_name: str, bill_desc: str = "",
                      specialty: str = None, top_k: int = 2) -> list[dict]:
        """
        根据清单名称和专业，查找最相关的方法卡片

        匹配策略（按优先级）：
        1. 关键词匹配：清单名称包含卡片的关键词
        2. 专业匹配：同专业的卡片优先
        3. 类别包含：清单名称包含卡片类别名

        参数:
            bill_name: 清单名称（如"镀锌钢管管道安装 DN25 丝接"）
            bill_desc: 清单特征描述
            specialty: 专业分类（如"C10"）
            top_k: 返回前K张最相关的卡片

        返回:
            [{"category": "管道安装", "method_text": "...", ...}, ...]
        """
        conn = self._connect(row_factory=True)
        try:
            rows = conn.execute("SELECT * FROM method_cards").fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        full_text = f"{bill_name} {bill_desc}".strip().lower()
        scored = []

        for row in rows:
            card = dict(row)
            score = 0

            # 解析关键词列表
            try:
                keywords = json.loads(card.get("keywords", "[]"))
            except (json.JSONDecodeError, TypeError):
                keywords = []

            # 关键词匹配得分（每命中一个关键词+10分）
            keyword_hits = 0
            for kw in keywords:
                if isinstance(kw, str) and kw.lower() in full_text:
                    keyword_hits += 1
            score += keyword_hits * 10

            # 类别名匹配（卡片类别出现在清单文本中 +5分）
            category = card.get("category", "")
            if category and category.lower() in full_text:
                score += 5

            # 专业匹配（同专业 +3分）
            card_spec = card.get("specialty", "")
            if specialty and card_spec and specialty == card_spec:
                score += 3

            # 没有任何匹配的卡片不返回
            if score <= 0:
                continue

            card["_score"] = score
            # 解析JSON字段
            try:
                card["pattern_keys"] = json.loads(card.get("pattern_keys", "[]"))
            except (json.JSONDecodeError, TypeError):
                card["pattern_keys"] = []
            card["keywords"] = keywords

            scored.append(card)

        # 按得分降序排序
        scored.sort(key=lambda x: x["_score"], reverse=True)

        # 去掉内部评分字段，返回前top_k
        results = []
        for card in scored[:top_k]:
            card.pop("_score", None)
            results.append(card)

        return results

    def get_all_cards(self) -> list[dict]:
        """获取所有方法卡片（用于导出Markdown）"""
        conn = self._connect(row_factory=True)
        try:
            rows = conn.execute(
                "SELECT * FROM method_cards ORDER BY specialty, category"
            ).fetchall()
        finally:
            conn.close()

        cards = []
        for row in rows:
            card = dict(row)
            try:
                card["pattern_keys"] = json.loads(card.get("pattern_keys", "[]"))
            except (json.JSONDecodeError, TypeError):
                card["pattern_keys"] = []
            try:
                card["keywords"] = json.loads(card.get("keywords", "[]"))
            except (json.JSONDecodeError, TypeError):
                card["keywords"] = []
            cards.append(card)
        return cards

    def get_stats(self) -> dict:
        """统计信息"""
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM method_cards").fetchone()[0]
            specialties = conn.execute(
                "SELECT DISTINCT specialty FROM method_cards WHERE specialty != ''"
            ).fetchall()
            avg_samples = conn.execute(
                "SELECT AVG(sample_count) FROM method_cards"
            ).fetchone()[0] or 0
        finally:
            conn.close()

        return {
            "total_cards": total,
            "specialties": [s[0] for s in specialties],
            "avg_sample_count": round(avg_samples, 1),
        }

    def export_markdown(self, output_path: str = None) -> str:
        """
        导出所有方法卡片为Markdown文档

        参数:
            output_path: 输出文件路径，默认 knowledge_notes/method_cards.md

        返回:
            生成的Markdown文本
        """
        if output_path is None:
            output_path = str(Path(__file__).parent.parent / "knowledge_notes" / "method_cards.md")

        cards = self.get_all_cards()
        stats = self.get_stats()

        lines = [
            "# 方法论卡片库",
            "",
            f"共 {stats['total_cards']} 张卡片，"
            f"覆盖专业: {', '.join(stats['specialties']) if stats['specialties'] else '无'}",
            "",
            "---",
            "",
        ]

        # 按专业分组
        by_specialty = {}
        for card in cards:
            spec = card.get("specialty", "未分类") or "未分类"
            by_specialty.setdefault(spec, []).append(card)

        for spec, spec_cards in sorted(by_specialty.items()):
            lines.append(f"## 专业: {spec}")
            lines.append("")

            for card in spec_cards:
                lines.append(f"### {card['category']}")
                lines.append("")
                lines.append(f"- 基于 {card.get('sample_count', 0)} 条样本，"
                             f"确认率 {card.get('confirm_rate', 0):.0%}")
                if card.get("keywords"):
                    lines.append(f"- 关键词: {', '.join(card['keywords'])}")
                lines.append("")
                lines.append(card.get("method_text", ""))
                lines.append("")
                if card.get("common_errors"):
                    lines.append("**常见错误:**")
                    lines.append(card["common_errors"])
                    lines.append("")
                lines.append("---")
                lines.append("")

        md_text = "\n".join(lines)

        # 写入文件
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(md_text, encoding="utf-8")
        logger.info(f"方法卡片已导出: {output_path} ({stats['total_cards']}张)")

        return md_text
