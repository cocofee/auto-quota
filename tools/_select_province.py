"""
定额库选择脚本（供bat文件调用）

流程：先选省份 → 再选该省份下的定额库
1. 显示所有省份（地区），用户选一个
2. 显示该省份下所有定额库，用户选主定额
3. 如果同省份还有其他已导入的定额库，可选辅助定额（用于安装清单中的土建/市政项目）
4. 写入临时文件供bat读取
"""

import sys
import re
import sqlite3
from pathlib import Path
from collections import OrderedDict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config


def _extract_region(name: str) -> str:
    """从定额库名称提取地区（用于分组）"""
    m = re.match(r'^(.{2,4}?)(省|市)', name)
    if m:
        region = m.group(1)
        suffix = m.group(2)
        # "广西市政" → "广西"（"市"属于"市政"，不是地名后缀）
        if suffix == "市" and len(name) > m.end() and name[m.end()] == "政":
            return region
        return region
    return name[:2]


def _get_db_count(province_name: str) -> str:
    """获取定额数量"""
    db_path = config.get_quota_db_path(province_name)
    if not db_path.exists():
        return "首次导入"
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
        conn.close()
        return f"{count}条"
    except Exception:
        return "已存在"


def main():
    db_provinces = config.list_db_provinces()
    data_provinces = config.list_all_provinces()
    not_imported = [p for p in data_provinces if p not in db_provinces]
    all_provinces = db_provinces + not_imported

    if not all_provinces:
        print()
        print("  [提示] 没有找到任何定额库")
        print(f"  请在 {config.QUOTA_DATA_DIR} 下放入定额Excel")
        print()
        sys.exit(1)

    # ---- 按地区分组 ----
    region_groups = OrderedDict()  # {地区: [(名称, 数量)]}
    for p in all_provinces:
        region = _extract_region(p)
        info = _get_db_count(p)
        if region not in region_groups:
            region_groups[region] = []
        region_groups[region].append((p, info))

    regions = list(region_groups.keys())

    # ============================================================
    # 第1步：选省份
    # ============================================================
    if len(regions) == 1:
        # 只有一个省份，自动选择
        selected_region = regions[0]
        print()
        print(f"  只有1个省份，自动选择: {selected_region}")
    else:
        print()
        print("  可用省份:")
        print()
        for i, region in enumerate(regions, 1):
            count = len(region_groups[region])
            print(f"    [{i}] {region}  ({count}个定额库)")
        print()
        try:
            choice = input("  选省份编号: ").strip()
            idx = int(choice) - 1
            if idx < 0 or idx >= len(regions):
                print(f"  [错误] 编号超出范围（1~{len(regions)}）")
                sys.exit(1)
            selected_region = regions[idx]
        except (ValueError, EOFError):
            print("  [错误] 请输入数字编号")
            sys.exit(1)

    # ============================================================
    # 第2步：选该省份下的主定额库
    # ============================================================
    items = region_groups[selected_region]  # [(名称, 数量)]

    if len(items) == 1:
        # 该省份只有1个定额库，自动选择
        selected_main = items[0][0]
        print(f"  {selected_region}只有1个定额库，自动选择: {selected_main}")
    else:
        print()
        print(f"  {selected_region} 的定额库:")
        print()
        for i, (name, info) in enumerate(items, 1):
            print(f"    [{i}] {name}  ({info})")
        print()
        try:
            choice = input("  选主定额库编号: ").strip()
            idx = int(choice) - 1
            if idx < 0 or idx >= len(items):
                print(f"  [错误] 编号超出范围（1~{len(items)}）")
                sys.exit(1)
            selected_main = items[idx][0]
        except (ValueError, EOFError):
            print("  [错误] 请输入数字编号")
            sys.exit(1)

    print()
    print(f"  主定额: {selected_main}")

    # ============================================================
    # 第3步：选辅助定额库（可选，同省份下其他已导入的库）
    # ============================================================
    aux_candidates = []
    for i, (name, info) in enumerate(items, 1):
        if name != selected_main and info != "首次导入":
            aux_candidates.append((i, name, info))

    selected_aux = []
    if aux_candidates:
        print()
        print("  同省份其他定额库（安装清单中有土建/市政项目时选上）:")
        print("  直接回车跳过")
        print()
        for i, name, info in aux_candidates:
            print(f"    [{i}] {name}  ({info})")
        print()
        try:
            aux_input = input("  辅助定额库编号（多选逗号分隔）: ").strip()
            if aux_input:
                for part in aux_input.split(","):
                    part = part.strip()
                    if part:
                        aux_idx = int(part) - 1
                        if 0 <= aux_idx < len(items):
                            aux_name = items[aux_idx][0]
                            if aux_name != selected_main:
                                selected_aux.append(aux_name)
        except (ValueError, EOFError):
            pass

    if selected_aux:
        print()
        print("  已选辅助:")
        for p in selected_aux:
            print(f"    - {p}")

    # ---- 写入临时文件 ----
    tmp_main = PROJECT_ROOT / ".tmp_selected_province.txt"
    with open(tmp_main, "w", encoding="gbk") as f:
        f.write(selected_main)

    tmp_aux = PROJECT_ROOT / ".tmp_selected_aux_provinces.txt"
    if selected_aux:
        with open(tmp_aux, "w", encoding="gbk") as f:
            f.write(",".join(selected_aux))
    else:
        tmp_aux.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
