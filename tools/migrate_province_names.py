"""
省份名称标准化迁移工具

将旧的简化省份名称（如"北京2024消耗量"）迁移为广联达标准全称
（如"北京市建设工程施工消耗量标准(2024)"）。

用法：
    python tools/migrate_province_names.py           # 预览模式（只显示不执行）
    python tools/migrate_province_names.py --execute  # 实际执行迁移

特性：
    - 自动检测映射关系（对比 data/quota_data/ 和 db/provinces/）
    - 幂等设计（可重复运行，已迁移的自动跳过）
    - 迁移前自动备份所有 SQLite 文件
    - 支持增量迁移（只处理已改名的省份，未改名的跳过）
"""

import os
import re
import sys
import json
import time
import shutil
import sqlite3
import hashlib
import argparse
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 关键目录
DB_PROVINCES_DIR = PROJECT_ROOT / "db" / "provinces"
QUOTA_RULES_DIR = PROJECT_ROOT / "data" / "quota_rules"
QUOTA_DATA_DIR = PROJECT_ROOT / "data" / "quota_data"
CHROMA_DIR = PROJECT_ROOT / "db" / "chroma"
COMMON_DB_DIR = PROJECT_ROOT / "db" / "common"


# ============================================================
# 映射检测
# ============================================================

def scan_new_names():
    """扫描 data/quota_data/ 下所有新名称（广联达标准全称）

    支持嵌套结构：data/quota_data/北京/北京市建设工程...(2024)/
    返回：{新名称: 实际路径}
    """
    new_names = {}
    if not QUOTA_DATA_DIR.exists():
        return new_names

    for parent in sorted(QUOTA_DATA_DIR.iterdir()):
        if not parent.is_dir():
            continue
        # 检查是否直接含xlsx（扁平结构）
        has_xlsx = any(parent.glob("*.xlsx"))
        if has_xlsx:
            new_names[parent.name] = parent
        else:
            # 嵌套结构：检查子目录
            for sub in sorted(parent.iterdir()):
                if sub.is_dir() and any(sub.glob("*.xlsx")):
                    new_names[sub.name] = sub
    return new_names


def scan_old_names():
    """扫描 db/provinces/ 下所有旧名称

    返回：旧名称列表
    """
    old_names = []
    if not DB_PROVINCES_DIR.exists():
        return old_names
    for item in sorted(DB_PROVINCES_DIR.iterdir()):
        if item.is_dir():
            old_names.append(item.name)
    return old_names


def extract_year(name):
    """从名称中提取年份（如"北京2024消耗量" → "2024"）"""
    match = re.search(r'(\d{4})', name)
    return match.group(1) if match else None


def extract_keywords(name):
    """从旧名称中提取关键中文词（用于匹配）

    将关键词拆分为2字符的重叠片段（bigram），提高部分匹配能力。
    例如："房屋修缮" → ["房屋", "屋修", "修缮"]
    这样即使新名称是"房屋修工程"，"房屋"和"屋修"仍能匹配上。
    """
    # 去掉省份名和年份，剩下的就是关键词
    cleaned = re.sub(r'[\d]+', '', name)  # 去数字
    # 常见省份/城市名
    for city in ['北京', '上海', '天津', '重庆', '广东', '山东', '福建',
                 '浙江', '江苏', '河北', '河南', '湖北', '湖南', '四川',
                 '辽宁', '吉林', '黑龙江', '广西', '云南', '贵州', '陕西',
                 '甘肃', '青海', '内蒙古', '宁夏', '新疆', '西藏', '海南',
                 '安徽', '江西', '山西']:
        cleaned = cleaned.replace(city, '')
    # 提取中文字符
    chars = re.findall(r'[\u4e00-\u9fff]', cleaned)
    if not chars:
        return []
    # 生成2字符重叠片段（bigram），提高模糊匹配能力
    bigrams = []
    for i in range(len(chars) - 1):
        bigrams.append(chars[i] + chars[i + 1])
    # 也保留单字符（作为最后的兜底匹配）
    return bigrams if bigrams else chars


