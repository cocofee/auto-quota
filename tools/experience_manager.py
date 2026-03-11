# -*- coding: utf-8 -*-
"""
经验库统一管理工具 — 查看/搜索/晋升/清理/体检

合并自原4个工具：experience_view/promote/clean/health

用法：
    python tools/experience_manager.py stats                          # 查看统计
    python tools/experience_manager.py search "镀锌钢管"               # 搜索记录
    python tools/experience_manager.py list                           # 浏览最近记录
    python tools/experience_manager.py promote --list                 # 列出候选层
    python tools/experience_manager.py promote --all                  # 批量晋升
    python tools/experience_manager.py clean --scan                   # 扫描脏数据
    python tools/experience_manager.py clean --fix                    # 自动清理
    python tools/experience_manager.py health                         # 权威层体检
    python tools/experience_manager.py health --fix                   # 体检+自动降级
"""

import sys
import io
import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.sqlite import connect as db_connect
import config


# ============================================================
# 通用工具函数
# ============================================================

def _resolve_province(name):
    """解析省份名称（模糊匹配）"""
    try:
        return config.resolve_province(name, interactive=False)
    except Exception:
        return name


def _print_records(records):
    """格式化打印经验记录列表"""
    for r in records:
        rid = r.get("id", "?")
        name = r.get("bill_name", "") or "(无名称)"
        confidence = r.get("confidence", 0)
        confirm_count = r.get("confirm_count", 0)
        layer = r.get("layer", "?")
        source = r.get("source", "?")

        # 解析定额编号列表
        quota_ids_raw = r.get("quota_ids", "[]")
        if isinstance(quota_ids_raw, str):
            try:
                quota_ids = json.loads(quota_ids_raw)
            except Exception:
                quota_ids = []
        else:
            quota_ids = quota_ids_raw or []

        quota_names_raw = r.get("quota_names", "[]")
        if isinstance(quota_names_raw, str):
            try:
                quota_names = json.loads(quota_names_raw)
            except Exception:
                quota_names = []
        else:
            quota_names = quota_names_raw or []

        # 更新时间
        updated = r.get("updated_at")
        if updated:
            try:
                time_str = datetime.fromtimestamp(float(updated)).strftime("%m-%d %H:%M")
            except Exception:
                time_str = "?"
        else:
            time_str = "?"

        # 置信度标记
        if confidence >= 85:
            conf_mark = "★★★"
        elif confidence >= 60:
            conf_mark = "★★"
        else:
            conf_mark = "★"

        # 层级标记
        layer_mark = "权威" if layer == "authority" else "候选"

        print(f"[{rid}] {name}")
        for i, qid in enumerate(quota_ids):
            qname = quota_names[i] if i < len(quota_names) else ""
            print(f"  定额: {qid} {qname}")
        print(f"  置信:{confidence}{conf_mark}  确认:{confirm_count}次  "
              f"层级:{layer_mark}  来源:{source}  更新:{time_str}")
        print(f"{'─' * 50}")


# ============================================================
# stats 子命令 — 查看统计信息
# ============================================================

def cmd_stats(args):
    """显示经验库统计信息"""
    from src.experience_db import ExperienceDB
    db = ExperienceDB()
    s = db.get_stats()

    print("=" * 50)
    print("经验库统计")
    print("=" * 50)
    print(f"  总记录数:   {s['total']}")
    print(f"  权威层:     {s['authority']}  （用户确认/修正）")
    print(f"  候选层:     {s['candidate']}  （自动匹配/导入）")
    print(f"  平均置信度: {s['avg_confidence']}")
    print()

    # 按来源
    by_source = s.get("by_source", {})
    if by_source:
        print("按来源:")
        source_labels = {
            "user_correction": "用户修正",
            "user_confirmed": "用户确认",
            "project_import": "项目导入",
            "auto_match": "自动匹配",
        }
        for src, cnt in by_source.items():
            label = source_labels.get(src, src)
            print(f"  {label}: {cnt}条")
        print()

    # 按省份
    by_province = s.get("by_province", {})
    if by_province:
        print("按省份:")
        for prov, cnt in by_province.items():
            print(f"  {prov}: {cnt}条")


