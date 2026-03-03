# -*- coding: utf-8 -*-
"""分析203条synonym_gap错误的品类/省份分布，找出高频缺口"""
import json
import re
from collections import Counter, defaultdict

with open('tests/cross_province_tests/_latest_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 收集synonym_gap案例（算法名和正确名几乎无关键词重叠）
gaps = []
for r in data['results']:
    prov = r['province']
    for d in r['details']:
        if not d['is_match']:
            stored_kw = set((d['stored_names'][0] if d['stored_names'] else '').replace('(', ' ').replace(')', ' ').split())
            algo_kw = set(d['algo_name'].replace('(', ' ').replace(')', ' ').split())
            ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内'}
            stored_kw -= ignore
            algo_kw -= ignore
            overlap = stored_kw & algo_kw
            
            # 判断根因
            if len(overlap) > 0:
                continue  # wrong_tier，跳过
            
            # 判断wrong_book
            def get_book(qid):
                m = re.match(r'(C?\d+)-', qid)
                return m.group(1) if m else ''
            sb = get_book(d['stored_ids'][0] if d['stored_ids'] else '')
            ab = get_book(d['algo_id'])
            if sb and ab and sb != ab:
                continue  # wrong_book，跳过
            
            gaps.append({
                'province': prov[:12],
                'bill': d['bill_name'],
                'bill_text': d.get('bill_text', d['bill_name']),
                'algo': d['algo_name'],
                'stored': d['stored_names'][0] if d['stored_names'] else '?',
                'algo_id': d['algo_id'],
                'stored_id': d['stored_ids'][0] if d['stored_ids'] else '?',
            })

print(f"=== synonym_gap分析（{len(gaps)}条） ===\n")

# 按省份统计
prov_count = Counter(e['province'] for e in gaps)
print("=== 按省份 ===")
for p, c in prov_count.most_common():
    print(f"  {p:15s}: {c:3d}条")

# 尝试自动分类品类（从清单名/正确定额名提取关键词）
def classify_category(bill, stored):
    """根据清单名和正确定额名判断品类"""
    text = f"{bill} {stored}"
    categories = [
        ('灯具', ['灯', '灯具', '照明', '吸顶灯', '筒灯', '射灯', '壁灯', '路灯', '应急灯', '标志灯']),
        ('电缆', ['电缆', 'YJV', 'YJY', 'BV', 'WDZN', 'WDZB', 'RVS', 'RVVP', 'NH-']),
        ('配管', ['配管', 'SC', 'JDG', 'KBG', 'PVC', '穿线管', '线管', '钢管敷设']),
        ('配线', ['配线', '穿线', 'BV线', '导线', '照明线', '动力线']),
        ('管道', ['管道', '钢管', '铸铁管', '塑料管', '给水管', '排水管', '消防管', 'PPR', 'PE管', 'PVC管']),
        ('阀门', ['阀门', '阀', '蝶阀', '球阀', '闸阀', '截止阀', '止回阀', '减压阀']),
        ('风管', ['风管', '通风管', '风道', '保温风管']),
        ('风口', ['风口', '散流器', '百叶', '回风口', '送风口']),
        ('风阀', ['风阀', '防火阀', '排烟阀', '调节阀', '止回阀']),
        ('配电箱', ['配电箱', '配电柜', '动力箱', '照明箱', '控制箱']),
        ('开关插座', ['开关', '插座', '面板']),
        ('桥架', ['桥架', '线槽', '走线架']),
        ('消防', ['喷头', '消火栓', '报警', '探测器', '消防', '灭火器']),
        ('水泵', ['水泵', '泵', '潜水泵', '离心泵']),
        ('卫生器具', ['卫生', '便器', '洗脸盆', '洗手盆', '浴缸', '水龙头', '地漏']),
        ('接地防雷', ['接地', '防雷', '避雷', '接地母线', '接地极']),
        ('套管', ['套管', '穿墙套管', '柔性套管', '防水套管']),
        ('支架', ['支架', '支吊架', '托架', '吊架', '管卡', '管夹']),
        ('保温', ['保温', '绝热', '岩棉', '橡塑', '聚氨酯']),
        ('弱电', ['弱电', '网线', '双绞线', '光纤', '信息', '监控', '门禁', '对讲']),
        ('房建装饰', ['抹灰', '涂料', '乳胶漆', '贴砖', '吊顶', '天棚', '地面', '墙面', '砌块', '砌体',
                   '混凝土', '钢筋', '模板', '脚手架', '土方', '垫层', '找平', '防水层']),
    ]
    for cat, keywords in categories:
        if any(kw in text for kw in keywords):
            return cat
    return '其他'

# 分类
cat_count = Counter()
cat_examples = defaultdict(list)
for e in gaps:
    cat = classify_category(e['bill'], e['stored'])
    e['category'] = cat
    cat_count[cat] += 1
    cat_examples[cat].append(e)

print()
print("=== 按品类 ===")
for cat, count in cat_count.most_common():
    pct = count / len(gaps) * 100
    print(f"  {cat:10s}: {count:3d}条 ({pct:4.1f}%)")

# 打印每个品类的前5条案例
print()
print("=" * 100)
print("=== 各品类详细案例（找同义词缺口） ===")
for cat, count in cat_count.most_common():
    print(f"\n--- {cat} ({count}条) ---")
    for ex in cat_examples[cat][:5]:
        print(f"  {ex['province']:12s} | 清单: {ex['bill'][:30]}")
        print(f"  {'':12s} | 算法: {ex['algo'][:55]}")
        print(f"  {'':12s} | 正确: {ex['stored'][:55]}")
        # 提取潜在同义词关系
        bill_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ex['bill']))
        stored_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', ex['stored']))
        # 清单里有但定额里没有的关键词 → 可能需要同义词映射
        bill_only = bill_words - stored_words
        if bill_only:
            print(f"  {'':12s} | 清单独有词: {bill_only}")
        print()