def _get_old_db_specialty(old_name):
    """从旧数据库中获取主要specialty，用于消歧

    例如"广东2018"的数据库里主要是"安装"→ 应匹配"通用安装工程"
    """
    db_path = DB_PROVINCES_DIR / old_name / "quota.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        # 取出现次数最多的specialty
        rows = cur.execute(
            "SELECT specialty, COUNT(*) as cnt FROM quotas "
            "GROUP BY specialty ORDER BY cnt DESC LIMIT 1"
        ).fetchall()
        conn.close()
        if rows:
            return rows[0][0]  # 如 "安装"、"土建"
    except Exception:
        pass
    return None


def detect_mappings():
    """自动检测旧名称到新名称的映射关系

    算法：
    1. 对每个旧名称，提取年份和关键词（bigram）
    2. 在新名称中找同年份的候选
    3. 用bigram匹配打分，取最佳匹配
    4. 同分时，用旧数据库的specialty消歧（如"安装"→"通用安装工程"）
    5. 仍然同分（多个候选无法区分），标记为"ambiguous"跳过

    返回：{旧名称: 新名称}，无法匹配的不包含在内
    """
    new_names = scan_new_names()
    old_names = scan_old_names()

    if not new_names or not old_names:
        return {}

    mappings = {}

    for old_name in old_names:
        # 已是新名称格式（旧名和新名完全一致），跳过
        if old_name in new_names:
            continue

        old_year = extract_year(old_name)
        old_keywords = extract_keywords(old_name)

        # 在新名称中查找候选
        candidates = []
        for new_name in new_names:
            # 新名称已被其他旧名称认领，跳过
            if new_name in [v for v in mappings.values()]:
                continue

            new_year = extract_year(new_name)

            # 年份必须匹配
            if old_year and new_year and old_year != new_year:
                continue

            # 计算bigram匹配分数
            score = 0
            for kw in old_keywords:
                if kw in new_name:
                    score += 2  # 每个匹配的bigram +2分

            # 省份名匹配（旧名称的前两个字通常是省份简称）
            old_prefix = old_name[:2]
            if old_prefix in new_name:
                score += 3

            candidates.append((new_name, score))

        if not candidates:
            continue

        # 取最高分的候选
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_score = candidates[0][1]

        if best_score <= 0:
            continue

        # 找出所有并列最高分的候选
        tied = [c for c in candidates if c[1] == best_score]

        if len(tied) == 1:
            # 唯一最高分，直接确认
            mappings[old_name] = tied[0][0]
        else:
            # 同分消歧：用旧数据库的specialty匹配
            specialty = _get_old_db_specialty(old_name)
            if specialty:
                # specialty关键词映射（"安装"→在新名称中找"安装"或"通用安装"）
                specialty_keywords = {
                    "安装": ["安装", "通用安装"],
                    "土建": ["建筑", "房屋建筑", "装饰"],
                    "市政": ["市政"],
                    "园林": ["园林", "绿化"],
                    "消防": ["消防"],
                }
                spec_kws = specialty_keywords.get(specialty, [specialty])
                for new_name, _ in tied:
                    if any(kw in new_name for kw in spec_kws):
                        mappings[old_name] = new_name
                        break

            if old_name not in mappings:
                # 仍然无法消歧，跳过并报告
                print(f"  警告: '{old_name}' 有{len(tied)}个同分候选，无法自动匹配：")
                for c, s in tied[:5]:
                    print(f"    - {c} (分数={s})")
                print(f"  → 请手动在 data/quota_data/ 中确认对应关系")

    return mappings


