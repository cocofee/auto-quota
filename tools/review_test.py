# -*- coding: utf-8 -*-
"""
批量审核工具 - 逐文件测试定额匹配质量

使用方式：
    python tools/review_test.py <清单Excel路径> [--batch-size 20]

功能：
    1. 读取清单Excel，运行自动匹配
    2. 把结果按批次（默认20条一批）输出到 output/review/ 目录
    3. 每批一个txt文件，格式清晰，方便人工审核
    4. 同时输出完整JSON，方便后续处理
"""

import sys
import os
import json
import argparse
import re
from pathlib import Path

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_matching(excel_path: str, use_experience: bool = False) -> dict:
    """调用 main.py 的匹配逻辑，返回完整结果

    参数:
        excel_path: 清单Excel路径
        use_experience: 是否使用经验库（默认False=纯搜索测试）
    """
    # 用模拟命令行参数的方式调用
    original_argv = sys.argv
    argv = [
        'main.py',
        excel_path,
        '--mode', 'search',
        '--json-output', '_temp_review.json',
    ]
    if not use_experience:
        argv.append('--no-experience')
    sys.argv = argv
    try:
        from main import main
        main()
    finally:
        sys.argv = original_argv

    # 读取结果JSON
    with open('_temp_review.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    os.remove('_temp_review.json')
    return data


def format_confidence(conf: int) -> str:
    """置信度格式化为星级"""
    if conf >= 85:
        return f"{conf} ★★★推荐"
    elif conf >= 60:
        return f"{conf} ★★参考"
    else:
        return f"{conf} ★待审"


def format_description(desc: str, max_lines: int = 3) -> str:
    """格式化清单描述，提取关键信息"""
    if not desc:
        return "(无描述)"
    # 把换行分隔的描述提取出来
    lines = desc.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    # 去掉空行，最多取max_lines行
    lines = [l.strip() for l in lines if l.strip()][:max_lines]
    return ' | '.join(lines)


def write_batch(results: list, batch_num: int, total_batches: int,
                total_items: int, output_dir: str, project_name: str):
    """写一批审核结果到txt文件"""
    start_idx = (batch_num - 1) * len(results)
    filepath = os.path.join(output_dir, f"review_{project_name}_batch{batch_num}.txt")

    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"  审核批次 {batch_num}/{total_batches}  |  项目: {project_name}")
    lines.append(f"  本批: 第{start_idx+1}-{start_idx+len(results)}条 / 共{total_items}条")
    lines.append(f"{'='*70}")
    lines.append("")

    for i, r in enumerate(results):
        idx = start_idx + i + 1  # 全局序号
        bill = r.get("bill_item", {})
        name = bill.get("name", "???")
        desc = bill.get("description", "")
        unit = bill.get("unit", "")
        qty = bill.get("quantity", "")
        conf = r.get("confidence", 0)
        source = r.get("match_source", "search")

        # 措施项跳过，简洁显示
        if source == "skip_measure":
            lines.append(f"[{idx}/{total_items}] {name}  ⊘ 措施项，不套定额")
            lines.append(f"{'─'*70}")
            continue

        # 定额信息
        quotas = r.get("quotas", [])
        if quotas:
            q = quotas[0]
            quota_id = q.get("quota_id", "")
            quota_name = q.get("name", "")
            quota_unit = q.get("unit", "")
            reason = q.get("reason", "")
        else:
            quota_id = "(未匹配)"
            quota_name = ""
            quota_unit = ""
            reason = r.get("no_match_reason", "")

        # 备选定额
        alts = r.get("alternatives", [])

        # 格式化输出
        conf_str = format_confidence(conf)
        desc_short = format_description(desc)

        lines.append(f"[{idx}/{total_items}] {name}  ({unit} {qty})")
        lines.append(f"  描述: {desc_short}")
        lines.append(f"  定额: {quota_id} {quota_name}")
        if reason:
            lines.append(f"  原因: {reason}")
        lines.append(f"  置信: {conf_str}  来源: {source}")
        # 备选
        if alts:
            for j, alt in enumerate(alts):
                alt_conf = format_confidence(alt.get("confidence", 0))
                lines.append(f"  备选{j+1}: {alt['quota_id']} {alt['name']}  ({alt_conf})")
        lines.append(f"{'─'*70}")

    # 汇总统计（措施项单独统计，不影响绿/黄/红）
    measure = sum(1 for r in results if r.get("match_source") == "skip_measure")
    actual = [r for r in results if r.get("match_source") != "skip_measure"]
    green = sum(1 for r in actual if r.get("confidence", 0) >= 85)
    yellow = sum(1 for r in actual if 60 <= r.get("confidence", 0) < 85)
    red = sum(1 for r in actual if r.get("confidence", 0) < 60)
    stat = f"本批统计: 绿色{green} 黄色{yellow} 红色{red} / 实际{len(actual)}条"
    if measure:
        stat += f" (另有{measure}条措施项跳过)"
    lines.append(stat)
    lines.append("")
    lines.append("审核说明：请检查每条定额是否正确，告诉我哪几条需要修正。")
    lines.append("格式：[序号] 错误原因 或 [序号] 应该是XXX")
    lines.append("")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    return filepath


