# -*- coding: utf-8 -*-
"""
Jarvis 学习回收工具 — 从广联达修正版Excel自动学习

用法：
    python tools/jarvis_learn.py "原始输出.xlsx" "广联达修正版.xlsx"
    python tools/jarvis_learn.py "原始输出.xlsx" "广联达修正版.xlsx" --province "北京2024"
    python tools/jarvis_learn.py "原始输出.xlsx" "广联达修正版.xlsx" --all-authority

或直接用"学习回收.bat"拖拽两个文件。

工作原理：
    1. 读取Jarvis原始输出Excel和用户在广联达修正后的Excel
    2. 按行序号逐条对比定额编号
    3. 改了的 = 用户纠正 → 写入经验库权威层（source=user_correction）
    4. 没改的 = 默认存候选层（source=auto_review），加 --all-authority 存权威层
    5. 输出学习报告

层级策略说明：
    默认模式：用户每天跑200条，只改明显离谱的，没改的不代表确认过
      → 改过的存权威层（可直通匹配），没改过的存候选层（仅供参考）
    --all-authority 模式：用户逐条检查确认过所有结果
      → 全部存权威层
"""

import sys
import os
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def learn(original_path: str, corrected_path: str,
          province: str = None, all_authority: bool = False) -> dict:
    """对比原始输出和修正版，自动学习差异

    参数:
        original_path: Jarvis输出的原始Excel
        corrected_path: 用户在广联达中修正后导出的Excel
        province: 省份名称（默认使用配置中的省份）
        all_authority: 是否全部存权威层（用户确认过全部结果时使用）

    返回:
        学习统计 {"total", "confirmed", "corrected", "skipped", "details"}
    """
    from src.diff_learner import DiffLearner

    learner = DiffLearner()
    result = learner.diff_and_learn(original_path, corrected_path,
                                    province=province,
                                    all_authority=all_authority)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis 学习回收：对比原始输出和广联达修正版，自动学习",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/jarvis_learn.py "匹配结果_xxx.xlsx" "广联达修正版.xlsx"
  python tools/jarvis_learn.py "匹配结果_xxx.xlsx" "修正版.xlsx" --province "北京2024"
""",
    )
    parser.add_argument("original", help="Jarvis输出的原始Excel路径")
    parser.add_argument("corrected", help="广联达修正后导出的Excel路径")
    parser.add_argument("--province", default=None,
                        help="省份名称（默认使用配置中的省份）")
    # 层级控制：默认"改过的→权威层，没改过的→候选层"
    # 加 --all-authority 后全部进权威层（适合用户逐条检查确认过的场景）
    parser.add_argument("--all-authority", action="store_true",
                        help="全部存权威层（用户已逐条确认时使用）")

    args = parser.parse_args()

    # 检查文件存在
    for fp, label in [(args.original, "原始文件"), (args.corrected, "修正文件")]:
        if not os.path.exists(fp):
            print(f"错误：{label}不存在 - {fp}")
            sys.exit(1)

    # 省份解析
    province = args.province
    if province:
        from config import resolve_province
        try:
            province = resolve_province(province)
        except Exception as e:
            print(f"错误：省份解析失败 - {e}")
            sys.exit(1)

    # 执行学习
    print("=" * 60)
    print("Jarvis 学习回收")
    print("=" * 60)
    print(f"  原始文件: {args.original}")
    print(f"  修正文件: {args.corrected}")
    if province:
        print(f"  省份: {province}")
    # 告知用户当前的层级策略
    if args.all_authority:
        print(f"  层级策略: 全部存权威层（--all-authority）")
    else:
        print(f"  层级策略: 改过的→权威层，没改过的→候选层")
    print()

    result = learn(args.original, args.corrected, province=province,
                   all_authority=args.all_authority)

    # 输出报告
    total = result["total"]
    confirmed = result["confirmed"]
    corrected = result["corrected"]
    skipped = result["skipped"]

    print()
    print("=" * 60)
    print("学习完成")
    print("=" * 60)
    print(f"  清单总数:   {total}")
    print(f"  确认正确:   {confirmed} 条 ({confirmed * 100 // max(total, 1)}%)")
    print(f"  用户纠正:   {corrected} 条 ({corrected * 100 // max(total, 1)}%)")
    print(f"  跳过:       {skipped} 条")
    print()

    if result["details"]:
        print("修正详情:")
        for d in result["details"]:
            print(f"  {d['bill_name'][:40]}")
            print(f"    原始: {', '.join(d['original_quotas'])}")
            print(f"    修正: {', '.join(d['corrected_quotas'])}")
        print()

    if corrected > 0:
        print(f"已学习 {corrected} 条纠正，下次匹配同样的清单会更准确。")
    elif confirmed > 0:
        print(f"已确认 {confirmed} 条匹配结果，系统信心值已提升。")
    else:
        print("没有可学习的内容。")

    print("=" * 60)


if __name__ == "__main__":
    main()