def detect_db_field_mappings(dir_mappings):
    """检测SQLite数据库中的province字段映射

    数据库中存的省份值可能和目录名不同（如目录是"北京2024消耗量"，
    但DB里存的是"北京2024"）。需要额外处理这些变体。

    参数：dir_mappings = {旧目录名: 新目录名}
    返回：{旧DB值: 新名称}，包含目录映射和DB字段变体
    """
    db_mappings = {}

    # 1. 目录名映射直接继承
    for old_dir, new_name in dir_mappings.items():
        db_mappings[old_dir] = new_name

    # 2. 扫描各数据库中实际存在的省份值，尝试匹配
    db_files = {
        "experience.db": [("experiences", "province")],
        "learning_notes.db": [("learning_notes", "province")],
        "rule_knowledge.db": [("rules", "province")],
        "universal_kb.db": [("knowledge", "source_province")],
    }

    existing_db_values = set()
    for db_name, tables in db_files.items():
        db_path = COMMON_DB_DIR / db_name
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            for table, column in tables:
                try:
                    rows = cur.execute(
                        f"SELECT DISTINCT {column} FROM {table}"
                    ).fetchall()
                    for row in rows:
                        if row[0]:
                            existing_db_values.add(row[0])
                except sqlite3.OperationalError:
                    pass  # 表或列不存在
            conn.close()
        except Exception:
            pass

    # 3. 为每个DB中的旧值找匹配
    for db_value in existing_db_values:
        if db_value in db_mappings:
            continue  # 已有映射

        # 跳过特殊值
        if db_value in ('通用', ''):
            continue

        # 尝试匹配到某个目录映射的新名称
        db_year = extract_year(db_value)
        db_keywords = extract_keywords(db_value)

        for old_dir, new_name in dir_mappings.items():
            old_dir_year = extract_year(old_dir)

            # 年份匹配
            if db_year and old_dir_year and db_year == old_dir_year:
                # 关键词匹配
                match = True
                for kw in db_keywords:
                    if kw not in old_dir and kw not in new_name:
                        match = False
                        break
                if match:
                    db_mappings[db_value] = new_name
                    break

    return db_mappings


# ============================================================
# 迁移操作
# ============================================================

def _safe_dir_name(name):
    """复制 config.py 中的哈希逻辑，计算旧的 ChromaDB 目录名"""
    ascii_part = "".join(c for c in name if c.isascii() and c.isalnum())
    if ascii_part == name:
        return name
    hash_suffix = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"{ascii_part}_{hash_suffix}" if ascii_part else f"p_{hash_suffix}"


def backup_sqlite(db_path):
    """备份SQLite文件"""
    if not db_path.exists():
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".db.bak.{timestamp}")
    shutil.copy2(str(db_path), str(backup_path))
    return backup_path


def rename_directory(old_path, new_path):
    """重命名目录（幂等）

    返回：(成功, 消息)
    """
    if not old_path.exists():
        if new_path.exists():
            return True, f"  已迁移（跳过）: {new_path.name}"
        return False, f"  旧目录不存在: {old_path}"

    if new_path.exists():
        return False, f"  冲突：新旧目录同时存在\n    旧: {old_path}\n    新: {new_path}"

    try:
        os.rename(str(old_path), str(new_path))
        return True, f"  重命名: {old_path.name} → {new_path.name}"
    except OSError as e:
        return False, f"  重命名失败: {e}"


def update_sqlite_field(db_path, table, column, old_new_map, execute=False):
    """更新SQLite表中的province字段

    返回：[(旧值, 新值, 影响行数), ...]
    """
    if not db_path.exists():
        return []

    results = []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        for old_val, new_val in old_new_map.items():
            if old_val == new_val:
                continue

            # 检查有多少行需要更新
            try:
                count = cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
                    (old_val,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                continue  # 表或列不存在

            if count > 0:
                if execute:
                    cur.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        (new_val, old_val)
                    )
                results.append((old_val, new_val, count))

        if execute:
            conn.commit()
        conn.close()
    except Exception as e:
        results.append(("ERROR", str(e), 0))

    return results


