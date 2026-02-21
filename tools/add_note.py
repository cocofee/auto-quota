# -*- coding: utf-8 -*-
"""
快捷笔记工具 — 随手记录零散的造价知识

把日常积累的经验（论坛答疑、微信群讨论、和AI的对话等）
快速存入规则知识库，生成方法卡片时会自动检索融入。

用法：
    # 记一条笔记（自动存入当前省份）
    python tools/add_note.py "PPR管热熔连接DN换算要看外径不是内径"

    # 指定省份
    python tools/add_note.py "消防喷淋管用沟槽连接" --province "北京2024"

    # 指定专业
    python tools/add_note.py "配电箱回路数向上取档" --specialty "电气"

    # 从文件导入（把聊天记录/论坛帖子粘贴到txt里）
    python tools/add_note.py --file "笔记.txt"

    # 批量导入 knowledge/笔记/ 目录下所有txt
    python tools/add_note.py --import-all

    # 查看已有笔记
    python tools/add_note.py --list
"""

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

# 把项目根目录加入路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
import config


# 笔记目录（和规则库分开，方便管理）
NOTES_DIR = config.KNOWLEDGE_DIR / "笔记"


def add_note(text: str, province: str = None,
             specialty: str = "", source: str = "手动笔记") -> bool:
    """
    添加一条笔记到规则知识库

    参数:
        text: 笔记内容
        province: 省份（None=用"通用"，表示跨省通用知识）
        specialty: 专业（安装/土建/市政/电气等）
        source: 来源标记（如"论坛"、"微信群"、"AI讨论"）

    返回:
        是否成功添加（重复内容返回False）
    """
    text = text.strip()
    if not text:
        print("  [错误] 笔记内容不能为空")
        return False

    # 省份默认"通用"（跨省通用知识）
    province = province or "通用"

    from src.rule_knowledge import RuleKnowledge
    kb = RuleKnowledge(province=province)

    # 用content_hash去重
    content_hash = hashlib.md5(
        f"{province}:{specialty}:{text}".encode()
    ).hexdigest()

    conn = kb._connect()
    try:
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM rules WHERE content_hash = ?",
            (content_hash,)
        ).fetchone()

        if existing:
            print(f"  [跳过] 该笔记已存在（ID: {existing[0]}）")
            return False

        # 提取关键词
        keywords = kb._extract_keywords(text)

        # 存入规则库
        conn.execute("""
            INSERT INTO rules (province, specialty, chapter, section, content,
                               content_hash, source_file, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (province, specialty, "笔记", source, text,
              content_hash, f"笔记:{source}", " ".join(keywords)))
        conn.commit()

        # 更新向量索引
        try:
            kb._update_vector_index()
        except Exception:
            pass  # 向量索引失败不影响主流程

        print(f"  [已记录] {text[:50]}...")
        print(f"  省份: {province} | 专业: {specialty or '未指定'} | 来源: {source}")
        return True

    except Exception as e:
        conn.rollback()
        print(f"  [错误] 保存失败: {e}")
        return False
    finally:
        conn.close()


def import_notes_dir(province: str = None) -> dict:
    """
    批量导入 knowledge/笔记/ 目录下的所有txt文件

    目录结构：
        knowledge/笔记/论坛问答.txt
        knowledge/笔记/微信群讨论.txt
        knowledge/笔记/AI对话摘要.txt
        knowledge/笔记/北京2024/给排水笔记.txt  （按省份子目录）

    返回:
        {"total": 总段数, "added": 新增段数, "skipped": 已存在段数}
    """
    if not NOTES_DIR.exists():
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  已创建笔记目录: {NOTES_DIR}")
        print(f"  请在此目录下放入笔记txt文件，然后重新运行")
        return {"total": 0, "added": 0, "skipped": 0}

    from src.rule_knowledge import RuleKnowledge

    total_stats = {"total": 0, "added": 0, "skipped": 0}

    # 处理根目录下的txt文件（省份用"通用"或指定的省份）
    default_province = province or "通用"
    kb = RuleKnowledge(province=default_province)

    for txt_file in sorted(NOTES_DIR.glob("*.txt")):
        stats = kb.import_file(
            str(txt_file), province=default_province,
            chapter="笔记"
        )
        total_stats["total"] += stats["total"]
        total_stats["added"] += stats["added"]
        total_stats["skipped"] += stats["skipped"]

    # 处理省份子目录（如 knowledge/笔记/北京2024/）
    for sub_dir in sorted(NOTES_DIR.iterdir()):
        if not sub_dir.is_dir():
            continue
        sub_province = sub_dir.name
        sub_kb = RuleKnowledge(province=sub_province)
        for txt_file in sorted(sub_dir.glob("*.txt")):
            stats = sub_kb.import_file(
                str(txt_file), province=sub_province,
                chapter="笔记"
            )
            total_stats["total"] += stats["total"]
            total_stats["added"] += stats["added"]
            total_stats["skipped"] += stats["skipped"]

    print(f"\n  笔记导入完成: {total_stats['added']}段新增, {total_stats['skipped']}段已存在")
    return total_stats


def list_notes(province: str = None, limit: int = 20):
    """列出已有笔记"""
    from src.rule_knowledge import RuleKnowledge
    kb = RuleKnowledge()

    conn = kb._connect(row_factory=True)
    try:
        if province:
            rows = conn.execute(
                "SELECT * FROM rules WHERE chapter = '笔记' AND province = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (province, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rules WHERE chapter = '笔记' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("  暂无笔记记录")
        return

    print(f"\n  最近 {len(rows)} 条笔记:")
    print(f"  {'─' * 60}")
    for row in rows:
        row = dict(row)
        content = row.get("content", "")[:60]
        province_name = row.get("province", "")
        source = row.get("source_file", "").replace("笔记:", "")
        specialty = row.get("specialty", "")
        spec_info = f" [{specialty}]" if specialty else ""
        print(f"  #{row['id']} ({province_name}{spec_info}) {content}")
    print(f"  {'─' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description="快捷笔记工具 — 随手记录零散的造价知识",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 快速记一条
  python tools/add_note.py "PPR管热熔连接DN换算要看外径不是内径"

  # 指定省份和来源
  python tools/add_note.py "消防喷淋管沟槽连接" --province "北京2024" --source "微信群"

  # 从文件导入
  python tools/add_note.py --file "论坛问答.txt" --province "通用"

  # 批量导入笔记目录
  python tools/add_note.py --import-all

  # 查看已有笔记
  python tools/add_note.py --list
        """,
    )
    parser.add_argument("text", nargs="?", default=None,
                        help="笔记内容（一句话）")
    parser.add_argument("--province", default=None,
                        help="省份（默认'通用'，表示跨省通用知识）")
    parser.add_argument("--specialty", default="",
                        help="专业（如：安装/土建/电气）")
    parser.add_argument("--source", default="手动笔记",
                        help="来源标记（如：论坛/微信群/AI讨论）")
    parser.add_argument("--file", default=None,
                        help="从txt文件导入笔记")
    parser.add_argument("--import-all", action="store_true",
                        help="批量导入 knowledge/笔记/ 目录")
    parser.add_argument("--list", action="store_true",
                        help="列出已有笔记")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="跳过确认直接保存（供skill调用）")

    args = parser.parse_args()

    if args.list:
        list_notes(province=args.province)
    elif args.import_all:
        import_notes_dir(province=args.province)
    elif args.file:
        # 从文件导入
        from src.rule_knowledge import RuleKnowledge
        province = args.province or "通用"
        kb = RuleKnowledge(province=province)
        stats = kb.import_file(args.file, province=province, chapter="笔记")
        print(f"  文件导入完成: {stats['added']}段新增, {stats['skipped']}段已存在")
    elif args.text:
        # 命令行直传文本
        if args.yes:
            # --yes 跳过确认（skill调用时已在对话中确认）
            add_note(args.text, province=args.province,
                     specialty=args.specialty, source=args.source)
        else:
            # 先确认再存
            print()
            print(f"  内容: {args.text}")
            print(f"  省份: {args.province or '通用'} | 专业: {args.specialty or '未指定'} | 来源: {args.source}")
            try:
                confirm = input("  确认记录？(y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm in ("y", "yes", ""):
                add_note(args.text, province=args.province,
                         specialty=args.specialty, source=args.source)
            else:
                print("  已取消")
    else:
        # 没有参数，进入交互模式
        print()
        print("  快捷笔记 — 输入内容后确认记录，输入 q 退出")
        print()
        province = args.province or "通用"
        while True:
            try:
                text = input(f"  [{province}] 记笔记: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text or text.lower() in ("q", "quit", "exit"):
                break
            # 确认后才存
            try:
                confirm = input("  确认记录？(y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if confirm in ("y", "yes", ""):
                add_note(text, province=province,
                         specialty=args.specialty, source=args.source)
            else:
                print("  已跳过")
            print()
            print()


if __name__ == "__main__":
    main()
