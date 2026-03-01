# -*- coding: utf-8 -*-
"""
自动更新前端更新日志

发布新版本时自动调用，从 git log 提取最近的提交记录，
更新 web/frontend/src/constants/changelog.ts 中的版本号和更新内容。

用法：
    python tools/bump_changelog.py 0.1.30
    python tools/bump_changelog.py          # 自动从 lzc-manifest.yml 读版本号
"""

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# 更新日志文件路径
CHANGELOG_FILE = Path(__file__).parent.parent / "web/frontend/src/constants/changelog.ts"
MANIFEST_FILE = Path(__file__).parent.parent / "lzc-manifest.yml"


def get_version_from_manifest() -> str:
    """从 lzc-manifest.yml 读取当前版本号"""
    text = MANIFEST_FILE.read_text(encoding="utf-8")
    match = re.search(r'^version:\s*(.+)$', text, re.MULTILINE)
    if not match:
        print("  [错误] 无法从 lzc-manifest.yml 读取版本号")
        sys.exit(1)
    return match.group(1).strip().strip('"')


def get_recent_commits() -> list[str]:
    """获取自上次 deploy 提交以来的所有非 deploy 提交消息"""
    try:
        # 找到最近的 deploy 提交（上一个版本的发布点）
        result = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            capture_output=True, text=True, encoding="utf-8"
        )
        lines = result.stdout.strip().splitlines()
    except Exception:
        return []

    changes = []
    for line in lines:
        # 跳过 deploy 提交本身
        msg = line.split(" ", 1)[1] if " " in line else line
        if msg.startswith("deploy:"):
            break  # 碰到上一次 deploy 就停
        # 提取有意义的提交（去掉前缀如 feat: fix: 等）
        clean = re.sub(r'^(feat|fix|refactor|chore|docs|test|style|perf):\s*', '', msg)
        if clean and len(clean) > 3:
            changes.append(clean)

    return changes


def update_changelog(new_version: str, changes: list[str]):
    """更新 changelog.ts 文件

    自动生成的条目默认 type='admin'（仅管理员可见）。
    部署后可手动编辑 changelog.ts，把用户关心的改动改为 type='user'。
    """
    if not CHANGELOG_FILE.exists():
        print(f"  [错误] 文件不存在: {CHANGELOG_FILE}")
        sys.exit(1)

    content = CHANGELOG_FILE.read_text(encoding="utf-8")

    # 1. 更新 APP_VERSION
    content = re.sub(
        r"export const APP_VERSION = '[^']+';",
        f"export const APP_VERSION = '{new_version}';",
        content
    )

    # 2. 在 CHANGELOG 数组最前面插入新条目（新格式：带 type 字段）
    today = date.today().isoformat()
    # 默认都标为 admin，部署后手动把用户关心的改成 user
    changes_lines = ",\n".join(
        f"      {{ type: 'admin', text: '{c}' }}" for c in changes
    )
    new_entry = (
        f"  {{\n"
        f"    version: '{new_version}',\n"
        f"    date: '{today}',\n"
        f"    changes: [\n"
        f"{changes_lines},\n"
        f"    ],\n"
        f"  }},\n"
    )

    # 在 CHANGELOG 数组开头插入
    content = re.sub(
        r'(export const CHANGELOG: ChangelogEntry\[\] = \[\n)',
        r'\g<1>' + new_entry,
        content
    )

    CHANGELOG_FILE.write_text(content, encoding="utf-8")
    print(f"  [OK] changelog.ts 已更新: v{new_version}")
    print(f"       日期: {today}")
    print(f"       更新项: {len(changes)}条（默认admin，需手动改user）")
    for c in changes:
        print(f"         - {c}")


def main():
    # 版本号：命令行参数 > manifest 文件
    if len(sys.argv) > 1:
        new_version = sys.argv[1]
    else:
        new_version = get_version_from_manifest()

    print(f"  [INFO] 目标版本: v{new_version}")

    # 获取最近的提交记录
    changes = get_recent_commits()
    if not changes:
        changes = [f"v{new_version} 更新"]
        print("  [WARN] 没有找到新的提交记录，使用默认描述")

    # 更新文件
    update_changelog(new_version, changes)


if __name__ == "__main__":
    main()