# ============================================================
# search 子命令 — 搜索经验记录
# ============================================================

def cmd_search(args):
    """搜索经验库记录"""
    keyword = args.keyword
    if not keyword:
        print("错误：请输入搜索关键词")
        return

    from src.experience_db import ExperienceDB
    db = ExperienceDB()

    province = _resolve_province(args.province) if args.province else None
    records = db.find_experience(keyword, province=province, limit=args.limit or 20)

    if not records:
        print(f"未找到包含「{keyword}」的记录")
        return

    print(f"找到 {len(records)} 条记录（关键词: {keyword}）")
    print()
    _print_records(records)


# ============================================================
# list 子命令 — 分页浏览
# ============================================================

def cmd_list(args):
    """分页浏览经验库记录"""
    page = max(1, args.page or 1)
    page_size = 20
    offset = (page - 1) * page_size

    province = _resolve_province(args.province) if args.province else None

    db_path = config.get_experience_db_path()
    if not db_path.exists():
        print("经验库为空（数据库文件不存在）")
        return

    conn = db_connect(db_path, row_factory=True)
    try:
        if province:
            total = conn.execute(
                "SELECT COUNT(*) FROM experiences WHERE province = ?", (province,)
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]

        if total == 0:
            print("经验库为空")
            return

        total_pages = (total + page_size - 1) // page_size
        if page > total_pages:
            print(f"只有 {total_pages} 页，请输入 1-{total_pages}")
            return

        if province:
            rows = conn.execute("""
                SELECT id, bill_name, quota_ids, quota_names, confidence,
                       confirm_count, source, layer, province, updated_at
                FROM experiences
                WHERE province = ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """, (province, page_size, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, bill_name, quota_ids, quota_names, confidence,
                       confirm_count, source, layer, province, updated_at
                FROM experiences
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """, (page_size, offset)).fetchall()
    finally:
        conn.close()

    prov_info = f"  省份: {province}" if province else ""
    print(f"经验库记录  第{page}/{total_pages}页  共{total}条{prov_info}")
    print()
    _print_records([dict(r) for r in rows])
    print()
    if page < total_pages:
        next_cmd = f"python tools/experience_manager.py list --page {page + 1}"
        if province:
            next_cmd += f' --province "{args.province}"'
        print(f"下一页: {next_cmd}")


# ============================================================
# promote 子命令 — 候选层晋升
# ============================================================

def cmd_promote(args):
    """候选层审核晋升"""
    from src.experience_db import ExperienceDB
    db = ExperienceDB()

    province = args.province
    source = args.source

    # --limit 的默认值：--all模式下默认0（全部），其他模式默认50
    if args.limit is None:
        effective_limit = 0 if args.all else 50
    else:
        effective_limit = args.limit

    if args.list:
        _promote_list(db, province, source, effective_limit or 50)
    elif args.all:
        _promote_all(db, province, source, effective_limit)
    else:
        _promote_interactive(db, province, source, effective_limit or 50)


def _promote_list(db, province, source, limit):
    """列出候选层数据"""
    records = db.get_candidate_records(province=province, limit=limit)
    if source:
        records = [r for r in records if r["source"] == source]

    if not records:
        print("候选层没有数据。")
        return

    print(f"候选层共 {len(records)} 条记录：\n")
    print(f"{'序号':>4}  {'ID':>6}  {'来源':<14}  {'清单名称':<25}  {'定额编号':<15}  {'定额名称'}")
    print("-" * 100)

    for i, r in enumerate(records, 1):
        bill = (r.get("bill_name") or r["bill_text"][:25])[:25]
        qid = r["quota_ids"][0] if r["quota_ids"] else "?"
        qname = r["quota_names"][0][:20] if r["quota_names"] else "?"
        src = r["source"]
        print(f"{i:>4}  #{r['id']:<5}  {src:<14}  {bill:<25}  {qid:<15}  {qname}")


def _promote_interactive(db, province, source, limit):
    """交互式逐条审核"""
    records = db.get_candidate_records(province=province, limit=limit)
    if source:
        records = [r for r in records if r["source"] == source]

    if not records:
        print("候选层没有待审核数据。")
        return

    print("=" * 70)
    print("候选层审核（逐条确认）")
    print("=" * 70)
    print(f"共 {len(records)} 条待审核\n")
    print("操作说明：y=晋升  n=跳过  d=删除  q=退出\n")

    promoted = skipped = deleted = 0

    for i, r in enumerate(records, 1):
        bill = r.get("bill_name") or r["bill_text"][:40]
        print(f"--- [{i}/{len(records)}] ID#{r['id']} ---")
        print(f"  清单: {bill[:50]}")
        print(f"  定额: {r['quota_ids']} {r['quota_names']}")
        print(f"  来源: {r['source']}  置信度: {r['confidence']}")
        notes = r.get("notes", "")
        if notes:
            print(f"  备注: {notes[:80]}")

        while True:
            choice = input("  操作 (y/n/d/q): ").strip().lower()
            if choice in ("y", "n", "d", "q"):
                break
            print("  请输入 y/n/d/q")

        if choice == "q":
            print("\n已退出审核。")
            break
        elif choice == "y":
            ok = db.promote_to_authority(r["id"], reason="用户手动审核确认")
            print("  ✓ 已晋升到权威层" if ok else "  × 晋升失败")
            if ok:
                promoted += 1
        elif choice == "d":
            conn = None
            try:
                conn = db._connect()
                conn.execute("DELETE FROM experiences WHERE id = ? AND layer = 'candidate'",
                             (r["id"],))
                conn.commit()
                deleted += 1
                print("  × 已删除")
            except Exception as e:
                print(f"  删除失败: {e}")
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
        else:
            skipped += 1
            print("  - 跳过")
        print()

    print("=" * 70)
    print(f"审核结果: 晋升{promoted}条  跳过{skipped}条  删除{deleted}条")


def _promote_all(db, province, source, limit):
    """批量晋升所有候选层数据"""
    records = db.get_candidate_records(province=province, limit=limit)
    if source:
        records = [r for r in records if r["source"] == source]

    if not records:
        print("候选层没有数据。")
        return

    print(f"准备批量晋升 {len(records)} 条候选层记录...")
    promoted = failed = 0
    for r in records:
        ok = db.promote_to_authority(r["id"], reason="批量晋升")
        if ok:
            promoted += 1
        else:
            failed += 1
    print(f"批量晋升完成: 成功{promoted}条  失败{failed}条")


# ============================================================
# clean 子命令 — 脏数据清理
# ============================================================

# 清理规则的配置常量
MIN_TEXT_LENGTH = 4        # 清单文本最短长度
GARBLE_THRESHOLD = 0.5     # 乱码判定阈值


def cmd_clean(args):
    """经验库脏数据清理"""
    if args.purge_batch:
        _clean_purge_batch(args.purge_batch)
        return

    if args.dedup:
        _clean_dedup(province_filter=args.province, dry_run=not args.fix)
        return

    # 默认扫描
    print("扫描经验库中的问题数据...")
    result = _clean_scan(province_filter=args.province)

    s = result["summary"]
    print(f"\n扫描完成: {result['total']} 条")
    print(f"  正常: {result['normal']} 条")
    print(f"  文本太短: {s['text_too_short']} 条")
    print(f"  乱码: {s['garbled_text']} 条")
    print(f"  重复: {s['duplicates']} 条")
    print(f"  编号不存在: {s['quota_not_found']} 条")
    print(f"  问题总计: {s['total_issues']} 条")

    if args.fix:
        _clean_fix(result, dry_run=False)
    elif s["total_issues"] > 0:
        print(f"\n加 --fix 执行清理。")


def _clean_scan(province_filter=None):
    """扫描经验库中的问题数据"""
    conn = db_connect(config.get_experience_db_path(), row_factory=True)
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

    # 去重检测
    seen = defaultdict(list)

    for row in rows:
        row_id = row["id"]
        bill_text = row["bill_text"] or ""
        quota_ids = row["quota_ids"] or ""
        province = row["province"] or ""

        # 规则1：文本太短
        clean_text = re.sub(r'\s+', '', bill_text)
        if len(clean_text) < MIN_TEXT_LENGTH:
            issues["text_too_short"].append({
                "id": row_id, "bill_text": bill_text, "province": province,
            })
            continue

        # 规则2：乱码检测
        if _is_garbled(bill_text):
            issues["garbled_text"].append({
                "id": row_id, "bill_text": bill_text[:50], "province": province,
            })
            continue

        # 规则3：收集重复候选
        key = (province, bill_text.strip(), quota_ids.strip())
        seen[key].append(row_id)

    # 找出重复的
    for key, ids in seen.items():
        if len(ids) > 1:
            ids_sorted = sorted(ids)
            for dup_id in ids_sorted[:-1]:
                issues["duplicates"].append({
                    "id": dup_id, "province": key[0],
                    "bill_text": key[1][:30], "keep_id": ids_sorted[-1],
                })

    # 规则4：定额编号不存在
    issues["quota_not_found"] = _check_quota_exists(rows, province_filter)

    # 汇总
    issue_ids = set()
    for items in issues.values():
        for item in items:
            issue_ids.add(item["id"])

    return {
        "total": total,
        "normal": total - len(issue_ids),
        "issues": issues,
        "summary": {
            "text_too_short": len(issues["text_too_short"]),
            "garbled_text": len(issues["garbled_text"]),
            "duplicates": len(issues["duplicates"]),
            "quota_not_found": len(issues["quota_not_found"]),
            "total_issues": len(issue_ids),
        }
    }


def _is_garbled(text):
    """判断文本是否为乱码"""
    if not text:
        return False
    normal_pattern = re.compile(
        r'[\u4e00-\u9fa5a-zA-Z0-9\s\.\,\;\:\!\?\-\+\*\/\(\)\[\]\{\}×°%#&@=<>，。；：！？、（）【】｛｝""''·—…]'
    )
    normal_count = len(normal_pattern.findall(text))
    total_count = len(text)
    if total_count == 0:
        return False
    return (1 - normal_count / total_count) > GARBLE_THRESHOLD


def _check_quota_exists(rows, province_filter=None):
    """检查定额编号是否存在于定额库"""
    result = []
    try:
        provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
        if not provinces_dir.exists():
            return result

        quota_sets = {}  # province → set(quota_ids)

        for row in rows:
            prov = row["province"] or ""
            if province_filter and prov != province_filter:
                continue
            if row.get("layer") == "authority":
                continue

            quota_ids_str = row["quota_ids"] or "[]"
            try:
                quota_ids = json.loads(quota_ids_str)
            except json.JSONDecodeError:
                continue
            if not quota_ids:
                continue

            if prov not in quota_sets:
                quota_sets[prov] = _load_quota_ids(prov)
            if not quota_sets[prov]:
                continue

            for qid in quota_ids:
                if qid and qid not in quota_sets[prov]:
                    result.append({
                        "id": row["id"], "province": prov,
                        "bill_text": (row["bill_text"] or "")[:30],
                        "quota_id": qid,
                    })
                    break
    except Exception:
        pass
    return result


def _load_quota_ids(province):
    """加载某省份的所有定额编号"""
    provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
    for d in provinces_dir.iterdir():
        if d.is_dir() and d.name.startswith(province):
            db_file = d / "quota.db"
            if db_file.exists():
                try:
                    conn = db_connect(db_file)
                    try:
                        return {row[0] for row in conn.execute("SELECT quota_id FROM quotas")}
                    finally:
                        conn.close()
                except Exception:
                    pass
    return set()


def _ensure_trash_table(conn):
    """确保 _trash 表存在（回收站，误删可回滚）"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _trash (
            id INTEGER, bill_text TEXT, quota_ids TEXT,
            province TEXT, layer TEXT, source TEXT,
            clean_reason TEXT, clean_time TEXT, original_data TEXT
        )
    """)


def _clean_fix(scan_result, dry_run=True):
    """执行清理"""
    issues = scan_result["issues"]
    to_delete = set()
    reasons = {}

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

    conn = db_connect(config.get_experience_db_path(), row_factory=True)
    try:
        _ensure_trash_table(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        deleted = 0

        for row_id in to_delete:
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


def _clean_dedup(province_filter=None, dry_run=True):
    """只做去重"""
    result = _clean_scan(province_filter)
    dups = result["issues"]["duplicates"]

    if not dups:
        print("没有重复数据。")
        return

    print(f"发现 {len(dups)} 条重复数据")
    if dry_run:
        for d in dups[:10]:
            print(f"  id={d['id']} province={d['province']} "
                  f"bill_text={d['bill_text']} → 保留id={d['keep_id']}")
        if len(dups) > 10:
            print(f"  ...还有 {len(dups) - 10} 条")
        print("\n加 --fix 参数执行实际去重。")
        return

    fake_result = {
        "issues": {
            "text_too_short": [], "garbled_text": [],
            "duplicates": dups, "quota_not_found": [],
        }
    }
    _clean_fix(fake_result, dry_run=False)


def _clean_purge_batch(date_str):
    """按日期回滚某次批量导入"""
    conn = db_connect(config.get_experience_db_path(), row_factory=True)
    try:
        _ensure_trash_table(conn)

        rows = conn.execute("""
            SELECT * FROM experiences
            WHERE layer = 'candidate'
            AND date(created_at, 'unixepoch', 'localtime') = ?
        """, (date_str,)).fetchall()

        if not rows:
            rows = conn.execute("""
                SELECT * FROM experiences
                WHERE layer = 'candidate' AND created_at LIKE ?
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
# health 子命令 — 权威层体检
# ============================================================

def cmd_health(args):
    """经验库体检（用审核规则回扫权威层）"""
    from src.experience_db import ExperienceDB
    from src.review_checkers import (
        check_category_mismatch, check_material_mismatch,
        check_connection_mismatch, check_pipe_usage,
        check_parameter_deviation, check_sleeve_mismatch,
        check_electric_pair, check_elevator_type,
        check_elevator_floor, extract_description_lines,
    )

    db = ExperienceDB()
    stats = db.get_stats()

    print("=" * 70)
    print("经验库体检报告")
    print("=" * 70)
    print(f"  权威层总数: {stats['authority']}")
    print(f"  候选层总数: {stats['candidate']}")
    print(f"  检查模式: {'自动修复' if args.fix else '仅报告'}")
    if args.province:
        print(f"  筛选省份: {args.province}")
    print("-" * 70)

    records = db.get_authority_records(province=args.province, limit=args.limit)
    if not records:
        print("没有找到权威层记录。")
        return

    print(f"待检查: {len(records)} 条\n")

    # 逐条检查
    problems = []
    for record in records:
        quota_names = record.get("quota_names", [])
        quota_ids = record.get("quota_ids", [])
        if not quota_names or not quota_ids:
            continue

        quota_name = quota_names[0]
        quota_id = quota_ids[0]
        if not quota_name:
            continue

        bill_name = record.get("bill_name", "")
        bill_text = record.get("bill_text", "")
        desc = bill_text
        if bill_name and bill_text.startswith(bill_name):
            desc = bill_text[len(bill_name):].strip()

        item = {"name": bill_name or bill_text[:30], "description": desc}
        desc_lines = extract_description_lines(desc)

        error = (
            check_category_mismatch(item, quota_name, desc_lines)
            or check_sleeve_mismatch(item, quota_name, desc_lines)
            or check_material_mismatch(item, quota_name, desc_lines)
            or check_connection_mismatch(item, quota_name, desc_lines)
            or check_pipe_usage(item, quota_name, desc_lines)
            or check_parameter_deviation(item, quota_name, desc_lines)
            or check_electric_pair(item, quota_name, desc_lines)
            or check_elevator_type(item, quota_name, desc_lines)
            or check_elevator_floor(item, quota_name, desc_lines, quota_id=quota_id)
        )

        if error:
            problems.append((record, error))
            bill_short = (record.get("bill_name") or record["bill_text"][:30])
            quota_short = quota_name[:20]
            print(f"  [问题] #{record['id']} {bill_short[:25]:<25} "
                  f"→ {quota_short:<20} | {error['type']}: {error['reason'][:40]}")

    # 汇总
    print("\n" + "=" * 70)
    print(f"检查完成: {len(records)} 条已检查, {len(problems)} 条有问题")

    if not problems:
        print("所有权威层数据审核通过，经验库健康。")
        return

    problem_rate = len(problems) * 100 // max(len(records), 1)
    print(f"问题率: {problem_rate}%")

    type_counts = {}
    for _, error in problems:
        t = error.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("\n按错误类型分布:")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {cnt} 条")

    if args.fix:
        print(f"\n开始降级 {len(problems)} 条问题记录...")
        demoted = 0
        for record, error in problems:
            reason = f"{error['type']}: {error.get('reason', '')[:80]}"
            try:
                db.demote_to_candidate(record["id"], reason=reason)
                demoted += 1
            except Exception as e:
                print(f"  降级失败 #{record['id']}: {e}")
        print(f"降级完成: {demoted}/{len(problems)} 条已降级为候选层")
    else:
        print(f"\n要自动降级，请加 --fix 参数")


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="经验库统一管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令：
  stats       查看统计信息
  search      搜索经验记录
  list        分页浏览记录
  promote     候选层审核晋升
  clean       脏数据清理
  health      权威层体检

示例：
  python tools/experience_manager.py stats
  python tools/experience_manager.py search "镀锌钢管"
  python tools/experience_manager.py promote --list
  python tools/experience_manager.py clean --scan
  python tools/experience_manager.py health --fix
""",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # stats
    sub.add_parser("stats", help="查看统计信息")

    # search
    p_search = sub.add_parser("search", help="搜索经验记录")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("--province", help="按省份过滤")
    p_search.add_argument("--limit", type=int, default=20, help="最多返回条数（默认20）")

    # list
    p_list = sub.add_parser("list", help="分页浏览记录")
    p_list.add_argument("--page", type=int, default=1, help="页码（默认第1页）")
    p_list.add_argument("--province", help="按省份过滤")

    # promote
    p_promote = sub.add_parser("promote", help="候选层审核晋升")
    p_promote.add_argument("--list", action="store_true", help="只列出候选层（不修改）")
    p_promote.add_argument("--all", action="store_true", help="批量晋升所有（谨慎！）")
    p_promote.add_argument("--source", help="只审核指定来源")
    p_promote.add_argument("--province", help="只审核指定省份")
    p_promote.add_argument("--limit", type=int, default=None, help="最大条数")

    # clean
    p_clean = sub.add_parser("clean", help="脏数据清理")
    p_clean.add_argument("--scan", action="store_true", help="只扫描不删（默认）")
    p_clean.add_argument("--fix", action="store_true", help="执行清理")
    p_clean.add_argument("--dedup", action="store_true", help="只去重")
    p_clean.add_argument("--province", help="只处理某省份")
    p_clean.add_argument("--purge-batch", metavar="DATE", help="回滚某次批量导入")

    # health
    p_health = sub.add_parser("health", help="权威层体检")
    p_health.add_argument("--fix", action="store_true", help="自动降级问题条目")
    p_health.add_argument("--province", help="只检查指定省份")
    p_health.add_argument("--limit", type=int, default=0, help="只检查前N条（调试用）")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "promote":
        cmd_promote(args)
    elif args.command == "clean":
        cmd_clean(args)
    elif args.command == "health":
        cmd_health(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    # Windows终端编码兼容
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
