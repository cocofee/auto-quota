# -*- coding: utf-8 -*-
"""
修复跨省试卷元数据 — 补齐 bill_name 和 specialty

问题：
  - 重庆50题的 bill_name 全为空（数据存储时丢失了）
  - 多个省份的 specialty 字段为空（搜索无法定位专业册）

修复逻辑：
  1. bill_name 为空时，从 bill_text 开头提取（取第一个标签前的文字）
  2. specialty 为空时，从 quota_ids 的编号前缀推断专业册号

用法：
  python tools/fix_cross_tests.py           # 预览修复内容
  python tools/fix_cross_tests.py --apply   # 实际写入文件
"""

import os
import sys
import json
import re

# 试卷目录
TEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'tests', 'cross_province_tests')


def extract_bill_name(bill_text: str) -> str:
    """从 bill_text 开头提取清单名称

    重庆的数据格式通常是：
      "组合式全空气机组 规格型号:风量:36000m³/h..."
      "70℃防火阀 FD 名称:70℃防火阀 规格型号:常开,1800*630"
      "卧式暗装风机盘管 规格型号:风量:680m³/h..."

    策略：
      1. 优先：如果有"名称:"标签，取其值（最可靠）
      2. 其次：取 bill_text 开头到第一个标签之前，只取第一个中文词组
    """
    if not bill_text:
        return ""

    text = bill_text.strip()

    # 策略1：有"名称:"标签时，取标签后的值（和benchmark脚本fallback一致）
    name_label_match = re.search(r'名称[：:]\s*([^\n,，]+)', text)
    if name_label_match:
        name_val = name_label_match.group(1).strip()
        # 截断到下一个标签（如"规格型号:"）
        next_label = re.search(r'\s+(?:规格型号|规格|型号|材质|材料|敷设|安装|配置|甲供)[：:]', name_val)
        if next_label:
            name_val = name_val[:next_label.start()].strip()
        if name_val and len(name_val) <= 25:
            return name_val

    # 策略2：取开头到第一个标签前的部分
    label_pattern = r'(?:名称|规格型号|规格|型号|材质|材料|敷设方式|安装方式|配置形式|甲供材)[：:]'
    m = re.search(label_pattern, text)
    if m:
        name_part = text[:m.start()].strip()
    else:
        # 没找到标签，取第一行的前30个字符
        first_line = text.split('\n')[0].strip()
        name_part = first_line[:30]

    # 只取第一个中文词组（去掉型号/编号等英文噪音）
    # 如 "70℃防火阀 FD" → "70℃防火阀"
    name_part = name_part.strip()
    if name_part:
        # 按空格拆分，取到第一个纯英文/数字词之前
        parts = name_part.split()
        clean_parts = []
        for p in parts:
            # 如果是纯英文字母（型号标识如FD/SC/YJV），停止
            if re.match(r'^[A-Za-z]+$', p):
                break
            clean_parts.append(p)
        name_part = ' '.join(clean_parts) if clean_parts else parts[0]

    # 长度保护
    if len(name_part) > 25:
        parts = name_part.split()
        result = ""
        for p in parts:
            if len(result) + len(p) + 1 > 25:
                break
            result = f"{result} {p}" if result else p
        name_part = result or name_part[:25]

    return name_part


def infer_specialty(quota_ids: list) -> str:
    """从定额编号前缀推断专业册号

    编号格式举例：
      C4-8-23  → C4（电气）
      C10-1-10 → C10（给排水）
      CG0337   → CG（重庆格式，无法精确分册）
      CA0101   → CA（字母编码格式）

    返回：如 "C4", "C10", "CG" 等
    """
    if not quota_ids:
        return ""

    qid = quota_ids[0]

    # 格式1：C数字-数字-数字（如 C4-8-23, C10-1-10）
    m = re.match(r'(C\d+)-', qid)
    if m:
        return m.group(1)

    # 格式2：数字-数字-数字（如 4-8-23）→ 加C前缀
    m = re.match(r'(\d+)-', qid)
    if m:
        return f"C{m.group(1)}"

    # 格式3：C+字母+数字（如 CG0337, CA0101）
    # 这种格式是省级自编码（如重庆CG），无法精确对应标准册号
    # 返回空，让搜索走全库模式（比错误的册号路由更好）
    m = re.match(r'(C[A-Z])', qid)
    if m:
        return ""  # 不强行猜，避免错误路由

    return ""


def fix_test_set(fpath: str, apply: bool = False) -> dict:
    """修复单个试卷文件，返回修复统计"""
    with open(fpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    stats = {
        'total': len(data.get('items', [])),
        'name_fixed': 0,
        'specialty_fixed': 0,
        'name_examples': [],  # 修复样例
    }

    for item in data.get('items', []):
        # 修复 bill_name
        if not item.get('bill_name'):
            extracted = extract_bill_name(item.get('bill_text', ''))
            if extracted:
                if len(stats['name_examples']) < 3:
                    stats['name_examples'].append(
                        f"  {item.get('bill_text', '')[:40]}... → \"{extracted}\"")
                item['bill_name'] = extracted
                stats['name_fixed'] += 1

        # 修复 specialty
        if not item.get('specialty'):
            inferred = infer_specialty(item.get('quota_ids', []))
            if inferred:
                item['specialty'] = inferred
                stats['specialty_fixed'] += 1

    if apply:
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return stats


def main():
    import argparse
    ap = argparse.ArgumentParser(description='修复跨省试卷元数据')
    ap.add_argument('--apply', action='store_true', help='实际写入文件（不加则只预览）')
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding='utf-8')

    mode = "写入" if args.apply else "预览"
    print(f"跨省试卷元数据修复（{mode}模式）\n")

    total_name_fixed = 0
    total_spec_fixed = 0

    for fname in sorted(os.listdir(TEST_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue

        fpath = os.path.join(TEST_DIR, fname)
        stats = fix_test_set(fpath, apply=args.apply)

        prov = fname[:14]
        print(f"{prov}: {stats['total']}题, "
              f"名称修复={stats['name_fixed']}, 专业修复={stats['specialty_fixed']}")

        for ex in stats['name_examples']:
            print(ex)

        total_name_fixed += stats['name_fixed']
        total_spec_fixed += stats['specialty_fixed']

    print(f"\n总计: 名称修复={total_name_fixed}, 专业修复={total_spec_fixed}")
    if not args.apply:
        print("（预览模式，加 --apply 参数写入文件）")


if __name__ == '__main__':
    main()
