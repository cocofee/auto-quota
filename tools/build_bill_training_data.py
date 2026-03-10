"""
从广联达清单库构造Qwen3-Embedding补充训练数据

三层策略：
- Layer1 编码组正对：编码前6位相同的不同清单名互为正对（教模型语义相近性）
- Layer2 编码约束BM25：清单名做query→对应专业定额库BM25搜→只取高分Top1
- Layer3 质量门控：抽检+冲突检测

重要：2013和2024清单严格区分，每条数据标注bill_version

用法：
  python tools/build_bill_training_data.py --step analyze   # 分析数据分布
  python tools/build_bill_training_data.py --step layer1    # 生成Layer1编码组正对
  python tools/build_bill_training_data.py --step layer2    # 生成Layer2 BM25匹配
  python tools/build_bill_training_data.py --step merge     # 合并输出训练集
"""

import json
import sys
import random
from collections import defaultdict
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# GB50500 编码前缀 → 专业名称
SPECIALTY_MAP = {
    '01': '建筑工程', '02': '装饰装修', '03': '安装工程',
    '04': '市政工程', '05': '园林绿化', '06': '矿山工程',
    '07': '构筑物', '08': '城市轨道交通', '09': '爆破工程',
}

# 训练目标专业（这几个专业当前训练数据不足）
TARGET_SPECIALTIES = {'建筑工程', '装饰装修', '市政工程', '园林绿化'}

# 行业专业（编码格式不是标准9位GB50500，需要特殊处理）
INDUSTRY_SPECIALTIES = {'石油石化', '光伏发电'}

# 输出目录
OUTPUT_DIR = ROOT / 'output' / 'temp' / 'bill_training'


