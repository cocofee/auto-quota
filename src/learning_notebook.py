"""
学习笔记模块
功能：记录Agent每次处理清单的推理过程，为后续自动提炼规则提供原始材料。

每条笔记包含：
- 清单信息（名称、特征、参数）
- 模式键（用于聚类相似清单）
- 大模型的分析推理（为什么选这个定额）
- 最终结果（选了哪个定额）
- 用户反馈（确认/修正）

使用位置：agent_matcher.py 匹配完一条清单后调用 record_note()
"""

import json
import re
import sqlite3
import time
import sys
from pathlib import Path
from collections import Counter

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def get_notebook_db_path():
    """获取学习笔记数据库路径"""
    return config.COMMON_DB_DIR / "learning_notes.db"


class LearningNotebook:
    """
    学习笔记 - 记录Agent的推理过程

    用途：
    1. 记录每次Agent处理清单的推理过程（学习笔记）
    2. 按模式键聚类相似清单，统计处理模式
    3. 记录用户反馈（确认/修正）
    4. 找出可以提炼为规则的模式（积累够了的）
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or get_notebook_db_path()
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
            CREATE TABLE IF NOT EXISTS learning_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- 输入信息（清单）
                bill_text TEXT NOT NULL,
                bill_name TEXT,
                bill_description TEXT,
                bill_unit TEXT,
                specialty TEXT,

                -- 模式标识（用于聚类和规则提炼）
                pattern_key TEXT,

                -- Agent推理过程
                reasoning TEXT,
                search_query TEXT,

                -- 最终结果
                result_quota_ids TEXT,
                result_quota_names TEXT,
                confidence INTEGER,

                -- 用户反馈
                user_feedback TEXT DEFAULT 'pending',
                corrected_quota_ids TEXT,

                -- 元数据
                llm_type TEXT,
                elapsed_seconds REAL,
                province TEXT,
                project_name TEXT,
                created_at REAL
            )
        """)
        # 索引：按模式键快速聚类查询
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_key
            ON learning_notes(pattern_key)
        """)
        # 索引：按反馈状态过滤
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_feedback
            ON learning_notes(user_feedback)
        """)
        conn.commit()
        conn.close()

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数，降低锁冲突概率。"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA busy_timeout=5000")
        if row_factory:
            conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _safe_json_list(raw):
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, tuple):
            return list(raw)
        if not isinstance(raw, str):
            return []
        raw = raw.strip()
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _as_json_list_text(value):
        """把任意输入归一化为 JSON 数组文本，避免脏数据进入库。"""
        if isinstance(value, (list, tuple)):
            return json.dumps(list(value), ensure_ascii=False)
        if isinstance(value, str):
            return json.dumps(LearningNotebook._safe_json_list(value), ensure_ascii=False)
        return "[]"

    def record_note(self, note: dict) -> int:
        """
        记录一条学习笔记

        参数:
            note: 笔记内容字典，包含以下字段：
                bill_text: 清单完整文本（必须）
                bill_name: 清单名称
                bill_description: 特征描述
                bill_unit: 单位
                specialty: 专业分类(C10等)
                pattern_key: 模式键（如果不传则自动提取）
                reasoning: 大模型的分析推理文本
                search_query: 搜索时使用的query
                result_quota_ids: 最终选的定额编号列表
                result_quota_names: 定额名称列表
                confidence: 置信度
                llm_type: 使用的大模型
                elapsed_seconds: 耗时
                province: 省份
                project_name: 项目名

        返回:
            笔记ID
        """
        bill_text = note.get("bill_text", "")
        if not bill_text:
            logger.warning("学习笔记缺少 bill_text，跳过记录")
            return -1

        # 自动提取模式键
        pattern_key = note.get("pattern_key") or extract_pattern_key(
            note.get("bill_name", ""),
            note.get("bill_description", "")
        )

        # 列表字段转JSON
        quota_ids = self._as_json_list_text(note.get("result_quota_ids", []))
        quota_names = self._as_json_list_text(note.get("result_quota_names", []))

        conn = self._connect()
        try:
            cursor = conn.execute("""
                INSERT INTO learning_notes (
                    bill_text, bill_name, bill_description, bill_unit, specialty,
                    pattern_key, reasoning, search_query,
                    result_quota_ids, result_quota_names, confidence,
                    llm_type, elapsed_seconds, province, project_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_text,
                note.get("bill_name", ""),
                note.get("bill_description", ""),
                note.get("bill_unit", ""),
                note.get("specialty", ""),
                pattern_key,
                note.get("reasoning", ""),
                note.get("search_query", ""),
                quota_ids,
                quota_names,
                note.get("confidence", 0),
                note.get("llm_type", ""),
                note.get("elapsed_seconds", 0),
                note.get("province", config.CURRENT_PROVINCE),
                note.get("project_name", ""),
                time.time(),
            ))
            conn.commit()
            note_id = cursor.lastrowid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.debug(f"学习笔记已记录 #{note_id}: {note.get('bill_name', '')[:30]}")
        return note_id

    def mark_user_feedback(self, note_id: int, feedback: str,
                           corrected_quota_ids: list[str] = None):
        """
        记录用户反馈（确认/修正）

        参数:
            note_id: 笔记ID
            feedback: "confirmed"（确认正确）或 "corrected"（已修正）
            corrected_quota_ids: 如果修正了，新的正确定额编号列表
        """
        if feedback not in {"confirmed", "corrected", "pending"}:
            logger.warning(f"非法反馈值: {feedback}，已降级为 pending")
            feedback = "pending"
        if corrected_quota_ids is not None and not isinstance(corrected_quota_ids, list):
            logger.warning(f"学习笔记#{note_id} corrected_quota_ids 非列表，已忽略")
            corrected_quota_ids = None
        corrected_json = json.dumps(corrected_quota_ids, ensure_ascii=False) if corrected_quota_ids else None

        conn = self._connect()
        try:
            conn.execute("""
                UPDATE learning_notes
                SET user_feedback = ?, corrected_quota_ids = ?
                WHERE id = ?
            """, (feedback, corrected_json, note_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        logger.debug(f"学习笔记 #{note_id} 反馈: {feedback}")

    def get_notes_by_pattern(self, pattern_key: str) -> list[dict]:
        """
        按模式键查询笔记

        参数:
            pattern_key: 模式键（如 "管道安装_镀锌钢管_丝接_DN*"）

        返回:
            该模式下的所有笔记列表
        """
        conn = self._connect(row_factory=True)
        try:
            rows = conn.execute("""
                SELECT * FROM learning_notes
                WHERE pattern_key = ?
                ORDER BY created_at DESC
            """, (pattern_key,)).fetchall()
        finally:
            conn.close()

        return [self._row_to_dict(r) for r in rows]

    def get_extractable_patterns(self, min_count: int = 5,
                                  min_confirm_rate: float = 0.5) -> list[dict]:
        """
        获取可以提炼为规则的模式

        条件：
        1. 同一 pattern_key 下至少有 min_count 条笔记
        2. 确认率（confirmed/总数）>= min_confirm_rate
        3. 结果的定额家族一致率 >= 80%

        返回:
            可提炼模式列表，每个包含：
            - pattern_key: 模式键
            - total_count: 该模式下的笔记总数
            - confirmed_count: 已确认的笔记数
            - top_family: 最常见的定额家族前缀
            - consistency: 结果一致率
        """
        conn = self._connect(row_factory=True)
        try:
            # 找出笔记数 >= min_count 的模式
            patterns = conn.execute("""
                SELECT pattern_key, COUNT(*) as cnt,
                       SUM(CASE WHEN user_feedback = 'confirmed' THEN 1 ELSE 0 END) as confirmed
                FROM learning_notes
                WHERE pattern_key IS NOT NULL AND pattern_key != ''
                GROUP BY pattern_key
                HAVING cnt >= ?
            """, (min_count,)).fetchall()

            extractable = []
            for p in patterns:
                pattern_key = p["pattern_key"]
                total = p["cnt"]
                confirmed = p["confirmed"]

                # 检查确认率
                confirm_rate = confirmed / total if total > 0 else 0
                if confirm_rate < min_confirm_rate:
                    continue

                # 检查结果一致性（定额家族是否一致）
                rows = conn.execute("""
                    SELECT result_quota_ids FROM learning_notes
                    WHERE pattern_key = ? AND user_feedback != 'corrected'
                """, (pattern_key,)).fetchall()

                family_counter = Counter()
                for r in rows:
                    ids = self._safe_json_list(r["result_quota_ids"])
                    if not ids:
                        continue
                    # 取主定额的家族前缀（如 C10-1-80 → C10-1-）
                    main_id = str(ids[0]).strip()
                    if not main_id or "-" not in main_id:
                        continue
                    family = main_id.rsplit("-", 1)[0] + "-"
                    family_counter[family] += 1

                if not family_counter:
                    continue

                top_family, top_count = family_counter.most_common(1)[0]
                consistency = top_count / len(rows) if rows else 0

                if consistency >= 0.8:
                    extractable.append({
                        "pattern_key": pattern_key,
                        "total_count": total,
                        "confirmed_count": confirmed,
                        "confirm_rate": round(confirm_rate, 2),
                        "top_family": top_family,
                        "consistency": round(consistency, 2),
                    })
        finally:
            conn.close()
        return extractable

    def get_stats(self) -> dict:
        """统计信息"""
        conn = self._connect(row_factory=True)
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM learning_notes").fetchone()["c"]
            confirmed = conn.execute(
                "SELECT COUNT(*) as c FROM learning_notes WHERE user_feedback='confirmed'"
            ).fetchone()["c"]
            corrected = conn.execute(
                "SELECT COUNT(*) as c FROM learning_notes WHERE user_feedback='corrected'"
            ).fetchone()["c"]
            pending = conn.execute(
                "SELECT COUNT(*) as c FROM learning_notes WHERE user_feedback='pending'"
            ).fetchone()["c"]

            # 不同模式的数量
            pattern_count = conn.execute(
                "SELECT COUNT(DISTINCT pattern_key) as c FROM learning_notes "
                "WHERE pattern_key IS NOT NULL AND pattern_key != ''"
            ).fetchone()["c"]
        finally:
            conn.close()

        return {
            "total": total,
            "confirmed": confirmed,
            "corrected": corrected,
            "pending": pending,
            "pattern_count": pattern_count,
        }

    def _row_to_dict(self, row) -> dict:
        """sqlite3.Row 转 dict，JSON字段自动解析"""
        d = dict(row)
        # 解析JSON字段
        for key in ["result_quota_ids", "result_quota_names", "corrected_quota_ids"]:
            d[key] = self._safe_json_list(d.get(key))
        return d


def extract_pattern_key(bill_name: str, bill_description: str = "") -> str:
    """
    从清单中提取"模式键" — 把具体参数替换为通配符，保留结构特征

    例子：
    "镀锌钢管管道安装 DN25 丝接" → "管道安装_镀锌钢管_丝接_DN*"
    "镀锌钢管管道安装 DN50 丝接" → "管道安装_镀锌钢管_丝接_DN*"（相同模式）
    "PPR管道安装 DN20 热熔"     → "管道安装_PPR_热熔_DN*"（不同模式）

    这样DN25和DN50会归入同一个模式，方便积累后提炼规则。
    """
    full_text = f"{bill_name} {bill_description}".strip()
    if not full_text:
        return ""

    # 去掉数字参数，保留结构
    # 去掉DN后面的数字
    text = re.sub(r'[DdΦφ][NnEe]?\s*\d+', 'DN*', full_text)
    # 去掉截面数字（如 25mm²、4×70）
    text = re.sub(r'\d+[×x]\d+', '截面*', text)
    text = re.sub(r'\d+\.?\d*\s*mm[²2]?', '截面*', text)
    # 去掉其他独立数字（如 1000mm, 0.8mm）
    text = re.sub(r'\d+\.?\d*\s*mm', '*mm', text)
    # 去掉纯数字
    text = re.sub(r'\b\d+\.?\d*\b', '*', text)
    # 清理多余空格和符号
    text = re.sub(r'[，,。、；;：:（）()\s]+', '_', text)
    # 去掉连续的通配符和下划线
    text = re.sub(r'[_*]+', '_', text)
    text = text.strip('_')

    # 截断过长的键
    if len(text) > 80:
        text = text[:80]

    return text
