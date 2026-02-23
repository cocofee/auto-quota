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
import re
import sqlite3
import time
from pathlib import Path

from loguru import logger

from db.sqlite import connect as _db_connect, connect_init as _db_connect_init
from src.specialty_classifier import get_book_from_quota_id
import config


class ExperienceDB:
    """经验库：存储和查询历史匹配记录"""

    def __init__(self, province: str = None):
        self.province = province or config.get_current_province()
        self.db_path = config.get_experience_db_path()
        self.chroma_dir = config.get_chroma_experience_dir()

        # 向量模型和ChromaDB（延迟加载，避免启动时就占显存）
        self._model = None
        self._collection = None
        self._chroma_client = None

        # 确保数据库表存在
        self._init_db()

    def _init_db(self):
        """创建经验库SQLite表（如果不存在）"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = _db_connect_init(self.db_path)
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

        conn.commit()
        conn.close()

        logger.debug(f"经验库数据库已初始化: {self.db_path}")

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=row_factory)

    @staticmethod
    def _safe_json_list(raw):
        """安全解析JSON数组，异常时返回空列表，避免脏数据导致主流程崩溃。"""
        if isinstance(raw, list):
            return raw
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _json_dump(value) -> str:
        """统一 JSON 序列化（保留中文）。"""
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _source_to_layer(source: str) -> str:
        """来源到层级映射。

        authority（权威层，可直通匹配）：
          - user_correction: 用户手动修正
          - user_confirmed: 用户点击确认
          - project_import: 已完成项目导入（人工验证过的预算）
        candidate（候选层，仅供参考）：
          - auto_match: 系统自动匹配（未经人工验证）
          - auto_review: 贾维斯自动审核纠正（未经人工验证）
          - project_import_suspect: 项目导入但审核规则检测到问题（待人工确认）
        """
        authority_sources = ("user_correction", "user_confirmed", "project_import")
        return "authority" if source in authority_sources else "candidate"

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

    def _normalize_record_quota_fields(self, record: dict) -> dict:
        """统一把记录中的 quota_ids/quota_names/materials 解析成 list。"""
        record["quota_ids"] = self._safe_json_list(record.get("quota_ids"))
        record["quota_names"] = self._safe_json_list(record.get("quota_names"))
        record["materials"] = self._safe_json_list(record.get("materials"))
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
        """延迟初始化ChromaDB collection（通过全局ModelCache获取客户端，避免级联崩溃）"""
        from src.model_cache import ModelCache
        client = ModelCache.get_chroma_client(str(self.chroma_dir))
        # 客户端变了（被重建过），需要刷新collection
        if client is not self._chroma_client:
            self._chroma_client = client
            self._collection = client.get_or_create_collection(
                name="experiences",
                metadata={"hnsw:space": "cosine"}
            )
        return self._collection

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

            # --- 校验1: 清洗编号（去"换"后缀、"借"前缀、空格、乘数后缀） ---
            qid_clean = qid.strip().replace(" ", "")
            qid_clean = re.sub(r'换$', '', qid_clean)       # 去"换"后缀
            if qid_clean.startswith("借"):
                qid_clean = qid_clean[1:]                    # 去"借"前缀
            qid_clean = re.sub(r'\*[\d.]+$', '', qid_clean)  # 去"*数量"后缀
            qid_clean = qid_clean.strip()
            if qid_clean != original_qid.strip():
                warnings.append(f"定额编号'{original_qid}'已清洗为'{qid_clean}'")

            # --- 校验2: 编号是否存在（补子目直接跳过，不报错） ---
            if qid_clean.startswith("补子目"):
                warnings.append(f"跳过补子目: '{original_qid}'")
                continue
            if quota_map and qid_clean not in quota_map:
                # 降级为警告，不阻止导入（人工预算中的编号可能是换算/借用后的变体）
                warnings.append(f"定额编号'{qid_clean}'不在定额库中（仍保留导入）")

            # --- 校验3: 配电箱 vs 接线箱 ---
            if '配电箱' in bill_text and '接线箱' not in bill_text:
                q_info = quota_map.get(qid_clean, {})
                q_name = q_info.get('name', qname)
                if '接线箱' in q_name and '配电' not in q_name:
                    errors.append(f"清单是配电箱，但定额'{qid_clean}'是接线箱，不匹配")
                    continue

            # --- 校验4: 穿线 vs 电缆 ---
            if ('穿线' in bill_text or '穿铜芯线' in bill_text) and '电缆' not in bill_text:
                q_info = quota_map.get(qid_clean, {})
                q_name = q_info.get('name', qname)
                if '电缆' in q_name and '穿线' not in q_name and '穿铜芯' not in q_name:
                    errors.append(f"清单是穿线，但定额'{qid_clean}'是电缆定额，不匹配")
                    continue

            # --- 校验5: DN严重超档 ---
            bill_dn_m = re.search(r'DN\s*(\d+)', bill_text, re.IGNORECASE)
            if bill_dn_m and quota_map:
                q_info = quota_map.get(qid_clean, {})
                q_dn = q_info.get('dn')
                if q_dn:
                    bill_dn = float(bill_dn_m.group(1))
                    quota_dn = float(q_dn)
                    if bill_dn > quota_dn * 2:
                        errors.append(f"DN严重超档：清单DN{int(bill_dn)}，定额'{qid_clean}'只到DN{int(quota_dn)}")
                        continue

            # --- 校验6: 回路数严重不匹配 ---
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
                        continue
                else:
                    q_circuit_m = re.search(r'(\d+)', q_name) if '回路' in q_name else None
                    if q_circuit_m:
                        bc = int(bill_circuit_m.group(1))
                        qc = int(q_circuit_m.group(1))
                        if bc > qc:
                            errors.append(f"回路超档：清单{bc}回路 > 定额'{qid_clean}'的{qc}回路")
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
        """获取定额库映射（按省份缓存，避免重复读取）"""
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
            cache_by_province[province] = quota_map
            self._quota_map_cache_by_province = cache_by_province
            return quota_map
        except Exception as e:
            logger.warning(f"加载定额库映射失败: {e}")
            return {}

    # ================================================================
    # 写入经验
    # ================================================================

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
                       specialty: str = None) -> int:
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
        province = province or self.province
        now = time.time()

        # ========== 自动推断专业册号（调用方没传 specialty 时从定额编号推断）==========
        if not specialty and quota_ids:
            for qid in quota_ids:
                inferred = get_book_from_quota_id(qid)
                if inferred:
                    specialty = inferred
                    break

        # ========== 定额校验（除了用户手动修正，其他来源都要校验）==========
        # user_correction 是用户亲手改的，信任度最高，跳过校验
        if source != "user_correction":
            validation = self._validate_quota_ids(
                bill_text, quota_ids, quota_names, province=province)
            if not validation["valid"]:
                logger.warning(
                    f"经验库写入被拦截 [{source}]: '{bill_text[:50]}' → {quota_ids} "
                    f"原因: {validation['errors']}"
                )
                return -1  # 校验失败，拒绝写入
            # 使用清洗后的编号和名称
            quota_ids = validation["cleaned_ids"]
            quota_names = validation["cleaned_names"]

        # 获取当前定额库版本号（绑定到经验记录）
        quota_db_ver = config.get_current_quota_version(province)

        # 根据来源设置层级（详见 _source_to_layer() 注释）
        layer = self._source_to_layer(source)
        quota_ids_json = self._json_dump(quota_ids)
        quota_names_json = self._json_dump(quota_names or [])
        materials_json = self._json_dump(materials or [])

        inserted_new = False
        conn = self._connect()
        cursor = conn.cursor()
        try:
            # 事务化“查重+写入/更新”，避免并发下重复插入
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                SELECT id FROM experiences
                WHERE bill_text = ? AND province = ?
                LIMIT 1
            """, (bill_text, province))
            existing = cursor.fetchone()

            if existing:
                record_id = self._update_experience(
                    int(existing[0]), quota_ids, quota_names,
                    source, confidence,
                    quota_db_version=quota_db_ver,
                    materials_json=materials_json,
                    specialty=specialty,
                    conn=conn, cursor=cursor, commit=False
                )
            else:
                cursor.execute("""
                    INSERT INTO experiences
                    (bill_text, bill_name, bill_code, bill_unit,
                     quota_ids, quota_names, materials, source, confidence,
                     confirm_count, province, project_name,
                     created_at, updated_at, notes, quota_db_version, layer, specialty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bill_text, bill_name, bill_code, bill_unit,
                    quota_ids_json,
                    quota_names_json,
                    materials_json,
                    source, confidence,
                    province, project_name, now, now, notes,
                    quota_db_ver, layer, specialty,
                ))
                record_id = int(cursor.lastrowid)
                inserted_new = True

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 新建记录才追加向量索引；更新走原id即可
        if inserted_new:
            self._add_to_vector_index(record_id, bill_text, province=province)
            logger.debug(f"经验库新增: [{source}] '{bill_text[:50]}' → {quota_ids}")
        else:
            logger.debug(f"经验库更新(事务路径): ID={record_id}, 来源={source}")
        return record_id

    def _update_experience(self, record_id: int, quota_ids: list[str],
                           quota_names: list[str], source: str,
                           confidence: int, quota_db_version: str = None,
                           materials_json: str = None,
                           specialty: str = None,
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

        confidence_floor = self._safe_int(confidence, 80)

        if source == "user_correction":
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
                    layer = 'authority',
                    source = 'project_import',
                    materials = CASE
                        WHEN ? != '[]' THEN ?
                        ELSE materials
                    END,
                    quota_db_version = COALESCE(?, quota_db_version),
                    specialty = CASE WHEN specialty IS NULL OR specialty = '' THEN ? ELSE specialty END,
                    updated_at = ?
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                project_floor, materials_json or '[]', materials_json or '[]',
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
                WHERE id = ? AND source NOT IN ('user_correction', 'user_confirmed')
            """, (
                self._json_dump(quota_ids),
                self._json_dump(quota_names or []),
                confidence_floor, materials_json or '[]', materials_json or '[]',
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

        # ========== 自动晋升：候选层达到门槛自动晋升为权威层 ==========
        # 多次独立匹配结果一致 = 数据可信，无需人工逐条审核
        # 门槛按置信度分级：高置信度要求少、低置信度要求多
        # 注意：project_import_suspect 是审核不通过被强制降级的，不参与自动晋升
        if source != "project_import_suspect":
            cursor.execute("""
                UPDATE experiences SET layer = 'authority'
                WHERE id = ? AND layer = 'candidate'
                  AND source != 'project_import_suspect'
                  AND (
                    (confidence >= 95 AND confirm_count >= 2)
                    OR (confidence >= 90 AND confirm_count >= 3)
                    OR (confidence >= 85 AND confirm_count >= 5)
                  )
            """, (record_id,))
            if cursor.rowcount > 0:
                logger.info(f"经验库自动晋升: ID={record_id} 候选层→权威层（达到确认门槛）")

        if commit:
            conn.commit()
        if owns_conn:
            conn.close()

        logger.debug(f"经验库更新: ID={record_id}, 来源={source}")
        return record_id

    def _find_exact_match(self, bill_text: str, province: str,
                          authority_only: bool = False) -> dict:
        """精确查找相同清单文本的经验记录

        参数:
            bill_text: 清单文本
            province: 省份
            authority_only: 是否只查权威层（直通匹配时为True）
        """
        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            authority_clause = " AND layer = 'authority'" if authority_only else ""
            cursor.execute(f"""
                SELECT * FROM experiences
                WHERE bill_text = ? AND province = ?{authority_clause}
                ORDER BY confidence DESC, confirm_count DESC, updated_at DESC, id DESC
                LIMIT 1
            """, (bill_text, province))

            row = cursor.fetchone()
        finally:
            conn.close()

        if row:
            return dict(row)
        return None

    def _add_to_vector_index(self, record_id: int, bill_text: str,
                             province: str = None):
        """将经验记录添加到向量索引（带省份metadata，支持按省份过滤）"""
        province = province or self.province
        try:
            embedding = self.model.encode(
                [bill_text],
                normalize_embeddings=True
            )
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

    def search_similar(self, query_text: str, top_k: int = 5,
                       min_confidence: int = 60,
                       province: str = None) -> list[dict]:
        """
        从经验库中搜索相似的历史匹配

        版本校验规则：
        - 经验记录的 quota_db_version 与当前定额库版本一致 → 正常返回（允许直通）
        - 版本不一致或经验没有版本号 → 降级：match_type 标记为 "stale"，
          调用方应把它当参考而非直通

        参数:
            query_text: 新的清单文本
            top_k: 返回前K条相似记录
            min_confidence: 最低置信度过滤
            province: 省份过滤

        返回:
            相似的经验记录列表，每条包含:
            {id, bill_text, quota_ids, quota_names, similarity, confidence, ...}
        """
        province = province or self.province

        # 获取当前定额库版本（用于校验经验记录是否过期）
        current_version = config.get_current_quota_version(province)
        stale_exact = None

        # 先尝试精确匹配（最快）—— 直通匹配只查权威层
        exact = self._find_exact_match(query_text, province, authority_only=True)
        if exact and exact.get("confidence", 0) >= min_confidence:
            exact["similarity"] = 1.0  # 精确匹配相似度为1
            self._normalize_record_quota_fields(exact)

            # 版本校验：版本一致才标记为 "exact"（允许直通）
            record_version = exact.get("quota_db_version", "")
            if current_version and record_version and record_version == current_version:
                exact["match_type"] = "exact"
            elif not current_version or not record_version:
                # 版本信息缺失（老数据或尚未导入定额）→ 降级为 stale
                # 缺版本号说明是早期数据，定额可能已更新，不应直通高置信匹配
                exact["match_type"] = "stale"
                logger.debug(f"经验库版本信息缺失（经验:'{record_version}' 当前:'{current_version}'），降级为参考")
            else:
                # 版本不一致 → 降级为"过期参考"，不应直通
                exact["match_type"] = "stale"
                logger.debug(f"经验库版本不一致（经验:{record_version} vs 当前:{current_version}），降级为参考")
            if exact["match_type"] == "exact":
                return [exact]
            stale_exact = exact

        # 向量相似搜索
        collection_count = self.collection.count()
        if collection_count == 0:
            return [stale_exact] if stale_exact else []
        # 多取一些结果，避免候选层记录在向量层面挤掉权威层记录
        # 后续排序时会优先保留权威层，再截断到 top_k
        n_results = min(max(top_k * 3, 15), collection_count)

        try:
            # 向量模型不可用时快速跳过（不重复报错，依赖精确匹配兜底）
            if self.model is None:
                return [stale_exact] if stale_exact else []

            query_prefix = "为这个句子生成表示以用于检索中文文档: "
            query_embedding = self.model.encode(
                [query_prefix + query_text],
                normalize_embeddings=True
            )

            # 先尝试按省份过滤的向量搜索
            try:
                results = self.collection.query(
                    query_embeddings=query_embedding.tolist(),
                    n_results=n_results,
                    where={"province": province},  # 按省份过滤向量搜索
                )
            except Exception as where_err:
                # 旧索引可能没有province metadata，where过滤会报错
                logger.warning(f"经验库按省份过滤失败({where_err})，降级为全库搜索。"
                              f"建议重建向量索引以获得更好的多省份隔离")
                results = self.collection.query(
                    query_embeddings=query_embedding.tolist(),
                    n_results=n_results,
                )

            # 兼容旧索引：按省份过滤后无结果时，尝试无过滤搜索（SQL层仍会过滤省份）
            if not results or not results.get("ids") or not results.get("ids")[0]:
                results = self.collection.query(
                    query_embeddings=query_embedding.tolist(),
                    n_results=n_results,
                )

            if not results or not results.get("ids") or not results.get("ids")[0]:
                return []

            # 获取匹配的记录ID和相似度（防御性处理长度不一致/非法ID）
            raw_ids = results.get("ids", [[]])[0]
            raw_distances = results["distances"][0] if results.get("distances") else []
            if len(raw_distances) != len(raw_ids):
                logger.warning(
                    f"经验库向量检索返回长度不一致: ids={len(raw_ids)}, "
                    f"distances={len(raw_distances)}，已按最低相似度补齐/截断"
                )
            distances = list(raw_distances[:len(raw_ids)])
            if len(distances) < len(raw_ids):
                distances.extend([1.0] * (len(raw_ids) - len(distances)))

            matched_ids = []
            similarities = []
            for mid, dist in zip(raw_ids, distances):
                try:
                    db_id = int(mid)
                except (TypeError, ValueError):
                    logger.warning(f"经验库向量检索返回非法ID，已跳过: {mid!r}")
                    continue
                matched_ids.append(db_id)
                similarities.append(max(0.0, min(1.0, 1 - dist)))

            if not matched_ids:
                return [stale_exact] if stale_exact else []

            # 从SQLite获取完整记录
            conn = self._connect(row_factory=True)
            try:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(matched_ids))
                # 同时查权威层和候选层，候选层记录后续标记 match_type="candidate"
                cursor.execute(f"""
                    SELECT * FROM experiences
                    WHERE id IN ({placeholders})
                    AND province = ?
                    AND confidence >= ?
                    AND layer IN ('authority', 'candidate')
                """, matched_ids + [province, min_confidence])
                rows = {row["id"]: dict(row) for row in cursor.fetchall()}
            finally:
                conn.close()

            # 组装结果
            similar_records = []
            for db_id, sim in zip(matched_ids, similarities):
                if db_id in rows:
                    record = rows[db_id]
                    record["similarity"] = sim
                    self._normalize_record_quota_fields(record)

                    # 候选层记录标记为 "candidate"，不参与直通，仅作参考
                    if record.get("layer") == "candidate":
                        record["match_type"] = "candidate"
                    # 权威层记录做版本校验：
                    # 仅"当前版本+记录版本"均存在且一致时，才可标记为 similar；
                    # 其余情况（缺版本号或不一致）一律降级为 stale。
                    else:
                        record_version = record.get("quota_db_version", "")
                        if current_version and record_version and record_version == current_version:
                            record["match_type"] = "similar"
                        else:
                            record["match_type"] = "stale"

                    similar_records.append(record)

            # 按相似度降序排序，同相似度下权威层优先于候选层
            # 避免 top_k 截断时候选层记录挤掉有效的权威层记录
            _layer_priority = {"authority": 0, "candidate": 1}
            similar_records.sort(
                key=lambda x: (-x["similarity"],
                               _layer_priority.get(x.get("layer", "candidate"), 1))
            )

            # 权威层优先截断：先取权威层记录，再用候选层补齐到 top_k
            # 这样即使候选层相似度更高，也不会把权威层挤出结果
            authority_recs = [r for r in similar_records if r.get("layer") == "authority"]
            candidate_recs = [r for r in similar_records if r.get("layer") != "authority"]
            truncated = authority_recs[:top_k]
            remaining = top_k - len(truncated)
            if remaining > 0:
                truncated.extend(candidate_recs[:remaining])
            # 合并后重新按相似度排序，保持结果的自然顺序
            truncated.sort(
                key=lambda x: (-x["similarity"],
                               _layer_priority.get(x.get("layer", "candidate"), 1))
            )

            # 精确命中过期时，保留为首条参考，但不阻断后续有效记录
            if stale_exact:
                merged = [stale_exact]
                seen = {stale_exact.get("id")}
                for rec in truncated:
                    rec_id = rec.get("id")
                    if rec_id in seen:
                        continue
                    merged.append(rec)
                    seen.add(rec_id)
                truncated = merged

            return truncated[:top_k]

        except Exception as e:
            logger.warning(f"经验库向量搜索失败: {e}")
            return [stale_exact] if stale_exact else []

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

    # get_reference_cases — 已拆分到 experience_manager.py
    # import_from_project — 已拆分到 experience_importer.py
    # rebuild_vector_index — 已拆分到 experience_importer.py

    # ================================================================
    # 统计信息
    # ================================================================

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

            cursor.execute("SELECT COUNT(*) FROM experiences WHERE layer = 'candidate'")
            candidate_count = cursor.fetchone()[0]

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

            # 平均置信度
            cursor.execute("SELECT AVG(confidence) FROM experiences")
            avg_confidence = cursor.fetchone()[0] or 0
        finally:
            conn.close()

        # 向量索引数量
        try:
            vector_count = self.collection.count()
        except Exception as e:
            logger.debug(f"经验库向量索引计数失败，按0返回: {e}")
            vector_count = 0

        return {
            "total": total,
            "authority": authority_count,
            "candidate": candidate_count,
            "by_source": by_source,
            "by_province": by_province,
            "avg_confidence": round(avg_confidence, 1),
            "vector_count": vector_count,
        }

    # demote_to_candidate / promote_to_authority / mark_stale_experiences
    # get_authority_records / get_candidate_records
    # — 已拆分到 experience_manager.py

# ================================================================
# 方法重绑定：把拆分出去的函数挂回 ExperienceDB 类
# 调用方仍然用 db.import_from_project(...) 等，无需感知拆分
# ================================================================
from src import experience_importer as _exp_importer
from src import experience_manager as _exp_manager

ExperienceDB.import_from_project = _exp_importer.import_from_project
ExperienceDB.rebuild_vector_index = _exp_importer.rebuild_vector_index
ExperienceDB.get_reference_cases = _exp_manager.get_reference_cases
ExperienceDB.demote_to_candidate = _exp_manager.demote_to_candidate
ExperienceDB.promote_to_authority = _exp_manager.promote_to_authority
ExperienceDB.mark_stale_experiences = _exp_manager.mark_stale_experiences
ExperienceDB.get_authority_records = _exp_manager.get_authority_records
ExperienceDB.get_candidate_records = _exp_manager.get_candidate_records

# ================================================================
# 命令行入口：查看经验库状态
# ================================================================

if __name__ == "__main__":
    db = ExperienceDB()
    stats = db.get_stats()

    print("=" * 50)
    print("经验库状态")
    print("=" * 50)
    print(f"  总记录数: {stats['total']}")
    print(f"  向量索引: {stats['vector_count']}条")
    print(f"  平均置信度: {stats['avg_confidence']}")
    print(f"  按来源:")
    for source, cnt in stats.get("by_source", {}).items():
        print(f"    {source}: {cnt}条")
    print(f"  按省份:")
    for prov, cnt in stats.get("by_province", {}).items():
        print(f"    {prov}: {cnt}条")