def update_universal_kb_province_list(db_path, old_new_map, execute=False):
    """更新 universal_kb 中 JSON 数组格式的 province_list

    province_list 存的是 JSON 数组字符串，如 '["北京2024"]'
    需要解析 JSON → 替换 → 写回
    """
    if not db_path.exists():
        return []

    results = []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        try:
            rows = cur.execute(
                "SELECT id, province_list FROM knowledge WHERE province_list IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            conn.close()
            return []

        updated_count = 0
        for row_id, plist_str in rows:
            try:
                plist = json.loads(plist_str)
            except (json.JSONDecodeError, TypeError):
                continue

            changed = False
            new_plist = []
            for p in plist:
                if p in old_new_map and p != old_new_map[p]:
                    new_plist.append(old_new_map[p])
                    changed = True
                else:
                    new_plist.append(p)

            if changed:
                if execute:
                    cur.execute(
                        "UPDATE knowledge SET province_list = ? WHERE id = ?",
                        (json.dumps(new_plist, ensure_ascii=False), row_id)
                    )
                updated_count += 1

        if updated_count > 0:
            results.append(("province_list JSON更新", "", updated_count))

        if execute:
            conn.commit()
        conn.close()
    except Exception as e:
        results.append(("ERROR", str(e), 0))

    return results


def find_chroma_dirs_for_province(province_name):
    """找到某个省份对应的 ChromaDB 目录"""
    safe_name = _safe_dir_name(province_name)
    quota_dir = CHROMA_DIR / f"{safe_name}_quota"
    return quota_dir


# ============================================================
# 主流程
# ============================================================

def preview(dir_mappings, db_mappings):
    """预览所有将要执行的操作"""
    print("=" * 60)
    print("  省份名称标准化迁移 —— 预览")
    print("=" * 60)
    print()

    if not dir_mappings:
        print("未检测到需要迁移的省份。")
        print()
        print("可能的原因：")
        print("  1. data/quota_data/ 下的目录还没有改为广联达标准全称")
        print("  2. 所有省份已经迁移完成")
        print()
        # 列出当前状态
        old_names = scan_old_names()
        new_names = scan_new_names()
        if old_names:
            print(f"db/provinces/ 下的目录: {', '.join(old_names)}")
        if new_names:
            print(f"data/quota_data/ 下的目录: {', '.join(new_names.keys())}")
        return False

    # 1. 目录映射
    print("【目录重命名】")
    print()
    for old_name, new_name in dir_mappings.items():
        print(f"  {old_name}")
        print(f"    → {new_name}")
        print()

    # 2. db/provinces/ 重命名
    print("【db/provinces/ 目录迁移】")
    for old_name, new_name in dir_mappings.items():
        old_path = DB_PROVINCES_DIR / old_name
        new_path = DB_PROVINCES_DIR / new_name
        if old_path.exists():
            print(f"  {old_name}/ → {new_name}/")
        elif new_path.exists():
            print(f"  {new_name}/ （已迁移）")
        else:
            print(f"  {old_name}/ （不存在，跳过）")
    print()

    # 3. data/quota_rules/ 重命名
    print("【data/quota_rules/ 目录迁移】")
    for old_name, new_name in dir_mappings.items():
        old_path = QUOTA_RULES_DIR / old_name
        new_path = QUOTA_RULES_DIR / new_name
        if old_path.exists():
            print(f"  {old_name}/ → {new_name}/")
        elif new_path.exists():
            print(f"  {new_name}/ （已迁移）")
        else:
            print(f"  {old_name}/ （不存在，跳过）")
    print()

    # 4. SQLite 字段更新
    print("【SQLite 数据库更新】")
    db_updates = [
        ("experience.db", "experiences", "province"),
        ("learning_notes.db", "learning_notes", "province"),
        ("rule_knowledge.db", "rules", "province"),
        ("universal_kb.db", "knowledge", "source_province"),
    ]
    for db_name, table, column in db_updates:
        db_path = COMMON_DB_DIR / db_name
        results = update_sqlite_field(db_path, table, column, db_mappings,
                                      execute=False)
        if results:
            for old_val, new_val, count in results:
                if old_val == "ERROR":
                    print(f"  {db_name}: 错误 - {new_val}")
                else:
                    print(f"  {db_name}.{table}.{column}: "
                          f"'{old_val}' → '{new_val}' ({count}条)")
        else:
            print(f"  {db_name}: 无需更新")

    # universal_kb 的 province_list (JSON数组)
    ub_path = COMMON_DB_DIR / "universal_kb.db"
    ub_results = update_universal_kb_province_list(ub_path, db_mappings,
                                                    execute=False)
    if ub_results:
        for desc, _, count in ub_results:
            print(f"  universal_kb.db: {desc} ({count}条)")
    print()

    # 5. ChromaDB 清理
    print("【ChromaDB 索引清理】")
    print("  以下旧索引目录将被删除（系统会自动重建）：")
    for old_name in dir_mappings:
        chroma_path = find_chroma_dirs_for_province(old_name)
        if chroma_path.exists():
            print(f"  删除: {chroma_path.name}/")
        else:
            print(f"  {_safe_dir_name(old_name)}_quota/ （不存在，跳过）")
    print()

    # 6. 未迁移的省份（给出具体建议）
    old_names = scan_old_names()
    unmapped = [n for n in old_names if n not in dir_mappings
                and n not in [v for v in dir_mappings.values()]]
    if unmapped:
        print("【未迁移的省份】")
        for name in unmapped:
            # 检查是否是多专业混合库
            specialty = _get_old_db_specialty(name)
            db_path = DB_PROVINCES_DIR / name / "quota.db"
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path))
                    cur = conn.cursor()
                    specs = cur.execute(
                        "SELECT specialty, COUNT(*) FROM quotas "
                        "GROUP BY specialty ORDER BY COUNT(*) DESC"
                    ).fetchall()
                    conn.close()
                    spec_info = ", ".join(f"{s}({c}条)" for s, c in specs if s)
                except Exception:
                    spec_info = ""
                    specs = []

                if len(specs) > 1:
                    print(f"  {name} — 多专业混合库({spec_info})")
                    print(f"    → 建议：删除 db/provinces/{name}/ 后按新目录分别导入")
                else:
                    print(f"  {name} — {spec_info or '空库'}")
                    print(f"    → 请在 data/quota_data/ 中添加对应的广联达标准全称目录")
            else:
                print(f"  {name} — 数据库为空")
                print(f"    → 可直接删除 db/provinces/{name}/")
        print()

    return True


