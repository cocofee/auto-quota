"""
省份选择辅助脚本（供bat文件调用）

扫描所有可用省份，让用户选择，将结果写入临时文件。
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config


def main():
    # 列出已构建数据库的省份（匹配需要的是已导入的DB，不是数据文件夹）
    provinces = config.list_db_provinces()

    if not provinces:
        print()
        print("  [提示] 没有找到任何已导入的省份数据库")
        print(f"  数据库目录: {config.PROVINCES_DB_DIR}")
        # 检查是否有数据文件但未导入
        data_provinces = config.list_all_provinces()
        if data_provinces:
            print()
            print(f"  发现以下省份有定额Excel但尚未导入数据库:")
            for p in data_provinces:
                print(f"    · {p}")
            print()
            print(f"  请先运行: python tools/import_all.py --province \"省份名\"")
        else:
            print(f"  请在 {config.QUOTA_DATA_DIR} 下创建省份目录并放入Excel")
        print()
        sys.exit(1)

    # 显示已导入的省份列表
    print()
    print("  可用省份/版本（已导入数据库）:")
    print()
    for i, p in enumerate(provinces, 1):
        # 统计数据库中的定额数量
        db_path = config.get_quota_db_path(p)
        db_info = ""
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path), timeout=5)
                count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
                conn.close()
                db_info = f"{count}条定额"
            except Exception:
                db_info = "数据库已存在"
        print(f"    [{i}] {p}  ({db_info})")

    # 提示有数据但未导入的省份
    data_provinces = config.list_all_provinces()
    not_imported = [p for p in data_provinces if p not in provinces]
    if not_imported:
        print()
        print("  以下省份有Excel数据但尚未导入:")
        for p in not_imported:
            print(f"    · {p}  (需运行 import_all.py)")
    print()

    if len(provinces) == 1:
        selected = provinces[0]
        print(f"  只有1个省份，自动选择: {selected}")
    else:
        try:
            choice = input("  请输入编号: ").strip()
            idx = int(choice) - 1
            if idx < 0 or idx >= len(provinces):
                print(f"  [错误] 编号超出范围（1~{len(provinces)}）")
                sys.exit(1)
            selected = provinces[idx]
        except (ValueError, EOFError):
            print("  [错误] 请输入数字编号")
            sys.exit(1)

    # 显示选中的省份信息
    db_path = config.get_quota_db_path(selected)
    print()
    print(f"  已选择: {selected}")
    print(f"  数据库: {db_path}")
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path), timeout=5)
            count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
            specialties = [row[0] for row in conn.execute(
                "SELECT DISTINCT specialty FROM quotas ORDER BY specialty").fetchall()]
            conn.close()
            print(f"  定额数: {count}条")
            print(f"  专业: {', '.join(specialties)}")
        except Exception:
            pass

    # 写入临时文件供bat读取
    tmp_file = PROJECT_ROOT / ".tmp_selected_province.txt"
    with open(tmp_file, "w", encoding="gbk") as f:
        f.write(selected)


if __name__ == "__main__":
    main()
