# -*- coding: utf-8 -*-
"""
从造价HOME XML数据中挖掘同义词缺口

原理：
  真实项目的 清单名称→定额名称 就是天然的"同义词对"。
  拿这些对去和现有同义词表比对，找出还没覆盖的。

用法：
    python tools/mine_synonyms_from_xml.py <xml路径>
    python tools/mine_synonyms_from_xml.py <xml路径> --province "四川省2015序列定额"
    python tools/mine_synonyms_from_xml.py <xml路径> --test   # 用Jarvis跑匹配测试
"""
import json
import os
import re
import sys
from collections import defaultdict

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools.parse_zaojia_xml import parse_file


def strip_params(name):
    """去掉定额名称中的参数后缀，提取家族名
    例如：'镀锌钢管敷设 砖、混凝土结构暗配 公称直径（DN） ≤40' → '镀锌钢管敷设'
    """
    # 去掉方括号内容（四川的系数标注）
    name = re.sub(r'\s*\[.*?\]', '', name)
    # 去掉 "公称直径..." 及之后的参数
    name = re.sub(r'\s*(?:公称(?:直径|外径)|电缆截面|导线截面|截面|外径|管径|规格|功率|容量|额定电流|高度|宽度|面积|长度|重量)\s*[（(].*$', '', name)
    # 去掉末尾 ≤xxx 数字
    name = re.sub(r'\s*[≤≥<>]\s*\d+.*$', '', name)
    # 去掉末尾纯数字
    name = re.sub(r'\s+\d+(\.\d+)?$', '', name)
    return name.strip()


def extract_bill_core(bill_name):
    """从清单名称提取核心关键词
    例如：'配管 SC40' → '配管'
          '电力电缆 YJV22-0.6/1KV-5X16' → '电力电缆'
          '4米庭院灯' → '庭院灯'
    """
    # 去掉型号规格（大写字母+数字的组合）
    core = re.sub(r'\s*[A-Z][A-Z0-9/.*×xX+-]+\S*', '', bill_name)
    # 去掉 SC/JDG/KBG + 数字
    core = re.sub(r'\s*(?:SC|JDG|KBG|RC|PC|DN|Dg)\s*\d+\s*', '', core, flags=re.IGNORECASE)
    # 去掉前面的数字+单位
    core = re.sub(r'^\d+(?:米|m|mm|kw|kva|A)?\s*', '', core, flags=re.IGNORECASE)
    # 去掉 DN100mm 这种
    core = re.sub(r'\s*DN?\d+(?:mm)?\s*', '', core, flags=re.IGNORECASE)
    # 去掉 -0.6/1KV- 这种电压规格
    core = re.sub(r'\s*-?\d+\.?\d*/\d+[kK][vV]-?', '', core)
    # 去掉 4X120+1X70 这种截面规格
    core = re.sub(r'\s*\d+[xX×]\d+(\+\d+[xX×]\d+)*', '', core)
    return core.strip()


def load_synonyms():
    """加载现有同义词表"""
    syn_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'engineering_synonyms.json')
    with open(syn_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('_')}


