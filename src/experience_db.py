"""
经验库模块
功能：
1. 存储历史匹配记录：清单描述 → 正确的定额编号（们）
2. 向量化存储，支持相似度搜索（新清单来了找相似历史记录）
3. 支持从已完成项目批量导入
4. 支持用户修正后自动学习

核心思想：
- 系统每次正确匹配都是一次"经验"
- 用户每次修正也是一次"经验"
- 新清单先查经验库，找到高度相似的历史记录就直接用，不走搜索+大模型
- 这让系统"越用越准"，重复类型的清单不再需要重新匹配

数据结构：
- bill_text: 清单文本（项目名称+特征描述）
- quota_ids: 正确的定额编号列表（JSON格式）
- quota_names: 对应的定额名称列表（JSON格式）
- source: 来源（user_correction=用户修正, project_import=项目导入, auto_match=自动匹配确认）
- confidence: 置信度（0-100，被多次确认的更高）
- confirm_count: 被确认次数（越多越可靠）
"""

import json
import hashlib
import re
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from db.sqlite import connect as _db_connect, connect_init as _db_connect_init
from src.specialty_classifier import get_book_from_quota_id
from src.utils import safe_json_list
import config

# L7: 经验库模糊匹配用的文本归一化函数（顶层导入，避免每次查询重复import）
try:
    from src.text_normalizer import normalize_for_match as _normalize_for_match
except ImportError:
    _normalize_for_match = None


@dataclass
class ExperienceInput:
    """经验记录输入参数，替代add_experience的17个散装参数。"""
    bill_text: str
    quota_ids: list[str]
    quota_names: list[str] = None
    materials: list[dict] = None
    bill_name: str = None
    bill_code: str = None
    bill_unit: str = None
    source: str = "auto_match"
    confidence: int = 80
    province: str = None
    project_name: str = None
    notes: str = None
    specialty: str = None
    skip_vector: bool = False
    skip_fts: bool = False
    feature_text: str = None
    install_method: str = None
    parse_status: str = ""


