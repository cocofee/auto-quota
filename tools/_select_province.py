#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
兼容旧版 .bat 的定额库选择脚本。

交互流程：
1. 先选省份/大类
2. 若该大类下有城市分组，再选城市
3. 再选具体定额库
4. 自动推断辅助定额库，并写入 .bat 读取的临时文件
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

MAIN_OUTPUT = PROJECT_ROOT / ".tmp_selected_province.txt"
AUX_OUTPUT = PROJECT_ROOT / ".tmp_selected_aux_provinces.txt"
NO_SUBGROUP = "__NO_SUBGROUP__"


def _cleanup_outputs() -> None:
    for path in (MAIN_OUTPUT, AUX_OUTPUT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _load_candidates(allow_new: bool) -> list[str]:
    if allow_new:
        provinces = config.list_all_provinces()
        if provinces:
            return provinces
    return config.list_db_provinces()


def _pick_default(provinces: list[str]) -> str:
    current = getattr(config, "CURRENT_PROVINCE", "")
    if current in provinces:
        return current
    return provinces[0]


def _build_group_tree(provinces: list[str]) -> dict[str, dict[str, list[str]]]:
    groups = config.get_province_groups()
    subgroups = config.get_province_subgroups()
    tree: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for province in provinces:
        group = groups.get(province, province[:2])
        subgroup = subgroups.get(province, NO_SUBGROUP)
        tree[group][subgroup].append(province)

    normalized: dict[str, dict[str, list[str]]] = {}
    for group, subgroup_map in sorted(tree.items(), key=lambda item: item[0]):
        normalized[group] = {}
        for subgroup, items in sorted(subgroup_map.items(), key=lambda item: item[0]):
            normalized[group][subgroup] = sorted(items)
    return normalized


def _print_header(title: str, allow_new: bool) -> None:
    print()
    print("============================================================")
    print(f"  {title}")
    print("============================================================")
    if allow_new:
        print("  范围: 已导入库 + 可新建导入库")
    else:
        print("  范围: 已构建定额库")
    print()


def _prompt_choice(count: int, allow_default: bool = False, allow_back: bool = False) -> str | None:
    hints = ["q=取消"]
    if allow_back:
        hints.insert(0, "b=返回上一级")
    if allow_default:
        hints.insert(0, "直接回车=默认")
    print(f"  {'，'.join(hints)}")

    while True:
        try:
            raw = input(f"\n请输入编号 [1-{count}]: ").strip()
        except EOFError:
            return None

        if not raw:
            return "" if allow_default else None
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        if allow_back and raw.lower() in {"b", "back"}:
            return "__BACK__"
        if raw.isdigit() and 1 <= int(raw) <= count:
            return raw
        print(f"  [错误] 请输入 1-{count} 的编号。")


def _select_group(tree: dict[str, dict[str, list[str]]], default_group: str, allow_new: bool) -> str | None:
    groups = list(tree)
    _print_header("先选择省份", allow_new)
    for idx, group in enumerate(groups, start=1):
        province_count = sum(len(items) for items in tree[group].values())
        default_mark = "  [默认]" if group == default_group else ""
        print(f"  [{idx:>2}] {group}  ({province_count}个定额){default_mark}")
    print()

    choice = _prompt_choice(len(groups), allow_default=True)
    if choice is None:
        return None
    if choice == "":
        return default_group
    return groups[int(choice) - 1]


def _select_subgroup(
    group: str,
    subgroup_map: dict[str, list[str]],
    default_subgroup: str,
    allow_new: bool,
) -> str | None:
    labels = [label for label in subgroup_map if label != NO_SUBGROUP]
    if not labels:
        return NO_SUBGROUP

    _print_header(f"再选择城市: {group}", allow_new)
    for idx, label in enumerate(labels, start=1):
        province_count = len(subgroup_map[label])
        default_mark = "  [默认]" if label == default_subgroup else ""
        print(f"  [{idx:>2}] {label}  ({province_count}个定额){default_mark}")
    print()

    choice = _prompt_choice(len(labels), allow_default=(default_subgroup in labels), allow_back=True)
    if choice is None:
        return None
    if choice == "__BACK__":
        return "__BACK__"
    if choice == "":
        return default_subgroup
    return labels[int(choice) - 1]


def _select_province(
    title: str,
    provinces: list[str],
    default_province: str,
    allow_new: bool,
) -> str | None:
    _print_header(title, allow_new)
    for idx, province in enumerate(provinces, start=1):
        default_mark = "  [默认]" if province == default_province else ""
        print(f"  [{idx:>2}] {province}{default_mark}")
    print()

    choice = _prompt_choice(len(provinces), allow_default=(default_province in provinces), allow_back=True)
    if choice is None:
        return None
    if choice == "__BACK__":
        return "__BACK__"
    if choice == "":
        return default_province
    return provinces[int(choice) - 1]


def _interactive_select(provinces: list[str], allow_new: bool) -> str | None:
    default_province = _pick_default(provinces)
    tree = _build_group_tree(provinces)

    default_group = next(
        (group for group, subgroup_map in tree.items() if any(default_province in items for items in subgroup_map.values())),
        next(iter(tree)),
    )
    default_subgroup = next(
        (
            subgroup
            for subgroup, items in tree[default_group].items()
            if default_province in items
        ),
        NO_SUBGROUP,
    )

    current_group = default_group
    current_subgroup = default_subgroup

    while True:
        selected_group = _select_group(tree, current_group, allow_new)
        if not selected_group:
            return None
        current_group = selected_group

        subgroup_map = tree[selected_group]
        selected_subgroup = _select_subgroup(selected_group, subgroup_map, current_subgroup, allow_new)
        if selected_subgroup is None:
            return None
        if selected_subgroup == "__BACK__":
            continue
        current_subgroup = selected_subgroup

        province_title = f"最后选择定额库: {selected_group}"
        if selected_subgroup != NO_SUBGROUP:
            province_title = f"最后选择定额库: {selected_group}/{selected_subgroup}"
        selected_province = _select_province(
            province_title,
            subgroup_map[selected_subgroup],
            default_province,
            allow_new,
        )
        if selected_province is None:
            return None
        if selected_province == "__BACK__":
            continue
        return selected_province


def _write_outputs(main_province: str, aux_provinces: list[str]) -> None:
    MAIN_OUTPUT.write_text(main_province, encoding="utf-8")
    if aux_provinces:
        AUX_OUTPUT.write_text(",".join(aux_provinces), encoding="utf-8")
    else:
        try:
            AUX_OUTPUT.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="交互式选择主定额库并生成 bat 临时文件")
    parser.add_argument(
        "--allow-new",
        action="store_true",
        help="允许选择尚未构建 db、但已经存在 quota_data 源文件的定额库",
    )
    args = parser.parse_args()

    _cleanup_outputs()

    provinces = _load_candidates(args.allow_new)
    if not provinces:
        print()
        if args.allow_new:
            print("  [错误] 未发现可用定额库。")
            print(f"  请检查 {config.QUOTA_DATA_DIR} 是否存在可导入的 Excel。")
        else:
            print("  [错误] 未发现已构建的定额库。")
            print("  请先运行定额安装，或改用允许新建导入的入口。")
        print()
        return 1

    selected = _interactive_select(provinces, args.allow_new)
    if not selected:
        print("\n  已取消。")
        return 1

    aux_provinces = config.get_sibling_provinces(selected)
    _write_outputs(selected, aux_provinces)

    print()
    print(f"  主定额库: {selected}")
    if aux_provinces:
        print(f"  辅助定额库: {', '.join(aux_provinces)}")
    else:
        print("  辅助定额库: 无")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
