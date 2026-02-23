# -*- coding: utf-8 -*-
"""
候选层审核晋升工具 — 审核候选层数据，确认无误后晋升为权威层

候选层数据来源：
  - auto_review: Jarvis自动审核纠正（未经人工验证）
  - auto_match: 系统自动匹配（未经人工验证）
  - project_import被降级: 导入时定额编号校验不通过

用法:
  python tools/experience_promote.py                    # 交互式审核（逐条确认）
  python tools/experience_promote.py --list             # 只列出候选层数据
  python tools/experience_promote.py --all              # 全部晋升（谨慎！）
  python tools/experience_promote.py --source auto_review  # 只审核指定来源
"""

import sys
import io
import argparse

from src.experience_db import ExperienceDB


def list_candidates(province=None, source=None, limit=50):
    """列出候选层数据"""
    db = ExperienceDB()
    records = db.get_candidate_records(province=province, limit=limit)

    if source:
        records = [r for r in records if r["source"] == source]

    if not records:
        print("候选层没有数据。")
        return []

    print(f"候选层共 {len(records)} 条记录：\n")
    print(f"{'序号':>4}  {'ID':>6}  {'来源':<14}  {'清单名称':<25}  {'定额编号':<15}  {'定额名称'}")
    print("-" * 100)

    for i, r in enumerate(records, 1):
        bill = (r.get("bill_name") or r["bill_text"][:25])[:25]
        qid = r["quota_ids"][0] if r["quota_ids"] else "?"
        qname = r["quota_names"][0][:20] if r["quota_names"] else "?"
        src = r["source"]
        print(f"{i:>4}  #{r['id']:<5}  {src:<14}  {bill:<25}  {qid:<15}  {qname}")

    return records


def interactive_review(province=None, source=None, limit=50):
    """交互式逐条审核候选层数据"""
    db = ExperienceDB()
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
    print("操作说明：")
    print("  y = 确认正确，晋升到权威层")
    print("  n = 跳过（保留在候选层）")
    print("  d = 删除（从候选层移除）")
    print("  q = 退出审核")
    print()

    promoted = 0
    skipped = 0
    deleted = 0

    for i, r in enumerate(records, 1):
        bill = r.get("bill_name") or r["bill_text"][:40]
        qids = r["quota_ids"]
        qnames = r["quota_names"]
        src = r["source"]
        notes = r.get("notes", "")

        print(f"--- [{i}/{len(records)}] ID#{r['id']} ---")
        print(f"  清单: {bill[:50]}")
        print(f"  定额: {qids} {qnames}")
        print(f"  来源: {src}  置信度: {r['confidence']}")
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
            if ok:
                promoted += 1
                print("  ✓ 已晋升到权威层")
            else:
                print("  × 晋升失败（可能已不在候选层）")
        elif choice == "d":
            # 删除候选层记录
            try:
                conn = db._connect()
                conn.execute("DELETE FROM experiences WHERE id = ? AND layer = 'candidate'",
                             (r["id"],))
                conn.commit()
                conn.close()
                deleted += 1
                print("  × 已删除")
            except Exception as e:
                print(f"  删除失败: {e}")
        else:
            skipped += 1
            print("  - 跳过")

        print()

    print("=" * 70)
    print(f"审核结果: 晋升{promoted}条  跳过{skipped}条  删除{deleted}条")
    print("=" * 70)


def promote_all(province=None, source=None, limit=0):
    """批量晋升所有候选层数据（谨慎使用）"""
    db = ExperienceDB()
    records = db.get_candidate_records(province=province, limit=limit)

    if source:
        records = [r for r in records if r["source"] == source]

    if not records:
        print("候选层没有数据。")
        return

    print(f"准备批量晋升 {len(records)} 条候选层记录...")
    promoted = 0
    failed = 0

    for r in records:
        ok = db.promote_to_authority(r["id"], reason="批量晋升")
        if ok:
            promoted += 1
        else:
            failed += 1

    print(f"批量晋升完成: 成功{promoted}条  失败{failed}条")


def main():
    parser = argparse.ArgumentParser(description="候选层审核晋升工具")
    parser.add_argument("--list", action="store_true",
                        help="只列出候选层数据（不修改）")
    parser.add_argument("--all", action="store_true",
                        help="批量晋升所有候选层数据（谨慎！）")
    parser.add_argument("--source", type=str, default=None,
                        help="只审核指定来源（如 auto_review, auto_match）")
    parser.add_argument("--province", type=str, default=None,
                        help="只审核指定省份")
    parser.add_argument("--limit", type=int, default=None,
                        help="每次审核的最大条数（默认50，--all模式默认全部）")
    args = parser.parse_args()

    # --limit 的默认值：--all模式下默认0（全部），其他模式默认50
    if args.limit is None:
        effective_limit = 0 if args.all else 50
    else:
        effective_limit = args.limit

    if args.list:
        list_candidates(province=args.province, source=args.source,
                        limit=effective_limit or 50)
    elif args.all:
        promote_all(province=args.province, source=args.source,
                    limit=effective_limit)
    else:
        interactive_review(province=args.province, source=args.source,
                           limit=effective_limit or 50)


if __name__ == "__main__":
    # Windows终端编码兼容
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
