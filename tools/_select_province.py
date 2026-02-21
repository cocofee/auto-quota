"""
定额库选择脚本（供bat文件调用）

流程：先选省份 → 再选定额库（支持多选）
1. 显示所有省份（地区），用户选一个
2. 显示该省份下所有定额库，用户多选（第一个为主定额，其余为辅助定额）
3. 写入临时文件供bat读取
"""

import sys
import re
from pathlib import Path
from collections import OrderedDict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db.sqlite import connect as _db_connect
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
        conn = _db_connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM quotas").fetchone()[0]
        conn.close()
        return f"{count}条"
    except Exception:
        return "已存在"


def main(allow_new=False):
    """
    定额库选择主流程

    参数:
        allow_new: 是否允许选择未导入的库（导入场景True，匹配场景False）
    """
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
    # 第2步：选定额库（支持多选，第一个为主定额）
    # ============================================================
    items = region_groups[selected_region]  # [(名称, 数量)]

    if len(items) == 1:
        # 该省份只有1个定额库，自动选择（匹配场景要求必须已导入）
        if not allow_new and items[0][1] == "首次导入":
            print(f"  [错误] {items[0][0]} 尚未导入数据，请先运行导入")
            sys.exit(1)
        selected_main = items[0][0]
        selected_aux = []
        print(f"  {selected_region}只有1个定额库，自动选择: {selected_main}")
    else:
        print()
        print(f"  {selected_region} 的定额库:")
        print()
        for i, (name, info) in enumerate(items, 1):
            print(f"    [{i}] {name}  ({info})")
        print()
        # 匹配场景下，只有已导入的库才能选（"首次导入"的还没数据，选了也搜不到）
        imported_indices = [i for i, (_, info) in enumerate(items) if info != "首次导入"]
        try:
            choice = input("  选定额库编号（多选用逗号分隔）: ").strip()
            # 解析用户输入的编号列表
            selected_indices = []
            for part in choice.split(","):
                part = part.strip()
                if part:
                    idx = int(part) - 1
                    if idx < 0 or idx >= len(items):
                        print(f"  [错误] 编号 {part} 超出范围（1~{len(items)}）")
                        sys.exit(1)
                    if not allow_new and idx not in imported_indices:
                        print(f"  [错误] [{part}] {items[idx][0]} 尚未导入数据，无法用于匹配")
                        sys.exit(1)
                    if idx not in selected_indices:
                        selected_indices.append(idx)
            if not selected_indices:
                print("  [错误] 至少选择1个定额库")
                sys.exit(1)
        except (ValueError, EOFError):
            print("  [错误] 请输入数字编号")
            sys.exit(1)

        # 第一个为主定额，其余为辅助定额
        selected_main = items[selected_indices[0]][0]
        selected_aux = [items[i][0] for i in selected_indices[1:]]

    # 显示选择结果
    print()
    if selected_aux:
        print("  已选定额:")
        print(f"    · {selected_main}")
        for p in selected_aux:
            print(f"    · {p}")
    else:
        print(f"  已选定额: {selected_main}")

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
    # --allow-new 参数：导入场景允许选未导入的库
    allow_new = "--allow-new" in sys.argv
    main(allow_new=allow_new)
