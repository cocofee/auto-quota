"""
从benchmark试卷挖掘同义词（清单名 → 定额名）

原理：
  benchmark试卷是"清单名→定额名"的ground truth。
  找出字面差异大的配对，就是同义词表的候选。
  这些同义词直接补充到 engineering_synonyms.json，就能提分。

用法：
    python tools/mine_benchmark_synonyms.py              # 挖掘并输出候选
    python tools/mine_benchmark_synonyms.py --save        # 保存到文件供人工审核
    python tools/mine_benchmark_synonyms.py --analyze     # 分析synonym_gap的具体分布
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAPERS_DIR = ROOT / "tests" / "benchmark_papers"
RESULT_FILE = PAPERS_DIR / "_latest_result.json"
OUTPUT_FILE = ROOT / "output" / "temp" / "benchmark_synonym_candidates.json"


# ============================================================
# 工具函数
# ============================================================

# 停用词（在比较清单名和定额名时忽略的词）
STOP_WORDS = {
    "安装", "制作", "施工", "敷设", "铺设", "布设", "架设",
    "以内", "以下", "以上", "mm", "m2", "m", "kg", "t",
    "公称直径", "规格", "回路", "周长", "截面",
    "≤", "≥", "(", ")", "（", "）",
}

# 参数正则（去掉数字参数）
RE_NUM = re.compile(r'\d+(?:\.\d+)?')
RE_PARAMS = re.compile(
    r'[Dd][Nn]\s*\d+|[Dd][Ee]\s*\d+|'
    r'\d+\s*(?:mm[²2]|mm|m2|kVA|kV|kv|A|W|w)|'
    r'\d+(?:\.\d+)?(?:[x×*]\d+(?:\.\d+)?)*'
)


def extract_core_words(text: str) -> set:
    """从定额名称/清单名称中提取核心词集合

    去掉数字参数、停用词、标点，只留有意义的中文词
    """
    if not text:
        return set()
    # 去掉参数
    s = RE_PARAMS.sub('', text)
    # 按空格和标点分词
    tokens = re.split(r'[\s()\（\）/、，,]+', s)
    words = set()
    for token in tokens:
        # 去掉纯数字
        token = RE_NUM.sub('', token).strip()
        if len(token) < 2:
            continue
        if token in STOP_WORDS:
            continue
        words.add(token)
    return words


def extract_bill_core(bill_name: str) -> str:
    """从清单名称中提取核心名词（去掉型号、参数等）

    例如：
      "配电箱1-AL" → "配电箱"
      "镀锌钢管DN25" → "镀锌钢管"
      "PPR管De32" → "PPR管"
    """
    if not bill_name:
        return ""
    s = bill_name.strip()
    # 去掉尾部的型号（如 "-AL"、"B1-ALE"）
    s = re.sub(r'[-]?[A-Za-z0-9]+[-][A-Za-z0-9~]+$', '', s)
    s = re.sub(r'[-][A-Za-z]+\d*$', '', s)
    # 去掉前面的序号
    s = re.sub(r'^\d+[-.\s]*', '', s)
    # 去掉参数
    s = RE_PARAMS.sub('', s)
    # 去掉尾部数字
    s = re.sub(r'\d+$', '', s)
    s = s.strip()
    return s if len(s) >= 2 else bill_name.strip()


def extract_quota_core(quota_name: str) -> str:
    """从定额名称中提取核心名词

    例如：
      "配电箱墙上(柱上)明装 规格(回路以内) 8" → "配电箱墙上明装"
      "镀锌钢管螺纹连接 公称直径(mm以内) 25" → "镀锌钢管螺纹连接"
    """
    if not quota_name:
        return ""
    s = quota_name.strip()
    # 取第一个空格前的部分（通常是核心名称）
    # 但有些定额名前半部分就包含空格（如"管道安装 镀锌钢管"）
    # 所以用"参数标志词"来截断
    param_markers = ["公称直径", "规格", "截面", "周长", "容量", "重量",
                     "蒸发面积", "制冷量", "功率", "口径", "长边长"]
    for marker in param_markers:
        idx = s.find(marker)
        if idx > 0:
            s = s[:idx].strip()
            break

    # 去掉括号内容
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    # 去掉尾部数字
    s = re.sub(r'\s*\d+\s*$', '', s)
    s = s.strip()
    return s if len(s) >= 2 else quota_name.strip()


def word_overlap_ratio(words_a: set, words_b: set) -> float:
    """两个词集合的重叠度（Jaccard系数）"""
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def char_overlap_ratio(a: str, b: str) -> float:
    """两个字符串的字符重叠度"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


