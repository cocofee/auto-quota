"""
定额安装：省份初始化工具（菜单界面）

供 scripts/定额安装.bat 调用，所有中文显示都在Python里处理，
避免bat文件的编码问题。
"""

import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config


def clear_screen():
    import os
    os.system('cls' if os.name == 'nt' else 'clear')


def show_main_menu():
    """主菜单"""
    clear_screen()
    print()
    print("  ========================================")
    print("    定额安装：省份初始化工具")
    print("  ========================================")
    print()
    print("    首次使用新省份时，需要导入定额数据。")
    print("    安装完成后日常使用不需要再次运行此工具。")
    print()
    print("  ----------------------------------------")
    print("    [1] 导入定额库（必选）")
    print("        | 自动筛选未导入的省份")
    print()
    print("    [2] 导入定额规则（可选）")
    print("        | 导入定额说明文本，提升匹配准确率")
    print()
    print("    [q] 退出")
    print("  ----------------------------------------")
    print()


def get_db_count(province_name):
    """获取定额数量"""
    from db.sqlite import connect as _db_connect
    db_path = config.get_quota_db_path(province_name)
    if not db_path.exists():
        return "首次导入"
    try:
        conn = _db_connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
        conn.close()
        return f"{count}条"
    except Exception:
        return "已存在"


def select_province_for_import():
    """选择要导入的省份（支持已导入的重新导入）"""
    data_provinces = config.list_all_provinces()
    if not data_provinces:
        print()
        print(f"  [提示] 没有找到任何定额数据")
        print(f"  请在 {config.QUOTA_DATA_DIR} 下放入定额Excel")
        print()
        return None

    # 分为已导入和未导入
    need_import = []
    already_imported = []
    for p in data_provinces:
        info = get_db_count(p)
        if info == "首次导入":
            need_import.append((p, info))
        else:
            bm25_path = config.get_province_db_dir(p) / "bm25_index.json"
            if not bm25_path.exists():
                need_import.append((p, info))
            else:
                already_imported.append((p, info))

    # 显示列表
    print()
    print("  ============================================")
    print("    选择要导入的定额库")
    print("  ============================================")
    print()

    all_items = []

    if need_import:
        print("  --- 待导入 ---")
        for p, info in need_import:
            all_items.append((p, info))
            idx = len(all_items)
            print(f"    [{idx}] {p}  ({info})")
        print()

    if already_imported:
        print("  --- 已导入（选择可重新导入）---")
        for p, info in already_imported:
            all_items.append((p, info))
            idx = len(all_items)
            print(f"    [{idx}] {p}  ({info})")
        print()

    if not all_items:
        print("  没有可导入的定额库")
        return None

    print(f"  共 {len(all_items)} 个定额库")
    print()

    try:
        choice = input("  选编号（q=退出）: ").strip()
        if choice.lower() in ('q', 'quit', ''):
            return None
        idx = int(choice) - 1
        if idx < 0 or idx >= len(all_items):
            print(f"  [错误] 编号超出范围（1~{len(all_items)}）")
            return None
        return all_items[idx][0]
    except (ValueError, EOFError):
        print("  [错误] 请输入数字编号")
        return None


def do_import_quota():
    """导入定额库流程"""
    province = select_province_for_import()
    if not province:
        return

    print()
    print("  ============================================")
    print(f"  省份: {province}")
    print(f"  操作: 导入定额 + 重建索引")
    print("  ============================================")
    print()
    print("  注意: 相同专业的旧数据会被替换，不同专业不受影响")
    print()

    try:
        confirm = input("  确认开始导入? [Y/n]: ").strip()
    except EOFError:
        return

    if confirm.lower() == 'n':
        return

    print()
    print("  ============================================")
    print("  开始导入...")
    print("  ============================================")
    print()

    # 调用导入脚本
    result = subprocess.run(
        [sys.executable, "tools/import_all.py", "--province", province],
        cwd=str(PROJECT_ROOT)
    )

    print()
    if result.returncode == 0:
        print("  ============================================")
        print("  导入完成!")
        print("  ============================================")
    else:
        print("  ============================================")
        print("  [错误] 导入过程出现问题")
        print("  ============================================")
    print()


def do_import_rules():
    """导入定额规则流程"""
    print()
    print("  ============================================")
    print("    导入定额规则")
    print("  ============================================")
    print()
    print("  使用说明：")
    print("  1. 在 knowledge/定额规则/ 下按省份建文件夹")
    print("  2. 把定额说明文本文件(.txt)放到对应省份文件夹中")
    print("  3. 运行此工具自动导入并构建索引")
    print()
    print("  文件夹结构示例：")
    print("    knowledge/定额规则/北京2024/安装定额说明.txt")
    print("    knowledge/定额规则/北京2024/给排水定额说明.txt")
    print("    knowledge/定额规则/山东2024/安装定额说明.txt")
    print()
    print("  ============================================")
    print()

    rules_dir = PROJECT_ROOT / "knowledge" / "定额规则"
    if not rules_dir.exists():
        print("  [提示] knowledge/定额规则/ 目录不存在，正在创建...")
        rules_dir.mkdir(parents=True)
        print()
        print("  已创建目录，请按以下步骤操作：")
        print("    1. 在 knowledge/定额规则/ 下新建省份文件夹")
        print("    2. 把定额说明文本放进去（.txt格式）")
        print("    3. 再次运行此工具")
        print()
        return

    # 导入
    subprocess.run(
        [sys.executable, "src/rule_knowledge.py", "import"],
        cwd=str(PROJECT_ROOT)
    )
    print()
    # 统计
    subprocess.run(
        [sys.executable, "src/rule_knowledge.py", "stats"],
        cwd=str(PROJECT_ROOT)
    )
    print()


def main():
    while True:
        show_main_menu()
        try:
            choice = input("  请选择: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == '1':
            do_import_quota()
            input("  按回车返回菜单...")
        elif choice == '2':
            do_import_rules()
            input("  按回车返回菜单...")
        elif choice.lower() in ('q', 'quit'):
            break
        else:
            print()
            print("  [错误] 请输入 1-2 或 q")
            import time
            time.sleep(1.5)

    print()
    print("  再见!")
    print()


if __name__ == "__main__":
    main()
