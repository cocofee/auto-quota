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
import re
import sqlite3
import sys
import time
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.sqlite import connect as _db_connect, connect_init as _db_connect_init
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
        conn = _db_connect_init(self.db_path)
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_method_source_province
            ON method_cards(source_province)
        """)
        # 迁移：为已有数据库添加 universal_method 字段（跨省通用方法论）
        try:
            conn.execute(
                "ALTER TABLE method_cards ADD COLUMN universal_method TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass  # 字段已存在，忽略
        conn.commit()
        conn.close()

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=row_factory)

    def add_card(self, category: str, specialty: str,
                 pattern_keys: list, keywords: list,
                 method_text: str, common_errors: str = "",
                 sample_count: int = 0, confirm_rate: float = 0,
                 source_province: str = "",
                 universal_method: str = "") -> int:
        """
        添加一张方法卡片

        用 (category, specialty, 主模式键) 做唯一标识：
        - 同一个模式键重复生成 → 更新已有卡片（版本号+1）
        - 不同模式键即使类别相同 → 创建新卡片（如"阀门安装"可以有多张卡片）

        返回:
            卡片ID
        """
        now = time.time()
        pattern_keys_json = json.dumps(pattern_keys, ensure_ascii=False)
        keywords_json = json.dumps(keywords, ensure_ascii=False)

        source_province = str(source_province or "").strip()

        conn = self._connect()
        try:
            # 检查是否已存在同模式键+同省份+同专业的卡片（精确匹配）
            existing = conn.execute(
                "SELECT id, version FROM method_cards WHERE pattern_keys = ? AND source_province = ? AND specialty = ?",
                (pattern_keys_json, source_province, specialty or "")
            ).fetchone()

            if existing:
                # 更新已有卡片
                card_id, old_version = existing
                conn.execute("""
                    UPDATE method_cards SET
                        category = ?,
                        pattern_keys = ?,
                        keywords = ?,
                        method_text = ?,
                        universal_method = ?,
                        common_errors = ?,
                        sample_count = ?,
                        confirm_rate = ?,
                        source_province = ?,
                        version = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (category,
                      pattern_keys_json, keywords_json,
                      method_text, universal_method or "",
                      common_errors,
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
                        method_text, universal_method, common_errors,
                        sample_count, confirm_rate, source_province,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (category, specialty or "", pattern_keys_json, keywords_json,
                      method_text, universal_method or "", common_errors,
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
                      specialty: str = None, province: str = None,
                      top_k: int = 2) -> list[dict]:
        """
        根据清单名称和专业，查找最相关的方法卡片

        匹配策略（两轮查询）：
        第1轮（同省优先）：查同省或无省份的卡片，标记 _scope="local"
        第2轮（跨省补充）：如果第1轮不够top_k，从其他省份找有通用方法论的卡片，
                          标记 _scope="universal"

        评分规则（按优先级）：
        1. pattern_keys匹配：清单文本包含卡片模式键中的关键词（最精准）
        2. 关键词匹配：清单名称包含卡片的关键词
        3. 类别名匹配：清单名称包含卡片类别名
        4. 专业匹配：同专业的卡片优先
        5. 省份匹配：同省份的卡片优先（仅第1轮）

        参数:
            bill_name: 清单名称（如"镀锌钢管管道安装 DN25 丝接"）
            bill_desc: 清单特征描述
            specialty: 专业分类（如"C10"）
            province: 省份/定额库名称
            top_k: 返回前K张最相关的卡片

        返回:
            [{"category": "...", "method_text": "...", "_scope": "local"/"universal", ...}, ...]
        """
        specialty = str(specialty or "").strip()
        province = str(province or "").strip()
        full_text = f"{bill_name} {bill_desc}".strip().lower()

        # ==================== 第1轮：同省优先 ====================
        local_cards = self._query_and_score(
            full_text, specialty, province,
            scope="local", prefetch_limit=max(50, top_k * 25)
        )

        # 多样性重排 + 取top_k
        results = self._diversity_rerank(local_cards, top_k)

        # ==================== 第2轮：跨省补充 ====================
        # 如果同省卡片不够 top_k，从其他省份找通用方法论
        if len(results) < top_k and province:
            # 已经选中的类别+专业组合，用于去重
            seen_keys = set()
            for r in results:
                base_cat = re.sub(r'\(\d+\)$', '', r.get("category", "")).strip()
                seen_keys.add((base_cat, r.get("specialty", "")))

            universal_cards = self._query_and_score(
                full_text, specialty, province,
                scope="universal", prefetch_limit=max(50, top_k * 25)
            )

            # 跨省卡片去重：如果和同省卡片的（类别+专业）重复，跳过
            for card in universal_cards:
                if len(results) >= top_k:
                    break
                base_cat = re.sub(r'\(\d+\)$', '', card.get("category", "")).strip()
                key = (base_cat, card.get("specialty", ""))
                if key not in seen_keys:
                    results.append(card)
                    seen_keys.add(key)

        results = results[:top_k]

        # 冲突检测：top2卡片专业不同时，标注提示
        if len(results) >= 2:
            spec1 = results[0].get("specialty", "")
            spec2 = results[1].get("specialty", "")
            if spec1 and spec2 and spec1 != spec2:
                warning = (f"注意: 同时匹配到{spec1}({results[0].get('category','')})和"
                           f"{spec2}({results[1].get('category','')})的方法卡片，建议保守决策")
                for r in results:
                    r["conflict_warning"] = warning

        # 去掉内部评分字段
        for card in results:
            card.pop("_score", None)

        return results

    def _query_and_score(self, full_text: str, specialty: str,
                         province: str, scope: str = "local",
                         prefetch_limit: int = 50) -> list[dict]:
        """
        查询并评分方法卡片（内部方法）

        参数:
            full_text: 清单完整文本（小写）
            specialty: 专业分类
            province: 省份/定额库
            scope: "local"=同省查询, "universal"=跨省查询（只找有通用方法论的）
            prefetch_limit: 预取条数

        返回:
            评分后的卡片列表（按分数降序）
        """
        where_parts = []
        params = []

        if scope == "local":
            # 同省查询：同省或无省份的卡片
            if specialty:
                where_parts.append("(specialty = ? OR specialty = '')")
                params.append(specialty)
            if province:
                where_parts.append("(source_province = ? OR source_province = '')")
                params.append(province)
        else:
            # 跨省查询：其他省份的卡片，且必须有通用方法论
            if specialty:
                where_parts.append("(specialty = ? OR specialty = '')")
                params.append(specialty)
            if province:
                where_parts.append("source_province != ?")
                params.append(province)
            # 只找有方法论内容的卡片（新卡片用universal_method，旧卡片回退用method_text）
            where_parts.append("(universal_method != '' OR method_text != '')")

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        conn = self._connect(row_factory=True)
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM method_cards
                {where_sql}
                ORDER BY sample_count DESC, updated_at DESC
                LIMIT ?
                """,
                params + [prefetch_limit]
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        scored = []
        for row in rows:
            card = dict(row)
            score = 0

            # 解析JSON字段
            try:
                keywords = json.loads(card.get("keywords", "[]"))
            except (json.JSONDecodeError, TypeError):
                keywords = []
            try:
                pattern_keys = json.loads(card.get("pattern_keys", "[]"))
            except (json.JSONDecodeError, TypeError):
                pattern_keys = []

            # ① pattern_keys匹配（最精准，权重最高）
            pk_hits = 0
            for pk in pattern_keys:
                for part in pk.split("_"):
                    part = part.strip("*").strip()
                    if len(part) >= 2 and part.lower() in full_text:
                        pk_hits += 1
            score += min(pk_hits, 5) * 3  # 每命中+3，上限15分

            # ② 关键词匹配得分（每命中一个关键词+10分）
            keyword_hits = 0
            for kw in keywords:
                if isinstance(kw, str) and kw.lower() in full_text:
                    keyword_hits += 1
            score += keyword_hits * 10

            # ③ 类别名匹配（卡片类别出现在清单文本中 +5分）
            category = card.get("category", "")
            cat_core = category.replace("安装", "").replace("敷设", "").strip().lower()
            if len(cat_core) >= 2 and cat_core in full_text:
                score += 5

            # ④ 专业匹配（同专业 +3分）
            card_spec = card.get("specialty", "")
            if specialty and card_spec and specialty == card_spec:
                score += 3
            elif specialty and not card_spec:
                score += 1

            # ⑤ 同省份卡片优先（仅同省查询时加分）
            if scope == "local" and province and card.get("source_province", "") == province:
                score += 4

            # 没有任何匹配的卡片不返回
            if score <= 0:
                continue

            card["_score"] = score
            card["_scope"] = scope  # 标记来源：local=同省, universal=跨省
            card["pattern_keys"] = pattern_keys
            card["keywords"] = keywords

            scored.append(card)

        # 按得分降序排序
        scored.sort(key=lambda x: x["_score"], reverse=True)
        return scored

    def _diversity_rerank(self, scored: list[dict], top_k: int) -> list[dict]:
        """
        多样性重排：同基础类别（去掉序号后缀）只保留最高分的一张

        参数:
            scored: 评分后的卡片列表（已按分数降序）
            top_k: 需要返回的卡片数

        返回:
            去重后的卡片列表
        """
        results = []
        seen_base_categories = set()
        remaining = []  # 被去重跳过的卡片（备选）

        for card in scored:
            base_cat = re.sub(r'\(\d+\)$', '', card.get("category", "")).strip()
            if base_cat in seen_base_categories:
                remaining.append(card)
                continue
            seen_base_categories.add(base_cat)
            results.append(card)
            if len(results) >= top_k:
                break

        # 如果去重后不够top_k，用备选卡片补齐
        if len(results) < top_k:
            for card in remaining:
                results.append(card)
                if len(results) >= top_k:
                    break

        return results[:top_k]

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

    def _get_universal_notes(self) -> list[dict]:
        """读取通用知识笔记（province='通用' 且 chapter='笔记'）"""
        try:
            from src.rule_knowledge import RuleKnowledge
            kb = RuleKnowledge(province="通用")
            conn = kb._connect(row_factory=True)
            rows = conn.execute(
                "SELECT specialty, content, source_file FROM rules "
                "WHERE province = '通用' AND chapter = '笔记' "
                "ORDER BY specialty, id"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

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
        ]

        # 插入通用知识区块（从规则知识库读取 province="通用" 的笔记）
        universal_notes = self._get_universal_notes()
        if universal_notes:
            lines.append("---")
            lines.append("")
            lines.append("## 通用知识（跨省适用）")
            lines.append("")
            # 按专业分组展示
            notes_by_spec = {}
            for note in universal_notes:
                spec = note.get("specialty", "") or "未分类"
                notes_by_spec.setdefault(spec, []).append(note)
            for spec, notes in sorted(notes_by_spec.items()):
                lines.append(f"### {spec}")
                lines.append("")
                for note in notes:
                    content = note.get("content", "")
                    source = note.get("source_file", "").replace("笔记:", "")
                    lines.append(f"- {content}")
                lines.append("")

        lines.append("---")
        lines.append("")

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
                # 显示来源省份/定额库信息
                source = card.get("source_province", "")
                source_info = f"，来源定额: {source}" if source else ""
                lines.append(f"- 基于 {card.get('sample_count', 0)} 条样本，"
                             f"确认率 {card.get('confirm_rate', 0):.0%}{source_info}")
                if card.get("keywords"):
                    lines.append(f"- 关键词: {', '.join(card['keywords'])}")
                lines.append("")
                # 双层展示：通用方法论 + 省份定额参考
                universal = card.get("universal_method", "")
                province_ref = card.get("method_text", "")
                if universal:
                    lines.append("**通用方法论（适用所有省份）：**")
                    lines.append("")
                    lines.append(universal)
                    lines.append("")
                    if province_ref:
                        prov_label = source if source else "当前省份"
                        lines.append(f"**本省定额参考（{prov_label}）：**")
                        lines.append("")
                        lines.append(province_ref)
                        lines.append("")
                else:
                    # 旧卡片没有通用方法论，直接显示method_text
                    lines.append(province_ref)
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
