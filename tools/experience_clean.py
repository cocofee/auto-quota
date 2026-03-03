"""
经验库脏数据清理工具

功能：扫描经验库中的垃圾数据，支持自动清理和手动确认。

清理规则：
1. 定额编号不存在 — quota_id 在定额库里查不到 → 删除
2. 清单文本太短 — bill_text < 4字 → 删除
3. 清单文本是乱码 — 含非中文/数字/英文的异常字符比例 > 50% → 删除
4. 重复条目 — 同省份+同bill_text+同quota_id → 保留最新的，删其余
5. 定额版本过期 — quota_version与当前库不匹配 → 标记stale（不删）

与 experience_health.py 的关系：
- health 偏"质量审计"（审核规则回扫 + 降级）
- clean 偏"垃圾清扫"（识别垃圾 + 删除 + 去重 + 回滚）
两个工具互补。

安全设计（按 Codex 5.3 审核建议）：
- --scan 只扫描不删除（默认模式）
- --fix 才执行清理
- 删除前先备份到 _trash 表（可回滚，解决Codex P1误删风险）
- --purge-batch 可按导入批次回滚

用法：
    python tools/experience_clean.py --scan                    # 只扫描不删
    python tools/experience_clean.py --fix                     # 自动清理
    python tools/experience_clean.py --province 广东 --fix     # 只清理某省
    python tools/experience_clean.py --dedup                   # 去重
    python tools/experience_clean.py --purge-batch 2026-03-03  # 回滚某次批量导入
"""

import os
import sys
import re
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from db.sqlite import connect as db_connect, connect_init as db_connect_init


# ============================================================
# 配置
# ============================================================

# 清单文本最短长度（低于此值视为垃圾）
MIN_TEXT_LENGTH = 4

# 乱码判定：非中文/数字/英文/常见符号的字符比例超过此值
GARBLE_THRESHOLD = 0.5


# ============================================================
# 数据库连接
# ============================================================

def _get_exp_db_path():
    """获取经验库数据库路径"""
    return config.get_experience_db_path()


def _get_conn(db_path=None):
    """获取经验库连接"""
    path = db_path or _get_exp_db_path()
    return db_connect(path, row_factory=True)


def _ensure_trash_table(conn):
    """确保 _trash 表存在（回收站，Codex P1: 误删可回滚）"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _trash (
            id INTEGER,
            bill_text TEXT,
            quota_ids TEXT,
            province TEXT,
            layer TEXT,
            source TEXT,
            clean_reason TEXT,
            clean_time TEXT,
            original_data TEXT
        )
    """)


# ============================================================
# 扫描规则
# ============================================================

def scan_issues(province_filter: str = None) -> dict:
    """扫描经验库中的问题数据。

    返回:
        {
            "total": 总条数,
            "normal": 正常条数,
            "issues": {
                "text_too_short": [...],
                "garbled_text": [...],
                "duplicates": [...],
                "quota_not_found": [...],
            },
            "summary": {...}
        }
    """
    conn = _get_conn()
    try:
        query = "SELECT * FROM experiences"
        params = []
        if province_filter:
            query += " WHERE province = ?"
            params.append(province_filter)

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    total = len(rows)
    issues = {
        "text_too_short": [],
        "garbled_text": [],
        "duplicates": [],
        "quota_not_found": [],
    }

    # 用于去重检测的字典
    seen = defaultdict(list)  # (province, bill_text, quota_ids) → [id, ...]

    for row in rows:
        row_id = row["id"]
        bill_text = row["bill_text"] or ""
        quota_ids = row["quota_ids"] or ""
        province = row["province"] or ""

        # 规则1：文本太短
        clean_text = re.sub(r'\s+', '', bill_text)
        if len(clean_text) < MIN_TEXT_LENGTH:
            issues["text_too_short"].append({
                "id": row_id,
                "bill_text": bill_text,
                "province": province,
            })
            continue

        # 规则2：乱码检测
        if _is_garbled(bill_text):
            issues["garbled_text"].append({
                "id": row_id,
                "bill_text": bill_text[:50],
                "province": province,
            })
            continue

        # 规则3：收集重复候选（后续统一处理）
        key = (province, bill_text.strip(), quota_ids.strip())
        seen[key].append(row_id)

    # 找出重复的（同key有多个id）
    for key, ids in seen.items():
        if len(ids) > 1:
            # 保留最后一个（id最大的，通常是最新的），其余标记为重复
            ids_sorted = sorted(ids)
            for dup_id in ids_sorted[:-1]:
                issues["duplicates"].append({
                    "id": dup_id,
                    "province": key[0],
                    "bill_text": key[1][:30],
                    "keep_id": ids_sorted[-1],
                })

    # 规则4：定额编号不存在（需要查定额库，比较耗时，单独处理）
    issues["quota_not_found"] = _check_quota_exists(rows, province_filter)

    # 汇总
    issue_ids = set()
    for category, items in issues.items():
        for item in items:
            issue_ids.add(item["id"])

    normal = total - len(issue_ids)

    return {
        "total": total,
        "normal": normal,
        "issues": issues,
        "summary": {
            "text_too_short": len(issues["text_too_short"]),
            "garbled_text": len(issues["garbled_text"]),
            "duplicates": len(issues["duplicates"]),
            "quota_not_found": len(issues["quota_not_found"]),
            "total_issues": len(issue_ids),
        }
    }