class ExperienceDB:
    """经验库：存储和查询历史匹配记录"""

    def __init__(self, province: str = None, db_path: Path | None = None):
        self.province = province or config.get_current_province()
        self.db_path = Path(db_path) if db_path else config.get_experience_db_path()
        self.chroma_dir = config.get_chroma_experience_dir()

        # 向量模型和ChromaDB（延迟加载，避免启动时就占显存）
        self._model = None
        self._collection = None
        self._chroma_client = None
        # 锁：防止多线程并发初始化/重建 collection 时的竞态条件
        self._collection_lock = threading.Lock()

        # 确保数据库表存在
        self._init_db()

    def _init_db(self):
        """创建经验库SQLite表（如果不存在）"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = _db_connect_init(self.db_path)
        try:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bill_text TEXT NOT NULL,              -- 清单文本（项目名称+特征描述）
                    bill_name TEXT,                       -- 项目名称（单独存，方便展示）
                    bill_code TEXT,                       -- 清单编码（参考用）
                    bill_unit TEXT,                       -- 计量单位
                    quota_ids TEXT NOT NULL,              -- 正确的定额编号列表（JSON数组）
                    quota_names TEXT,                     -- 对应定额名称列表（JSON数组）
                    source TEXT DEFAULT 'auto_match',     -- 来源：user_correction/project_import/auto_match
                    confidence INTEGER DEFAULT 80,        -- 置信度（0-100）
                    confirm_count INTEGER DEFAULT 1,      -- 被确认次数
                    province TEXT,                        -- 所属省份/版本
                    project_name TEXT,                    -- 来源项目名称
                    created_at REAL,                      -- 创建时间戳
                    updated_at REAL,                      -- 最后更新时间戳
                    notes TEXT,                           -- 备注
                    quota_db_version TEXT DEFAULT '',      -- 写入时的定额库版本号（用于版本校验）
                    layer TEXT DEFAULT 'candidate',         -- 数据层级：authority=权威层 / candidate=候选层
                    specialty TEXT,                         -- 所属专业册号（如"C10"），用于按专业过滤
                    materials TEXT DEFAULT '[]'             -- 主材列表（JSON数组），格式：[{"quota_code":"4-14","name":"开关","code":"260101Z@2","unit":"只"},...]
                )
            """)

            # 兼容旧数据库：如果表已存在但缺少新列，自动加上
            try:
                cursor.execute("SELECT quota_db_version FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN quota_db_version TEXT DEFAULT ''")
                logger.info("经验库已升级：新增 quota_db_version 字段")

            try:
                cursor.execute("SELECT layer FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN layer TEXT DEFAULT 'candidate'")
                logger.info("经验库已升级：新增 layer 字段（两层机制）")

            try:
                cursor.execute("SELECT specialty FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN specialty TEXT")
                logger.info("经验库已升级：新增 specialty 字段（专业分类）")

            try:
                cursor.execute("SELECT materials FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN materials TEXT DEFAULT '[]'")
                logger.info("经验库已升级：新增 materials 字段（主材信息）")

            # L7: 归一化文本字段（模糊匹配用）
            try:
                cursor.execute("SELECT normalized_text FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN normalized_text TEXT DEFAULT ''")
                logger.info("经验库已升级：新增 normalized_text 字段（模糊匹配支持）")

            # 争议标记：纠正经验库直通结果时自动标记，供定期审核用
            try:
                cursor.execute("SELECT disputed FROM experiences LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute("ALTER TABLE experiences ADD COLUMN disputed INTEGER DEFAULT 0")
                logger.info("经验库已升级：新增 disputed 字段（争议标记）")

            self._ensure_column(cursor, "experiences", "feature_text", "TEXT DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "materials_signature", "TEXT DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "install_method", "TEXT DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "quota_fingerprint", "TEXT DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "quota_codes_sorted", "TEXT DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "promoted_at", "REAL DEFAULT NULL")
            self._ensure_column(cursor, "experiences", "promoted_from", "TEXT DEFAULT NULL")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS promotion_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id INTEGER NOT NULL,
                    from_layer TEXT NOT NULL,
                    to_layer TEXT NOT NULL,
                    group_key TEXT NOT NULL,
                    matching_project_count INTEGER NOT NULL,
                    quota_consistency_rate REAL NOT NULL,
                    promoted_at REAL NOT NULL
                )
            """)

            # 全文搜索索引（加速精确文本查找）
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_bill_text
                ON experiences(bill_text)
            """)

            # 省份索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_province
                ON experiences(province)
            """)
            # 组合索引：加速按省份+清单文本查重
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_province_bill_text
                ON experiences(province, bill_text)
            """)
            # L7: 归一化文本+省份组合索引（加速模糊匹配查询）
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_province_normalized_text
                ON experiences(province, normalized_text)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_experience_layer
                ON experiences(layer)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_experience_structure
                ON experiences(specialty, bill_unit, materials_signature)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_experience_quota_fingerprint
                ON experiences(quota_fingerprint)
            """)

            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
                        experience_id UNINDEXED,
                        bill_text,
                        normalized_text,
                        feature_text,
                        quota_names
                    )
                """)
            except sqlite3.OperationalError as exc:
                logger.warning(f"经验库 FTS5 初始化失败，降级为非BM25检索: {exc}")

            conn.commit()

            # L7: 一次性迁移旧记录的 normalized_text（只在字段为空时执行）
            self._migrate_normalized_text(conn)
            self._ensure_fts_seeded(conn)
        finally:
            conn.close()

        logger.debug(f"经验库数据库已初始化: {self.db_path}")

    def _migrate_normalized_text(self, conn):
        """一次性批量迁移旧记录的 normalized_text（L7模糊匹配）

        只处理 normalized_text 为空的记录，已有值的跳过。
        约12K条记录，纯正则操作，<15秒完成。
        """
        if not _normalize_for_match:
            logger.debug("text_normalizer 模块不可用，跳过 normalized_text 迁移")
            return

        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM experiences WHERE normalized_text IS NULL OR normalized_text = ''"
        )
        empty_count = cursor.fetchone()[0]
        if empty_count == 0:
            return

        logger.info(f"经验库迁移：{empty_count} 条旧记录需要生成 normalized_text...")
        cursor.execute(
            "SELECT id, bill_text FROM experiences "
            "WHERE normalized_text IS NULL OR normalized_text = ''"
        )
        batch = []
        for row in cursor.fetchall():
            norm = _normalize_for_match(row[1]) if row[1] else ""
            batch.append((norm, row[0]))

        cursor.executemany(
            "UPDATE experiences SET normalized_text = ? WHERE id = ?", batch
        )
        conn.commit()
        logger.info(f"经验库迁移完成：{len(batch)} 条记录已更新 normalized_text")

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=row_factory)

    @staticmethod
    def _ensure_column(cursor, table: str, column: str, ddl: str):
        try:
            cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            logger.info(f"经验库已升级：新增 {column} 字段")

    @staticmethod
    def _json_dump(value) -> str:
        """统一 JSON 序列化（保留中文）。"""
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _safe_text(value) -> str:
        return str(value or "").strip()

    def _fts_available(self, cursor) -> bool:
        try:
            cursor.execute("SELECT COUNT(*) FROM experience_fts")
            cursor.fetchone()
            return True
        except sqlite3.OperationalError:
            return False

    def _ensure_fts_seeded(self, conn):
        cursor = conn.cursor()
        if not self._fts_available(cursor):
            return
        try:
            cursor.execute("SELECT COUNT(*) FROM experience_fts")
            fts_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM experiences")
            exp_count = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            return
        if exp_count == 0 or fts_count > 0:
            return
        try:
            self.build_fts_index(conn=conn)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                logger.debug("经验库 FTS 首次灌库遇到数据库锁，跳过本次初始化")
                return
            raise

    def build_fts_index(self, conn=None):
        owns_conn = conn is None
        conn = conn or self._connect()
        cursor = conn.cursor()
        if not self._fts_available(cursor):
            if owns_conn:
                conn.close()
            return 0
        cursor.execute("DELETE FROM experience_fts")
        cursor.execute("""
            INSERT INTO experience_fts (experience_id, bill_text, normalized_text, feature_text, quota_names)
            SELECT id, bill_text, COALESCE(normalized_text, ''), COALESCE(feature_text, ''), COALESCE(quota_names, '')
            FROM experiences
        """)
        if owns_conn:
            conn.commit()
            conn.close()
        return cursor.rowcount

    def _upsert_fts_record(self, record_id: int, *, bill_text: str, normalized_text: str = "", feature_text: str = "", quota_names_json: str = ""):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if not self._fts_available(cursor):
                return
            quota_names_text = quota_names_json or ""
            cursor.execute("DELETE FROM experience_fts WHERE experience_id = ?", (str(record_id),))
            cursor.execute(
                """
                INSERT INTO experience_fts (experience_id, bill_text, normalized_text, feature_text, quota_names)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(record_id), bill_text or "", normalized_text or "", feature_text or "", quota_names_text),
            )
            conn.commit()
        except Exception as exc:
            logger.debug(f"经验库 FTS 单条同步失败: {exc}")
        finally:
            conn.close()

    @staticmethod
    def _unit_equivalent(unit: str) -> str:
        value = str(unit or "").strip().lower()
        if not value:
            return ""
        groups = {
            "设备": {"台", "套"},
            "长度": {"m", "米", "延长米"},
            "面积": {"m2", "㎡", "m²", "平方米"},
            "体积": {"m3", "m³", "立方米"},
            "项处": {"项", "处"},
        }
        for alias, values in groups.items():
            if value in values:
                return alias
        return value

    @staticmethod
    def _jaccard_similarity(left: str, right: str) -> float:
        a = {part for part in str(left or "").split("|") if part}
        b = {part for part in str(right or "").split("|") if part}
        if not a and not b:
            return 0.0
        return len(a & b) / max(len(a | b), 1)

    def _recall_exact(self, query_item: dict, *, layer: str, province_mode: str, limit: int = 20) -> list[dict]:
        clauses = ["normalized_text = ?", "normalized_text != ''", "layer = ?"]
        params: list = [query_item["normalized_text"], layer]
        if province_mode == "local":
            clauses.append("province = ?")
            params.append(query_item["province"])
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM experiences
                WHERE {' AND '.join(clauses)}
                ORDER BY confidence DESC, confirm_count DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                [*params, limit],
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _recall_bm25(self, query_item: dict, *, layer: str, province_mode: str, limit: int = 30) -> list[dict]:
        normalized_text = self._safe_text(query_item.get("normalized_text"))
        raw_text = self._safe_text(query_item.get("raw_text"))
        tokens = [token for token in re.findall(r"[\u4e00-\u9fffa-zA-Z0-9]+", raw_text or normalized_text) if len(token) >= 1]
        match_query = " ".join(tokens[:8]) or normalized_text
        if not match_query:
            return []
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            if not self._fts_available(cursor):
                return []
            province_clause = " AND e.province = ?" if province_mode == "local" else ""
            params = [match_query, layer]
            if province_mode == "local":
                params.append(query_item["province"])
            params.append(limit)
            cursor.execute(
                f"""
                SELECT e.*, bm25(experience_fts) AS bm25_score
                FROM experience_fts
                JOIN experiences e ON e.id = CAST(experience_fts.experience_id AS INTEGER)
                WHERE experience_fts MATCH ?
                  AND e.layer = ?{province_clause}
                ORDER BY bm25_score ASC, e.confidence DESC, e.confirm_count DESC
                LIMIT ?
                """,
                params,
            )
            rows = []
            for row in cursor.fetchall():
                record = dict(row)
                raw_bm25 = float(record.pop("bm25_score", 0.0) or 0.0)
                record["bm25_score"] = 1.0 / (1.0 + max(raw_bm25, 0.0))
                rows.append(record)
            return rows
        except sqlite3.OperationalError as exc:
            logger.debug(f"经验库 BM25 召回失败，降级跳过: {exc}")
            return []
        finally:
            conn.close()

    def _recall_structural(self, query_item: dict, *, layer: str, province_mode: str, limit: int = 30) -> list[dict]:
        clauses = ["layer = ?"]
        params: list = [layer]
        specialty = self._safe_text(query_item.get("specialty"))
        unit = self._safe_text(query_item.get("unit"))
        materials_signature = self._safe_text(query_item.get("materials_signature"))
        if specialty:
            clauses.append("specialty = ?")
            params.append(specialty)
        if unit:
            clauses.append("bill_unit = ?")
            params.append(unit)
        if materials_signature:
            clauses.append("materials_signature = ?")
            params.append(materials_signature)
        if len(clauses) == 1:
            return []
        if province_mode == "local":
            clauses.append("province = ?")
            params.append(query_item["province"])
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT *
                FROM experiences
                WHERE {' AND '.join(clauses)}
                ORDER BY confidence DESC, confirm_count DESC, updated_at DESC
                LIMIT ?
                """,
                [*params, limit],
            )
            rows = []
            for row in cursor.fetchall():
                record = dict(row)
                record["structural_score"] = 1.0
                rows.append(record)
            return rows
        finally:
            conn.close()

    def _recall_vector_candidates(self, query_text: str, *, top_k: int, province: str) -> list[tuple[int, float]]:
        if not getattr(config, "VECTOR_ENABLED", True):
            return []
        coll = self.collection
        if coll is None or coll.count() == 0 or self.model is None:
            return []
        fetch_k = min(max(top_k * 4, 30), coll.count())
        try:
            from src.model_profile import encode_queries
            query_embedding = encode_queries(self.model, [query_text])
            try:
                results = coll.query(
                    query_embeddings=query_embedding.tolist(),
                    n_results=fetch_k,
                    where={"province": province},
                )
            except Exception:
                results = coll.query(query_embeddings=query_embedding.tolist(), n_results=fetch_k)
            ids = results.get("ids", [[]])[0] if results else []
            distances = results.get("distances", [[]])[0] if results else []
            pairs = []
            for exp_id, distance in zip(ids, distances):
                try:
                    pairs.append((int(exp_id), max(0.0, min(1.0, 1.0 - float(distance or 1.0)))))
                except (TypeError, ValueError):
                    continue
            return pairs
        except Exception as exc:
            logger.debug(f"经验库向量召回失败，降级跳过: {exc}")
            return []

    def _fetch_records_by_ids(self, ids: list[int], *, min_confidence: int, province: str | None = None) -> dict[int, dict]:
        if not ids:
            return {}
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(ids))
            clauses = [f"id IN ({placeholders})", "confidence >= ?"]
            params: list = [*ids, min_confidence]
            if province:
                clauses.append("province = ?")
                params.append(province)
            cursor.execute(
                f"SELECT * FROM experiences WHERE {' AND '.join(clauses)}",
                params,
            )
            return {row["id"]: dict(row) for row in cursor.fetchall()}
        finally:
            conn.close()

    @staticmethod
    def _layer_score(layer: str) -> float:
        return {"authority": 1.0, "verified": 0.6, "candidate": 0.3}.get(str(layer or ""), 0.3)

    def _estimate_recall_score(self, record: dict) -> float:
        text_score = max(
            float(record.get("bm25_score", 0.0) or 0.0),
            float(record.get("vector_score", 0.0) or 0.0),
            float(record.get("similarity", 0.0) or 0.0),
            1.0 if record.get("_exact_match") else 0.0,
        )
        return 0.35 * text_score + 0.10 * self._layer_score(record.get("layer"))

    def _expand_query_layers(self, candidates: dict[int, dict], *, current_step: int) -> bool:
        green_hits = sum(1 for item in candidates.values() if self._estimate_recall_score(item) >= 0.85)
        yellow_hits = sum(1 for item in candidates.values() if self._estimate_recall_score(item) >= 0.60)
        if green_hits >= 3:
            return False
        if yellow_hits >= 5:
            return False
        return current_step < 6

    def _merge_recall_results(self, candidates: dict[int, dict], records: list[dict], *, channel: str):
        for record in records:
            record_id = int(record.get("id") or 0)
            if record_id <= 0:
                continue
            if record_id not in candidates:
                record["recalled_by"] = [channel]
                record["raw_scores"] = {}
                candidates[record_id] = record
            else:
                existing = candidates[record_id]
                existing.setdefault("recalled_by", [])
                if channel not in existing["recalled_by"]:
                    existing["recalled_by"].append(channel)
                for key in (
                    "bm25_score", "vector_score", "similarity", "structural_score",
                    "_exact_match",
                ):
                    if key in record and record.get(key) not in (None, ""):
                        if key == "_exact_match":
                            existing[key] = bool(existing.get(key) or record.get(key))
                        else:
                            existing[key] = max(float(existing.get(key, 0.0) or 0.0), float(record.get(key, 0.0) or 0.0))
            if "bm25_score" in record:
                candidates[record_id].setdefault("raw_scores", {})["bm25"] = float(record.get("bm25_score", 0.0) or 0.0)
            if "vector_score" in record:
                candidates[record_id].setdefault("raw_scores", {})["vector"] = float(record.get("vector_score", 0.0) or 0.0)

    def _hard_filter(self, candidate: dict, query_item: dict) -> dict | None:
        candidate_specialty = self._safe_text(candidate.get("specialty"))
        candidate_unit = self._safe_text(candidate.get("bill_unit"))
        candidate_materials = self._safe_text(candidate.get("materials_signature"))
        query_specialty = self._safe_text(query_item.get("specialty"))
        query_unit = self._safe_text(query_item.get("unit"))
        query_materials = self._safe_text(query_item.get("materials_signature"))
        risk_flags = list(candidate.get("risk_flags") or [])
        penalty_factor = 1.0

        if query_specialty and candidate_specialty and candidate_specialty != query_specialty:
            return None
        if query_unit and candidate_unit:
            if candidate_unit != query_unit:
                if self._unit_equivalent(candidate_unit) == self._unit_equivalent(query_unit):
                    pass
                else:
                    penalty_factor *= 0.3
                    risk_flags.append("unit_mismatch_severe")
        if query_materials and candidate_materials:
            candidate_first = candidate_materials.split("|", 1)[0]
            query_first = query_materials.split("|", 1)[0]
            if candidate_first and query_first and candidate_first != query_first:
                penalty_factor *= 0.5
                risk_flags.append("material_conflict")

        current_version = query_item.get("quota_version") or ""
        record_version = candidate.get("quota_db_version") or ""
        if current_version and record_version and current_version != record_version:
            risk_flags.append("version_mismatch")
        if query_item.get("province") and candidate.get("province") and candidate.get("province") != query_item.get("province"):
            risk_flags.append("cross_province")

        candidate["risk_flags"] = sorted(set(risk_flags))
        candidate["penalty_factor"] = penalty_factor
        return candidate

    def _distinct_project_count(self, record: dict) -> int:
        normalized_text = self._safe_text(record.get("normalized_text"))
        specialty = self._safe_text(record.get("specialty"))
        bill_unit = self._safe_text(record.get("bill_unit"))
        if not normalized_text:
            return min(int(record.get("confirm_count") or 1), 5)
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(DISTINCT project_name)
                FROM experiences
                WHERE normalized_text = ?
                  AND specialty = ?
                  AND bill_unit = ?
                  AND project_name IS NOT NULL
                  AND project_name != ''
                """,
                (normalized_text, specialty, bill_unit),
            )
            count = cursor.fetchone()[0] or 0
            return max(int(count), min(int(record.get("confirm_count") or 1), 5))
        finally:
            conn.close()

    def _compute_experience_total_score(self, candidate: dict, query_item: dict) -> tuple[float, dict]:
        bm25_score = float(candidate.get("bm25_score", 0.0) or 0.0)
        vector_score = float(candidate.get("vector_score", candidate.get("similarity", 0.0)) or 0.0)
        if candidate.get("_exact_match"):
            bm25_score = max(bm25_score, 1.0)
            vector_score = max(vector_score, 1.0)
        text_score = min(1.0, max(0.0, 0.4 * bm25_score + 0.6 * vector_score))
        specialty_score = 1.0
        unit_score = 1.0
        query_unit = self._safe_text(query_item.get("unit"))
        candidate_unit = self._safe_text(candidate.get("bill_unit"))
        if query_unit and candidate_unit:
            if query_unit == candidate_unit:
                unit_score = 1.0
            elif self._unit_equivalent(query_unit) == self._unit_equivalent(candidate_unit):
                unit_score = 0.8
            else:
                unit_score = 0.0
        material_score = self._jaccard_similarity(candidate.get("materials_signature"), query_item.get("materials_signature"))
        source_score = self._layer_score(candidate.get("layer"))
        consensus_score = min(self._distinct_project_count(candidate) / 5.0, 1.0)
        total = (
            0.35 * text_score +
            0.20 * specialty_score +
            0.15 * unit_score +
            0.15 * material_score +
            0.10 * source_score +
            0.05 * consensus_score
        ) * float(candidate.get("penalty_factor", 1.0) or 1.0)
        dimension_scores = {
            "text": round(text_score, 6),
            "specialty": round(specialty_score, 6),
            "unit": round(unit_score, 6),
            "material": round(material_score, 6),
            "source": round(source_score, 6),
            "consensus": round(consensus_score, 6),
        }
        return float(total), dimension_scores

    def _has_authority_conflict(self, candidate: dict) -> bool:
        normalized_text = self._safe_text(candidate.get("normalized_text"))
        specialty = self._safe_text(candidate.get("specialty"))
        bill_unit = self._safe_text(candidate.get("bill_unit"))
        if not normalized_text:
            return False
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(DISTINCT quota_fingerprint)
                FROM experiences
                WHERE layer = 'authority'
                  AND normalized_text = ?
                  AND specialty = ?
                  AND bill_unit = ?
                  AND quota_fingerprint IS NOT NULL
                  AND quota_fingerprint != ''
                """,
                (normalized_text, specialty, bill_unit),
            )
            count = cursor.fetchone()[0] or 0
            return count > 1
        finally:
            conn.close()

    def _apply_gate(self, candidate: dict, query_item: dict):
        total_score = float(candidate.get("total_score", 0.0) or 0.0)
        risk_flags = set(candidate.get("risk_flags") or [])
        if self._has_authority_conflict(candidate):
            risk_flags.add("authority_conflict")
        candidate["risk_flags"] = sorted(risk_flags)
        query_unit = self._safe_text(query_item.get("unit"))
        candidate_unit = self._safe_text(candidate.get("bill_unit"))
        unit_ok = not query_unit or not candidate_unit or query_unit == candidate_unit or self._unit_equivalent(query_unit) == self._unit_equivalent(candidate_unit)
        version_ok = not query_item.get("quota_version") or not candidate.get("quota_db_version") or query_item.get("quota_version") == candidate.get("quota_db_version")
        has_red_flag = any(flag in risk_flags for flag in {"authority_conflict", "unit_mismatch_severe", "material_conflict", "specialty_mismatch"})
        if (
            total_score >= 0.85 and
            candidate.get("layer") == "authority" and
            unit_ok and version_ok and
            not has_red_flag
        ):
            candidate["gate"] = "green"
        elif total_score < 0.60 or has_red_flag:
            candidate["gate"] = "red"
        else:
            candidate["gate"] = "yellow"

    def _build_query_item(self, query_text: str, *, province: str, specialty: str = "", unit: str = "", materials_signature: str = "", install_method: str = "", quota_version: str = "") -> dict:
        return {
            "raw_text": query_text,
            "normalized_text": _normalize_for_match(query_text) if _normalize_for_match else self._safe_text(query_text),
            "province": province,
            "specialty": self._safe_text(specialty),
            "unit": self._safe_text(unit),
            "materials_signature": self._safe_text(materials_signature),
            "install_method": self._safe_text(install_method),
            "quota_version": quota_version or config.get_current_quota_version(province),
        }

    @staticmethod
    def _source_to_layer(source: str) -> str:
        authority_sources = {
            "user_correction",
            "user_confirmed",
            "openclaw_approved",
            "multi_project_promoted",
            "promote_from_candidate",
        }
        verified_sources = {
            "completed_project",
            "reviewed_import",
        }
        if source in authority_sources:
            return "authority"
        if source in verified_sources:
            return "verified"
        return "candidate"

    @staticmethod
    def _safe_int(value, default: int) -> int:
        """安全转 int，失败返回默认值。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: int, low: int, high: int) -> int:
        return max(min(value, high), low)

    @staticmethod
    def _normalize_confidence_value(value) -> int:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0
        if 0 <= numeric <= 1:
            numeric *= 100
        return max(0, min(int(round(numeric)), 100))

    @staticmethod
    def _coerce_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _meets_verified_criteria(self, *,
                                 bill_text: str,
                                 bill_unit: str,
                                 specialty: str,
                                 quota_ids: list[str],
                                 quota_names: list[str],
                                 confidence: int,
                                 parse_status: str = "") -> bool:
        if str(parse_status or "").strip().lower() == "error":
            return False
        if not str(bill_text or "").strip():
            return False
        if not str(bill_unit or "").strip():
            return False
        if not str(specialty or "").strip():
            return False
        if not quota_ids or not any(str(item or "").strip() for item in quota_ids):
            return False
        if not quota_names or not any(str(item or "").strip() for item in quota_names):
            return False
        return self._normalize_confidence_value(confidence) >= 50

    def _determine_layer(self, *,
                         source: str,
                         bill_text: str,
                         bill_unit: str,
                         specialty: str,
                         quota_ids: list[str],
                         quota_names: list[str],
                         confidence: int,
                         parse_status: str = "") -> str:
        base_layer = self._source_to_layer(source)
        if base_layer != "verified":
            return base_layer
        if self._meets_verified_criteria(
            bill_text=bill_text,
            bill_unit=bill_unit,
            specialty=specialty,
            quota_ids=quota_ids,
            quota_names=quota_names,
            confidence=confidence,
            parse_status=parse_status,
        ):
            return "verified"
        return "candidate"

    def _determine_backfill_layer(self, record: dict) -> str:
        source = self._safe_text(record.get("source"))
        current_layer = self._safe_text(record.get("layer"))
        if current_layer == "deleted":
            return "deleted"

        quota_ids = safe_json_list(record.get("quota_ids"))
        quota_names = safe_json_list(record.get("quota_names"))
        base_layer = self._determine_layer(
            source=source,
            bill_text=record.get("bill_text", ""),
            bill_unit=record.get("bill_unit", ""),
            specialty=record.get("specialty", ""),
            quota_ids=quota_ids,
            quota_names=quota_names,
            confidence=record.get("confidence", 0),
            parse_status=record.get("parse_status", ""),
        )
        if base_layer != "candidate":
            return base_layer

        # Compatibility bridge for legacy imported authority data:
        # old imports may already be trustworthy enough for verified, but should no longer
        # remain direct authority after the new three-layer rules landed.
        if source in {"project_import", "oss_import", "batch_import", "auto_review"}:
            if current_layer == "authority" and self._meets_verified_criteria(
                bill_text=record.get("bill_text", ""),
                bill_unit=record.get("bill_unit", ""),
                specialty=record.get("specialty", ""),
                quota_ids=quota_ids,
                quota_names=quota_names,
                confidence=record.get("confidence", 0),
                parse_status=record.get("parse_status", ""),
            ):
                return "verified"
        return base_layer

    @staticmethod
    def _build_group_key(normalized_text: str, specialty: str, bill_unit: str, quota_version: str) -> str:
        payload = "||".join([
            str(normalized_text or "").strip(),
            str(specialty or "").strip(),
            str(bill_unit or "").strip(),
            str(quota_version or "").strip(),
        ])
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _material_category_from_text(text: str) -> str:
        value = str(text or "").strip().lower()
        if not value:
            return "other"
        mapping = [
            ("steel_pipe", ["钢管", "镀锌钢管", "焊接钢管", "无缝钢管"]),
            ("copper_pipe", ["铜管", "紫铜管"]),
            ("plastic_pipe", ["ppr", "pe", "pvc", "hdpe", "塑料管"]),
            ("valve", ["阀门", "闸阀", "截止阀", "球阀", "蝶阀", "止回阀"]),
            ("insulation", ["保温", "橡塑", "岩棉", "玻璃棉"]),
            ("fan_coil", ["风机盘管", "fcu"]),
            ("ahu", ["空调机组", "ahu", "组合式空调"]),
            ("chiller", ["冷水机组", "冷机", "离心机", "螺杆机"]),
            ("pump", ["水泵", "循环泵", "加压泵", "消防泵"]),
            ("duct", ["风管", "镀锌风管", "铁皮风管", "复合风管"]),
            ("cable", ["电缆", "电线", "bv", "yjv", "wdzn"]),
            ("bridge", ["桥架", "线槽", "电缆桥架"]),
            ("sprinkler", ["喷淋头", "喷头", "洒水喷头"]),
            ("fire_hydrant", ["消火栓", "消防栓"]),
            ("fitting", ["管件", "弯头", "三通", "法兰"]),
        ]
        for category, keywords in mapping:
            matched = False
            for keyword in keywords:
                needle = keyword.lower()
                if re.fullmatch(r"[a-z0-9_.-]+", needle):
                    if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", value):
                        matched = True
                        break
                elif needle in value:
                    matched = True
                    break
            if matched:
                return category
        return "other"

    def _compute_material_signature(self, materials) -> str:
        items = safe_json_list(materials)
        if not items:
            return ""
        ranked = []
        for index, item in enumerate(items):
            if isinstance(item, dict):
                name = item.get("name") or item.get("material_name") or item.get("raw_name") or ""
                amount = self._coerce_float(item.get("amount"))
                if amount is None:
                    unit_price = self._coerce_float(item.get("unit_price"))
                    qty = self._coerce_float(item.get("qty") or item.get("quantity"))
                    if unit_price is not None and qty is not None:
                        amount = unit_price * qty
                score = amount if amount is not None else -(index + 1)
            else:
                name = str(item or "")
                score = -(index + 1)
            ranked.append((score, self._material_category_from_text(name)))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        categories = []
        seen = set()
        for _, category in ranked:
            if category in seen:
                continue
            seen.add(category)
            categories.append(category)
            if len(categories) >= 3:
                break
        return "|".join(sorted(categories))

    def _compute_quota_fingerprint(self, quota_ids: list[str]) -> tuple[str, str]:
        cleaned = sorted({str(item).strip() for item in (quota_ids or []) if str(item).strip()})
        if not cleaned:
            return "", "[]"
        joined = "|".join(cleaned)
        digest = hashlib.md5(joined.encode("utf-8")).hexdigest()[:8]
        return digest, self._json_dump(cleaned)

    def _normalize_record_quota_fields(self, record: dict) -> dict:
        """统一把记录中的 quota_ids/quota_names/materials 解析成 list。"""
        record["quota_ids"] = safe_json_list(record.get("quota_ids"))
        record["quota_names"] = safe_json_list(record.get("quota_names"))
        record["materials"] = safe_json_list(record.get("materials"))
        return record

    @property
    def model(self):
        """从全局 ModelCache 获取向量模型（与定额搜索共用同一个BGE模型）"""
        if self._model is None:
            from src.model_cache import ModelCache
            self._model = ModelCache.get_vector_model()
        return self._model

    @property
    def collection(self):
        """延迟初始化ChromaDB collection（通过全局ModelCache获取客户端，避免级联崩溃）

        修复：先创建collection再保存client引用，防止get_or_create_collection失败后
        self._chroma_client已赋值导致后续调用跳过初始化、返回None的问题。

        自动修复：ChromaDB升级后旧索引格式不兼容时（如dimensionality错误），
        自动删除旧索引并从SQLite重建，用户无感。

        线程安全：用 _collection_lock 保护，防止多线程并发初始化/重建时竞态。
        """
        # 快速路径：已初始化且客户端未变，无需加锁
        if self._collection is not None and self._chroma_client is not None:
            try:
                from src.model_cache import ModelCache
                current_client = ModelCache.get_chroma_client(str(self.chroma_dir))
                if current_client is self._chroma_client:
                    return self._collection
            except Exception:
                pass

        # 慢路径：需要初始化或刷新，加锁保护
        with self._collection_lock:
            try:
                from src.model_cache import ModelCache
                client = ModelCache.get_chroma_client(str(self.chroma_dir))
                # 客户端变了（被重建过），需要刷新collection
                if client is not self._chroma_client:
                    # 先创建collection，成功后再保存client引用
                    # （如果get_or_create_collection失败，下次还会重试）
                    coll = client.get_or_create_collection(
                        name="experiences",
                        metadata={"hnsw:space": "cosine"}
                    )
                    # 健康探测：检测旧索引是否与当前ChromaDB版本兼容
                    # ChromaDB版本更新可能改变异常消息措辞，所以匹配多种已知关键词
                    try:
                        coll.count()
                    except (AttributeError, Exception) as probe_err:
                        err_msg = str(probe_err).lower()
                        # 已知的不兼容异常关键词（覆盖不同ChromaDB版本的报错措辞）
                        rebuild_keywords = [
                            "dimensionality", "dimension", "mismatch",
                            "incompatible", "has no attribute", "corrupt",
                            "invalid", "segment", "index",
                        ]
                        if any(kw in err_msg for kw in rebuild_keywords):
                            logger.warning(f"经验库向量索引格式不兼容（{probe_err}），自动重建...")
                            coll = self._auto_rebuild_collection(client)
                        else:
                            raise
                    self._collection = coll
                    self._chroma_client = client
            except Exception as e:
                logger.warning(f"ChromaDB collection初始化失败: {e}")
                # 返回None，调用方需要处理
            return self._collection

    def _auto_rebuild_collection(self, client):
        """ChromaDB索引格式不兼容时，自动删旧索引并从SQLite重建"""
        import shutil
        # 删除旧索引目录
        chroma_path = Path(str(self.chroma_dir))
        if chroma_path.exists():
            shutil.rmtree(chroma_path, ignore_errors=True)
            logger.info(f"已删除旧索引目录: {chroma_path}")

        # 重新创建客户端和collection
        from src.model_cache import ModelCache
        # 清除ModelCache中缓存的旧客户端（目录已删，旧客户端失效）
        path_str = str(self.chroma_dir)
        if path_str in ModelCache._chroma_clients:
            del ModelCache._chroma_clients[path_str]
        client = ModelCache.get_chroma_client(path_str)
        coll = client.get_or_create_collection(
            name="experiences",
            metadata={"hnsw:space": "cosine"}
        )
        self._collection = coll
        self._chroma_client = client

        # 从SQLite重建向量索引（后台执行，不阻塞当前请求）
        def _rebuild_in_background():
            try:
                self.rebuild_vector_index()
                logger.info("经验库向量索引自动重建完成")
            except Exception as e:
                logger.error(f"经验库向量索引自动重建失败: {e}")
        threading.Thread(target=_rebuild_in_background, daemon=True).start()
        return coll

    # ================================================================
    # 定额校验（导入时自动审查）
    # ================================================================

    def _validate_quota_ids(self, bill_text: str, quota_ids: list[str],
                            quota_names: list[str] = None,
                            province: str = None) -> dict:
        """
        校验定额编号是否合理，防止错误数据进入经验库

        校验内容：
        1. 定额编号是否存在于定额库
        2. 编号是否带多余后缀（换、*系数等）
        3. 配电箱不应套接线箱定额
        4. 管内穿线不应套电缆定额（反之亦然）
        5. 回路数/DN/截面等参数是否严重超档

        返回:
            {
                "valid": True/False,      # 是否通过校验
                "cleaned_ids": [...],     # 清洗后的定额编号
                "cleaned_names": [...],   # 清洗后的定额名称（去掉无效的）
                "warnings": [...],        # 警告信息列表
                "errors": [...],          # 错误信息列表（有error则valid=False）
            }
        """
        warnings = []
        errors = []
        cleaned_ids = []
        cleaned_names = []
        quota_names = quota_names or []

        # 加载定额库映射（延迟加载，缓存到实例）
        quota_map = self._get_quota_map(province=province)

        for i, qid in enumerate(quota_ids):
            qname = quota_names[i] if i < len(quota_names) else ""
            original_qid = qid
            skip_this = False  # 标记是否跳过当前定额

            # --- 校验1: 清洗编号（去"换"后缀、"借"前缀、空格、乘数后缀） ---
            try:
                qid_clean = qid.strip().replace(" ", "")
                qid_clean = re.sub(r'换$', '', qid_clean)       # 去"换"后缀
                if qid_clean.startswith("借"):
                    qid_clean = qid_clean[1:]                    # 去"借"前缀
                qid_clean = re.sub(r'\*[\d.]+$', '', qid_clean)  # 去"*数量"后缀
                qid_clean = qid_clean.strip()
                if qid_clean != original_qid.strip():
                    warnings.append(f"定额编号'{original_qid}'已清洗为'{qid_clean}'")
            except Exception as e:
                logger.warning(f"经验库校验规则1（编号清洗）异常，跳过此规则: {e}")
                qid_clean = qid.strip()  # 清洗失败用原始值

            # --- 校验2: 编号是否存在（补子目直接跳过，不报错） ---
            try:
                if qid_clean.startswith("补子目"):
                    warnings.append(f"跳过补子目: '{original_qid}'")
                    continue
                if quota_map and qid_clean not in quota_map:
                    # 降级为警告，不阻止导入（人工预算中的编号可能是换算/借用后的变体）
                    warnings.append(f"定额编号'{qid_clean}'不在定额库中（仍保留导入）")
            except Exception as e:
                logger.warning(f"经验库校验规则2（编号存在性）异常，跳过此规则: {e}")

            # --- 校验3: 配电箱 vs 接线箱 ---
            try:
                if '配电箱' in bill_text and '接线箱' not in bill_text:
                    q_info = quota_map.get(qid_clean, {})
                    q_name = q_info.get('name', qname)
                    if '接线箱' in q_name and '配电' not in q_name:
                        errors.append(f"清单是配电箱，但定额'{qid_clean}'是接线箱，不匹配")
                        skip_this = True
            except Exception as e:
                logger.warning(f"经验库校验规则3（配电箱vs接线箱）异常，跳过此规则: {e}")

            if skip_this:
                continue

            # --- 校验4: 穿线 vs 电缆 ---
            try:
                if ('穿线' in bill_text or '穿铜芯线' in bill_text) and '电缆' not in bill_text:
                    q_info = quota_map.get(qid_clean, {})
                    q_name = q_info.get('name', qname)
                    if '电缆' in q_name and '穿线' not in q_name and '穿铜芯' not in q_name:
                        errors.append(f"清单是穿线，但定额'{qid_clean}'是电缆定额，不匹配")
                        skip_this = True
            except Exception as e:
                logger.warning(f"经验库校验规则4（穿线vs电缆）异常，跳过此规则: {e}")

            if skip_this:
                continue

            # --- 校验5: DN严重超档 ---
            try:
                bill_dn_m = re.search(r'DN\s*(\d+)', bill_text, re.IGNORECASE)
                if bill_dn_m and quota_map:
                    q_info = quota_map.get(qid_clean, {})
                    q_dn = q_info.get('dn')
                    if q_dn:
                        bill_dn = float(bill_dn_m.group(1))
                        quota_dn = float(q_dn)
                        if bill_dn > quota_dn * 2:
                            errors.append(f"DN严重超档：清单DN{int(bill_dn)}，定额'{qid_clean}'只到DN{int(quota_dn)}")
                            skip_this = True
            except Exception as e:
                logger.warning(f"经验库校验规则5（DN超档）异常，跳过此规则: {e}")

            if skip_this:
                continue

            # --- 校验6: 回路数严重不匹配 ---
            try:
                bill_circuit_m = re.search(r'(\d+)\s*回路', bill_text)
                if bill_circuit_m and quota_map:
                    q_info = quota_map.get(qid_clean, {})
                    q_name = q_info.get('name', qname)
                    q_circuits = q_info.get('circuits')
                    if q_circuits is not None:
                        bc = int(bill_circuit_m.group(1))
                        qc = int(q_circuits)
                        if bc > qc:
                            errors.append(f"回路超档：清单{bc}回路 > 定额'{qid_clean}'的{qc}回路")
                            skip_this = True
                    else:
                        q_circuit_m = re.search(r'(\d+)', q_name) if '回路' in q_name else None
                        if q_circuit_m:
                            bc = int(bill_circuit_m.group(1))
                            qc = int(q_circuit_m.group(1))
                            if bc > qc:
                                errors.append(f"回路超档：清单{bc}回路 > 定额'{qid_clean}'的{qc}回路")
                                skip_this = True
            except Exception as e:
                logger.warning(f"经验库校验规则6（回路超档）异常，跳过此规则: {e}")

            if skip_this:
                continue

            # 通过所有校验，保留此定额
            cleaned_ids.append(qid_clean)
            if i < len(quota_names):
                cleaned_names.append(quota_names[i])

        # 最终判断
        valid = len(cleaned_ids) > 0 and len(errors) == 0

        if warnings:
            logger.debug(f"经验库校验警告: {warnings}")
        if errors:
            logger.warning(f"经验库校验失败: {errors}")

        return {
            "valid": valid,
            "cleaned_ids": cleaned_ids,
            "cleaned_names": cleaned_names,
            "warnings": warnings,
            "errors": errors,
        }

    def _get_quota_map(self, province: str = None) -> dict:
        """获取定额库映射（按省份缓存，最多保留3个省份，避免内存无限增长）"""
        province = province or self.province
        cache_by_province = getattr(self, "_quota_map_cache_by_province", {})
        if province in cache_by_province:
            return cache_by_province[province]

        try:
            quota_db_path = config.get_quota_db_path(province)
            if not quota_db_path.exists():
                return {}
            conn = _db_connect(quota_db_path, row_factory=True)
            try:
                col_info = {
                    row[1] for row in conn.execute("PRAGMA table_info(quotas)").fetchall()
                }
                has_circuits_col = "circuits" in col_info
                select_cols = "quota_id, name, dn, cable_section, material"
                if has_circuits_col:
                    select_cols += ", circuits"
                rows = conn.execute(
                    f"SELECT {select_cols} FROM quotas"
                ).fetchall()
            finally:
                conn.close()
            quota_map = {
                row['quota_id']: {
                    'name': row['name'],
                    'dn': row['dn'],
                    'cable_section': row['cable_section'],
                    'material': row['material'],
                    'circuits': row['circuits'] if has_circuits_col else None,
                }
                for row in rows
            }
            # 缓存上限：最多保留3个省份的映射，超出时删除最早加载的
            if len(cache_by_province) >= 3:
                oldest_key = next(iter(cache_by_province))
                del cache_by_province[oldest_key]
                logger.debug(f"定额映射缓存已满，清除最早的省份: {oldest_key}")
            cache_by_province[province] = quota_map
            self._quota_map_cache_by_province = cache_by_province
            return quota_map
        except Exception as e:
            logger.warning(f"加载定额库映射失败: {e}")
            return {}

    def _get_quota_version_cached(self, province: str = None) -> str:
        province = province or self.province
        cache_by_province = getattr(self, "_quota_version_cache_by_province", {})
        if province in cache_by_province:
            return cache_by_province[province]
        version = config.get_current_quota_version(province)
        if len(cache_by_province) >= 6:
            oldest_key = next(iter(cache_by_province))
            del cache_by_province[oldest_key]
        cache_by_province[province] = version
        self._quota_version_cache_by_province = cache_by_province
        return version

    # ================================================================
    # 写入经验
    # ================================================================

    def add(self, entry: ExperienceInput) -> int:
        """添加经验记录（推荐接口，用ExperienceInput替代17个散装参数）。"""
        return self.add_experience(
            bill_text=entry.bill_text,
            quota_ids=entry.quota_ids,
            quota_names=entry.quota_names,
            materials=entry.materials,
            bill_name=entry.bill_name,
            bill_code=entry.bill_code,
            bill_unit=entry.bill_unit,
            source=entry.source,
            confidence=entry.confidence,
            province=entry.province,
            project_name=entry.project_name,
            notes=entry.notes,
            specialty=entry.specialty,
            skip_vector=entry.skip_vector,
            skip_fts=entry.skip_fts,
            feature_text=entry.feature_text,
            install_method=entry.install_method,
            parse_status=entry.parse_status,
        )

    def add_experience(self, bill_text: str, quota_ids: list[str],
                       quota_names: list[str] = None,
                       materials: list[dict] = None,
                       bill_name: str = None, bill_code: str = None,
                       bill_unit: str = None,
                       source: str = "auto_match",
                       confidence: int = 80,
                       province: str = None,
                       project_name: str = None,
                       notes: str = None,
                       specialty: str = None,
                       skip_vector: bool = False,
                       skip_fts: bool = False,
                       feature_text: str = None,
                       install_method: str = None,
                       parse_status: str = "") -> int:
        """
        添加一条经验记录

        参数:
            bill_text: 清单完整文本（名称+特征描述）
            quota_ids: 匹配的定额编号列表
            quota_names: 对应的定额名称列表
            materials: 主材列表 [{"quota_code":"4-14-379","name":"开关","code":"26010101Z@2","unit":"只"},...]
            source: 来源（user_correction/project_import/auto_match）
            confidence: 置信度（0-100）
            specialty: 所属专业册号（如"C10"），由specialty_classifier判断

        返回:
            新记录的ID，校验失败返回 -1
        """
        prepared = self._prepare_experience_payload(
            bill_text=bill_text,
            quota_ids=quota_ids,
            quota_names=quota_names,
            materials=materials,
            bill_name=bill_name,
            bill_code=bill_code,
            bill_unit=bill_unit,
            source=source,
            confidence=confidence,
            province=province,
            project_name=project_name,
            notes=notes,
            specialty=specialty,
            feature_text=feature_text,
            install_method=install_method,
            parse_status=parse_status,
        )
        if prepared is None:
            return -1

        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            record_id, inserted_new = self._write_prepared_experience(
                prepared,
                conn=conn,
                cursor=cursor,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 新建记录才追加向量索引；更新走原id即可
        # skip_vector=True 时跳过逐条写入（批量导入场景，导入完后统一调用 rebuild_vector_index）
        if not skip_fts:
            self._upsert_fts_record(
                record_id,
                bill_text=prepared["bill_text"],
                normalized_text=prepared["normalized_text"],
                feature_text=prepared["feature_text"] or "",
                quota_names_json=prepared["quota_names_json"],
            )
        if inserted_new:
            if not skip_vector:
                self._add_to_vector_index(record_id, prepared["bill_text"], province=prepared["province"])
            logger.debug(f"经验库新增: [{prepared['source']}] '{prepared['bill_text'][:50]}' → {prepared['quota_ids']}")
        else:
            logger.debug(f"经验库更新(事务路径): ID={record_id}, 来源={prepared['source']}")
        return record_id

    def _prepare_experience_payload(self, *,
                                    bill_text: str,
                                    quota_ids: list[str],
                                    quota_names: list[str] = None,
                                    materials: list[dict] = None,
                                    bill_name: str = None,
                                    bill_code: str = None,
                                    bill_unit: str = None,
                                    source: str = "auto_match",
                                    confidence: int = 80,
                                    province: str = None,
                                    project_name: str = None,
                                    notes: str = None,
                                    specialty: str = None,
                                    feature_text: str = None,
                                    install_method: str = None,
                                    parse_status: str = "") -> dict | None:
        province = province or self.province
        quota_names = quota_names or []
        materials = materials or []

        if quota_names and len(quota_names) != len(quota_ids):
            logger.warning(
                f"经验库写入拒绝: quota_ids({len(quota_ids)})与quota_names({len(quota_names)})"
                f"长度不一致, bill_text='{bill_text[:50]}'"
            )
            return None

        if quota_ids:
            cleaned_pairs = [
                (qid, quota_names[i] if quota_names and i < len(quota_names) else "")
                for i, qid in enumerate(quota_ids)
                if qid and str(qid).strip()
            ]
            if not cleaned_pairs:
                logger.warning(f"经验库写入拒绝: 所有quota_id均为空, bill_text='{bill_text[:50]}'")
                return None
            quota_ids = [p[0] for p in cleaned_pairs]
            quota_names = [p[1] for p in cleaned_pairs]

        if not specialty and quota_ids:
            for qid in quota_ids:
                inferred = get_book_from_quota_id(qid)
                if inferred:
                    specialty = inferred
                    break

        if source not in {"user_correction", "openclaw_approved"}:
            validation = self._validate_quota_ids(
                bill_text, quota_ids, quota_names, province=province)
            if not validation["valid"]:
                logger.warning(
                    f"经验库写入被拦截 [{source}]: '{bill_text[:50]}' → {quota_ids} "
                    f"原因: {validation['errors']}"
                )
                return None
            quota_ids = validation["cleaned_ids"]
            quota_names = validation["cleaned_names"]

        confidence = self._normalize_confidence_value(confidence)
        quota_db_ver = self._get_quota_version_cached(province)
        materials_signature = self._compute_material_signature(materials)
        quota_fingerprint, quota_codes_sorted_json = self._compute_quota_fingerprint(quota_ids)
        layer = self._determine_layer(
            source=source,
            bill_text=bill_text,
            bill_unit=bill_unit or "",
            specialty=specialty or "",
            quota_ids=quota_ids,
            quota_names=quota_names or [],
            confidence=confidence,
            parse_status=parse_status,
        )
        normalized_text = _normalize_for_match(bill_text) if _normalize_for_match else ""
        return {
            "bill_text": bill_text,
            "bill_name": bill_name,
            "bill_code": bill_code,
            "bill_unit": bill_unit,
            "quota_ids": quota_ids,
            "quota_names": quota_names or [],
            "quota_ids_json": self._json_dump(quota_ids),
            "quota_names_json": self._json_dump(quota_names or []),
            "materials": materials,
            "materials_json": self._json_dump(materials),
            "source": source,
            "confidence": confidence,
            "province": province,
            "project_name": project_name,
            "notes": notes,
            "specialty": specialty,
            "feature_text": feature_text,
            "install_method": install_method,
            "parse_status": parse_status,
            "quota_db_ver": quota_db_ver,
            "materials_signature": materials_signature,
            "quota_fingerprint": quota_fingerprint,
            "quota_codes_sorted_json": quota_codes_sorted_json,
            "layer": layer,
            "normalized_text": normalized_text,
            "now": time.time(),
        }

    def _write_prepared_experience(self, prepared: dict, *, conn, cursor,
                                   existing_id: int | None = None) -> tuple[int, bool]:
        existing = (existing_id,) if existing_id else None
        if existing is None:
            cursor.execute("""
                SELECT id FROM experiences
                WHERE bill_text = ? AND province = ?
                LIMIT 1
            """, (prepared["bill_text"], prepared["province"]))
            existing = cursor.fetchone()
        inserted_new = False

        if existing:
            record_id = self._update_experience(
                int(existing[0]), prepared["quota_ids"], prepared["quota_names"],
                prepared["source"], prepared["confidence"],
                quota_db_version=prepared["quota_db_ver"],
                materials_json=prepared["materials_json"],
                specialty=prepared["specialty"],
                project_name=prepared["project_name"],
                notes=prepared["notes"],
                feature_text=prepared["feature_text"],
                install_method=prepared["install_method"],
                materials_signature=prepared["materials_signature"],
                quota_fingerprint=prepared["quota_fingerprint"],
                quota_codes_sorted_json=prepared["quota_codes_sorted_json"],
                parse_status=prepared["parse_status"],
                bill_text=prepared["bill_text"],
                bill_unit=prepared["bill_unit"] or "",
                conn=conn, cursor=cursor, commit=False
            )
        else:
            cursor.execute("""
                INSERT INTO experiences
                (bill_text, bill_name, bill_code, bill_unit,
                 quota_ids, quota_names, materials, source, confidence,
                 confirm_count, province, project_name,
                 created_at, updated_at, notes, quota_db_version, layer, specialty,
                 normalized_text, feature_text, materials_signature,
                 install_method, quota_fingerprint, quota_codes_sorted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prepared["bill_text"], prepared["bill_name"], prepared["bill_code"], prepared["bill_unit"],
                prepared["quota_ids_json"], prepared["quota_names_json"], prepared["materials_json"],
                prepared["source"], prepared["confidence"],
                prepared["province"], prepared["project_name"], prepared["now"], prepared["now"],
                prepared["notes"], prepared["quota_db_ver"], prepared["layer"], prepared["specialty"],
                prepared["normalized_text"], prepared["feature_text"], prepared["materials_signature"],
                prepared["install_method"], prepared["quota_fingerprint"], prepared["quota_codes_sorted_json"],
            ))
            record_id = int(cursor.lastrowid)
            inserted_new = True
        return record_id, inserted_new

    def _prefetch_existing_experience_ids(self, prepared_records: list[dict], *, cursor) -> dict[tuple[str, str], int]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for prepared in prepared_records:
            province = str(prepared.get("province") or "")
            bill_text = str(prepared.get("bill_text") or "")
            if province and bill_text:
                grouped[province].append(bill_text)

        existing_ids: dict[tuple[str, str], int] = {}
        chunk_size = 300
        for province, bill_texts in grouped.items():
            unique_bill_texts = list(dict.fromkeys(bill_texts))
            for start in range(0, len(unique_bill_texts), chunk_size):
                chunk = unique_bill_texts[start:start + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                cursor.execute(
                    f"""
                    SELECT id, bill_text, province
                    FROM experiences
                    WHERE province = ?
                      AND bill_text IN ({placeholders})
                    """,
                    [province, *chunk],
                )
                for row in cursor.fetchall():
                    existing_ids[(str(row[2] or ""), str(row[1] or ""))] = int(row[0])
        return existing_ids

    def bulk_add_experiences(self, records: list[dict], *,
                             skip_vector: bool = True,
                             skip_fts: bool = True) -> dict:
        prepared_records: list[dict] = []
        rejected = 0
        prepared_cache: dict[str, dict | None] = {}
        for record in records or []:
            cache_key = self._json_dump(
                {
                    "bill_text": record.get("bill_text"),
                    "quota_ids": record.get("quota_ids"),
                    "quota_names": record.get("quota_names"),
                    "materials": record.get("materials"),
                    "bill_name": record.get("bill_name"),
                    "bill_code": record.get("bill_code"),
                    "bill_unit": record.get("bill_unit"),
                    "source": record.get("source"),
                    "confidence": record.get("confidence"),
                    "province": record.get("province"),
                    "project_name": record.get("project_name"),
                    "notes": record.get("notes"),
                    "specialty": record.get("specialty"),
                    "feature_text": record.get("feature_text"),
                    "install_method": record.get("install_method"),
                    "parse_status": record.get("parse_status"),
                }
            )
            if cache_key not in prepared_cache:
                prepared_cache[cache_key] = self._prepare_experience_payload(**record)
            prepared = prepared_cache[cache_key]
            if prepared is None:
                rejected += 1
                continue
            prepared_records.append(dict(prepared))

        if not prepared_records:
            return {"written": 0, "rejected": rejected, "inserted": 0, "updated": 0, "record_ids": []}

        conn = self._connect()
        cursor = conn.cursor()
        record_ids: list[int] = []
        inserted = 0
        updated = 0
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing_ids = self._prefetch_existing_experience_ids(prepared_records, cursor=cursor)
            for prepared in prepared_records:
                record_id, inserted_new = self._write_prepared_experience(
                    prepared,
                    conn=conn,
                    cursor=cursor,
                    existing_id=existing_ids.get((prepared["province"], prepared["bill_text"])),
                )
                record_ids.append(record_id)
                if inserted_new:
                    inserted += 1
                else:
                    updated += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if not skip_fts:
            for prepared, record_id in zip(prepared_records, record_ids):
                self._upsert_fts_record(
                    record_id,
                    bill_text=prepared["bill_text"],
                    normalized_text=prepared["normalized_text"],
                    feature_text=prepared["feature_text"] or "",
                    quota_names_json=prepared["quota_names_json"],
                )
        if not skip_vector:
            for prepared, record_id in zip(prepared_records, record_ids):
                self._add_to_vector_index(record_id, prepared["bill_text"], province=prepared["province"])

        return {
            "written": len(record_ids),
            "rejected": rejected,
            "inserted": inserted,
            "updated": updated,
            "record_ids": record_ids,
        }

    def add_experience_text(self, *,
                            province: str,
                            bill_text: str,
                            quota_ids: list[str],
                            quota_names: list[str] | None = None,
                            bill_name: str = "",
                            bill_code: str = "",
                            bill_unit: str = "",
                            specialty: str = "",
                            materials: list[dict] | None = None,
                            project_name: str = "",
                            notes: str = "",
                            confidence: int = 95) -> dict:
        """
        Minimal formal-layer write entry for staging promotions.

        This path is intentionally narrower than the general add_experience() flow:
        - requires reviewed structured payload
        - deduplicates exact authority records
        - uses user_correction semantics so reviewed staging can correct old records
        """
        province = str(province or "").strip()
        bill_text = str(bill_text or "").strip()
        bill_name = str(bill_name or "").strip()
        bill_code = str(bill_code or "").strip()
        bill_unit = str(bill_unit or "").strip()
        specialty = str(specialty or "").strip()
        project_name = str(project_name or "").strip()
        notes = str(notes or "").strip()
        if isinstance(quota_ids, str):
            quota_ids = [quota_ids]
        if isinstance(quota_names, str):
            quota_names = [quota_names]
        quota_ids = [str(item).strip() for item in (quota_ids or []) if str(item).strip()]
        quota_names = [str(item).strip() for item in (quota_names or []) if str(item).strip()]
        materials = materials if isinstance(materials, list) else []

        if not province or not bill_text or not quota_ids:
            raise ValueError("province, bill_text and quota_ids are required")
        if quota_names and len(quota_names) != len(quota_ids):
            raise ValueError("quota_names must align with quota_ids")

        content_hash = hashlib.md5(
            json.dumps(
                {
                    "province": province,
                    "bill_text": bill_text,
                    "quota_ids": quota_ids,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        conn = self._connect(row_factory=True)
        try:
            row = conn.execute(
                """
                SELECT id, quota_ids, quota_names, layer
                FROM experiences
                WHERE bill_text = ? AND province = ?
                ORDER BY confidence DESC, confirm_count DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                (bill_text, province),
            ).fetchone()
        finally:
            conn.close()

        if row:
            existing = self._normalize_record_quota_fields(dict(row))
            if existing.get("layer") == "authority" and existing.get("quota_ids") == quota_ids:
                return {
                    "experience_id": int(existing["id"]),
                    "added": False,
                    "skipped": True,
                    "content_hash": content_hash,
                }

        record_id = self.add_experience(
            bill_text=bill_text,
            bill_name=bill_name or None,
            bill_code=bill_code or None,
            bill_unit=bill_unit or None,
            quota_ids=quota_ids,
            quota_names=quota_names or [],
            materials=materials,
            source="user_correction",
            confidence=max(int(confidence or 95), 95),
            province=province,
            project_name=project_name or None,
            notes=notes or None,
            specialty=specialty or None,
        )
        if record_id <= 0:
            raise ValueError("failed to write reviewed experience into ExperienceDB")

        return {
            "experience_id": int(record_id),
            "added": True,
            "skipped": False,
            "content_hash": content_hash,
        }

    def _update_experience(self, record_id: int, quota_ids: list[str],
                           quota_names: list[str], source: str,
                           confidence: int, quota_db_version: str = None,
                           materials_json: str = None,
                           specialty: str = None,
                           project_name: str = None,
                           notes: str = None,
                           feature_text: str = None,
                           install_method: str = None,
                           materials_signature: str = None,
                           quota_fingerprint: str = None,
                           quota_codes_sorted_json: str = None,
                           parse_status: str = "",
                           bill_text: str = "",
                           bill_unit: str = "",
                           conn=None, cursor=None,
                           commit: bool = True) -> int:
        """更新已有的经验记录

        按来源分级处理，防止 auto_match 不断膨胀置信度：
        - user_correction:       用户手动换了定额 → 更新定额 + 大幅涨分(+10)
        - user_confirmed:        用户点了"确认正确" → 涨分(+5) + 确认次数+1
        - project_import:        从已完成项目导入 → 小幅涨分(+2)
        - project_import_suspect:导入时审核不通过 → 降级到候选层，不涨分
        - auto_match:            系统自动匹配 → 只更新时间戳，不涨分不涨确认次数
        """
        now = time.time()

        owns_conn = conn is None or cursor is None
        if owns_conn:
            conn = self._connect()
            cursor = conn.cursor()

        confidence_floor = self._normalize_confidence_value(confidence)
        target_layer = self._determine_layer(
            source=source,
            bill_text=bill_text,
            bill_unit=bill_unit,
            specialty=specialty or "",
            quota_ids=quota_ids,
            quota_names=quota_names or [],
            confidence=confidence_floor,
            parse_status=parse_status,
        )

        if source in {"user_correction", "openclaw_approved"}:
            # 用户手动修正 → 最高信任：更新定额、涨分、涨确认次数、晋升权威层
            cursor.execute("""
                UPDATE experiences SET
                    quota_ids = ?,
                    quota_names = ?,
                    source = ?,
                    confidence = MIN(MAX(confidence + 10, ?), 100),
                    confirm_count = confirm_count + 1,
                    layer = 'authority',
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ?
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                source, confidence_floor, quota_db_version,
                specialty or '', now, record_id,
            ))
        elif source == "user_confirmed":
            # 用户点了"确认正确" → 高信任：涨分、涨确认次数、晋升权威层（但不改定额）
            cursor.execute("""
                UPDATE experiences SET
                    source = CASE
                        WHEN source = 'user_correction' THEN source
                        ELSE 'user_confirmed'
                    END,
                    confidence = MIN(MAX(confidence + 5, ?), 100),
                    confirm_count = confirm_count + 1,
                    layer = 'authority',
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ?
            """, (confidence_floor, quota_db_version,
                  specialty or '', now, record_id))
        elif source == "project_import":
            # 已完成项目导入 → 中等信任：小幅涨分，刷新定额和主材（解析改进后重新导入能修正旧数据）
            # 同时晋升：如果之前是 project_import_suspect（候选层），此次干净导入应恢复为权威层
            project_floor = self._clamp(confidence_floor, 0, 95)
            cursor.execute("""
                UPDATE experiences SET
                    quota_ids = ?,
                    quota_names = ?,
                    confidence = MIN(MAX(confidence + 2, ?), 95),
                    confirm_count = confirm_count + 1,
                    layer = 'candidate',
                    source = 'project_import',
                    materials = CASE
                        WHEN ? != '[]' THEN ?
                        ELSE materials
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed', 'openclaw_approved')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                project_floor, materials_json or '[]', materials_json or '[]',
                quota_db_version, specialty or '', now, record_id,
            ))
        elif source in {"completed_project", "reviewed_import"}:
            verified_floor = self._clamp(confidence_floor, 0, 95)
            cursor.execute("""
                UPDATE experiences SET
                    quota_ids = ?,
                    quota_names = ?,
                    confidence = MIN(MAX(confidence + 2, ?), 95),
                    confirm_count = confirm_count + 1,
                    layer = ?,
                    source = ?,
                    materials = CASE
                        WHEN ? != '[]' THEN ?
                        ELSE materials
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed', 'openclaw_approved')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                verified_floor, target_layer, source,
                materials_json or '[]', materials_json or '[]',
                quota_db_version, specialty or '', now, record_id,
            ))
        elif source == "project_import_suspect":
            # 导入时审核不通过 → 降级到候选层，不涨分，更新定额（方便后续人工审核）
            cursor.execute("""
                UPDATE experiences SET
                    quota_ids = ?,
                    quota_names = ?,
                    layer = 'candidate',
                    source = 'project_import_suspect',
                    confidence = MIN(confidence, ?),
                    materials = CASE
                        WHEN ? != '[]' THEN ?
                        ELSE materials
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed', 'openclaw_approved')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                confidence_floor, materials_json or '[]', materials_json or '[]',
                quota_db_version, specialty or '', now, record_id,
            ))
        elif source == "batch_import":
            # 批量导入（外部XML等数据）→ 进候选层，但允许多项目确认后自动晋升
            # 关键逻辑：同一项目重复导入不涨确认次数，不同项目独立确认才涨
            # 不覆盖用户手动修正/确认过的记录，也不降级已有的 project_import 权威层记录
            cursor.execute("""
                UPDATE experiences SET
                    quota_ids = ?,
                    quota_names = ?,
                    materials = CASE
                        WHEN ? != '[]' THEN ?
                        ELSE materials
                    END,
                    source = 'batch_import',
                    layer = CASE WHEN layer = 'authority' THEN 'authority' ELSE 'candidate' END,
                    confirm_count = CASE
                        WHEN quota_ids = ?
                         AND ? IS NOT NULL
                         AND (project_name IS NULL OR project_name != ?)
                        THEN confirm_count + 1
                        ELSE confirm_count
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed', 'openclaw_approved', 'project_import')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                materials_json or '[]', materials_json or '[]',
                self._json_dump(quota_ids), project_name, project_name,
                quota_db_version, specialty or '', now, record_id,
            ))
        else:
            # auto_match / auto_review 或其他未知来源
            # 如果定额编号一致（多次匹配结果相同），递增确认次数；否则只记录时间
            cursor.execute("""
                UPDATE experiences SET
                    confirm_count = CASE
                        WHEN quota_ids = ? THEN confirm_count + 1
                        ELSE confirm_count
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ?
            """, (self._json_dump(quota_ids), quota_db_version,
                  specialty or '', now, record_id))

        cursor.execute("""
            UPDATE experiences SET
                feature_text = COALESCE(NULLIF(?, ''), feature_text),
                install_method = COALESCE(NULLIF(?, ''), install_method),
                materials_signature = CASE
                    WHEN ? IS NOT NULL AND ? != '' THEN ?
                    ELSE materials_signature
                END,
                quota_fingerprint = COALESCE(NULLIF(?, ''), quota_fingerprint),
                quota_codes_sorted = COALESCE(NULLIF(?, ''), quota_codes_sorted)
            WHERE id = ?
        """, (
            feature_text or "",
            install_method or "",
            materials_signature, materials_signature, materials_signature,
            quota_fingerprint or "",
            quota_codes_sorted_json or "",
            record_id,
        ))

        if notes:
            cursor.execute("""
                UPDATE experiences SET
                    notes = CASE
                        WHEN notes IS NULL OR notes = '' THEN ?
                        ELSE notes || '\n' || ?
                    END
                WHERE id = ?
            """, (notes, notes, record_id))

        if commit:
            conn.commit()
            self._upsert_fts_record(
                record_id,
                bill_text=bill_text,
                normalized_text=_normalize_for_match(bill_text) if _normalize_for_match else bill_text,
                feature_text=feature_text or "",
                quota_names_json=self._json_dump(quota_names or []),
            )
        if owns_conn:
            conn.close()

        logger.debug(f"经验库更新: ID={record_id}, 来源={source}")
        return record_id

    def _find_exact_match(self, bill_text: str, province: str,
                          authority_only: bool = False) -> dict:
        """精确查找相同清单文本的经验记录（含L7归一化模糊匹配）

        匹配优先级：
          第1级：bill_text 完全相同（最精确）
          第2级：normalized_text 相同（L7模糊匹配，容忍空格/标点/格式差异）

        参数:
            bill_text: 清单文本
            province: 省份
            authority_only: 是否只查权威层（直通匹配时为True）
        """
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            authority_clause = " AND layer = 'authority'" if authority_only else ""

            # 第1级：完全精确匹配（现有逻辑不变）
            cursor.execute(f"""
                SELECT * FROM experiences
                WHERE bill_text = ? AND province = ?{authority_clause}
                ORDER BY confidence DESC, confirm_count DESC, updated_at DESC, id DESC
                LIMIT 1
            """, (bill_text, province))
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 第2级：L7 归一化匹配（容忍格式差异）
            if getattr(config, 'EXPERIENCE_FUZZY_MATCH_ENABLED', False) and _normalize_for_match:
                try:
                    norm_text = _normalize_for_match(bill_text)
                    if norm_text:  # 归一化后非空才查（避免空字符串匹配所有空记录）
                        cursor.execute(f"""
                            SELECT * FROM experiences
                            WHERE normalized_text = ? AND province = ?
                                  AND normalized_text != ''{authority_clause}
                            ORDER BY confidence DESC, confirm_count DESC, updated_at DESC, id DESC
                            LIMIT 1
                        """, (norm_text, province))
                        row = cursor.fetchone()
                        if row:
                            result = dict(row)
                            result["_match_method"] = "normalized"  # 标记匹配方式（调试用）
                            return result
                except Exception as e:
                    logger.debug(f"归一化匹配异常（不影响主流程）: {e}")
        finally:
            conn.close()

        return None

    def _add_to_vector_index(self, record_id: int, bill_text: str,
                             province: str = None):
        """将经验记录添加到向量索引（带省份metadata，支持按省份过滤）"""
        province = province or self.province
        try:
            if self.collection is None:
                logger.warning("向量索引不可用，跳过添加")
                return
            from src.model_profile import encode_documents
            embedding = encode_documents(self.model, [bill_text])
            self.collection.upsert(
                ids=[str(record_id)],
                documents=[bill_text],
                embeddings=embedding.tolist(),
                metadatas=[{"province": province}],
            )
        except Exception as e:
            logger.warning(f"向量索引添加失败: {e}")

    # ================================================================
    # 查询经验
    # ================================================================

    def backfill_experience_enhancements(self, *,
                                         batch_size: int = 1000,
                                         limit: int | None = None,
                                         sources: list[str] | None = None,
                                         include_deleted: bool = False,
                                         dry_run: bool = False) -> dict:
        batch_size = max(int(batch_size or 1000), 1)
        remaining = None if limit is None else max(int(limit), 0)
        source_filters = [self._safe_text(item) for item in (sources or []) if self._safe_text(item)]
        processed = 0
        updated = 0
        normalized_updated = 0
        material_updated = 0
        quota_updated = 0
        layer_updated = 0
        layer_transitions: dict[str, int] = defaultdict(int)

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            where_clauses = []
            params: list = []
            if not include_deleted:
                where_clauses.append("COALESCE(layer, '') != 'deleted'")
            if source_filters:
                placeholders = ",".join(["?"] * len(source_filters))
                where_clauses.append(f"source IN ({placeholders})")
                params.extend(source_filters)
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            offset = 0
            while remaining is None or remaining > 0:
                fetch_size = batch_size if remaining is None else min(batch_size, remaining)
                cursor.execute(
                    f"""
                    SELECT id, bill_text, bill_unit, specialty, quota_ids, quota_names,
                           materials, source, confidence, layer, normalized_text,
                           materials_signature, quota_fingerprint, quota_codes_sorted
                    FROM experiences
                    {where_sql}
                    ORDER BY id
                    LIMIT ? OFFSET ?
                    """,
                    [*params, fetch_size, offset],
                )
                rows = cursor.fetchall()
                if not rows:
                    break
                updates = []
                for row in rows:
                    record = dict(row)
                    processed += 1
                    quota_ids = safe_json_list(record.get("quota_ids"))
                    normalized_text = _normalize_for_match(record.get("bill_text", "")) if _normalize_for_match else self._safe_text(record.get("bill_text"))
                    materials_signature = self._compute_material_signature(record.get("materials"))
                    quota_fingerprint, quota_codes_sorted_json = self._compute_quota_fingerprint(quota_ids)
                    target_layer = self._determine_backfill_layer(record)

                    current_normalized = self._safe_text(record.get("normalized_text"))
                    current_material = self._safe_text(record.get("materials_signature"))
                    current_fingerprint = self._safe_text(record.get("quota_fingerprint"))
                    current_codes = self._safe_text(record.get("quota_codes_sorted"))
                    current_layer = self._safe_text(record.get("layer"))

                    changed = False
                    if normalized_text != current_normalized:
                        normalized_updated += 1
                        changed = True
                    if materials_signature != current_material:
                        material_updated += 1
                        changed = True
                    if quota_fingerprint != current_fingerprint or quota_codes_sorted_json != current_codes:
                        quota_updated += 1
                        changed = True
                    if target_layer != current_layer:
                        layer_updated += 1
                        layer_transitions[f"{current_layer or 'empty'}->{target_layer or 'empty'}"] += 1
                        changed = True
                    if changed:
                        updated += 1
                        updates.append((
                            normalized_text,
                            materials_signature,
                            quota_fingerprint,
                            quota_codes_sorted_json,
                            target_layer,
                            int(record["id"]),
                        ))

                if updates and not dry_run:
                    cursor.executemany(
                        """
                        UPDATE experiences
                        SET normalized_text = ?,
                            materials_signature = ?,
                            quota_fingerprint = ?,
                            quota_codes_sorted = ?,
                            layer = ?
                        WHERE id = ?
                        """,
                        updates,
                    )
                    conn.commit()

                offset += len(rows)
                if remaining is not None:
                    remaining -= len(rows)

            if not dry_run:
                self.build_fts_index(conn=conn)
        finally:
            conn.close()

        return {
            "processed": processed,
            "updated": updated,
            "normalized_updated": normalized_updated,
            "materials_signature_updated": material_updated,
            "quota_fingerprint_updated": quota_updated,
            "layer_updated": layer_updated,
            "layer_transitions": dict(sorted(layer_transitions.items())),
            "dry_run": bool(dry_run),
        }

    def run_promotion_scan(self, *,
                           batch_size: int = 500,
                           limit_groups: int | None = None,
                           group_keys: list[str] | None = None,
                           dry_run: bool = False) -> dict:
        batch_size = max(int(batch_size or 500), 1)
        group_key_filters = [self._safe_text(item) for item in (group_keys or []) if self._safe_text(item)]
        promoted_records = 0
        promoted_groups = 0
        scanned_groups = 0
        skipped_conflicts = 0
        skipped_threshold = 0
        promotion_logs = []
        updates = []
        now = time.time()

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, normalized_text, specialty, bill_unit, quota_db_version,
                       quota_fingerprint, project_name, layer
                FROM experiences
                WHERE layer = 'verified'
                  AND normalized_text IS NOT NULL AND normalized_text != ''
                  AND specialty IS NOT NULL AND specialty != ''
                  AND bill_unit IS NOT NULL AND bill_unit != ''
                  AND quota_db_version IS NOT NULL AND quota_db_version != ''
                  AND quota_fingerprint IS NOT NULL AND quota_fingerprint != ''
                ORDER BY normalized_text, specialty, bill_unit, quota_db_version, id
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
            grouped: dict[str, list[dict]] = defaultdict(list)
            for row in rows:
                group_key = self._build_group_key(
                    row.get("normalized_text"),
                    row.get("specialty"),
                    row.get("bill_unit"),
                    row.get("quota_db_version"),
                )
                if group_key_filters and group_key not in group_key_filters:
                    continue
                row["group_key"] = group_key
                grouped[group_key].append(row)

            sorted_group_keys = sorted(grouped.keys())
            if limit_groups is not None:
                sorted_group_keys = sorted_group_keys[:max(int(limit_groups), 0)]

            for group_key in sorted_group_keys:
                group_rows = grouped[group_key]
                scanned_groups += 1
                normalized_text = self._safe_text(group_rows[0].get("normalized_text"))
                specialty = self._safe_text(group_rows[0].get("specialty"))
                bill_unit = self._safe_text(group_rows[0].get("bill_unit"))
                quota_version = self._safe_text(group_rows[0].get("quota_db_version"))

                cursor.execute(
                    """
                    SELECT COUNT(DISTINCT quota_fingerprint)
                    FROM experiences
                    WHERE layer = 'authority'
                      AND normalized_text = ?
                      AND specialty = ?
                      AND bill_unit = ?
                      AND quota_db_version = ?
                      AND quota_fingerprint IS NOT NULL
                      AND quota_fingerprint != ''
                    """,
                    (normalized_text, specialty, bill_unit, quota_version),
                )
                authority_fingerprint_count = cursor.fetchone()[0] or 0
                if authority_fingerprint_count > 1:
                    skipped_conflicts += 1
                    continue

                project_set = {self._safe_text(row.get("project_name")) for row in group_rows if self._safe_text(row.get("project_name"))}
                if len(project_set) < 3:
                    skipped_threshold += 1
                    continue

                fingerprint_projects: dict[str, set[str]] = defaultdict(set)
                fingerprint_record_ids: dict[str, list[int]] = defaultdict(list)
                for row in group_rows:
                    fingerprint = self._safe_text(row.get("quota_fingerprint"))
                    project_name = self._safe_text(row.get("project_name"))
                    if not fingerprint or not project_name:
                        continue
                    fingerprint_projects[fingerprint].add(project_name)
                    fingerprint_record_ids[fingerprint].append(int(row["id"]))

                if not fingerprint_projects:
                    skipped_threshold += 1
                    continue

                dominant_fingerprint, dominant_projects = max(
                    fingerprint_projects.items(),
                    key=lambda item: (len(item[1]), item[0]),
                )
                total_projects = len(project_set)
                matching_project_count = len(dominant_projects)
                quota_consistency_rate = matching_project_count / max(total_projects, 1)
                if matching_project_count < 3 or quota_consistency_rate < 0.8:
                    skipped_threshold += 1
                    continue

                record_ids = sorted(set(fingerprint_record_ids.get(dominant_fingerprint) or []))
                if not record_ids:
                    skipped_threshold += 1
                    continue

                promoted_groups += 1
                promoted_records += len(record_ids)
                for record_id in record_ids:
                    updates.append((now, "verified", "multi_project_promoted", record_id))
                promotion_logs.append((
                    group_key,
                    record_ids,
                    matching_project_count,
                    quota_consistency_rate,
                ))

            if updates and not dry_run:
                for chunk_start in range(0, len(updates), batch_size):
                    chunk = updates[chunk_start:chunk_start + batch_size]
                    cursor.executemany(
                        """
                        UPDATE experiences
                        SET promoted_at = ?,
                            promoted_from = ?,
                            source = ?,
                            layer = 'authority'
                        WHERE id = ?
                          AND layer = 'verified'
                        """,
                        chunk,
                    )
                for group_key, record_ids, matching_project_count, quota_consistency_rate in promotion_logs:
                    for record_id in record_ids:
                        cursor.execute(
                            """
                            INSERT INTO promotion_log
                            (experience_id, from_layer, to_layer, group_key,
                             matching_project_count, quota_consistency_rate, promoted_at)
                            VALUES (?, 'verified', 'authority', ?, ?, ?, ?)
                            """,
                            (record_id, group_key, matching_project_count, quota_consistency_rate, now),
                        )
                conn.commit()
        finally:
            conn.close()

        return {
            "scanned_groups": scanned_groups,
            "promoted_groups": promoted_groups,
            "promoted_records": promoted_records,
            "skipped_conflicts": skipped_conflicts,
            "skipped_threshold": skipped_threshold,
            "dry_run": bool(dry_run),
        }

    def search_experience(self, query_text: str, *, top_k: int = 10,
                          min_confidence: int = 60, province: str = None,
                          specialty: str = "", unit: str = "",
                          materials_signature: str = "",
                          install_method: str = "",
                          quota_version: str = "") -> list[dict]:
        province = province or self.province
        query_item = self._build_query_item(
            query_text,
            province=province,
            specialty=specialty,
            unit=unit,
            materials_signature=materials_signature,
            install_method=install_method,
            quota_version=quota_version,
        )

        merged: dict[int, dict] = {}
        has_runtime_recall_backend = getattr(self, "db_path", None) is not None or "_connect" in getattr(self, "__dict__", {})

        # 直通优先：本省 authority 精确命中且版本一致，直接 green 返回
        exact = self._find_exact_match(query_text, province, authority_only=True)
        if exact and exact.get("confidence", 0) >= min_confidence:
            self._normalize_record_quota_fields(exact)
            exact["similarity"] = 1.0
            exact["_exact_match"] = True
            exact["recalled_by"] = ["exact"]
            exact["raw_scores"] = {"bm25": 1.0, "vector": 1.0}
            exact["penalty_factor"] = 1.0
            total_score, dimension_scores = self._compute_experience_total_score(exact, query_item)
            exact["total_score"] = total_score
            exact["dimension_scores"] = dimension_scores
            self._apply_gate(exact, query_item)
            if exact.get("gate") == "green":
                exact["match_type"] = "exact"
                return [exact]
            # 轻量 stub 场景没有真实 recall backend，直接保留 non-green exact 作为 stale/similar 兜底。
            if not has_runtime_recall_backend:
                record_version = exact.get("quota_db_version", "")
                current_version = query_item.get("quota_version") or ""
                if exact.get("layer") == "candidate":
                    exact["match_type"] = "candidate"
                elif current_version and record_version and current_version == record_version:
                    exact["match_type"] = "similar"
                else:
                    exact["match_type"] = "stale"
                return [exact]
            # 非 green 的精确命中仍保留为候选，供 stale/similar 排序兜底。
            self._merge_recall_results(merged, [exact], channel="exact")

        expansion_steps = [
            ("authority", "local"),
            ("authority", "global"),
            ("verified", "local"),
            ("verified", "global"),
            ("candidate", "local"),
            ("candidate", "global"),
        ]
        vector_pairs = self._recall_vector_candidates(query_text, top_k=top_k, province=province)
        if has_runtime_recall_backend:
            vector_records = self._fetch_records_by_ids([item[0] for item in vector_pairs], min_confidence=min_confidence)
            for record_id, similarity in vector_pairs:
                if record_id in vector_records:
                    record = vector_records[record_id]
                    record["vector_score"] = similarity
                    record["similarity"] = similarity
                    self._merge_recall_results(merged, [record], channel="vector")

            for step_index, (layer, province_mode) in enumerate(expansion_steps, start=1):
                try:
                    exact_records = self._recall_exact(query_item, layer=layer, province_mode=province_mode, limit=top_k * 2)
                except Exception as exc:
                    logger.debug(f"经验库 exact 召回失败，降级跳过: {exc}")
                    exact_records = []
                for item in exact_records:
                    item["_exact_match"] = True
                    item["similarity"] = 1.0
                self._merge_recall_results(merged, exact_records, channel="exact")

                try:
                    bm25_records = self._recall_bm25(query_item, layer=layer, province_mode=province_mode, limit=top_k * 3)
                except Exception as exc:
                    logger.debug(f"经验库 BM25 召回失败，降级跳过: {exc}")
                    bm25_records = []
                self._merge_recall_results(merged, bm25_records, channel="bm25")

                try:
                    structural_records = self._recall_structural(query_item, layer=layer, province_mode=province_mode, limit=top_k * 3)
                except Exception as exc:
                    logger.debug(f"经验库结构化召回失败，降级跳过: {exc}")
                    structural_records = []
                self._merge_recall_results(merged, structural_records, channel="structural")

                if not self._expand_query_layers(merged, current_step=step_index):
                    break

        ranked = []
        current_version = query_item.get("quota_version") or ""
        for record in merged.values():
            if int(record.get("confidence") or 0) < min_confidence:
                continue
            self._normalize_record_quota_fields(record)
            filtered = self._hard_filter(record, query_item)
            if not filtered:
                continue
            total_score, dimension_scores = self._compute_experience_total_score(filtered, query_item)
            filtered["total_score"] = total_score
            filtered["dimension_scores"] = dimension_scores
            filtered["similarity"] = max(
                float(filtered.get("similarity", 0.0) or 0.0),
                float(filtered.get("bm25_score", 0.0) or 0.0),
                float(filtered.get("vector_score", 0.0) or 0.0),
            )
            record_version = filtered.get("quota_db_version", "")
            if filtered.get("layer") == "candidate":
                filtered["match_type"] = "candidate"
            elif current_version and record_version and current_version == record_version:
                filtered["match_type"] = "similar"
            else:
                filtered["match_type"] = "stale"
            self._apply_gate(filtered, query_item)
            ranked.append(filtered)

        layer_priority = {"authority": 0, "verified": 1, "candidate": 2}
        gate_priority = {"green": 0, "yellow": 1, "red": 2}
        ranked.sort(
            key=lambda item: (
                gate_priority.get(item.get("gate", "red"), 2),
                -float(item.get("total_score", 0.0) or 0.0),
                layer_priority.get(item.get("layer", "candidate"), 2),
                -int(item.get("confidence", 0) or 0),
                -int(item.get("confirm_count", 0) or 0),
            )
        )
        return ranked[:top_k]

    def search_similar(self, query_text: str, top_k: int = 5,
                       min_confidence: int = 60,
                       province: str = None) -> list[dict]:
        return self.search_experience(
            query_text,
            top_k=top_k,
            min_confidence=min_confidence,
            province=province,
        )

    def get_feedback_bias_data(self, province: str, limit: int = 2000) -> list[tuple[str, str]]:
        """获取反馈偏置计算所需的原始数据（source, bill_text）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT source, bill_text
                FROM experiences
                WHERE bill_text IS NOT NULL
                  AND province = ?
                  AND source IN ('user_correction', 'user_confirmed')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (province, limit),
            ).fetchall()
            return [(r["source"], r["bill_text"]) for r in rows]
        except Exception:
            return []

    def search_cross_province(self, query_text: str, current_province: str,
                              top_k: int = 3) -> list[dict]:
        """L5跨省搜索：查其他省份的经验作为搜索参考

        只搜权威层+高置信度数据，排除当前省份。
        返回定额名称（不含编号）作为搜索提示，不直通匹配。

        参数:
            query_text: 清单文本
            current_province: 当前省份（将被排除）
            top_k: 返回条数

        返回:
            [{quota_names: [...], similarity: float, source_province: str}, ...]
        """
        min_similarity = getattr(config, "CROSS_PROVINCE_MIN_SIMILARITY", 0.80)
        min_confidence = getattr(config, "CROSS_PROVINCE_MIN_CONFIDENCE", 85)

        # 向量搜索关闭时直接跳过
        if not getattr(config, "VECTOR_ENABLED", True):
            return []

        if self.collection is None:
            logger.warning("经验库向量索引不可用，跳过跨省搜索")
            return []
        coll = self.collection  # 缓存到局部变量
        collection_count = coll.count()
        if collection_count == 0:
            return []

        try:
            if self.model is None:
                return []

            query_prefix = ""  # 前缀由model_profile统一管理
            from src.model_profile import encode_queries
            query_embedding = encode_queries(self.model, [query_text])
            # 向量搜索全库（不按省份过滤）
            n_results = min(max(top_k * 5, 20), collection_count)
            results = coll.query(
                query_embeddings=query_embedding.tolist(),
                n_results=n_results,
            )

            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            raw_ids = results["ids"][0]
            raw_distances = results.get("distances", [[]])[0]
            distances = list(raw_distances[:len(raw_ids)])
            if len(distances) < len(raw_ids):
                distances.extend([1.0] * (len(raw_ids) - len(distances)))

            matched_ids = []
            similarities = []
            for mid, dist in zip(raw_ids, distances):
                try:
                    db_id = int(mid)
                except (TypeError, ValueError):
                    continue
                sim = max(0.0, min(1.0, 1 - dist))
                if sim >= min_similarity:
                    matched_ids.append(db_id)
                    similarities.append(sim)

            if not matched_ids:
                return []

            # SQL过滤：排除当前省份，只取权威层+高置信度
            conn = self._connect(row_factory=True)
            try:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(matched_ids))
                cursor.execute(f"""
                    SELECT id, bill_text, quota_names, province, confidence
                    FROM experiences
                    WHERE id IN ({placeholders})
                    AND province != ?
                    AND layer = 'authority'
                    AND confidence >= ?
                """, matched_ids + [current_province, min_confidence])
                rows = {row["id"]: dict(row) for row in cursor.fetchall()}
            finally:
                conn.close()

            # 组装结果（只返回定额名称，不返回编号）
            cross_refs = []
            for db_id, sim in zip(matched_ids, similarities):
                if db_id in rows:
                    record = rows[db_id]
                    quota_names_raw = record.get("quota_names", "[]")
                    try:
                        import json
                        quota_names = json.loads(quota_names_raw) if isinstance(
                            quota_names_raw, str) else quota_names_raw
                    except (json.JSONDecodeError, TypeError):
                        quota_names = []
                    # 归一化为 list[str]：防止字符串被extend拆成单字符
                    if isinstance(quota_names, str):
                        quota_names = [quota_names]
                    elif isinstance(quota_names, list):
                        quota_names = [str(n) for n in quota_names if n]
                    else:
                        quota_names = []
                    if quota_names:
                        cross_refs.append({
                            "quota_names": quota_names,
                            "similarity": sim,
                            "source_province": record.get("province", ""),
                            "confidence": record.get("confidence", 0),
                        })

            # 按相似度排序，截断
            cross_refs.sort(key=lambda x: -x["similarity"])
            return cross_refs[:top_k]

        except Exception as e:
            logger.debug(f"L5跨省搜索失败（不影响主流程）: {e}")
            return []

    def find_experience(self, bill_text: str, province: str = None,
                        limit: int = 20) -> list[dict]:
        """
        兼容查询接口：按清单文本/名称查找经验记录（用于工具脚本快速排查）。

        排序规则：
        1. 精确 bill_text 命中
        2. 精确 bill_name 命中
        3. bill_text LIKE 命中
        4. bill_name LIKE 命中
        5. 同组内按 confidence/confirm_count/updated_at/id 逆序
        """
        text = (bill_text or "").strip()
        if not text:
            return []

        try:
            limit_val = int(limit)
        except (TypeError, ValueError):
            limit_val = 20
        limit_val = max(1, min(limit_val, 100))

        province = province or self.province
        like_pattern = f"%{text}%"
        text_match_condition = """
            (
                bill_text = ?
                OR COALESCE(bill_name, '') = ?
                OR bill_text LIKE ?
                OR COALESCE(bill_name, '') LIKE ?
            )
        """
        rank_order_clause = """
            CASE
                WHEN bill_text = ? THEN 0
                WHEN COALESCE(bill_name, '') = ? THEN 1
                WHEN bill_text LIKE ? THEN 2
                WHEN COALESCE(bill_name, '') LIKE ? THEN 3
                ELSE 4
            END ASC,
            confidence DESC,
            confirm_count DESC,
            updated_at DESC,
            id DESC
        """
        where_clause = text_match_condition
        params = [text, text, like_pattern, like_pattern]
        if province:
            where_clause = f"province = ? AND {text_match_condition}"
            params.insert(0, province)
        params.extend([text, text, like_pattern, like_pattern, limit_val])

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT *
                FROM experiences
                WHERE {where_clause}
                ORDER BY {rank_order_clause}
                LIMIT ?
            """, params)
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning(f"查询经验记录失败: {e}")
            return []
        finally:
            conn.close()

        records = []
        for row in rows:
            item = dict(row)
            records.append(self._normalize_record_quota_fields(item))
        return records

    @staticmethod
    def _experience_importer_module():
        from src import experience_importer
        return experience_importer

    @staticmethod
    def _experience_manager_module():
        from src import experience_manager
        return experience_manager

    def get_reference_cases(self, query_text: str, top_k: int = 3,
                            province: str = None,
                            specialty: str = None) -> list[dict]:
        return self._experience_manager_module().get_reference_cases(
            self,
            query_text,
            top_k=top_k,
            province=province,
            specialty=specialty,
        )

    def import_from_project(self, records: list[dict],
                            project_name: str = None,
                            province: str = None,
                            enabled_checkers: list = None) -> dict:
        return self._experience_importer_module().import_from_project(
            self,
            records,
            project_name=project_name,
            province=province,
            enabled_checkers=enabled_checkers,
        )

    def rebuild_vector_index(self):
        return self._experience_importer_module().rebuild_vector_index(self)

    # get_reference_cases — 已拆分到 experience_manager.py
    # import_from_project — 已拆分到 experience_importer.py
    # rebuild_vector_index — 已拆分到 experience_importer.py

    # ================================================================
    # 统计信息
    # ================================================================

    def flag_disputed(self, bill_name: str, province: str, reason: str = "") -> int:
        """标记权威层中匹配的记录为"有争议"

        当用户纠正了一条经验库直通的结果时调用。
        按省份+清单名称模糊匹配权威层记录，将 disputed 字段+1。

        参数:
            bill_name: 清单项名称
            province: 省份
            reason: 争议原因（写入notes）
        返回:
            被标记的记录数
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            # 用 bill_name 模糊匹配权威层记录（清单名称可能包含在 bill_text 中）
            cursor.execute("""
                UPDATE experiences
                SET disputed = COALESCE(disputed, 0) + 1,
                    notes = CASE
                        WHEN notes IS NULL OR notes = '' THEN ?
                        ELSE notes || '\n' || ?
                    END,
                    updated_at = ?
                WHERE layer = 'authority'
                  AND province = ?
                  AND (bill_name = ? OR bill_text LIKE ?)
            """, (
                f"[争议] {reason}",
                f"[争议] {reason}",
                time.time(),
                province,
                bill_name,
                f"%{bill_name}%",
            ))
            conn.commit()
            affected = cursor.rowcount
            if affected > 0:
                logger.info(f"经验库争议标记: '{bill_name}' ({province}) → {affected}条权威记录被标记")
            return affected
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """获取经验库统计信息"""
        conn = self._connect()
        try:
            cursor = conn.cursor()

            # 总记录数
            cursor.execute("SELECT COUNT(*) FROM experiences")
            total = cursor.fetchone()[0]

            # 按层级统计
            cursor.execute("SELECT COUNT(*) FROM experiences WHERE layer = 'authority'")
            authority_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM experiences WHERE layer = 'verified'")
            verified_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM experiences WHERE layer = 'candidate'")
            candidate_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM experiences WHERE layer = 'deleted'")
            deleted_count = cursor.fetchone()[0]

            # 按来源分类统计
            cursor.execute("""
                SELECT source, COUNT(*) as cnt
                FROM experiences
                GROUP BY source
            """)
            by_source = {row[0]: row[1] for row in cursor.fetchall()}

            # 按省份统计
            cursor.execute("""
                SELECT province, COUNT(*) as cnt
                FROM experiences
                GROUP BY province
            """)
            by_province = {row[0]: row[1] for row in cursor.fetchall()}

            # 按专业统计
            cursor.execute("""
                SELECT specialty, COUNT(*) as cnt
                FROM experiences
                GROUP BY specialty
            """)
            by_specialty = {(row[0] or ''): row[1] for row in cursor.fetchall()}

            # 平均置信度
            cursor.execute("SELECT AVG(confidence) FROM experiences")
            avg_confidence = cursor.fetchone()[0] or 0
        finally:
            conn.close()

        # 向量索引数量
        try:
            vector_count = self.collection.count() if self.collection is not None else 0
        except Exception as e:
            logger.debug(f"经验库向量索引计数失败，按0返回: {e}")
            vector_count = 0

        return {
            "total": total,
            "authority": authority_count,
            "verified": verified_count,
            "candidate": candidate_count,
            "deleted": deleted_count,
            "by_source": by_source,
            "by_province": by_province,
            "by_specialty": by_specialty,
            "avg_confidence": round(avg_confidence, 1),
            "vector_count": vector_count,
        }

    def demote_to_candidate(self, record_id: int, reason: str = ""):
        return self._experience_manager_module().demote_to_candidate(
            self,
            record_id,
            reason=reason,
        )

    def promote_to_authority(self, record_id: int, reason: str = ""):
        return self._experience_manager_module().promote_to_authority(
            self,
            record_id,
            reason=reason,
        )

    def mark_stale_experiences(self, province: str, current_version: str) -> int:
        return self._experience_manager_module().mark_stale_experiences(
            self,
            province,
            current_version,
        )

    def get_authority_records(self, province: str = None,
                              limit: int = 0) -> list[dict]:
        return self._experience_manager_module().get_authority_records(
            self,
            province=province,
            limit=limit,
        )

    def get_candidate_records(self, province: str = None,
                              limit: int = 50,
                              exclude_demoted: bool = False) -> list[dict]:
        return self._experience_manager_module().get_candidate_records(
            self,
            province=province,
            limit=limit,
            exclude_demoted=exclude_demoted,
        )
