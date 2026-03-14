"""
同名多义分析工具：找出清单库中同一个名称出现在多个专业的情况。

用途：
1. 统计同名多义名称的数量和分布
2. 找出高频冲突的专业对（如K/J都有"钢管"）
3. 为上下文消歧提供数据基础

用法：python tools/analyze_homonyms.py
"""

import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

# 添加项目根目录到path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bill_code_matcher import (
    _load_bill_library_index, _extract_core_name,
    CODE_PREFIX_TO_APPENDIX, MAJOR_CATEGORY
)


def analyze():
    """分析同名多义情况。"""
    index = _load_bill_library_index()

    # 统计：有多少名称只对应1个专业，多少对应多个
    single = []      # 只在一个专业出现
    multi = []       # 在多个专业出现（同名多义）
    multi_major = [] # 跨大类（如安装+房建），更严重

    for name, candidates in index.items():
        # 收集这个名称涉及的专业大类和附录
        majors = set(c["major"] for c in candidates)
        appendixes = set(c["appendix"] for c in candidates if c["appendix"])

        if len(majors) > 1:
            # 跨大类同名多义（安装+房建等）
            multi_major.append((name, candidates))
            multi.append((name, candidates))
        elif len(appendixes) > 1:
            # 同大类内跨附录（如安装内 K和J 都有）
            multi.append((name, candidates))
        else:
            single.append(name)

    print("=" * 70)
    print("清单库同名多义分析")
    print("=" * 70)
    print(f"\n总名称数:       {len(index)}")
    print(f"单义名称:       {len(single)}  ({len(single)*100//len(index)}%)")
    print(f"同名多义(总):   {len(multi)}   ({len(multi)*100//len(index)}%)")
    print(f"  其中跨大类:   {len(multi_major)} (如安装+房建)")
    print(f"  同大类跨附录: {len(multi) - len(multi_major)} (如安装内K和J)")

    # 统计专业对冲突频次
    print("\n" + "-" * 70)
    print("专业对冲突排行（同一名称在这两个专业都出现）")
    print("-" * 70)

    pair_counter = Counter()
    pair_examples = defaultdict(list)

    for name, candidates in multi:
        # 获取所有专业标签
        labels = set()
        for c in candidates:
            if c["appendix"]:
                labels.add(c["appendix"])
            else:
                labels.add(MAJOR_CATEGORY.get(c["major"], c["major"]))

        labels = sorted(labels)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                pair = (labels[i], labels[j])
                pair_counter[pair] += 1
                if len(pair_examples[pair]) < 5:
                    pair_examples[pair].append(name)

    # 附录字母→中文名
    APPENDIX_NAMES = {
        "A": "机械设备", "B": "热力设备", "C": "静置设备",
        "D": "电气", "E": "智能化", "F": "仪表",
        "G": "通风空调", "H": "工业管道", "J": "消防",
        "K": "给排水", "L": "通信", "M": "刷油防腐",
        "N": "措施", "P": "安装附属",
    }
    for k, v in MAJOR_CATEGORY.items():
        APPENDIX_NAMES[v] = v

    for pair, count in pair_counter.most_common(30):
        a_name = APPENDIX_NAMES.get(pair[0], pair[0])
        b_name = APPENDIX_NAMES.get(pair[1], pair[1])
        examples = "、".join(pair_examples[pair][:5])
        print(f"  {pair[0]}({a_name}) vs {pair[1]}({b_name}): "
              f"{count}个名称  例: {examples}")

    # 列出所有跨大类的同名多义（最需要消歧的）
    print("\n" + "-" * 70)
    print("跨大类同名多义详细列表（前50个）")
    print("-" * 70)

    # 按候选数量降序
    multi_major.sort(key=lambda x: -len(x[1]))
    for name, candidates in multi_major[:50]:
        parts = []
        for c in candidates:
            major_name = MAJOR_CATEGORY.get(c["major"], c["major"])
            app = c["appendix"]
            label = f"{major_name}"
            if app:
                app_name = APPENDIX_NAMES.get(app, app)
                label += f"-{app}({app_name})"
            parts.append(f"{label}[{c['count']}次]")
        print(f"  {name}: {' / '.join(parts)}")

    # 列出安装内跨附录的高频冲突
    print("\n" + "-" * 70)
    print("安装内跨附录同名多义（前50个，按冲突程度排序）")
    print("-" * 70)

    install_multi = [(n, c) for n, c in multi if
                     all(cc["major"] == "03" for cc in c)]
    install_multi.sort(key=lambda x: -len(x[1]))

    for name, candidates in install_multi[:50]:
        parts = []
        for c in candidates:
            app = c["appendix"]
            app_name = APPENDIX_NAMES.get(app, app)
            parts.append(f"{app}({app_name})[{c['count']}次]")
        print(f"  {name}: {' / '.join(parts)}")

    # 输出给benchmark试卷分析用的数据
    print("\n" + "-" * 70)
    print("同名多义名称集合（供消歧规则使用）")
    print("-" * 70)

    # 构建 name → [可能的专业] 映射
    ambig_map = {}
    for name, candidates in multi:
        labels = []
        for c in candidates:
            if c["appendix"]:
                labels.append(c["appendix"])
            else:
                labels.append(c["major"])
        ambig_map[name] = sorted(set(labels))

    print(f"  共 {len(ambig_map)} 个多义名称")
    print(f"  可导出为 data/homonym_names.json 供消歧模块使用")

    # 保存多义名称映射
    output_path = ROOT / "data" / "homonym_names.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ambig_map, f, ensure_ascii=False, indent=2)
    print(f"  已保存到: {output_path}")

    return ambig_map


def analyze_benchmark_impact(ambig_map: dict):
    """分析同名多义对benchmark试卷路由的影响。

    看看路由错的题里，有多少是因为同名多义导致的。
    """
    papers_dir = ROOT / "tests" / "benchmark_papers"
    if not papers_dir.exists():
        print("\n⚠️ benchmark试卷目录不存在，跳过影响分析")
        return

    total = 0
    ambig_related = 0  # 名称是多义词的题目
    ambig_examples = []

    for paper_file in sorted(papers_dir.glob("*.json")):
        with open(paper_file, "r", encoding="utf-8") as f:
            paper = json.load(f)

        province = paper.get("province", paper_file.stem)
        for item in paper.get("items", []):
            total += 1
            bill_name = item.get("bill_name", "")
            core = _extract_core_name(bill_name)

            if core in ambig_map:
                ambig_related += 1
                if len(ambig_examples) < 20:
                    expected = item.get("specialty", "")
                    ambig_examples.append({
                        "name": bill_name,
                        "core": core,
                        "expected": expected,
                        "possible": ambig_map[core],
                        "province": province[:6],
                    })

    print("\n" + "=" * 70)
    print("同名多义对Benchmark的影响分析")
    print("=" * 70)
    print(f"\n总题目数:         {total}")
    print(f"涉及多义名称:     {ambig_related} ({ambig_related*100//max(total,1)}%)")
    print(f"不涉及多义名称:   {total - ambig_related}")

    if ambig_examples:
        print(f"\n示例（前{len(ambig_examples)}条）:")
        for ex in ambig_examples:
            print(f"  [{ex['province']}] {ex['name']}")
            print(f"    核心名: {ex['core']}, 期望: {ex['expected']}, "
                  f"可能: {','.join(ex['possible'])}")


if __name__ == "__main__":
    ambig_map = analyze()
    analyze_benchmark_impact(ambig_map)
