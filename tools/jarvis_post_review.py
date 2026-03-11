"""
Jarvis 实战跑后分析工具

功能：分析贾维斯跑完的匹配结果JSON，找出可疑错误并分类原因。
用法：python tools/jarvis_post_review.py <匹配结果JSON> [--top 10]

输出：
  1. 控制台打印最可疑的N条（默认10条）
  2. 错误原因统计（同义词缺口/参数错/排序偏差等）
  3. 追加到 output/temp/daily_error_log.jsonl（供autoresearch消化）
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# 添加项目根目录到path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _extract_dn(text):
    """从文本中提取DN/De值"""
    m = re.search(r'[Dd][NnEe]\s*(\d+)', text)
    return int(m.group(1)) if m else None


def _extract_material(text):
    """提取材质关键词"""
    materials = ['镀锌钢管', '钢塑复合', 'PPR', 'PVC', 'PE', 'UPVC',
                 '铸铁', '不锈钢', '铜管', '碳钢', '衬塑', '钢管']
    for m in materials:
        if m.lower() in text.lower():
            return m
    return None


def _classify_error(item):
    """分类单条匹配结果的可能错误原因

    参数:
        item: 匹配结果字典，包含 bill_item, quotas, confidence, match_source,
              all_candidate_ids, alternatives 等字段

    返回:
        {
            'suspect_level': 'high'/'medium'/'low',  # 可疑程度
            'error_type': str,                        # 错误类型
            'reason': str,                            # 原因说明
            'suggestion': str,                        # 修复建议
        }
    """
    bill = item.get('bill_item', {})
    bill_name = bill.get('name', '')
    bill_desc = bill.get('description', '')
    bill_text = f"{bill_name} {bill_desc}"
    params = bill.get('params', {})

    quotas = item.get('quotas', [])
    confidence = item.get('confidence', 0)
    match_source = item.get('match_source', '')
    alternatives = item.get('alternatives', [])
    candidates_count = item.get('candidates_count', 0)

    if not quotas:
        return {
            'suspect_level': 'high',
            'error_type': '无匹配',
            'reason': f'清单"{bill_name[:20]}"没有找到任何匹配定额',
            'suggestion': '检查清单文本是否太模糊或定额库是否覆盖',
        }

    q = quotas[0]
    quota_id = q.get('quota_id', '')
    quota_name = q.get('name', '')

    # 1. 低置信度 = 系统自己都不确定
    if confidence < 50:
        return {
            'suspect_level': 'high',
            'error_type': '低置信度',
            'reason': f'置信度仅{confidence}%，系统不确定',
            'suggestion': '需要人工确认',
        }

    # 2. 参数不匹配（DN、材质）
    bill_dn = _extract_dn(bill_text)
    quota_dn = _extract_dn(quota_name)
    if bill_dn and quota_dn and bill_dn != quota_dn:
        return {
            'suspect_level': 'high',
            'error_type': '参数不匹配',
            'reason': f'清单DN{bill_dn} ≠ 定额DN{quota_dn}',
            'suggestion': f'应该找DN{bill_dn}的定额',
        }

    bill_mat = _extract_material(bill_text)
    quota_mat = _extract_material(quota_name)
    if bill_mat and quota_mat and bill_mat.lower() != quota_mat.lower():
        return {
            'suspect_level': 'high',
            'error_type': '材质不匹配',
            'reason': f'清单材质"{bill_mat}" ≠ 定额材质"{quota_mat}"',
            'suggestion': f'应该找{bill_mat}相关的定额',
        }

    # 3. 专业大类可能搞错（给排水→电气 等明显跨专业）
    water_keywords = ['给水', '排水', '管道', '阀门', '水表', '消火栓']
    elec_keywords = ['配电', '电缆', '电线', '灯具', '开关', '插座']
    bill_is_water = any(k in bill_text for k in water_keywords)
    bill_is_elec = any(k in bill_text for k in elec_keywords)
    quota_is_water = any(k in quota_name for k in water_keywords)
    quota_is_elec = any(k in quota_name for k in elec_keywords)

    if bill_is_water and quota_is_elec:
        return {
            'suspect_level': 'high',
            'error_type': '专业分类错',
            'reason': f'清单是给排水，但匹配到了电气定额',
            'suggestion': '检查品类路由是否正确',
        }
    if bill_is_elec and quota_is_water:
        return {
            'suspect_level': 'high',
            'error_type': '专业分类错',
            'reason': f'清单是电气，但匹配到了给排水定额',
            'suggestion': '检查品类路由是否正确',
        }

    # 4. 置信度中等 + 有备选方案 = 可能排序偏差
    if 50 <= confidence < 75 and alternatives:
        alt_conf = alternatives[0].get('confidence', 0)
        if alt_conf > 0 and (confidence - alt_conf) < 10:
            return {
                'suspect_level': 'medium',
                'error_type': '排序偏差',
                'reason': f'第1名({confidence}%)和第2名({alt_conf}%)差距小，可能选错',
                'suggestion': f'备选: {alternatives[0].get("quota_id", "")} {alternatives[0].get("name", "")[:20]}',
            }

    # 5. 候选池太少 = 搜索可能偏了
    if candidates_count is not None and candidates_count <= 3 and confidence < 80:
        return {
            'suspect_level': 'medium',
            'error_type': '搜索偏差',
            'reason': f'只找到{candidates_count}个候选，搜索可能不够准',
            'suggestion': '检查搜索词或同义词是否覆盖',
        }

    # 6. 置信度75-85之间 = 轻微可疑
    if 60 <= confidence < 80:
        return {
            'suspect_level': 'low',
            'error_type': '中等置信度',
            'reason': f'置信度{confidence}%，大概率对但不完全确定',
            'suggestion': '',
        }

    # 7. 高置信度 = 大概率没问题
    return None


def _normalize_item(raw):
    """把不同格式的匹配结果统一成标准格式

    支持两种输入格式：
    1. jarvis_pipeline 输出（有 bill_item, quotas, confidence 等）
    2. 批量匹配输出（有 name, matched_quota_id, confidence 等）
    """
    # 已经是标准格式
    if 'bill_item' in raw:
        return raw

    # 批量匹配格式 → 转换
    return {
        'bill_item': {
            'name': raw.get('name', ''),
            'description': raw.get('description', ''),
            'params': {},
        },
        'quotas': [{
            'quota_id': raw.get('matched_quota_id', ''),
            'name': raw.get('matched_quota_name', ''),
        }] if raw.get('matched_quota_id') else [],
        'confidence': raw.get('confidence', 0),
        'match_source': raw.get('match_source', 'search'),
        'alternatives': [],
        'candidates_count': None,
    }


def analyze_results(json_path, top_n=10):
    """分析匹配结果JSON，输出可疑条目和错误统计"""

    with open(json_path, 'r', encoding='utf-8') as f:
        results = json.load(f)

    # 兼容两种格式：直接列表 或 包含results字段的字典
    if isinstance(results, dict):
        items = results.get('results', results.get('items', []))
        province = results.get('province', '')
    else:
        items = results
        province = ''

    suspects = []  # 可疑条目
    error_counts = Counter()  # 错误类型计数
    total = 0
    skipped = 0

    for raw_item in items:
        item = _normalize_item(raw_item)
        match_source = item.get('match_source', '')
        if match_source == 'skip_measure':
            skipped += 1
            continue

        total += 1
        classification = _classify_error(item)
        if classification:
            bill = item.get('bill_item', {})
            quotas = item.get('quotas', [])
            q = quotas[0] if quotas else {}

            suspects.append({
                'seq': bill.get('seq', total),
                'bill_name': bill.get('name', '')[:30],
                'quota_id': q.get('quota_id', ''),
                'quota_name': q.get('name', '')[:30],
                'confidence': item.get('confidence', 0),
                **classification,
            })
            error_counts[classification['error_type']] += 1

    # 按可疑程度排序：high > medium > low
    level_order = {'high': 0, 'medium': 1, 'low': 2}
    suspects.sort(key=lambda x: (level_order.get(x['suspect_level'], 3), x['confidence']))

    # 输出
    print(f"\n{'='*60}")
    print(f"实战跑后分析: {os.path.basename(json_path)}")
    print(f"{'='*60}")
    print(f"总条数: {total}  跳过(措施费): {skipped}")
    print(f"可疑条目: {len(suspects)}（高{sum(1 for s in suspects if s['suspect_level']=='high')} / "
          f"中{sum(1 for s in suspects if s['suspect_level']=='medium')} / "
          f"低{sum(1 for s in suspects if s['suspect_level']=='low')}）")

    if error_counts:
        print(f"\n错误类型分布:")
        for err_type, cnt in error_counts.most_common():
            print(f"  {err_type}: {cnt}条")

    # 只打印top_n条
    print(f"\n最可疑的{min(top_n, len(suspects))}条:")
    print(f"{'-'*60}")
    for i, s in enumerate(suspects[:top_n]):
        level_mark = {'high': '!!!', 'medium': '! ', 'low': '  '}
        mark = level_mark.get(s['suspect_level'], '  ')
        print(f"{mark} #{s['seq']} [{s['error_type']}] 置信度{s['confidence']}%")
        print(f"    清单: {s['bill_name']}")
        print(f"    定额: {s['quota_id']} {s['quota_name']}")
        print(f"    原因: {s['reason']}")
        if s['suggestion']:
            print(f"    建议: {s['suggestion']}")
        print()

    # 追加到日志文件（供autoresearch消化）
    log_dir = Path('output/temp')
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'daily_error_log.jsonl'

    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'source_file': os.path.basename(json_path),
        'province': province,
        'total': total,
        'suspect_count': len(suspects),
        'error_distribution': dict(error_counts),
        'top_suspects': suspects[:top_n],
    }

    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    print(f"分析日志已追加到: {log_file}")

    return suspects, error_counts


def main():
    parser = argparse.ArgumentParser(description='Jarvis实战跑后分析')
    parser.add_argument('json_path', help='匹配结果JSON文件路径')
    parser.add_argument('--top', type=int, default=10, help='显示最可疑的N条（默认10）')
    args = parser.parse_args()

    if not os.path.exists(args.json_path):
        print(f"错误: 文件不存在 {args.json_path}")
        sys.exit(1)

    analyze_results(args.json_path, top_n=args.top)


if __name__ == '__main__':
    main()
