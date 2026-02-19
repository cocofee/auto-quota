# -*- coding: utf-8 -*-
"""
贾维斯纠正工具 - 将审核纠正直接写回匹配结果Excel

用法：
    python tools/jarvis_correct.py <匹配结果Excel> <纠正JSON>

纠正JSON格式：
[
  {"seq": 25, "quota_id": "C4-8-234", "quota_name": "户内干包式电缆终端头 70mm²"},
  {"seq": 35, "quota_id": "C4-11-287", "quota_name": "管内穿铜芯线动力线路 截面2.5"}
]

输出：在原文件旁边生成 xxx_已审核.xlsx
"""
import sys
import os
import json
import argparse
from pathlib import Path

def correct_excel(excel_path: str, corrections: list, output_path: str = None) -> str:
    """将纠正写回匹配结果Excel

    参数:
        excel_path: 匹配结果Excel路径
        corrections: 纠正列表，每项包含 seq(序号)、quota_id(定额编号)、quota_name(定额名称)
        output_path: 输出路径，默认在原文件名后加"_已审核"

    返回:
        输出文件路径
    """
    import openpyxl

    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"文件不存在: {excel_path}")

    wb = openpyxl.load_workbook(excel_path)
    ws = wb.active

    # 建立序号→行号的映射
    # 结构：清单项行（col1有序号） + 下一行是定额行（col2有定额编号如C4-xxx）
    seq_to_quota_row = {}
    for row in range(1, ws.max_row + 1):
        seq_val = ws.cell(row=row, column=1).value
        if seq_val is None:
            continue
        try:
            seq = int(seq_val)
        except (ValueError, TypeError):
            continue

        # 这行是清单项，下一行应该是定额行
        quota_row = row + 1
        if quota_row <= ws.max_row:
            seq_to_quota_row[seq] = quota_row

    if not seq_to_quota_row:
        raise RuntimeError("Excel中未找到有效的序号行，请检查文件格式")

    # 应用纠正
    applied = 0
    skipped = []
    for corr in corrections:
        seq = corr.get("seq")
        quota_id = corr.get("quota_id", "")
        quota_name = corr.get("quota_name", "")

        if seq is None:
            skipped.append(f"缺少seq字段: {corr}")
            continue

        try:
            seq_int = int(seq)
        except (ValueError, TypeError):
            skipped.append(f"seq不是有效数字: {seq}")
            continue

        quota_row = seq_to_quota_row.get(seq_int)
        if quota_row is None:
            skipped.append(f"序号{seq}在Excel中未找到")
            continue

        # 更新定额编号（B列=col2）和定额名称（C列=col3）
        old_id = ws.cell(row=quota_row, column=2).value or ""
        old_name = ws.cell(row=quota_row, column=3).value or ""

        ws.cell(row=quota_row, column=2).value = quota_id
        ws.cell(row=quota_row, column=3).value = quota_name

        # 更新清单行的推荐度和匹配说明（上一行）
        item_row = quota_row - 1
        ws.cell(row=item_row, column=10).value = "★★★已审核"
        old_reason = ws.cell(row=item_row, column=11).value or ""
        ws.cell(row=item_row, column=11).value = (
            f"Jarvis纠正: {old_id} → {quota_id}"
        )

        applied += 1

    # 生成输出路径
    if not output_path:
        p = Path(excel_path)
        output_path = str(p.parent / f"{p.stem}_已审核{p.suffix}")

    wb.save(output_path)

    print(f"纠正完成: 共{len(corrections)}条，成功{applied}条")
    if skipped:
        print(f"跳过{len(skipped)}条:")
        for s in skipped:
            print(f"  - {s}")
    print(f"输出文件: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="贾维斯纠正工具")
    parser.add_argument("excel_path", help="匹配结果Excel路径")
    parser.add_argument("corrections_json", help="纠正JSON文件路径")
    parser.add_argument("--output", help="输出文件路径（默认在原文件名后加_已审核）")
    args = parser.parse_args()

    with open(args.corrections_json, "r", encoding="utf-8") as f:
        corrections = json.load(f)

    if not isinstance(corrections, list):
        print("错误: JSON必须是数组格式")
        sys.exit(1)

    correct_excel(args.excel_path, corrections, args.output)


if __name__ == "__main__":
    main()
