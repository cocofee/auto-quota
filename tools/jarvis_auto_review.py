# -*- coding: utf-8 -*-
"""
Jarvis 自动审核脚本 - 三阶段编排层

Phase 1: 检测（纯规则，0 DB查询，毫秒级）
Phase 2: 纠正（统一调度，一次DB连接）
Phase 3: 输出（摘要 + 纠正JSON）

检测规则在 src/review_checkers.py
纠正逻辑在 src/review_correctors.py
本文件只负责编排流程。

用法：
    python tools/jarvis_auto_review.py "output/review/review_xxx.json" --province "北京2024"
"""

import sys
import os
import json
import re
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.sqlite import connect as _db_connect
from config import get_quota_db_path, OUTPUT_DIR, CURRENT_PROVINCE

# 检测器：纯规则判断，不查DB
from src.review_checkers import (
    extract_dn, extract_description_lines,
    check_category_mismatch, check_pipe_usage, check_material_mismatch,
    check_connection_mismatch, check_parameter_deviation, check_measure_item,
    check_elevator_type, check_elevator_floor, check_elevator_completeness,
    check_sleeve_mismatch, check_electric_pair,
)

# 纠正器：查DB搜索正确定额
from src.review_correctors import correct_error


# ============================================================
# Phase 1: 检测（纯规则，不查DB）
# ============================================================

def _detect_phase(results):
    """遍历所有清单项，用规则检测错误

    返回: (detected_errors, measure_items, no_match_items, correct_count)
        - detected_errors: [{seq, bill_item, quota_id, quota_name, error, dn, confidence}, ...]
        - measure_items: [{seq, name, reason}, ...]
        - no_match_items: [{seq, name, reason}, ...]
        - correct_count: 正确条数
    """
    detected_errors = []
    measure_items = []
    no_match_items = []
    correct_count = 0

    for i, r in enumerate(results):
        seq = i + 1
        bill = r.get("bill_item", {})
        bill_name = bill.get("name", "")
        desc = bill.get("description", "")
        desc_lines = extract_description_lines(desc)
        params = bill.get("params", {})
        dn = params.get("dn") or extract_dn(' '.join(desc_lines))
        match_source = r.get("match_source", "")

        # 已跳过的措施项
        if match_source == "skip_measure":
            measure_items.append({"seq": seq, "name": bill_name})
            continue

        quotas = r.get("quotas", [])
        if not quotas:
            no_match_items.append({
                "seq": seq,
                "name": bill_name,
                "reason": "无匹配结果",
            })
            continue

        q = quotas[0] if isinstance(quotas[0], dict) else {}
        quota_id = q.get("quota_id", "")
        quota_name = q.get("name", "")
        confidence = r.get("confidence", 0)

        # 依次检查各类错误（短路：第一个命中即停）
        error = check_measure_item(bill, desc_lines)
        if error:
            measure_items.append({
                "seq": seq,
                "name": bill_name,
                "reason": error["reason"],
            })
            continue

        error = (
            check_category_mismatch(bill, quota_name, desc_lines)
            or check_sleeve_mismatch(bill, quota_name, desc_lines)
            or check_material_mismatch(bill, quota_name, desc_lines)
            or check_connection_mismatch(bill, quota_name, desc_lines)
            or check_pipe_usage(bill, quota_name, desc_lines)
            or check_parameter_deviation(bill, quota_name, desc_lines)
            or check_electric_pair(bill, quota_name, desc_lines)
            or check_elevator_type(bill, quota_name, desc_lines)
            or check_elevator_floor(bill, quota_name, desc_lines, quota_id)
        )

        if error:
            detected_errors.append({
                "seq": seq,
                "bill_item": bill,
                "quota_id": quota_id,
                "quota_name": quota_name,
                "error": error,
                "dn": dn,
                "confidence": confidence,
            })
        else:
            correct_count += 1

    return detected_errors, measure_items, no_match_items, correct_count


# ============================================================
# Phase 2: 纠正（查DB，统一调度）
# ============================================================

def _correct_phase(detected_errors, province, db_conn):
    """对检测到的错误批量搜索纠正定额

    返回: (error_items, manual_items)
        - error_items: 已找到纠正定额的项
        - manual_items: 找不到纠正定额、需人工的项
    """
    error_items = []
    manual_items = []

    for d in detected_errors:
        bill = d["bill_item"]
        desc_lines = extract_description_lines(bill.get("description", ""))

        correction = correct_error(
            bill, d["error"], d["dn"], province, conn=db_conn
        )

        entry = {
            "seq": d["seq"],
            "name": bill.get("name", ""),
            "desc_short": desc_lines[0] if desc_lines else "",
            "dn": d["dn"],
            "current_quota_id": d["quota_id"],
            "current_quota_name": d["quota_name"],
            "error_type": d["error"]["type"],
            "error_reason": d["error"]["reason"],
            "confidence": d["confidence"],
        }

        if correction:
            entry["corrected_quota_id"] = correction[0]
            entry["corrected_quota_name"] = correction[1]
            error_items.append(entry)
        else:
            manual_items.append(entry)

    return error_items, manual_items


