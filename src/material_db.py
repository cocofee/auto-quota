"""
主材库管理模块

主材库是Jarvis系统的第4个数据层（和经验库/定额库/知识库并列），
负责"材料→价格"的管理。

三层分离架构（Codex 5.4审核通过）：
1. material_master — 材料主数据（"这是什么材料"）
2. price_fact — 价格事实（"这个材料多少钱"，区分信息价/市场价）
3. quota_material_observation — 定额关联（"这条定额要用什么材料"）

使用：
    from src.material_db import MaterialDB
    db = MaterialDB()
    db.add_material("镀锌钢管", "DN25", "m")
    db.add_price(material_id, 18.5, "official_info", "北京", ...)
"""

import sqlite3
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


# ======== 镀锌钢管理论重量表（GB/T 3091，每米公斤数）========
# 来源：国标焊接钢管（镀锌），含镀锌系数1.06
# 用于把吨价换算成米价：米价 = 吨价 × 每米重量(kg) ÷ 1000
_PIPE_WEIGHT_PER_METER = {
    "DN15": 1.357, "DN20": 1.764, "DN25": 2.554,
    "DN32": 3.306, "DN40": 3.84,  "DN50": 5.33,
    "DN65": 7.09,  "DN70": 7.09,  "DN80": 8.47,
    "DN100": 12.15, "DN125": 15.04, "DN150": 19.26,
    "DN200": 30.97, "DN250": 42.56, "DN300": 54.90,
}


def _extract_dn(text: str) -> Optional[str]:
    """从材料名称或规格中提取DN规格（如'DN25'、'DN 20×2.75'→'DN20'）"""
    if not text:
        return None
    # 匹配 DN15、DN 20、dn25 等
    m = re.search(r'[Dd][Nn]\s*(\d+)', text)
    if m:
        return f"DN{m.group(1)}"
    return None


def _convert_ton_to_meter(ton_price: float, name: str, spec: str) -> Optional[float]:
    """把吨价换算成米价（钢管类），查不到DN规格就返回None"""
    dn = _extract_dn(spec) or _extract_dn(name)
    if not dn:
        return None
    weight = _PIPE_WEIGHT_PER_METER.get(dn)
    if not weight:
        return None
    # 米价 = 吨价 × 每米重量(kg) ÷ 1000
    return round(ton_price * weight / 1000, 2)


def _try_convert_price(price: float, from_unit: str, to_unit: str,
                       name: str = "", spec: str = "") -> Optional[float]:
    """尝试单位换算，不支持的返回None

    支持的换算：
    - t → m：钢管按DN规格查理论重量
    - t → kg：÷1000
    - 百米 → m：÷100
    - 千米/km → m：÷1000
    """
    fu = (from_unit or "").strip().lower()
    tu = (to_unit or "").strip().lower()

    if fu == tu:
        return price  # 单位相同，不需要换算

    # 吨 → 米（钢管类）
    if fu == "t" and tu == "m":
        return _convert_ton_to_meter(price, name, spec)

    # 吨 → 公斤
    if fu == "t" and tu == "kg":
        return round(price / 1000, 2)

    # 百米 → 米
    if fu == "百米" and tu == "m":
        return round(price / 100, 2)

    # 千米/km → 米
    if fu in ("千米", "km", "条公里") and tu == "m":
        return round(price / 1000, 2)

    return None  # 不支持的换算，返回None（空着不填）


# 数据库路径
DB_PATH = Path(__file__).parent.parent / "db" / "common" / "material.db"