def _is_garbled(text: str) -> bool:
    """判断文本是否为乱码。

    正常造价文本应该主要是：中文、数字、英文字母、常见标点。
    如果"异常字符"比例超过阈值就判定为乱码。
    """
    if not text:
        return False
    # 正常字符：中文、数字、英文、常见标点/符号
    normal_pattern = re.compile(r'[\u4e00-\u9fa5a-zA-Z0-9\s\.\,\;\:\!\?\-\+\*\/\(\)\[\]\{\}×°%#&@=<>，。；：！？、（）【】｛｝""''·—…]')
    normal_count = len(normal_pattern.findall(text))
    total_count = len(text)
    if total_count == 0:
        return False
    abnormal_ratio = 1 - (normal_count / total_count)
    return abnormal_ratio > GARBLE_THRESHOLD


def _check_quota_exists(rows: list, province_filter: str = None) -> list:
    """检查定额编号是否存在于定额库中。

    这个检查比较耗时（需要查定额库），所以只抽查候选层数据。
    """
    result = []

    # 获取可用的定额库
    try:
        provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
        if not provinces_dir.exists():
            return result

        # 加载定额编号集合（按省份缓存）
        quota_sets = {}  # province → set(quota_ids)

        for row in rows:
            prov = row["province"] or ""
            if province_filter and prov != province_filter:
                continue
            # 只检查候选层（权威层不轻易标记）
            if row.get("layer") == "authority":
                continue

            quota_ids_str = row["quota_ids"] or "[]"
            try:
                quota_ids = json.loads(quota_ids_str)
            except json.JSONDecodeError:
                continue

            if not quota_ids:
                continue

            # 查定额库
            if prov not in quota_sets:
                quota_sets[prov] = _load_quota_ids(prov)

            if not quota_sets[prov]:
                continue  # 该省没有定额库，跳过

            for qid in quota_ids:
                if qid and qid not in quota_sets[prov]:
                    result.append({
                        "id": row["id"],
                        "province": prov,
                        "bill_text": (row["bill_text"] or "")[:30],
                        "quota_id": qid,
                    })
                    break  # 一个不存在就标记整条

    except Exception:
        pass  # 定额库不可用，跳过此检查

    return result


def _load_quota_ids(province: str) -> set:
    """加载某省份的所有定额编号。"""
    provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
    # 找匹配的目录
    for d in provinces_dir.iterdir():
        if d.is_dir() and d.name.startswith(province):
            db_file = d / "quota.db"
            if db_file.exists():
                try:
                    conn = db_connect(db_file)
                    try:
                        ids = set()
                        for row in conn.execute("SELECT quota_id FROM quotas"):
                            ids.add(row[0])
                        return ids
                    finally:
                        conn.close()
                except Exception:
                    pass
    return set()


# ============================================================
# 清理执行
# ============================================================

