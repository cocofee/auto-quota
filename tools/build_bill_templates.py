"""
清单组合模板 + 项目特征素材库生成器（五步法第2步）

基于 bill_library.db（80万条清单数据）生成：
1. 清单组合模板：按专业统计哪些清单项最常出现（编清单防漏项）
2. 项目特征素材库：按清单名称收集所有真实描述写法（编清单填描述）

用法：
    python tools/build_bill_templates.py              # 生成模板+素材
    python tools/build_bill_templates.py --stats       # 查看统计
    python tools/build_bill_templates.py --query 塑料管  # 查某个清单项
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.sqlite import connect_init, connect

# ============================================================
# 常量
# ============================================================

SOURCE_DB = Path(__file__).resolve().parent.parent / "data" / "bill_library.db"
# 模板数据写入同一个数据库（新增两张表）

# 覆盖率分档阈值
TIER_REQUIRED = 30   # ≥30% → 必选
TIER_COMMON = 15     # 15-30% → 常见
TIER_OPTIONAL = 5    # 5-15% → 可选
                     # <5% → 不收录

# 描述最低出现次数（去噪）
DESC_MIN_FREQ = 2


# ============================================================
# 建表
# ============================================================

def init_tables(conn):
    """在 bill_library.db 中创建模板表和素材表"""

    # 清单组合模板（每个专业中的常见清单项）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bill_templates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            specialty     TEXT NOT NULL,
            bill_name     TEXT NOT NULL,
            project_count INTEGER,
            coverage_pct  REAL,
            tier          TEXT,
            code_prefix   TEXT,
            avg_quantity  REAL,
            common_units  TEXT,
            UNIQUE(specialty, bill_name)
        )
    """)

    # 项目特征素材库（每个清单名称的描述写法）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bill_descriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_name    TEXT NOT NULL,
            description  TEXT NOT NULL,
            frequency    INTEGER,
            specialties  TEXT,
            example_code TEXT,
            UNIQUE(bill_name, description)
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_tpl_specialty ON bill_templates(specialty)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tpl_tier ON bill_templates(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_desc_name ON bill_descriptions(bill_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_desc_freq ON bill_descriptions(frequency)")

    conn.commit()


# ============================================================
# 第1部分：生成清单组合模板
# ============================================================

def build_templates(conn):
    """按专业生成清单组合模板"""
    # 清空旧数据
    conn.execute("DELETE FROM bill_templates")

    # 获取所有专业及其项目数
    specialties = conn.execute("""
        SELECT specialty, COUNT(DISTINCT file_path) as total
        FROM files WHERE status = 'standard'
        GROUP BY specialty
        ORDER BY total DESC
    """).fetchall()

    total_inserted = 0

    for spec_row in specialties:
        specialty = spec_row[0]
        total_projects = spec_row[1]

        if total_projects < 10:
            continue  # 项目太少，统计无意义

        # 按 bill_name 统计覆盖率
        rows = conn.execute("""
            SELECT
                b.bill_name,
                COUNT(DISTINCT b.file_path) as proj_cnt,
                ROUND(COUNT(DISTINCT b.file_path) * 100.0 / ?, 1) as pct,
                -- 最常见的编码前9位
                (SELECT SUBSTR(b2.bill_code, 1, 9)
                 FROM bill_items b2
                 JOIN files f2 ON b2.file_path = f2.file_path
                 WHERE f2.specialty = ? AND b2.bill_name = b.bill_name
                 GROUP BY SUBSTR(b2.bill_code, 1, 9)
                 ORDER BY COUNT(*) DESC LIMIT 1) as top_prefix,
                -- 平均工程量
                ROUND(AVG(CASE WHEN b.quantity > 0 AND b.quantity < 999999 THEN b.quantity END), 2) as avg_qty,
                -- 最常见单位
                (SELECT b3.unit
                 FROM bill_items b3
                 JOIN files f3 ON b3.file_path = f3.file_path
                 WHERE f3.specialty = ? AND b3.bill_name = b.bill_name AND b3.unit != ''
                 GROUP BY b3.unit ORDER BY COUNT(*) DESC LIMIT 1) as top_unit
            FROM bill_items b
            JOIN files f ON b.file_path = f.file_path
            WHERE f.specialty = ? AND f.status = 'standard'
            GROUP BY b.bill_name
            HAVING pct >= ?
            ORDER BY proj_cnt DESC
        """, (total_projects, specialty, specialty, specialty, TIER_OPTIONAL)).fetchall()

        spec_count = 0
        for row in rows:
            name, proj_cnt, pct, prefix, avg_qty, unit = row

            if pct >= TIER_REQUIRED:
                tier = "必选"
            elif pct >= TIER_COMMON:
                tier = "常见"
            else:
                tier = "可选"

            conn.execute("""
                INSERT OR REPLACE INTO bill_templates
                (specialty, bill_name, project_count, coverage_pct, tier, code_prefix, avg_quantity, common_units)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (specialty, name, proj_cnt, pct, tier, prefix, avg_qty, unit or ""))

            spec_count += 1

        total_inserted += spec_count
        req = sum(1 for r in rows if r[2] >= TIER_REQUIRED)
        com = sum(1 for r in rows if TIER_COMMON <= r[2] < TIER_REQUIRED)
        opt = spec_count - req - com
        print(f"  {specialty}: {spec_count}项 (必选{req}/常见{com}/可选{opt})，基于{total_projects}个项目")

    conn.commit()
    return total_inserted


# ============================================================
# 第2部分：生成项目特征素材库
# ============================================================

def build_descriptions(conn):
    """按清单名称收集所有描述写法"""
    # 清空旧数据
    conn.execute("DELETE FROM bill_descriptions")

    # 获取所有有描述的清单名称
    names = conn.execute("""
        SELECT DISTINCT bill_name FROM bill_items
        WHERE description != '' AND description IS NOT NULL
    """).fetchall()

    total_inserted = 0
    batch = []

    for idx, (name,) in enumerate(names):
        if (idx + 1) % 5000 == 0:
            print(f"  处理 {idx+1}/{len(names)} 个清单名称...")
            # 批量插入
            if batch:
                conn.executemany("""
                    INSERT OR REPLACE INTO bill_descriptions
                    (bill_name, description, frequency, specialties, example_code)
                    VALUES (?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
                batch = []

        # 按描述分组，统计频次和出现的专业
        rows = conn.execute("""
            SELECT
                b.description,
                COUNT(*) as freq,
                GROUP_CONCAT(DISTINCT f.specialty) as specs,
                MIN(b.bill_code) as example_code
            FROM bill_items b
            JOIN files f ON b.file_path = f.file_path
            WHERE b.bill_name = ? AND b.description != '' AND b.description IS NOT NULL
            GROUP BY b.description
            HAVING freq >= ?
            ORDER BY freq DESC
        """, (name, DESC_MIN_FREQ)).fetchall()

        for desc, freq, specs, code in rows:
            batch.append((name, desc, freq, specs, code))
            total_inserted += 1

    # 插入剩余批次
    if batch:
        conn.executemany("""
            INSERT OR REPLACE INTO bill_descriptions
            (bill_name, description, frequency, specialties, example_code)
            VALUES (?, ?, ?, ?, ?)
        """, batch)
        conn.commit()

    return total_inserted


# ============================================================
# 查询功能
# ============================================================

def query_item(conn, keyword):
    """查询某个清单项的模板和描述"""
    print(f"\n搜索: {keyword}")
    print("=" * 60)

    # 查模板
    templates = conn.execute("""
        SELECT specialty, bill_name, project_count, coverage_pct, tier, code_prefix, avg_quantity, common_units
        FROM bill_templates
        WHERE bill_name LIKE ?
        ORDER BY coverage_pct DESC
    """, (f"%{keyword}%",)).fetchall()

    if templates:
        print(f"\n清单组合模板（{len(templates)}条匹配）:")
        for t in templates:
            spec, name, cnt, pct, tier, prefix, avg_qty, unit = t
            qty_str = f"均量{avg_qty}" if avg_qty else ""
            print(f"  [{tier}] {spec}/{name}: {pct}%覆盖({cnt}个项目) {prefix or ''} {unit} {qty_str}")
    else:
        print("\n未找到匹配的模板")

    # 查描述
    descriptions = conn.execute("""
        SELECT bill_name, description, frequency, specialties
        FROM bill_descriptions
        WHERE bill_name LIKE ?
        ORDER BY frequency DESC
        LIMIT 30
    """, (f"%{keyword}%",)).fetchall()

    if descriptions:
        print(f"\n项目特征素材（前30条）:")
        for i, (name, desc, freq, specs) in enumerate(descriptions, 1):
            # 描述可能很长，截断显示
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            short_desc = short_desc.replace("\n", " | ")
            print(f"  {i}. [{name}] {short_desc} ({freq}次, {specs})")
    else:
        print("\n未找到匹配的描述")


def show_stats(conn):
    """显示统计"""
    print("=" * 60)
    print("清单组合模板 + 项目特征素材库 统计")
    print("=" * 60)

    # 模板统计
    total_tpl = conn.execute("SELECT COUNT(*) FROM bill_templates").fetchone()[0]
    if total_tpl == 0:
        print("\n模板表为空，请先运行生成命令")
        return

    print(f"\n清单组合模板: {total_tpl} 条")
    for row in conn.execute("""
        SELECT specialty, COUNT(*) as cnt,
               SUM(CASE WHEN tier='必选' THEN 1 ELSE 0 END) as req,
               SUM(CASE WHEN tier='常见' THEN 1 ELSE 0 END) as com,
               SUM(CASE WHEN tier='可选' THEN 1 ELSE 0 END) as opt
        FROM bill_templates
        GROUP BY specialty
        ORDER BY cnt DESC
    """):
        print(f"  {row[0]}: {row[1]}项 (必选{row[2]}/常见{row[3]}/可选{row[4]})")

    # 素材统计
    total_desc = conn.execute("SELECT COUNT(*) FROM bill_descriptions").fetchone()[0]
    unique_names = conn.execute("SELECT COUNT(DISTINCT bill_name) FROM bill_descriptions").fetchone()[0]
    print(f"\n项目特征素材: {total_desc} 条描述，覆盖 {unique_names} 个清单名称")

    # 描述最丰富的清单项
    print(f"\n描述最丰富的清单项（前10）:")
    for row in conn.execute("""
        SELECT bill_name, COUNT(*) as cnt, SUM(frequency) as total_freq
        FROM bill_descriptions
        GROUP BY bill_name
        ORDER BY cnt DESC
        LIMIT 10
    """):
        print(f"  {row[0]}: {row[1]}种描述 (总{row[2]}次)")


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="清单组合模板+项目特征素材库（五步法第2步）")
    parser.add_argument("--stats", action="store_true", help="查看统计")
    parser.add_argument("--query", help="查询某个清单项")
    args = parser.parse_args()

    if not SOURCE_DB.exists():
        print(f"数据库不存在: {SOURCE_DB}")
        print("请先运行: python tools/extract_bill_data.py")
        return

    conn = connect(SOURCE_DB)
    try:
        if args.stats:
            show_stats(conn)
        elif args.query:
            query_item(conn, args.query)
        else:
            # 生成模式
            print("=" * 60)
            print("生成清单组合模板 + 项目特征素材库")
            print("=" * 60)

            init_tables(conn)

            print("\n[1/2] 生成清单组合模板...")
            tpl_count = build_templates(conn)
            print(f"  共 {tpl_count} 条模板")

            print("\n[2/2] 生成项目特征素材库...")
            desc_count = build_descriptions(conn)
            print(f"  共 {desc_count} 条描述素材")

            print(f"\n生成完成！数据在 {SOURCE_DB}")
            print(f"  查看统计: python tools/build_bill_templates.py --stats")
            print(f"  查询清单: python tools/build_bill_templates.py --query 塑料管")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