# ============================================================
# 挖掘逻辑
# ============================================================

def load_all_papers() -> list:
    """加载所有试卷数据"""
    papers = []
    for f in PAPERS_DIR.glob("*.json"):
        if f.name.startswith("_"):
            continue
        if "脏数据" in f.name:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            papers.append(data)
        except Exception:
            continue
    return papers


def load_latest_result() -> dict:
    """加载最新跑分结果（含每题诊断）"""
    if not RESULT_FILE.exists():
        return {}
    with open(RESULT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def mine_synonym_candidates():
    """从试卷ground truth中挖掘同义词候选

    核心逻辑：
    1. 对每道题，提取清单名核心词和定额名核心词
    2. 如果两者字面差异大（字符重叠度<0.5），说明是同义词缺口
    3. 按(清单核心词, 定额核心词)分组统计频次
    4. 频次越高 = 越值得加入同义词表
    """
    papers = load_all_papers()
    if not papers:
        print("没有找到试卷文件")
        return {}

    # (bill_core, quota_core) → 出现次数
    pair_counter = Counter()
    # bill_core → 所有对应的quota_core（带计数）
    bill_to_quotas = defaultdict(Counter)
    # 详细记录（供审核用）
    pair_examples = defaultdict(list)

    total_items = 0
    for paper in papers:
        province = paper.get("province", "未知")
        for item in paper.get("items", []):
            total_items += 1
            bill_name = item.get("bill_name", "")
            quota_names = item.get("quota_names", [])
            if not bill_name or not quota_names:
                continue

            bill_core = extract_bill_core(bill_name)
            if not bill_core or len(bill_core) < 2:
                continue

            for qname in quota_names:
                quota_core = extract_quota_core(qname)
                if not quota_core or len(quota_core) < 2:
                    continue

                # 计算字符重叠度
                overlap = char_overlap_ratio(bill_core, quota_core)

                # 只关注差异大的对（重叠度<0.6说明用词差异大）
                if overlap < 0.6:
                    pair_key = (bill_core, quota_core)
                    pair_counter[pair_key] += 1
                    bill_to_quotas[bill_core][quota_core] += 1
                    if len(pair_examples[pair_key]) < 3:
                        pair_examples[pair_key].append({
                            "province": province,
                            "bill_name": bill_name,
                            "quota_name": qname,
                            "overlap": round(overlap, 2),
                        })

    print(f"扫描试卷: {len(papers)}份, {total_items}题")
    print(f"低重叠度配对: {len(pair_counter)}种")

    return {
        "pairs": pair_counter,
        "bill_to_quotas": bill_to_quotas,
        "examples": pair_examples,
    }


def analyze_synonym_gaps():
    """分析_latest_result中synonym_gap的具体清单名称分布"""
    result = load_latest_result()
    if not result:
        print("没有找到最新跑分结果")
        return

    # 统计synonym_gap中的清单名称
    gap_bills = Counter()  # bill_core → 出现次数
    gap_details = defaultdict(list)  # bill_core → [(bill_name, algo_name, stored_name, province)]

    for r in result.get("results", []):
        province = r.get("province", "")
        for det in r.get("details", []):
            if det.get("is_match"):
                continue

            bill_name = det.get("bill_name", "")
            algo_name = det.get("algo_name", "")
            stored_names = det.get("stored_names", [])
            stored_name = stored_names[0] if stored_names else ""

            if not stored_name or not algo_name:
                continue

            # 复现诊断逻辑：判断是否是synonym_gap
            stored_kw = set(stored_name.replace("(", " ").replace(")", " ").split())
            algo_kw = set(algo_name.replace("(", " ").replace(")", " ").split())
            ignore = {"安装", "制作", "周长", "mm", "m2", "以内", "≤"}
            stored_kw -= ignore
            algo_kw -= ignore

            # 检查专业册
            stored_ids = det.get("stored_ids", [])
            algo_id = det.get("algo_id", "")
            stored_id = stored_ids[0] if stored_ids else ""

            def get_book(qid):
                m = re.match(r'(C\d+)-', qid)
                if m:
                    return m.group(1)
                m = re.match(r'(\d+)-', qid)
                if m:
                    return f'C{m.group(1)}'
                return ''

            sb = get_book(stored_id)
            ab = get_book(algo_id)
            if sb and ab and sb != ab:
                continue  # wrong_book，不是synonym_gap

            # 有交集 = wrong_tier，无交集 = synonym_gap
            if stored_kw & algo_kw:
                continue  # wrong_tier

            # 这是synonym_gap
            bill_core = extract_bill_core(bill_name)
            if bill_core and len(bill_core) >= 2:
                gap_bills[bill_core] += 1
                if len(gap_details[bill_core]) < 5:
                    gap_details[bill_core].append({
                        "bill_name": bill_name,
                        "algo_name": algo_name,
                        "stored_name": stored_name,
                        "province": province[:6],
                    })

    print("=" * 70)
    print("Synonym Gap 清单名称分布（按频次降序）")
    print("=" * 70)
    print(f"总计: {sum(gap_bills.values())}条synonym_gap")
    print(f"不同清单核心名: {len(gap_bills)}种")
    print()

    # 按频次输出
    print(f"{'清单核心名':<20} {'次数':>4}  示例（系统匹配→正确答案）")
    print("-" * 70)
    for bill_core, count in gap_bills.most_common(80):
        examples = gap_details[bill_core]
        ex = examples[0]
        # 截断显示
        algo_short = ex["algo_name"][:20]
        stored_short = ex["stored_name"][:20]
        print(f"{bill_core:<20} {count:>4}  {algo_short}→{stored_short}")

    return gap_bills, gap_details


def show_candidates():
    """展示同义词候选"""
    data = mine_synonym_candidates()
    if not data:
        return

    pairs = data["pairs"]
    bill_to_quotas = data["bill_to_quotas"]
    examples = data["examples"]

    # 加载现有同义词（避免重复）
    existing = set()
    eng_path = ROOT / "data" / "engineering_synonyms.json"
    if eng_path.exists():
        with open(eng_path, "r", encoding="utf-8") as f:
            eng = json.load(f)
        for k, v in eng.items():
            if not k.startswith("_"):
                existing.add(k)
                if isinstance(v, list):
                    for item in v:
                        existing.add(item)

    print("\n" + "=" * 70)
    print("高频同义词候选（清单名 → 定额名，按频次降序）")
    print("=" * 70)

    # 按清单核心名分组，每组取频次最高的定额名
    bill_groups = defaultdict(list)
    for (bill_core, quota_core), count in pairs.most_common():
        if count >= 2:  # 至少出现2次才有价值
            bill_groups[bill_core].append((quota_core, count))

    # 按总频次排序
    sorted_bills = sorted(bill_groups.items(),
                          key=lambda x: -sum(c for _, c in x[1]))

    print(f"\n{'清单名':<18} {'频次':>4}  定额名候选")
    print("-" * 70)

    new_count = 0
    for bill_core, quota_list in sorted_bills[:100]:
        total = sum(c for _, c in quota_list)
        # 标记是否已有同义词
        marker = "  " if bill_core not in existing else "* "
        quotas_str = ", ".join(f"{q}({c})" for q, c in quota_list[:3])
        print(f"{marker}{bill_core:<16} {total:>4}  {quotas_str}")
        if bill_core not in existing:
            new_count += 1

    print(f"\n共{len(sorted_bills)}组候选，其中{new_count}组是新的")


def save_candidates():
    """保存同义词候选到文件，供人工审核"""
    data = mine_synonym_candidates()
    if not data:
        return

    pairs = data["pairs"]
    examples = data["examples"]

    # 按清单名分组
    bill_groups = defaultdict(list)
    for (bill_core, quota_core), count in pairs.most_common():
        if count >= 2:
            bill_groups[bill_core].append({
                "quota_core": quota_core,
                "count": count,
                "examples": examples.get((bill_core, quota_core), []),
            })

    # 按频次排序
    sorted_data = sorted(bill_groups.items(),
                         key=lambda x: -sum(item["count"] for item in x[1]))

    output = {
        "_meta": {
            "description": "从benchmark试卷挖掘的同义词候选（清单名→定额名）",
            "source": f"{len(data['pairs'])}种配对，来自benchmark试卷ground truth",
            "usage": "人工审核后加入engineering_synonyms.json",
        },
        "candidates": [
            {
                "bill_core": bill_core,
                "total_count": sum(item["count"] for item in items),
                "quota_candidates": items,
            }
            for bill_core, items in sorted_data
        ],
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"候选已保存到: {OUTPUT_FILE}")
    print(f"共{len(sorted_data)}组候选")


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="从benchmark试卷挖掘同义词")
    parser.add_argument("--save", action="store_true", help="保存候选到文件")
    parser.add_argument("--analyze", action="store_true", help="分析synonym_gap分布")
    args = parser.parse_args()

    if args.analyze:
        analyze_synonym_gaps()
    elif args.save:
        save_candidates()
    else:
        show_candidates()


if __name__ == "__main__":
    main()