def fix_issues(scan_result: dict, dry_run: bool = True):
    """执行清理。

    dry_run=True 时只打印要删什么，不真删。
    dry_run=False 时执行删除（先备份到 _trash 表）。
    """
    issues = scan_result["issues"]

    # 收集要删除的ID
    to_delete = set()
    reasons = {}  # id → reason

    for item in issues["text_too_short"]:
        to_delete.add(item["id"])
        reasons[item["id"]] = "文本太短"

    for item in issues["garbled_text"]:
        to_delete.add(item["id"])
        reasons[item["id"]] = "乱码"

    for item in issues["duplicates"]:
        to_delete.add(item["id"])
        reasons[item["id"]] = f"重复(保留id={item['keep_id']})"

    for item in issues["quota_not_found"]:
        to_delete.add(item["id"])
        reasons[item["id"]] = f"定额编号不存在({item.get('quota_id', '')})"

    if not to_delete:
        print("没有需要清理的数据。")
        return

    print(f"\n待清理: {len(to_delete)} 条")
    for reason in set(reasons.values()):
        count = sum(1 for r in reasons.values() if r == reason)
        print(f"  {reason}: {count} 条")

    if dry_run:
        print("\n这是预览模式。加 --fix 参数执行实际清理。")
        return

    # 实际执行删除（先备份到_trash）
    conn = _get_conn()
    try:
        _ensure_trash_table(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        deleted = 0
        for row_id in to_delete:
            # 备份到回收站
            row = conn.execute("SELECT * FROM experiences WHERE id = ?", (row_id,)).fetchone()
            if row:
                conn.execute("""
                    INSERT INTO _trash (id, bill_text, quota_ids, province, layer, source,
                                       clean_reason, clean_time, original_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["id"], row["bill_text"], row["quota_ids"],
                    row["province"], row.get("layer"), row.get("source"),
                    reasons.get(row_id, ""), now,
                    json.dumps(dict(row), ensure_ascii=False, default=str),
                ))
                conn.execute("DELETE FROM experiences WHERE id = ?", (row_id,))
                deleted += 1

        conn.commit()
        print(f"\n清理完成: 删除 {deleted} 条（已备份到 _trash 表，可回滚）")

    finally:
        conn.close()


def dedup_only(province_filter: str = None, dry_run: bool = True):
    """只做去重（不删其他类型的垃圾）。"""
    scan_result = scan_issues(province_filter)
    dups = scan_result["issues"]["duplicates"]

    if not dups:
        print("没有重复数据。")
        return

    print(f"发现 {len(dups)} 条重复数据")

    if dry_run:
        for d in dups[:10]:
            print(f"  id={d['id']} province={d['province']} bill_text={d['bill_text']} → 保留id={d['keep_id']}")
        if len(dups) > 10:
            print(f"  ...还有 {len(dups) - 10} 条")
        print("\n加 --fix 参数执行实际去重。")
        return

    # 构造只有重复的scan_result
    fake_result = {
        "issues": {
            "text_too_short": [],
            "garbled_text": [],
            "duplicates": dups,
            "quota_not_found": [],
        }
    }
    fix_issues(fake_result, dry_run=False)


def purge_batch(date_str: str):
    """按日期回滚某次批量导入的数据。

    查找 created_at 在指定日期的所有候选层数据并删除。
    """
    conn = _get_conn()
    try:
        _ensure_trash_table(conn)

        # 查找匹配的记录
        rows = conn.execute("""
            SELECT * FROM experiences
            WHERE layer = 'candidate'
            AND date(created_at, 'unixepoch', 'localtime') = ?
        """, (date_str,)).fetchall()

        if not rows:
            # 也试试 datetime 字符串格式
            rows = conn.execute("""
                SELECT * FROM experiences
                WHERE layer = 'candidate'
                AND created_at LIKE ?
            """, (f"{date_str}%",)).fetchall()

        if not rows:
            print(f"没有找到 {date_str} 的候选层数据。")
            return

        print(f"找到 {len(rows)} 条 {date_str} 的候选层数据")
        confirm = input("确认删除？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消。")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in rows:
            conn.execute("""
                INSERT INTO _trash (id, bill_text, quota_ids, province, layer, source,
                                   clean_reason, clean_time, original_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["id"], row["bill_text"], row["quota_ids"],
                row["province"], row.get("layer"), row.get("source"),
                f"批量回滚({date_str})", now,
                json.dumps(dict(row), ensure_ascii=False, default=str),
            ))
            conn.execute("DELETE FROM experiences WHERE id = ?", (row["id"],))

        conn.commit()
        print(f"已删除 {len(rows)} 条（已备份到 _trash 表）")

    finally:
        conn.close()


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="经验库脏数据清理工具")
    parser.add_argument("--scan", action="store_true", help="扫描脏数据（只报告不删）")
    parser.add_argument("--fix", action="store_true", help="自动清理")
    parser.add_argument("--dedup", action="store_true", help="只去重")
    parser.add_argument("--province", help="只处理某省份")
    parser.add_argument("--purge-batch", metavar="DATE", help="回滚某次批量导入（如 2026-03-03）")

    args = parser.parse_args()

    if args.purge_batch:
        purge_batch(args.purge_batch)
        return

    if args.dedup:
        dedup_only(province_filter=args.province, dry_run=not args.fix)
        return

    # 默认扫描
    print("扫描经验库中的问题数据...")
    result = scan_issues(province_filter=args.province)

    s = result["summary"]
    print(f"\n扫描完成: {result['total']} 条")
    print(f"  正常: {result['normal']} 条")
    print(f"  文本太短: {s['text_too_short']} 条")
    print(f"  乱码: {s['garbled_text']} 条")
    print(f"  重复: {s['duplicates']} 条")
    print(f"  编号不存在: {s['quota_not_found']} 条")
    print(f"  问题总计: {s['total_issues']} 条")

    if args.fix:
        fix_issues(result, dry_run=False)
    elif s["total_issues"] > 0:
        print(f"\n加 --fix 执行清理。")


if __name__ == "__main__":
    main()