class MaterialDB:
    """主材库数据库管理"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")  # 提高并发性能
        conn.execute("PRAGMA foreign_keys=ON")

        # ======== 材料主数据 ========
        # 每种材料一条记录，是主材库的"字典"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS material_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- 基本信息
                name TEXT NOT NULL,              -- 标准名称（如"镀锌钢管"）
                spec TEXT DEFAULT '',            -- 规格型号（如"DN25"）
                unit TEXT DEFAULT '',            -- 标准单位（如"m"）
                category TEXT DEFAULT '',        -- 材料大类（如"管材"、"电缆"、"阀门"）
                subcategory TEXT DEFAULT '',      -- 材料小类（如"镀锌钢管"、"PPR管"）
                -- 辅助字段
                brand TEXT DEFAULT '',            -- 品牌（可选）
                material_type TEXT DEFAULT '',    -- 材质（如"Q235"、"304不锈钢"）
                -- 搜索辅助
                search_text TEXT DEFAULT '',      -- 分词后的搜索文本
                -- 元数据
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                -- 唯一约束：名称+规格+单位确定一种材料
                UNIQUE(name, spec, unit)
            )
        """)

        # ======== 材料别名 ========
        # 同一种材料的不同叫法（如"热镀锌钢管"="镀锌焊接钢管"）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS material_alias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL,     -- 关联的标准材料ID
                alias_name TEXT NOT NULL,          -- 别名
                alias_spec TEXT DEFAULT '',        -- 别名对应的规格（可能和标准不同）
                source TEXT DEFAULT '',            -- 别名来源（手工/自动发现）
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (material_id) REFERENCES material_master(id)
            )
        """)

        # ======== 价格事实 ========
        # 每次查到/导入一条价格就存一条记录
        # 信息价和市场价在同一张表，用source_type区分
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_fact (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER NOT NULL,     -- 关联的材料ID
                -- 价格信息
                price_incl_tax REAL,              -- 含税价
                price_excl_tax REAL,              -- 不含税价
                tax_rate REAL DEFAULT 0.13,       -- 税率（默认13%）
                unit TEXT DEFAULT '',             -- 价格单位（必须和材料单位兼容）
                -- 来源区分（最关键的字段）
                source_type TEXT NOT NULL,        -- 价格类型：
                    -- 'official_info'   = 信息价（官方指导价，最权威）
                    -- 'market_web'      = 广材网市场价
                    -- 'manual_quote'    = 人工询价
                    -- 'historical_project' = 历史项目数据
                authority_level TEXT DEFAULT 'reference',  -- 权威等级：
                    -- 'official'   = 官方（信息价）
                    -- 'verified'   = 用户确认过
                    -- 'reference'  = 参考（未确认）
                -- 地域和时间
                province TEXT DEFAULT '',         -- 省份
                city TEXT DEFAULT '',             -- 城市
                period_start TEXT DEFAULT '',     -- 信息价期次开始（如2026-01-01）
                period_end TEXT DEFAULT '',       -- 信息价期次结束（如2026-02-28）
                price_date TEXT DEFAULT '',       -- 市场价查询日期
                -- 来源追踪
                source_doc TEXT DEFAULT '',       -- 来源文件名/URL
                batch_id INTEGER,                -- 导入批次ID
                -- 使用控制
                usable_for_quote INTEGER DEFAULT 1,  -- 是否可用于报价（0=仅参考，1=可报价）
                    -- 2023年旧价格、企业集采价等设为0，防止被误当最新价
                -- 元数据
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (material_id) REFERENCES material_master(id)
            )
        """)

        # ======== 定额→主材观测 ========
        # 每次Jarvis跑项目时，从清单里提取到的"定额需要哪些主材"
        # 积累多了可以晋升为模板
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quota_material_observation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- 定额信息
                quota_id TEXT DEFAULT '',          -- 定额编号（如C10-1-10）
                quota_name TEXT DEFAULT '',        -- 定额名称
                province TEXT DEFAULT '',          -- 省份
                -- 主材信息
                material_code TEXT DEFAULT '',     -- 广联达材料编码
                material_name TEXT NOT NULL,       -- 材料名称（原始）
                material_spec TEXT DEFAULT '',     -- 规格型号
                material_unit TEXT DEFAULT '',     -- 单位
                quantity REAL DEFAULT 0,           -- 定额用量
                -- 来源追踪
                project_name TEXT DEFAULT '',      -- 项目名称
                source_file TEXT DEFAULT '',       -- 来源文件
                -- 关联
                material_id INTEGER,              -- 匹配到的标准材料ID（可能为空）
                -- 元数据
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (material_id) REFERENCES material_master(id)
            )
        """)

        # ======== 定额→主材模板 ========
        # 从observation晋升出来的稳定关联（"这条定额通常需要这种材料"）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quota_material_template (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quota_name_pattern TEXT NOT NULL,  -- 定额名称模式（如"给水管道安装*"）
                material_id INTEGER NOT NULL,     -- 标准材料ID
                -- 统计信息
                observation_count INTEGER DEFAULT 0,  -- 观测次数
                confidence REAL DEFAULT 0,        -- 置信度
                -- 元数据
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (material_id) REFERENCES material_master(id),
                UNIQUE(quota_name_pattern, material_id)
            )
        """)

        # ======== 导入批次 ========
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_batch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,         -- 导入类型
                source_file TEXT DEFAULT '',       -- 来源文件
                province TEXT DEFAULT '',
                record_count INTEGER DEFAULT 0,    -- 导入条数
                parser_template TEXT DEFAULT '',   -- 使用的解析模板名称
                status TEXT DEFAULT 'completed',   -- 状态
                notes TEXT DEFAULT '',             -- 备注
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        # 索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mm_name ON material_master(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mm_category ON material_master(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mm_search ON material_master(search_text)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_material ON price_fact(material_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_source ON price_fact(source_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_province ON price_fact(province)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pf_period ON price_fact(period_start, period_end)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias_name ON material_alias(alias_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qmo_quota ON quota_material_observation(quota_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qmo_material ON quota_material_observation(material_name)")

        conn.commit()

        # ======== 表结构迁移（兼容旧数据库）========
        self._migrate(conn)

        conn.close()

    def _migrate(self, conn):
        """自动迁移旧表结构（加新字段）"""
        # price_fact 加 usable_for_quote 字段
        try:
            conn.execute("SELECT usable_for_quote FROM price_fact LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE price_fact ADD COLUMN usable_for_quote INTEGER DEFAULT 1")
            conn.commit()

        # import_batch 加 parser_template 和 notes 字段
        try:
            conn.execute("SELECT parser_template FROM import_batch LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE import_batch ADD COLUMN parser_template TEXT DEFAULT ''")
            conn.execute("ALTER TABLE import_batch ADD COLUMN notes TEXT DEFAULT ''")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ======== 材料主数据操作 ========

    def add_material(self, name: str, spec: str = "", unit: str = "",
                     category: str = "", subcategory: str = "",
                     brand: str = "", material_type: str = "") -> int:
        """
        添加或获取材料（如果已存在则返回已有ID）

        返回：material_id
        """
        conn = self._conn()
        try:
            # 先查是否已存在
            row = conn.execute(
                "SELECT id FROM material_master WHERE name=? AND spec=? AND unit=?",
                (name.strip(), spec.strip(), unit.strip())
            ).fetchone()
            if row:
                return row["id"]

            # 构建搜索文本
            search_text = f"{name} {spec} {category} {subcategory} {material_type}".strip()

            cursor = conn.execute(
                """INSERT INTO material_master
                   (name, spec, unit, category, subcategory, brand, material_type, search_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), spec.strip(), unit.strip(),
                 category.strip(), subcategory.strip(),
                 brand.strip(), material_type.strip(), search_text)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def find_material(self, name: str, spec: str = "", unit: str = "") -> Optional[dict]:
        """
        查找材料（精确匹配）

        返回：材料信息字典，或None
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM material_master WHERE name=? AND spec=? AND unit=?",
                (name.strip(), spec.strip(), unit.strip())
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def search_materials(self, keyword: str, limit: int = 10) -> list[dict]:
        """
        搜索材料（模糊匹配）

        返回：材料列表
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT * FROM material_master
                   WHERE name LIKE ? OR search_text LIKE ? OR spec LIKE ?
                   ORDER BY name LIMIT ?""",
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def search_price_by_name(self, name: str, province: str = "",
                             spec: str = "",
                             target_unit: str = "",
                             source_type: str = "") -> Optional[dict]:
        """按材料名查价格（给输出Excel主材行填单价用）

        匹配策略（由精到粗，命中即返回）：
        1. name+spec 精确匹配 material_master → 查该省最新价格
        2. name 精确匹配（忽略spec）→ 查价格
        3. name 模糊匹配（LIKE）→ 取第一个有价格的

        target_unit: 主材行期望的单位（如'm'），用于单位换算。
        source_type: 价格类型过滤，空=不限, 'government'=信息价, 'market'=市场价

        返回：{"price": 含税单价, "unit": 单位, "source": 来源说明} 或 None
        """
        if not name or not name.strip():
            return None

        name = name.strip()
        spec = spec.strip() if spec else ""
        target_unit = target_unit.strip() if target_unit else ""
        conn = self._conn()
        try:
            # 策略1：name+spec精确匹配
            if spec:
                mid = self._find_material_id(conn, name, spec)
                if mid:
                    price = self._get_price(conn, mid, province,
                                            target_unit, name, spec,
                                            source_type=source_type)
                    if price:
                        return price

            # 策略2：只用name匹配（spec为空或策略1没找到价格）
            mid = self._find_material_id(conn, name, "")
            if mid:
                # 查材料的spec（用于DN提取）
                mat_spec = self._get_material_spec(conn, mid)
                price = self._get_price(conn, mid, province,
                                        target_unit, name, mat_spec or spec,
                                        source_type=source_type)
                if price:
                    return price

            # 策略3：模糊匹配（name LIKE '%keyword%'），取第一个有价格的
            rows = conn.execute(
                """SELECT id, name, spec FROM material_master
                   WHERE name LIKE ? ORDER BY LENGTH(name) ASC LIMIT 10""",
                (f"%{name}%",)
            ).fetchall()
            for row in rows:
                price = self._get_price(
                    conn, row["id"], province,
                    target_unit, row["name"], row["spec"] or spec,
                    source_type=source_type)
                if price:
                    return price

            return None
        finally:
            conn.close()

    def _find_material_id(self, conn, name: str, spec: str) -> Optional[int]:
        """在material_master中查找材料ID"""
        if spec:
            row = conn.execute(
                "SELECT id FROM material_master WHERE name=? AND spec=?",
                (name, spec)
            ).fetchone()
            if row:
                return row["id"]
        # 不带spec查
        row = conn.execute(
            "SELECT id FROM material_master WHERE name=? ORDER BY id LIMIT 1",
            (name,)
        ).fetchone()
        return row["id"] if row else None

    def _get_material_spec(self, conn, material_id: int) -> Optional[str]:
        """获取材料的spec字段"""
        row = conn.execute(
            "SELECT spec FROM material_master WHERE id=?",
            (material_id,)
        ).fetchone()
        return row["spec"] if row else None

    def _get_price(self, conn, material_id: int, province: str,
                   target_unit: str = "", name: str = "",
                   spec: str = "", source_type: str = "") -> Optional[dict]:
        """查材料最新价格，优先本省，其次任意省

        source_type: 空=不限(先信息价后市场价), 'government'=只查信息价, 'market'=只查市场价
        如果价格单位和target_unit不一致，会尝试换算（如吨→米）。
        换算失败则返回None（主材行单价留空）。
        """
        # 按优先级依次查：本省 → 任意省 → 兜底
        queries = []

        if source_type == "market":
            # 只查市场价
            if province:
                queries.append((
                    """SELECT price_incl_tax, unit, province, source_type
                       FROM price_fact
                       WHERE material_id=? AND province=?
                         AND source_type IN ('market_web','manual_quote','historical_project','enterprise_price_lib')
                         AND usable_for_quote=1
                       ORDER BY created_at DESC LIMIT 1""",
                    (material_id, province),
                    lambda r: f"{r['province']}市场价",
                ))
            queries.append((
                """SELECT price_incl_tax, unit, province, source_type
                   FROM price_fact
                   WHERE material_id=?
                     AND source_type IN ('market_web','manual_quote','historical_project','enterprise_price_lib')
                     AND usable_for_quote=1
                   ORDER BY created_at DESC LIMIT 1""",
                (material_id,),
                lambda r: f"{r['province'] or ''}市场价",
            ))
        elif source_type == "government":
            # 只查信息价
            if province:
                queries.append((
                    """SELECT price_incl_tax, unit, province, period_end
                       FROM price_fact
                       WHERE material_id=? AND province=?
                         AND source_type IN ('official_info','info_price') AND usable_for_quote=1
                       ORDER BY period_end DESC LIMIT 1""",
                    (material_id, province),
                    lambda r: f"{r['province']}信息价",
                ))
            queries.append((
                """SELECT price_incl_tax, unit, province, period_end
                   FROM price_fact
                   WHERE material_id=? AND source_type IN ('official_info','info_price')
                     AND usable_for_quote=1
                   ORDER BY period_end DESC LIMIT 1""",
                (material_id,),
                lambda r: f"{r['province']}信息价",
            ))
        else:
            # 不限：先信息价后市场价（原有逻辑）
            if province:
                queries.append((
                    """SELECT price_incl_tax, unit, province, period_end
                       FROM price_fact
                       WHERE material_id=? AND province=?
                         AND source_type IN ('official_info','info_price') AND usable_for_quote=1
                       ORDER BY period_end DESC LIMIT 1""",
                    (material_id, province),
                    lambda r: f"{r['province']}信息价",
                ))
            queries.append((
                """SELECT price_incl_tax, unit, province, period_end
                   FROM price_fact
                   WHERE material_id=? AND source_type IN ('official_info','info_price')
                     AND usable_for_quote=1
                   ORDER BY period_end DESC LIMIT 1""",
                (material_id,),
                lambda r: f"{r['province']}信息价",
            ))
            queries.append((
                """SELECT price_incl_tax, unit, province, source_type
                   FROM price_fact
                   WHERE material_id=? AND usable_for_quote=1
                   ORDER BY created_at DESC LIMIT 1""",
                (material_id,),
                lambda r: f"{r['province'] or ''}市场价",
            ))

        for sql, params, source_fn in queries:
            row = conn.execute(sql, params).fetchone()
            if not row:
                continue

            raw_price = row["price_incl_tax"]
            price_unit = (row["unit"] or "").strip()
            source = source_fn(row)

            # 单位一致，直接返回
            if not target_unit or price_unit == target_unit:
                return {"price": raw_price, "unit": price_unit, "source": source}

            # 尝试单位换算
            converted = _try_convert_price(
                raw_price, price_unit, target_unit, name, spec)
            if converted is not None:
                return {
                    "price": converted,
                    "unit": target_unit,
                    "source": f"{source}({price_unit}→{target_unit})",
                }

        return None

    # ======== 别名操作 ========

    def add_alias(self, material_id: int, alias_name: str,
                  alias_spec: str = "", source: str = "manual"):
        """添加材料别名"""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO material_alias
                   (material_id, alias_name, alias_spec, source)
                   VALUES (?, ?, ?, ?)""",
                (material_id, alias_name.strip(), alias_spec.strip(), source)
            )
            conn.commit()
        finally:
            conn.close()

    def find_by_alias(self, alias_name: str) -> Optional[dict]:
        """通过别名查找标准材料"""
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT mm.* FROM material_master mm
                   JOIN material_alias ma ON mm.id = ma.material_id
                   WHERE ma.alias_name = ?""",
                (alias_name.strip(),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ======== 价格操作 ========

    def add_price(self, material_id: int, price_incl_tax: float,
                  source_type: str, province: str = "",
                  city: str = "", tax_rate: float = 0.13,
                  period_start: str = "", period_end: str = "",
                  price_date: str = "", source_doc: str = "",
                  batch_id: int = None, unit: str = "",
                  authority_level: str = "reference",
                  usable_for_quote: int = 1,
                  dedup: bool = False) -> int:
        """
        添加价格记录

        source_type: 'official_info' | 'market_web' | 'manual_quote' | 'historical_project'
                     | 'enterprise_price_lib'（企业集采价格库）
        authority_level: 'official' | 'verified' | 'reference'
        usable_for_quote: 1=可用于报价, 0=仅参考（如2023年旧价格）
        dedup: True时，同材料+同价格+同来源文件不重复插入
        """
        price_excl_tax = round(price_incl_tax / (1 + tax_rate), 2) if tax_rate > 0 else price_incl_tax
        if not price_date:
            price_date = datetime.now().strftime("%Y-%m-%d")

        # 信息价自动设为official级别
        if source_type == "official_info" and authority_level == "reference":
            authority_level = "official"

        conn = self._conn()
        try:
            # 去重检查：同材料+同价格+同来源文件
            if dedup and source_doc:
                existing = conn.execute(
                    "SELECT id FROM price_fact WHERE material_id=? AND price_incl_tax=? AND source_doc=?",
                    (material_id, price_incl_tax, source_doc)
                ).fetchone()
                if existing:
                    return existing["id"]

            cursor = conn.execute(
                """INSERT INTO price_fact
                   (material_id, price_incl_tax, price_excl_tax, tax_rate, unit,
                    source_type, authority_level, province, city,
                    period_start, period_end, price_date,
                    source_doc, batch_id, usable_for_quote)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (material_id, price_incl_tax, price_excl_tax, tax_rate, unit,
                 source_type, authority_level, province, city,
                 period_start, period_end, price_date,
                 source_doc, batch_id, usable_for_quote)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_latest_price(self, material_id: int, province: str = "",
                         prefer_official: bool = True,
                         include_reference: bool = False) -> Optional[dict]:
        """
        获取材料最新价格

        prefer_official=True时，优先返回信息价，没有才返回市场价
        include_reference=False时，排除usable_for_quote=0的参考价（如2023年旧价格）
        """
        conn = self._conn()
        try:
            # 可报价过滤条件（默认排除仅参考价格）
            quote_filter = "" if include_reference else " AND usable_for_quote=1"

            if prefer_official and province:
                # 先查信息价
                row = conn.execute(
                    f"""SELECT * FROM price_fact
                       WHERE material_id=? AND province=? AND source_type='official_info'
                       {quote_filter}
                       ORDER BY period_end DESC, created_at DESC LIMIT 1""",
                    (material_id, province)
                ).fetchone()
                if row:
                    return dict(row)

            # 查所有价格（按时间倒序）
            params = [material_id]
            sql = f"SELECT * FROM price_fact WHERE material_id=?{quote_filter}"
            if province:
                sql += " AND province=?"
                params.append(province)
            sql += " ORDER BY created_at DESC LIMIT 1"

            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ======== 定额关联操作 ========

    def add_quota_observation(self, quota_id: str, quota_name: str,
                              material_name: str, material_spec: str = "",
                              material_unit: str = "", quantity: float = 0,
                              material_code: str = "", province: str = "",
                              project_name: str = "", source_file: str = "",
                              material_id: int = None) -> int:
        """添加定额→主材观测记录"""
        conn = self._conn()
        try:
            cursor = conn.execute(
                """INSERT INTO quota_material_observation
                   (quota_id, quota_name, province, material_code,
                    material_name, material_spec, material_unit, quantity,
                    project_name, source_file, material_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (quota_id, quota_name, province, material_code,
                 material_name, material_spec, material_unit, quantity,
                 project_name, source_file, material_id)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    # ======== 统计 ========

    def stats(self) -> dict:
        """获取主材库统计信息"""
        conn = self._conn()
        try:
            material_count = conn.execute(
                "SELECT COUNT(*) FROM material_master").fetchone()[0]
            price_count = conn.execute(
                "SELECT COUNT(*) FROM price_fact").fetchone()[0]
            official_count = conn.execute(
                "SELECT COUNT(*) FROM price_fact WHERE source_type='official_info'").fetchone()[0]
            market_count = conn.execute(
                "SELECT COUNT(*) FROM price_fact WHERE source_type='market_web'").fetchone()[0]
            alias_count = conn.execute(
                "SELECT COUNT(*) FROM material_alias").fetchone()[0]
            observation_count = conn.execute(
                "SELECT COUNT(*) FROM quota_material_observation").fetchone()[0]
            template_count = conn.execute(
                "SELECT COUNT(*) FROM quota_material_template").fetchone()[0]

            return {
                "材料条数": material_count,
                "价格记录": price_count,
                "信息价": official_count,
                "市场价": market_count,
                "别名": alias_count,
                "定额关联观测": observation_count,
                "定额关联模板": template_count,
            }
        finally:
            conn.close()


# ======== 命令行工具 ========

def main():
    """命令行入口：查看主材库统计"""
    db = MaterialDB()
    s = db.stats()
    print("主材库统计：")
    for k, v in s.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