def load_bill_library():
    """加载清单库数据"""
    path = ROOT / 'data' / 'bill_library_all.json'
    print(f"加载清单库: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('libraries', data)


def classify_item(code: str) -> str:
    """根据编码前2位判断专业"""
    if len(code) >= 9:
        prefix2 = code[:2]
        return SPECIALTY_MAP.get(prefix2, '未知')
    return '未知'


def get_version(lib_name: str) -> str:
    """从库名判断版本（2024/2013/其他）"""
    if '2024' in lib_name:
        return '2024'
    elif '2013' in lib_name:
        return '2013'
    else:
        return 'other'


def extract_province(lib_name: str) -> str:
    """从库名提取省份"""
    return lib_name.split('|')[0] if '|' in lib_name else '未知'


def collect_bill_items(libs):
    """收集所有2013+2024版清单项，按专业+版本分组

    返回:
        items_by_version: {version: {specialty: {name: {provinces, codes}}}}
        all_names_by_sp: {specialty: [name, ...]}  # 去重后的独立名称列表
    """
    # name -> {specialty, versions: {ver: {provinces, codes}}}
    name_info = defaultdict(lambda: {
        'specialty': '未知',
        'versions': defaultdict(lambda: {'provinces': set(), 'codes': {}})
    })

    for lib_name, lib in libs.items():
        version = get_version(lib_name)
        if version not in ('2024', '2013'):
            continue
        province = extract_province(lib_name)
        for item in lib.get('items', []):
            sp = classify_item(item['code'])
            if sp not in TARGET_SPECIALTIES:
                continue
            name = item['name']
            name_info[name]['specialty'] = sp
            name_info[name]['versions'][version]['provinces'].add(province)
            name_info[name]['versions'][version]['codes'][province] = item['code']

    return name_info


def step_analyze(libs):
    """分析清单数据分布"""
    print("\n" + "=" * 60)
    print("清单库数据分析")
    print("=" * 60)

    name_info = collect_bill_items(libs)

    # 按专业+版本统计
    stats = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'provinces': set()}))
    for name, info in name_info.items():
        sp = info['specialty']
        for ver, vinfo in info['versions'].items():
            stats[sp][ver]['count'] += 1
            stats[sp][ver]['provinces'].update(vinfo['provinces'])

    print(f"\n{'专业':<12} {'版本':<6} {'独立名称':>8} {'省份数':>6}")
    print("-" * 40)
    for sp in sorted(stats.keys(), key=lambda x: sum(s['count'] for s in stats[x].values()), reverse=True):
        for ver in ['2024', '2013']:
            if ver not in stats[sp]:
                continue
            s = stats[sp][ver]
            print(f"{sp:<12} {ver:<6} {s['count']:>8} {len(s['provinces']):>6}")

    # 2024 vs 2013 重叠分析
    print(f"\n2024 vs 2013 清单名称重叠:")
    print(f"{'专业':<12} {'仅2024':>8} {'仅2013':>8} {'两版都有':>8} {'合计':>8}")
    print("-" * 50)
    for sp in TARGET_SPECIALTIES:
        only_24 = only_13 = both = 0
        for name, info in name_info.items():
            if info['specialty'] != sp:
                continue
            has_24 = '2024' in info['versions']
            has_13 = '2013' in info['versions']
            if has_24 and has_13:
                both += 1
            elif has_24:
                only_24 += 1
            elif has_13:
                only_13 += 1
        total = only_24 + only_13 + both
        print(f"{sp:<12} {only_24:>8} {only_13:>8} {both:>8} {total:>8}")

    # 编码组分析（前6位相同 = 相近工序）
    print(f"\n编码组分析（前6位相同的不同名称 = Layer1候选正对）:")
    for ver in ['2024', '2013']:
        code_groups = defaultdict(set)  # (sp, prefix6) -> {names}
        for name, info in name_info.items():
            sp = info['specialty']
            if ver not in info['versions']:
                continue
            for prov, code in info['versions'][ver]['codes'].items():
                if len(code) >= 6:
                    code_groups[(sp, code[:6])].add(name)

        # 只看有>=2个不同名称的组
        multi_name_groups = {k: v for k, v in code_groups.items() if len(v) >= 2}
        pair_count = sum(len(v) * (len(v) - 1) // 2 for v in multi_name_groups.values())
        by_sp = defaultdict(int)
        for (sp, _), names in multi_name_groups.items():
            by_sp[sp] += len(names) * (len(names) - 1) // 2
        print(f"\n  {ver}版:")
        for sp in TARGET_SPECIALTIES:
            print(f"    {sp}: {by_sp.get(sp, 0)}个候选正对")

    return name_info


def step_layer1(libs):
    """生成Layer1训练数据：编码前6位相同的不同清单名互为正对

    核心价值：教模型"挖一般土方"和"挖沟槽土方"在语义上相近
    每条数据标注 bill_version (2024/2013)，严格区分版本
    """
    print("\n" + "=" * 60)
    print("Layer1：编码组正对生成")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    name_info = collect_bill_items(libs)

    # 收集所有名称（用于负样本选取）
    all_names_by_sp = defaultdict(list)
    for name, info in name_info.items():
        all_names_by_sp[info['specialty']].append(name)

    triplets = []

    # 按版本分别生成（2024和2013的编码组独立处理）
    for ver in ['2024', '2013']:
        # 构建编码组：(specialty, code_prefix6) -> [不同的name]
        code_groups = defaultdict(set)
        for name, info in name_info.items():
            sp = info['specialty']
            if ver not in info['versions']:
                continue
            for prov, code in info['versions'][ver]['codes'].items():
                if len(code) >= 6:
                    code_groups[(sp, code[:6])].add(name)

        ver_count = 0
        for (sp, prefix6), names in code_groups.items():
            unique_names = list(names)
            if len(unique_names) < 2:
                continue

            # 同编码前6位下的不同名称互为正对
            # 限制每组最多生成10对（避免大组爆炸）
            pairs = [(unique_names[i], unique_names[j])
                     for i in range(len(unique_names))
                     for j in range(i + 1, len(unique_names))]
            if len(pairs) > 10:
                pairs = random.sample(pairs, 10)

            for name_a, name_b in pairs:
                # 选负样本：来自不同专业的清单名
                neg_specialties = [s for s in TARGET_SPECIALTIES
                                   if s != sp and len(all_names_by_sp[s]) > 0]
                if not neg_specialties:
                    continue
                neg_sp = random.choice(neg_specialties)
                negative = random.choice(all_names_by_sp[neg_sp])

                triplets.append({
                    'query': name_a,
                    'positive': name_b,
                    'negative': negative,
                    'specialty': sp,
                    'bill_version': ver,
                    'code_prefix': prefix6,
                    'source': f'layer1_{ver}_{prefix6}',
                })
                ver_count += 1

        print(f"\n{ver}版: {ver_count}条三元组")

    # 统计
    sp_ver_count = defaultdict(lambda: defaultdict(int))
    for t in triplets:
        sp_ver_count[t['specialty']][t['bill_version']] += 1

    print(f"\n按专业×版本统计:")
    print(f"{'专业':<12} {'2024':>6} {'2013':>6} {'合计':>6}")
    print("-" * 35)
    for sp in TARGET_SPECIALTIES:
        v24 = sp_ver_count[sp].get('2024', 0)
        v13 = sp_ver_count[sp].get('2013', 0)
        print(f"{sp:<12} {v24:>6} {v13:>6} {v24+v13:>6}")
    print(f"{'总计':<12} {sum(sp_ver_count[sp].get('2024',0) for sp in TARGET_SPECIALTIES):>6} "
          f"{sum(sp_ver_count[sp].get('2013',0) for sp in TARGET_SPECIALTIES):>6} "
          f"{len(triplets):>6}")

    # 保存
    output_path = OUTPUT_DIR / 'layer1_triplets.jsonl'
    with open(output_path, 'w', encoding='utf-8') as f:
        for t in triplets:
            f.write(json.dumps(t, ensure_ascii=False) + '\n')
    print(f"\n已保存: {output_path} ({len(triplets)}条)")

    # 保存按版本×专业的独立清单名列表（供Layer2使用）
    for ver in ['2024', '2013']:
        for sp in TARGET_SPECIALTIES:
            names = set()
            for name, info in name_info.items():
                if info['specialty'] == sp and ver in info['versions']:
                    names.add(name)
            if names:
                sp_path = OUTPUT_DIR / f'unique_names_{ver}_{sp}.txt'
                with open(sp_path, 'w', encoding='utf-8') as f:
                    for name in sorted(names):
                        f.write(name + '\n')
                print(f"  {ver}_{sp}: {len(names)}条 → {sp_path.name}")

    # 预览
    print(f"\n样本预览:")
    for sp in TARGET_SPECIALTIES:
        items = [t for t in triplets if t['specialty'] == sp][:3]
        if items:
            print(f"\n  === {sp} ===")
            for t in items:
                print(f"  [{t['bill_version']}] Q: {t['query']}")
                print(f"         P: {t['positive']}")
                print(f"         N: {t['negative'][:30]}...")
                print()

    return triplets


def step_layer2(libs):
    """生成Layer2：编码约束BM25匹配

    用清单名做query，在对应专业的定额库里BM25搜索，
    只取Top1且score较高的作为(清单名→定额名)正对。
    严格按bill_version标注。

    搜索策略：
    - 用多个代表省份的定额库搜索（北京2024为主，广东2024补充）
    - 只取BM25分数>阈值的高置信结果
    - 定额编号必须以对应专业册号开头（编码约束）
    """
    print("\n" + "=" * 60)
    print("Layer2：编码约束BM25匹配")
    print("=" * 60)

    # 加载BM25搜索引擎
    try:
        from src.hybrid_searcher import HybridSearcher
    except ImportError:
        print("错误: 无法导入搜索模块，请确保在项目根目录运行")
        return []

    # 专业→定额册号前缀映射（用于过滤搜索结果）
    SP_TO_BOOKS = {
        '建筑工程': ['A'],
        '装饰装修': ['A'],  # 建筑装饰合册
        '市政工程': ['D'],
        '园林绿化': ['E'],
    }

    # 按专业配置搜索省份（南北东西各选代表，覆盖更多定额命名风格）
    SP_SEARCH_PROVINCES = {
        '建筑工程': [
            '北京市建设工程施工消耗量标准(2024)',
            '广东省房屋建筑与装饰工程综合定额(2018)',
            '湖北省房屋建筑与装饰工程消耗量定额及全费用基价表(2024)',
            '浙江省房屋建筑与装饰工程预算定额(2018)',
            '辽宁省房屋建筑与装饰工程定额(2024)',
            '重庆市房屋建筑与装饰工程计价定额(2018)',
        ],
        '装饰装修': [
            '北京市建设工程施工消耗量标准(2024)',
            '广东省房屋建筑与装饰工程综合定额(2018)',
            '安徽省装饰装修工程计价定额(2018)',
            '山西省装饰工程预算定额(2018)',
            '河北省建设工程消耗量标准(2022)-装饰装修工程',
            '天津市装饰装修工程预算基价(2020)',
            '湖南省房屋建筑与装饰工程消耗量标准(2025)',
        ],
        '市政工程': [
            '北京市建设工程施工消耗量标准(2024)',
            '广东省市政工程综合定额(2018)',
            '湖北省市政工程消耗量定额及全费用基价表(2024)',
            '深圳市市政工程消耗量标准(2024)',
            '浙江省市政工程预算定额(2018)',
            '辽宁省市政工程定额(2024)',
        ],
        '园林绿化': [
            '北京市建设工程施工消耗量标准(2024)',
            '广东省园林绿化工程综合定额(2018)',
            '湖北省园林绿化工程消耗量定额及全费用基价表（2024)',
            '浙江省园林绿化及仿古建筑工程预算定额(2018)',
            '辽宁省园林绿化工程定额(2024)',
            '山东省园林绿化工程消耗量定额(2025)',
        ],
    }

    # 初始化搜索引擎（按专业加载对应省份）
    # 用字典去重，避免同一个省加载多次
    all_provinces = set()
    for provs in SP_SEARCH_PROVINCES.values():
        all_provinces.update(provs)

    searchers = {}  # province_name -> HybridSearcher
    for prov in sorted(all_provinces):
        try:
            searcher = HybridSearcher(province=prov)
            searcher.bm25_engine.ensure_index()
            searchers[prov] = searcher
            doc_count = len(searcher.bm25_engine.doc_ids) if hasattr(searcher.bm25_engine, 'doc_ids') else '?'
            print(f"  已加载: {prov} ({doc_count}条定额)")
        except Exception as e:
            print(f"  加载失败 {prov}: {e}")

    if not searchers:
        print("错误: 没有可用的搜索引擎")
        return []

    # 收集所有目标专业名称（用于负样本）
    name_info = collect_bill_items(libs)
    all_names_by_sp = defaultdict(list)
    for name, info in name_info.items():
        all_names_by_sp[info['specialty']].append(name)

    triplets = []
    # BM25分数阈值（bm25_score字段，>=10表示有较好的词匹配）
    SCORE_THRESHOLD = 10.0

    for ver in ['2024', '2013']:
        for sp in TARGET_SPECIALTIES:
            sp_path = OUTPUT_DIR / f'unique_names_{ver}_{sp}.txt'
            if not sp_path.exists():
                print(f"  跳过 {ver}_{sp}: 未找到名称列表，请先运行 --step layer1")
                continue

            with open(sp_path, 'r', encoding='utf-8') as f:
                names = [line.strip() for line in f if line.strip()]

            target_books = SP_TO_BOOKS.get(sp, [])
            print(f"\n处理 {ver}_{sp}: {len(names)}条, 册号约束={target_books}")

            matched = 0
            skipped = 0
            # 获取该专业对应的搜索省份
            sp_provinces = SP_SEARCH_PROVINCES.get(sp, [])
            sp_searchers = [(p, searchers[p]) for p in sp_provinces if p in searchers]

            if not sp_searchers:
                print(f"  警告: {sp}没有可用的搜索引擎，跳过")
                continue

            for i, name in enumerate(names):
                if i % 500 == 0 and i > 0:
                    print(f"  进度: {i}/{len(names)} matched={matched} skipped={skipped}")

                best_result = None
                best_score = 0

                for prov, searcher in sp_searchers:
                    try:
                        # 纯BM25搜索（快，不加载向量模型）
                        results = searcher.search_bm25_only(query=name, top_k=5)

                        if not results:
                            continue

                        # 在结果中找符合册号约束的最高分
                        for r in results:
                            code = r.get('quota_id', r.get('code', ''))
                            score = r.get('bm25_score', r.get('score', 0))

                            # 编码约束：定额编号必须以目标册号开头
                            if target_books:
                                code_match = any(code.startswith(b) for b in target_books)
                                if not code_match:
                                    continue

                            if score > best_score:
                                best_score = score
                                best_result = r

                    except Exception as e:
                        if i < 3:
                            print(f"  搜索出错 [{prov}][{name}]: {e}")

                # 质量门控
                if best_result and best_score >= SCORE_THRESHOLD:
                    quota_name = best_result.get('name', '')
                    quota_code = best_result.get('quota_id', best_result.get('code', ''))

                    # 选负样本（跨专业）
                    neg_specialties = [s for s in TARGET_SPECIALTIES
                                       if s != sp and len(all_names_by_sp[s]) > 0]
                    negative = ''
                    if neg_specialties:
                        neg_sp = random.choice(neg_specialties)
                        negative = random.choice(all_names_by_sp[neg_sp])

                    triplets.append({
                        'query': name,
                        'positive': quota_name,
                        'negative': negative,
                        'specialty': sp,
                        'bill_version': ver,
                        'score': round(best_score, 2),
                        'quota_code': quota_code,
                        'source': f'layer2_bm25_{ver}_{sp}',
                    })
                    matched += 1
                else:
                    skipped += 1

            print(f"  {ver}_{sp} 完成: matched={matched}, skipped={skipped}")

    # 保存
    if triplets:
        output_path = OUTPUT_DIR / 'layer2_triplets.jsonl'
        with open(output_path, 'w', encoding='utf-8') as f:
            for t in triplets:
                f.write(json.dumps(t, ensure_ascii=False) + '\n')
        print(f"\n已保存: {output_path} ({len(triplets)}条)")

        # 统计
        sp_ver_count = defaultdict(lambda: defaultdict(int))
        for t in triplets:
            sp_ver_count[t['specialty']][t['bill_version']] += 1
        print(f"\nLayer2按专业×版本:")
        print(f"{'专业':<12} {'2024':>6} {'2013':>6} {'合计':>6}")
        print("-" * 35)
        for sp in TARGET_SPECIALTIES:
            v24 = sp_ver_count[sp].get('2024', 0)
            v13 = sp_ver_count[sp].get('2013', 0)
            print(f"{sp:<12} {v24:>6} {v13:>6} {v24+v13:>6}")

    return triplets


def step_industry(libs):
    """生成行业训练数据：石油石化 + 光伏发电

    石油石化：8套清单库 → 2个定额库(石油预算2022 + 石油化工安装2025) BM25搜索
    光伏发电：2套清单库 → 2个定额库(光伏发电2016 + 太阳能热发电2023) BM25搜索
    行业编码不是标准9位GB50500，不做册号约束
    """
    print("\n" + "=" * 60)
    print("行业训练数据：石油石化 + 光伏发电")
    print("=" * 60)

    try:
        from src.hybrid_searcher import HybridSearcher
    except ImportError:
        print("错误: 无法导入搜索模块")
        return []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 行业配置：{行业名: {bill_keywords: 清单库名包含的关键词, quota_provinces: 定额库名}}
    INDUSTRY_CONFIG = {
        '石油石化': {
            'bill_keywords': ['石油', '炼油', '管道建设'],
            'quota_provinces': ['石油预算2022', '石油化工安装工程预算定额（2025)'],
        },
        '光伏发电': {
            'bill_keywords': ['光伏'],
            'quota_provinces': ['光伏发电2016定额', '太阳能热发电序列定额（2023）'],
        },
    }

    # 1. 收集行业清单名称
    industry_names = {}  # {行业: [name, ...]}
    for industry, cfg in INDUSTRY_CONFIG.items():
        names = set()
        for lib_name, lib in libs.items():
            # 检查库名是否属于该行业
            if any(kw in lib_name for kw in cfg['bill_keywords']):
                for item in lib.get('items', []):
                    if item.get('name', '').strip():
                        names.add(item['name'].strip())
        industry_names[industry] = sorted(names)
        print(f"  {industry}: {len(names)}个独立清单名")

    # 2. 加载行业定额库搜索引擎
    industry_searchers = {}  # {行业: [(province_name, searcher), ...]}
    for industry, cfg in INDUSTRY_CONFIG.items():
        searcher_list = []
        for prov in cfg['quota_provinces']:
            try:
                searcher = HybridSearcher(province=prov)
                searcher.bm25_engine.ensure_index()
                doc_count = len(searcher.bm25_engine.doc_ids) if hasattr(searcher.bm25_engine, 'doc_ids') else '?'
                searcher_list.append((prov, searcher))
                print(f"  已加载: {prov} ({doc_count}条定额)")
            except Exception as e:
                print(f"  加载失败 {prov}: {e}")
        industry_searchers[industry] = searcher_list

    # 3. BM25搜索生成(清单→定额)正对
    SCORE_THRESHOLD = 10.0
    triplets = []
    # 收集所有行业清单名（用于跨行业负样本）
    all_industry_names = []
    for names in industry_names.values():
        all_industry_names.extend(names)
    # 也收集民用专业名称做负样本
    name_info = collect_bill_items(libs)
    civil_names = [n for n, info in name_info.items() if info['specialty'] in TARGET_SPECIALTIES]

    for industry, names in industry_names.items():
        if not names:
            continue
        sp_searchers = industry_searchers.get(industry, [])
        if not sp_searchers:
            print(f"  {industry}: 没有可用的定额库搜索引擎，跳过")
            continue

        print(f"\n处理 {industry}: {len(names)}条清单, {len(sp_searchers)}个定额库")
        matched = 0
        skipped = 0

        for i, name in enumerate(names):
            if i % 500 == 0 and i > 0:
                print(f"  进度: {i}/{len(names)} matched={matched} skipped={skipped}")

            best_result = None
            best_score = 0
            best_prov = ''

            for prov, searcher in sp_searchers:
                try:
                    results = searcher.search_bm25_only(query=name, top_k=3)
                    if not results:
                        continue
                    for r in results:
                        score = r.get('bm25_score', r.get('score', 0))
                        if score > best_score:
                            best_score = score
                            best_result = r
                            best_prov = prov
                except Exception as e:
                    if i < 3:
                        print(f"  搜索出错 [{prov}][{name}]: {e}")

            if best_result and best_score >= SCORE_THRESHOLD:
                quota_name = best_result.get('name', '')
                quota_code = best_result.get('quota_id', best_result.get('code', ''))

                # 负样本：从民用专业或其他行业取
                neg_pool = civil_names if civil_names else all_industry_names
                negative = random.choice(neg_pool) if neg_pool else ''

                triplets.append({
                    'query': name,
                    'positive': quota_name,
                    'negative': negative,
                    'specialty': industry,
                    'bill_version': 'industry',
                    'score': round(best_score, 2),
                    'quota_code': quota_code,
                    'source': f'industry_{industry}_{best_prov}',
                })
                matched += 1
            else:
                skipped += 1

        print(f"  {industry} 完成: matched={matched}, skipped={skipped}")

    # 保存
    if triplets:
        output_path = OUTPUT_DIR / 'industry_triplets.jsonl'
        with open(output_path, 'w', encoding='utf-8') as f:
            for t in triplets:
                f.write(json.dumps(t, ensure_ascii=False) + '\n')
        print(f"\n已保存: {output_path} ({len(triplets)}条)")

        # 统计
        sp_count = defaultdict(int)
        for t in triplets:
            sp_count[t['specialty']] += 1
        print(f"\n行业训练数据统计:")
        for sp, cnt in sorted(sp_count.items(), key=lambda x: -x[1]):
            print(f"  {sp}: {cnt}条")

    return triplets


def step_power():
    """生成电力行业训练数据：定额库互搜（无清单库）

    电力有3个定额库(2013/2015/2020)但没有清单库，
    策略：不同系列中BM25高分匹配的定额名互为正对（教模型同类定额的不同叫法）
    """
    print("\n" + "=" * 60)
    print("电力行业训练数据：定额库互搜")
    print("=" * 60)

    try:
        from src.hybrid_searcher import HybridSearcher
    except ImportError:
        print("错误: 无法导入搜索模块")
        return []

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 电力定额库列表
    POWER_PROVINCES = [
        '电力2013序列估价表',
        '电力技改序列估价表（2015）',
        '电力技改序列定额（2020）',
    ]

    import sqlite3

    # 加载搜索引擎 + 从SQLite读取定额名称
    searchers = {}
    all_quota_names = {}  # {province: [name, ...]}
    for prov in POWER_PROVINCES:
        try:
            searcher = HybridSearcher(province=prov)
            searcher.bm25_engine.ensure_index()
            # 从定额数据库直接读取所有独立名称
            db_path = searcher.bm25_engine.db_path
            conn = sqlite3.connect(db_path)
            rows = conn.execute('SELECT DISTINCT name FROM quotas WHERE name IS NOT NULL AND name != ""').fetchall()
            conn.close()
            names = [r[0] for r in rows]
            quota_count = len(searcher.bm25_engine.quota_ids)
            searchers[prov] = searcher
            all_quota_names[prov] = names
            print(f"  已加载: {prov} ({quota_count}条定额, {len(names)}个独立名称)")
        except Exception as e:
            print(f"  加载失败 {prov}: {e}")

    if len(searchers) < 2:
        print("错误: 需要至少2个电力定额库才能互搜")
        return []

    # 选一个主库（最大的）做query源，其他库做搜索目标
    provs = list(searchers.keys())
    # 用第一个库的定额名去搜第二、三个库
    SCORE_THRESHOLD = 10.0
    triplets = []
    seen_pairs = set()

    for i, src_prov in enumerate(provs):
        src_names = all_quota_names.get(src_prov, [])
        if not src_names:
            print(f"  {src_prov}: 无法获取名称列表，跳过")
            continue

        # 去重
        src_names = list(set(src_names))

        for j, tgt_prov in enumerate(provs):
            if i == j:
                continue

            print(f"\n  {src_prov} → {tgt_prov}: {len(src_names)}条待搜")
            tgt_searcher = searchers[tgt_prov]
            matched = 0
            skipped = 0

            for k, name in enumerate(src_names):
                if k % 500 == 0 and k > 0:
                    print(f"    进度: {k}/{len(src_names)} matched={matched} skipped={skipped}")

                try:
                    results = tgt_searcher.search_bm25_only(query=name, top_k=3)
                    if not results:
                        skipped += 1
                        continue

                    best = results[0]
                    score = best.get('bm25_score', best.get('score', 0))
                    tgt_name = best.get('name', '')

                    # 质量门控：分数够高 + 不是完全相同的名称
                    if score >= SCORE_THRESHOLD and tgt_name and tgt_name != name:
                        pair_key = tuple(sorted([name, tgt_name]))
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)

                            # 负样本：随机取一个不同的定额名
                            neg_name = random.choice(src_names)
                            while neg_name == name or neg_name == tgt_name:
                                neg_name = random.choice(src_names)

                            triplets.append({
                                'query': name,
                                'positive': tgt_name,
                                'negative': neg_name,
                                'specialty': '电力',
                                'bill_version': 'industry',
                                'score': round(score, 2),
                                'source': f'power_{src_prov}_to_{tgt_prov}',
                            })
                            matched += 1
                    else:
                        skipped += 1
                except Exception as e:
                    skipped += 1
                    if k < 3:
                        print(f"    搜索出错 [{name}]: {e}")

            print(f"    完成: matched={matched}, skipped={skipped}")

    # 保存
    if triplets:
        output_path = OUTPUT_DIR / 'power_triplets.jsonl'
        with open(output_path, 'w', encoding='utf-8') as f:
            for t in triplets:
                f.write(json.dumps(t, ensure_ascii=False) + '\n')
        print(f"\n已保存: {output_path} ({len(triplets)}条)")

    return triplets