def execute_migration(dir_mappings, db_mappings):
    """执行迁移"""
    print("=" * 60)
    print("  省份名称标准化迁移 —— 执行")
    print("=" * 60)
    print()

    # 1. 备份所有SQLite
    print("【第1步】备份数据库...")
    db_files = ["experience.db", "learning_notes.db",
                "rule_knowledge.db", "universal_kb.db"]
    for db_name in db_files:
        db_path = COMMON_DB_DIR / db_name
        backup = backup_sqlite(db_path)
        if backup:
            print(f"  备份: {db_name} → {backup.name}")
        else:
            print(f"  跳过: {db_name}（不存在）")
    print()

    # 2. 重命名 db/provinces/
    print("【第2步】重命名 db/provinces/ 目录...")
    for old_name, new_name in dir_mappings.items():
        old_path = DB_PROVINCES_DIR / old_name
        new_path = DB_PROVINCES_DIR / new_name
        ok, msg = rename_directory(old_path, new_path)
        print(msg)
        if not ok and old_path.exists():
            print("    ⚠ 跳过此目录，继续处理其他")
    print()

    # 3. 重命名 data/quota_rules/
    print("【第3步】重命名 data/quota_rules/ 目录...")
    for old_name, new_name in dir_mappings.items():
        old_path = QUOTA_RULES_DIR / old_name
        new_path = QUOTA_RULES_DIR / new_name
        ok, msg = rename_directory(old_path, new_path)
        print(msg)
    print()

    # 4. 更新 SQLite 字段
    print("【第4步】更新数据库省份字段...")
    db_updates = [
        ("experience.db", "experiences", "province"),
        ("learning_notes.db", "learning_notes", "province"),
        ("rule_knowledge.db", "rules", "province"),
        ("universal_kb.db", "knowledge", "source_province"),
    ]
    for db_name, table, column in db_updates:
        db_path = COMMON_DB_DIR / db_name
        results = update_sqlite_field(db_path, table, column, db_mappings,
                                      execute=True)
        for old_val, new_val, count in results:
            if old_val == "ERROR":
                print(f"  {db_name}: 错误 - {new_val}")
            elif count > 0:
                print(f"  {db_name}: '{old_val}' → '{new_val}' ({count}条)")

    # universal_kb province_list
    ub_path = COMMON_DB_DIR / "universal_kb.db"
    ub_results = update_universal_kb_province_list(ub_path, db_mappings,
                                                    execute=True)
    for desc, _, count in ub_results:
        if count > 0:
            print(f"  universal_kb.db: {desc} ({count}条)")
    print()

    # 5. 清理旧的 ChromaDB 索引
    print("【第5步】清理旧的 ChromaDB 索引...")
    for old_name in dir_mappings:
        chroma_path = find_chroma_dirs_for_province(old_name)
        if chroma_path.exists():
            try:
                shutil.rmtree(str(chroma_path))
                print(f"  已删除: {chroma_path.name}/")
            except Exception as e:
                print(f"  删除失败: {chroma_path.name}/ ({e})")
        else:
            print(f"  跳过: {_safe_dir_name(old_name)}_quota/（不存在）")
    print()

    # 6. 完成
    print("=" * 60)
    print("  迁移完成！")
    print("=" * 60)
    print()
    print("后续操作：")
    print()
    print("  1. 重建定额索引（ChromaDB已清理，需要重建）：")
    for new_name in dir_mappings.values():
        print(f'     python tools/import_all.py --province "{new_name}" --skip-rules')
    print()
    print("  2. 重建经验库向量索引：")
    print("     python -c \"from src.experience_db import ExperienceDB; "
          "ExperienceDB().rebuild_vector_index()\"")
    print()
    print("  3. 验证匹配是否正常：")
    print('     python main.py "测试文件.xlsx" --limit 5')
    print()


def main():
    parser = argparse.ArgumentParser(
        description="省份名称标准化迁移工具"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="实际执行迁移（默认只预览）"
    )
    args = parser.parse_args()

    # 1. 检测目录映射
    dir_mappings = detect_mappings()

    # 2. 检测DB字段映射（扩展：包含DB中存的变体名称）
    db_mappings = detect_db_field_mappings(dir_mappings)

    if args.execute:
        if not dir_mappings:
            print("未检测到需要迁移的省份，无需执行。")
            return

        # 再次预览确认
        has_work = preview(dir_mappings, db_mappings)
        if not has_work:
            return

        print("-" * 60)
        confirm = input("确认执行以上迁移操作？[y/N]: ").strip().lower()
        if confirm != 'y':
            print("已取消。")
            return

        print()
        execute_migration(dir_mappings, db_mappings)
    else:
        # 预览模式
        preview(dir_mappings, db_mappings)
        print("-" * 60)
        print("以上为预览，添加 --execute 参数实际执行迁移：")
        print("  python tools/migrate_province_names.py --execute")
        print()


if __name__ == "__main__":
    main()