# ============================================================
# 三阶段编排入口
# ============================================================

def auto_review(json_path, province=None):
    """自动审核匹配结果（三阶段流水线）

    参数:
        json_path: 审核JSON文件路径
        province: 省份

    返回: (summary_text, auto_corrections, manual_items, measure_items)
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = data.get("results", [])
    total = len(results)

    # Phase 1: 检测（纯规则，不查DB）
    detected, measure_items, no_match_items, correct_count = _detect_phase(results)

    # Phase 2: 纠正（一次DB连接，批量处理）
    db_path = get_quota_db_path(province)
    db_conn = None
    if os.path.exists(db_path):
        db_conn = _db_connect(db_path)

    error_items, manual_from_correction = _correct_phase(detected, province, db_conn)

    if db_conn:
        db_conn.close()

    # 跨项完整性检查（规则9：电梯）
    completeness_reminders = check_elevator_completeness(results)
    completeness_items = []
    for reminder in completeness_reminders:
        completeness_items.append({
            "seq": 0,
            "name": "【跨项提醒】",
            "error_type": reminder["type"],
            "error_reason": reminder["reason"],
            "reason": reminder["reason"],
        })

    manual_items = no_match_items + manual_from_correction + completeness_items

    # Phase 3: 输出
    summary = generate_summary(total, correct_count, error_items,
                               manual_items, measure_items)

    auto_corrections = []
    for item in error_items:
        if "corrected_quota_id" in item:
            auto_corrections.append({
                "seq": item["seq"],
                "quota_id": item["corrected_quota_id"],
                "quota_name": item["corrected_quota_name"],
                "name": item["name"],
            })

    return summary, auto_corrections, manual_items, measure_items


# ============================================================
# 摘要生成
# ============================================================

def generate_summary(total, correct_count, error_items, manual_items, measure_items):
    """生成精简审核摘要（控制在3K字符以内）"""
    lines = []
    lines.append("=== 自动审核报告 ===")
    lines.append(f"总条数: {total} | 正确: {correct_count} | "
                 f"错误: {len(error_items)} | 需人工: {len(manual_items)} | "
                 f"措施项: {len(measure_items)}")
    lines.append("")

    # 错误项（已自动查找纠正定额）
    if error_items:
        lines.append("--- 错误项（已自动查找纠正定额）---")
        for item in error_items:
            desc_info = f"{item['name']}"
            if item.get('desc_short'):
                desc_info += f"({item['desc_short'][:20]})"
            if item.get('dn'):
                desc_info += f" DN{item['dn']}"
            lines.append(f"[{item['seq']}] {desc_info}")
            lines.append(f"  现: {item['current_quota_id']} {item['current_quota_name'][:35]}")
            if item.get('corrected_quota_id'):
                lines.append(f"  纠: {item['corrected_quota_id']} "
                             f"{item['corrected_quota_name'][:35]}")
            lines.append(f"  因: {item['error_reason']}")
        lines.append("")

    # 需人工确认
    if manual_items:
        lines.append("--- 需人工确认 ---")
        for item in manual_items:
            reason = item.get('error_reason', item.get('reason', ''))
            lines.append(f"[{item['seq']}] {item['name']} - {reason}")
        lines.append("")

    # 措施项目
    if measure_items:
        seqs = [str(m['seq']) for m in measure_items]
        lines.append(f"--- 措施项目（不套定额）---")
        lines.append(f"[{', '.join(seqs)}] {measure_items[0]['name']} x{len(measure_items)}条")
        lines.append("")

    return '\n'.join(lines)


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Jarvis 自动审核工具")
    parser.add_argument("json_path", help="审核JSON文件路径")
    parser.add_argument("--province", default=None, help=f"省份（默认{CURRENT_PROVINCE}）")
    args = parser.parse_args()

    json_path = args.json_path
    if not os.path.exists(json_path):
        print(f"错误: 文件不存在: {json_path}")
        sys.exit(1)

    # 运行自动审核
    summary, auto_corrections, manual_items, measure_items = auto_review(
        json_path, args.province
    )

    # 输出摘要到 stdout
    print(summary)

    # 保存纠正JSON
    temp_dir = OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 从文件名推断项目名
    project_name = Path(json_path).stem
    project_name = re.sub(r'^review_', '', project_name)

    if auto_corrections:
        corr_path = temp_dir / f"auto_corrections_{project_name}.json"
        with open(corr_path, 'w', encoding='utf-8') as f:
            json.dump(auto_corrections, f, ensure_ascii=False, indent=2)
        print(f"\n纠正JSON: {corr_path}")

    if manual_items:
        manual_path = temp_dir / f"manual_items_{project_name}.json"
        with open(manual_path, 'w', encoding='utf-8') as f:
            json.dump(manual_items, f, ensure_ascii=False, indent=2)
        print(f"人工审核: {manual_path}")

    # 退出码：有错误返回1，纯措施项返回0
    if auto_corrections or manual_items:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