def step_merge():
    print("\n" + "=" * 60)
    print("合并训练数据")
    print("=" * 60)

    all_triplets = []

    # 加载Layer1
    l1_path = OUTPUT_DIR / 'layer1_triplets.jsonl'
    if l1_path.exists():
        with open(l1_path, 'r', encoding='utf-8') as f:
            l1 = [json.loads(line) for line in f if line.strip()]
        print(f"Layer1: {len(l1)}条")
        all_triplets.extend(l1)

    # 加载Layer2
    l2_path = OUTPUT_DIR / 'layer2_triplets.jsonl'
    if l2_path.exists():
        with open(l2_path, 'r', encoding='utf-8') as f:
            l2 = [json.loads(line) for line in f if line.strip()]
        print(f"Layer2: {len(l2)}条")
        all_triplets.extend(l2)

    # 加载行业数据（石油石化+光伏发电）
    ind_path = OUTPUT_DIR / 'industry_triplets.jsonl'
    if ind_path.exists():
        with open(ind_path, 'r', encoding='utf-8') as f:
            ind = [json.loads(line) for line in f if line.strip()]
        print(f"行业(石油+光伏): {len(ind)}条")
        all_triplets.extend(ind)

    # 加载电力互搜数据
    pw_path = OUTPUT_DIR / 'power_triplets.jsonl'
    if pw_path.exists():
        with open(pw_path, 'r', encoding='utf-8') as f:
            pw = [json.loads(line) for line in f if line.strip()]
        print(f"电力互搜: {len(pw)}条")
        all_triplets.extend(pw)

    if not all_triplets:
        print("没有找到训练数据，请先运行 layer1 和/或 layer2")
        return

    # 加载现有训练数据，去重
    existing_path = ROOT / 'data' / 'qwen3_training_triplets.jsonl'
    existing_pairs = set()
    if existing_path.exists():
        with open(existing_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    # 用(query, positive)做去重key
                    existing_pairs.add((item.get('query', ''), item.get('positive', '')))
        print(f"现有训练集: {len(existing_pairs)}条unique (query,positive)对")

    # 去重
    before = len(all_triplets)
    deduped = []
    seen = set()
    for t in all_triplets:
        key = (t['query'], t['positive'])
        if key not in existing_pairs and key not in seen:
            seen.add(key)
            deduped.append(t)
    all_triplets = deduped
    print(f"去重: {before} → {len(all_triplets)} (移除{before - len(all_triplets)}条)")

    # 按专业×版本统计
    sp_ver_count = defaultdict(lambda: defaultdict(int))
    for t in all_triplets:
        sp_ver_count[t['specialty']][t.get('bill_version', '?')] += 1

    # 汇总所有专业（包括行业）
    all_specialties = sorted(sp_ver_count.keys())
    print(f"\n最终训练数据分布:")
    print(f"{'专业':<12} {'2024':>6} {'2013':>6} {'行业':>6} {'合计':>6}")
    print("-" * 45)
    for sp in all_specialties:
        v24 = sp_ver_count[sp].get('2024', 0)
        v13 = sp_ver_count[sp].get('2013', 0)
        v_ind = sp_ver_count[sp].get('industry', 0)
        total_sp = v24 + v13 + v_ind
        print(f"{sp:<12} {v24:>6} {v13:>6} {v_ind:>6} {total_sp:>6}")
    total = len(all_triplets)
    print(f"{'总计':<12} {'':>6} {'':>6} {'':>6} {total:>6}")

    # 保存合并结果
    output_path = OUTPUT_DIR / 'bill_supplement_triplets.jsonl'
    with open(output_path, 'w', encoding='utf-8') as f:
        for t in all_triplets:
            f.write(json.dumps(t, ensure_ascii=False) + '\n')
    print(f"\n已保存: {output_path}")

    # 可读预览
    preview_path = OUTPUT_DIR / 'preview_samples.txt'
    with open(preview_path, 'w', encoding='utf-8') as f:
        for sp in all_specialties:
            sp_items = [t for t in all_triplets if t['specialty'] == sp]
            f.write(f"\n{'=' * 50}\n{sp} (共{len(sp_items)}条，展示前20)\n{'=' * 50}\n")
            for t in sp_items[:20]:
                f.write(f"  [{t.get('bill_version','?')}] Q: {t['query']}\n")
                f.write(f"          P: {t['positive']}\n")
                if t.get('negative'):
                    f.write(f"          N: {t['negative']}\n")
                f.write(f"          来源: {t['source']}\n\n")
    print(f"预览: {preview_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='清单库训练数据构造工具')
    parser.add_argument('--step', choices=['analyze', 'layer1', 'layer2', 'industry', 'power', 'merge', 'all'],
                        default='analyze', help='执行步骤')
    args = parser.parse_args()

    if args.step in ('analyze', 'layer1', 'layer2', 'industry', 'all'):
        libs = load_bill_library()
        print(f"加载完成: {len(libs)}套清单库")

    if args.step == 'analyze':
        step_analyze(libs)
    elif args.step == 'layer1':
        step_layer1(libs)
    elif args.step == 'layer2':
        step_layer2(libs)
    elif args.step == 'industry':
        step_industry(libs)
    elif args.step == 'power':
        step_power()
    elif args.step == 'merge':
        step_merge()
    elif args.step == 'all':
        step_analyze(libs)
        step_layer1(libs)
        print("\n注意: Layer2/industry/power需要搜索引擎，请单独执行")
        step_merge()


if __name__ == '__main__':
    main()