def main():
    import argparse
    parser = argparse.ArgumentParser(description='从造价HOME XML挖掘同义词缺口')
    parser.add_argument('xml_path', help='XML文件路径')
    parser.add_argument('--province', type=str, default='', help='省份定额库名称（用于BM25验证）')
    parser.add_argument('--test', action='store_true', help='用Jarvis跑匹配测试')
    parser.add_argument('--specialty', type=str, default='AZ', help='筛选专业（默认AZ安装）')
    args = parser.parse_args()

    # 解析XML
    print(f"解析 {args.xml_path} ...")
    data, err = parse_file(args.xml_path)
    if err:
        print(f"解析错误: {err}")
        return

    print(f"项目: {data.get('project_name', '未知')}")
    print(f"格式: {data.get('format', '未知')}")
    print(f"总对数: {len(data['pairs'])}")

    # 筛选指定专业
    az_pairs = []
    for p in data['pairs']:
        quotas = p['quotas']
        az_quotas = [q for q in quotas if args.specialty in q.get('specialty', '')]
        if az_quotas:
            az_pairs.append({
                'bill_name': p['bill']['name'],
                'bill_feature': p['bill'].get('feature', ''),
                'quotas': az_quotas
            })

    print(f"安装类对数: {len(az_pairs)}")

    # 加载现有同义词
    existing_syn = load_synonyms()
    existing_keys = set(existing_syn.keys())
    existing_targets = set()
    for v in existing_syn.values():
        if isinstance(v, list):
            existing_targets.update(v)

    # 提取 清单核心词→定额家族名 映射
    bill_to_quota_family = defaultdict(lambda: defaultdict(int))  # {清单核心: {定额家族: 出现次数}}
    raw_mappings = []  # 保存原始映射用于输出

    for pair in az_pairs:
        bill_core = extract_bill_core(pair['bill_name'])
        if not bill_core or len(bill_core) < 2:
            continue
        for q in pair['quotas']:
            quota_family = strip_params(q['name'])
            if not quota_family:
                continue
            bill_to_quota_family[bill_core][quota_family] += 1
            raw_mappings.append({
                'bill_name': pair['bill_name'],
                'bill_core': bill_core,
                'quota_code': q['code'],
                'quota_name': q['name'],
                'quota_family': quota_family,
            })

    # 分析结果
    print(f"\n{'='*80}")
    print(f"清单核心词 → 定额家族名 映射（{len(bill_to_quota_family)} 个唯一清单词）")
    print(f"{'='*80}\n")

    # 分类：已覆盖 vs 未覆盖
    covered = []    # 同义词表已有
    uncovered = []  # 同义词表缺失
    self_match = [] # 清单词≈定额家族名，不需要同义词

    for bill_core, families in sorted(bill_to_quota_family.items(), key=lambda x: -sum(x[1].values())):
        # 取出现次数最多的定额家族
        top_family = max(families, key=families.get)
        count = families[top_family]

        # 检查是否已有同义词覆盖
        if bill_core in existing_keys:
            covered.append((bill_core, top_family, count, '同义词表已有'))
        elif bill_core in top_family or top_family in bill_core:
            # 清单词本身就是定额名的子串，BM25自然能搜到
            self_match.append((bill_core, top_family, count, '自然匹配'))
        else:
            uncovered.append((bill_core, top_family, count, '缺失'))

    # 输出结果
    print(f"  已有同义词覆盖: {len(covered)} 个")
    print(f"  自然匹配（不需要同义词）: {len(self_match)} 个")
    print(f"  ⚠️ 缺失同义词: {len(uncovered)} 个")
    print()

    if uncovered:
        print(f"{'清单核心词':20s} | {'定额家族名':35s} | 次数 | 状态")
        print(f"{'-'*20}-+-{'-'*35}-+------+------")
        for bill_core, top_family, count, status in sorted(uncovered, key=lambda x: -x[2]):
            print(f"{bill_core:20s} | {top_family:35s} | {count:4d} | {status}")

    if self_match:
        print(f"\n自然匹配（BM25能直接搜到）:")
        for bill_core, top_family, count, status in sorted(self_match, key=lambda x: -x[2])[:20]:
            print(f"  {bill_core:20s} → {top_family:35s} ({count}次)")

    if covered:
        print(f"\n已有同义词覆盖:")
        for bill_core, top_family, count, status in sorted(covered, key=lambda x: -x[2]):
            target = existing_syn.get(bill_core, ['?'])[0]
            match_mark = '✅' if target == top_family or top_family in target or target in top_family else '⚠️'
            print(f"  {match_mark} {bill_core:20s} → 同义词:{target:25s} | 四川实际:{top_family}")

    # 保存详细报告
    report = {
        'source': args.xml_path,
        'total_pairs': len(az_pairs),
        'unique_bill_cores': len(bill_to_quota_family),
        'covered': len(covered),
        'self_match': len(self_match),
        'uncovered': len(uncovered),
        'uncovered_details': [
            {'bill_core': b, 'quota_family': f, 'count': c}
            for b, f, c, _ in uncovered
        ],
        'covered_details': [
            {'bill_core': b, 'quota_family': f, 'existing_target': existing_syn.get(b, ['?'])[0]}
            for b, f, c, _ in covered
        ],
        'raw_mappings': raw_mappings[:200],  # 只保存前200条原始映射
    }

    report_path = os.path.join(os.path.dirname(__file__), '..', 'output', 'temp', 'synonym_mining_report.json')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存到: {report_path}")


if __name__ == '__main__':
    main()
