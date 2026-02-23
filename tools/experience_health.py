# -*- coding: utf-8 -*-
"""
经验库体检工具 — 用当前审核规则回扫权威层数据，降级错误条目

核心思想：
  审核规则会不断增强（新增材质规则、连接方式规则等），
  但已进入权威层的旧数据没有被重新检查过。
  这个工具用最新的审核规则回扫所有权威层数据，
  发现不通过的条目自动降级为候选层（不删除，降级即可）。

用法:
  python tools/experience_health.py                    # 扫描全部，只报告不修改
  python tools/experience_health.py --fix              # 扫描并自动降级问题条目
  python tools/experience_health.py --province 北京    # 只扫描指定省份
  python tools/experience_health.py --limit 100        # 只扫描前100条（调试）
"""

import argparse
import sys
import io

from src.experience_db import ExperienceDB
from src.review_checkers import (
    check_category_mismatch,
    check_material_mismatch,
    check_connection_mismatch,
    check_pipe_usage,
    check_parameter_deviation,
    check_sleeve_mismatch,
    check_electric_pair,
    check_elevator_type,
    check_elevator_floor,
    extract_description_lines,
)


def _check_record(record: dict) -> dict | None:
    """
    用审核规则检查一条经验库记录。

    参数:
        record: 经验库记录字典（含 bill_text, bill_name, quota_ids, quota_names）

    返回:
        审核错误字典（有问题时），None 表示通过
    """
    quota_names = record.get("quota_names", [])
    quota_ids = record.get("quota_ids", [])
    if not quota_names or not quota_ids:
        return None

    quota_name = quota_names[0]
    quota_id = quota_ids[0]
    if not quota_name:
        return None

    # 构造 item 字典（模拟清单项，review_checkers 需要这个格式）
    bill_name = record.get("bill_name", "")
    bill_text = record.get("bill_text", "")

    # 从 bill_text 中分离名称和描述
    # bill_text 的格式通常是 "名称 描述"，但精确分割困难
    # 用 bill_name 作为名称，bill_text 去掉 bill_name 的部分作为描述
    desc = bill_text
    if bill_name and bill_text.startswith(bill_name):
        desc = bill_text[len(bill_name):].strip()

    item = {"name": bill_name or bill_text[:30], "description": desc}
    desc_lines = extract_description_lines(desc)

    # 运行所有审核检查器
    error = (
        check_category_mismatch(item, quota_name, desc_lines)
        or check_sleeve_mismatch(item, quota_name, desc_lines)
        or check_material_mismatch(item, quota_name, desc_lines)
        or check_connection_mismatch(item, quota_name, desc_lines)
        or check_pipe_usage(item, quota_name, desc_lines)
        or check_parameter_deviation(item, quota_name, desc_lines)
        or check_electric_pair(item, quota_name, desc_lines)
        or check_elevator_type(item, quota_name, desc_lines)
        or check_elevator_floor(item, quota_name, desc_lines, quota_id=quota_id)
    )

    return error


def run_health_check(province: str = None, limit: int = 0,
                     fix: bool = False):
    """
    执行经验库体检。

    参数:
        province: 只检查指定省份（模糊匹配）
        limit: 只检查前N条（调试用）
        fix: 是否自动降级问题条目
    """
    db = ExperienceDB()
    stats = db.get_stats()

    print("=" * 70)
    print("经验库体检报告")
    print("=" * 70)
    print(f"  权威层总数: {stats['authority']}")
    print(f"  候选层总数: {stats['candidate']}")
    print(f"  检查模式: {'自动修复' if fix else '仅报告'}")
    if province:
        print(f"  筛选省份: {province}")
    print("-" * 70)

    # 获取权威层数据
    records = db.get_authority_records(province=province, limit=limit)
    if not records:
        print("没有找到权威层记录。")
        return

    print(f"待检查: {len(records)} 条\n")

    # 逐条检查
    problems = []
    checked = 0
    for record in records:
        checked += 1
        error = _check_record(record)
        if error:
            problems.append((record, error))
            status = "[问题]"
            bill_short = (record.get("bill_name") or record["bill_text"][:30])
            quota_short = record["quota_names"][0][:20] if record["quota_names"] else "?"
            print(f"  {status} #{record['id']} {bill_short[:25]:<25} "
                  f"→ {quota_short:<20} | {error['type']}: {error['reason'][:40]}")

    # 汇总
    print("\n" + "=" * 70)
    print(f"检查完成: {checked} 条已检查, {len(problems)} 条有问题")

    if not problems:
        print("所有权威层数据审核通过，经验库健康。")
        return

    problem_rate = len(problems) * 100 // max(checked, 1)
    print(f"问题率: {problem_rate}%")

    # 按错误类型统计
    type_counts = {}
    for _, error in problems:
        t = error.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    print("\n按错误类型分布:")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t}: {cnt} 条")

    # 自动修复
    if fix:
        print(f"\n开始降级 {len(problems)} 条问题记录...")
        demoted = 0
        for record, error in problems:
            reason = f"{error['type']}: {error.get('reason', '')[:80]}"
            try:
                db.demote_to_candidate(record["id"], reason=reason)
                demoted += 1
            except Exception as e:
                print(f"  降级失败 #{record['id']}: {e}")

        print(f"降级完成: {demoted}/{len(problems)} 条已降级为候选层")
        print("这些条目不再参与直通匹配，需要用户重新确认后才能恢复。")
    else:
        print(f"\n要自动降级这些问题记录，请加 --fix 参数:")
        print(f"  python tools/experience_health.py --fix")


def main():
    parser = argparse.ArgumentParser(description="经验库体检工具")
    parser.add_argument("--fix", action="store_true",
                        help="自动降级问题条目（不加则只报告）")
    parser.add_argument("--province", type=str, default=None,
                        help="只检查指定省份")
    parser.add_argument("--limit", type=int, default=0,
                        help="只检查前N条（调试用）")
    args = parser.parse_args()

    run_health_check(
        province=args.province,
        limit=args.limit,
        fix=args.fix,
    )


if __name__ == "__main__":
    # Windows终端编码兼容：避免GBK编码错误
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