def main():
    parser = argparse.ArgumentParser(description='批量审核工具')
    parser.add_argument('excel_path', help='清单Excel文件路径')
    parser.add_argument('--batch-size', type=int, default=20, help='每批条数（默认20）')
    parser.add_argument('--with-experience', action='store_true',
                        help='启用经验库（默认关闭，纯搜索测试）')
    args = parser.parse_args()

    excel_path = args.excel_path
    batch_size = args.batch_size

    if not os.path.exists(excel_path):
        print(f"错误：文件不存在 {excel_path}")
        sys.exit(1)

    # 从文件名提取项目名（去掉路径和扩展名，简化）
    project_name = Path(excel_path).stem
    # 简化过长的名称
    if len(project_name) > 30:
        project_name = project_name[:30]
    # 替换不能用在文件名里的字符
    project_name = re.sub(r'[\\/:*?"<>|]', '_', project_name)

    # 输出目录
    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'review')
    os.makedirs(output_dir, exist_ok=True)

    print(f"开始匹配: {excel_path}")
    print(f"项目名: {project_name}")
    print()

    # 运行匹配
    data = run_matching(excel_path, use_experience=args.with_experience)
    results = data.get('results', [])
    total = len(results)

    if total == 0:
        print("没有匹配结果！请检查清单文件格式。")
        sys.exit(1)

    # 统计（措施项单独统计）
    measure = sum(1 for r in results if r.get("match_source") == "skip_measure")
    actual = [r for r in results if r.get("match_source") != "skip_measure"]
    green = sum(1 for r in actual if r.get("confidence", 0) >= 85)
    yellow = sum(1 for r in actual if 60 <= r.get("confidence", 0) < 85)
    red = sum(1 for r in actual if r.get("confidence", 0) < 60)
    stat = f"匹配完成: 共{total}条  绿色{green} 黄色{yellow} 红色{red}"
    if measure:
        stat += f"  (另有{measure}条措施项自动跳过)"
    print(stat)
    print()

    # 分批输出
    total_batches = (total + batch_size - 1) // batch_size
    batch_files = []
    for b in range(total_batches):
        start = b * batch_size
        end = min(start + batch_size, total)
        batch_results = results[start:end]
        filepath = write_batch(batch_results, b + 1, total_batches,
                              total, output_dir, project_name)
        batch_files.append(filepath)
        print(f"  批次{b+1}/{total_batches}: {filepath}")

    # 保存完整JSON（方便后续处理）
    json_path = os.path.join(output_dir, f"review_{project_name}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n完整JSON: {json_path}")

    print(f"\n请从 batch1 开始审核，每批{batch_size}条。")


if __name__ == '__main__':
    main()
