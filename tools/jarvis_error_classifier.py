# -*- coding: utf-8 -*-
"""
Benchmark 错题原因分类器 — 把每条错题自动归类到8种原因

8种错误类型：
  1. 排序偏差        — 正确答案在候选池但没排第一
  2. 参数提取错(排序) — 排序偏差的细分：DN/规格匹配错
  3. 专业分类错(排序) — 排序偏差的细分：跨专业选错
  4. 清单太模糊      — 清单名称太短或太含糊，导致搜不到
  5. 同义词缺口      — 清单用词和定额用词不同
  6. 参数提取错(召回) — 召回问题中的DN/规格不匹配
  7. 搜索词偏差      — 候选池里完全没有正确答案（兜底）
  8. 多定额遗漏      — 应该匹配多个定额但只给了一个

用法：
    python tools/jarvis_error_classifier.py
    python tools/jarvis_error_classifier.py --input tests/benchmark_papers/_latest_result.json
    python tools/jarvis_error_classifier.py --top 20   # 每种类型展示前20个示例
"""

import sys
import os
import re
import json
import argparse
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── 默认路径 ──
DEFAULT_INPUT = PROJECT_ROOT / "tests" / "benchmark_papers" / "_latest_result.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "temp" / "error_classification.json"

# ── 12册专业分类映射（编号前缀 → 专业名称） ──
BOOK_NAMES = {
    "C1": "机械设备", "C2": "热力设备", "C3": "静置设备",
    "C4": "电气", "C5": "智能化", "C6": "自动化控制",
    "C7": "通风空调", "C8": "工业管道", "C9": "消防",
    "C10": "给排水", "C11": "建筑智能化（旧）", "C12": "刷油防腐",
}

# ── 常见同义词对（清单叫法 vs 定额叫法） ──
# 如果清单和定额名称中出现这些对应关系，判定为同义词缺口
SYNONYM_PAIRS = [
    ("镀锌钢管", "热镀锌钢管"), ("镀锌钢管", "焊接钢管"),
    ("PPR", "聚丙烯"), ("PE管", "聚乙烯"),
    ("PVC", "硬聚氯乙烯"), ("PVC", "塑料管"),
    ("铝塑复合管", "铝塑管"),
    ("不锈钢管", "薄壁不锈钢"), ("不锈钢管", "不锈钢薄壁管"),
    ("球阀", "阀门"), ("截止阀", "阀门"), ("闸阀", "阀门"), ("蝶阀", "阀门"),
    ("灯具", "灯"), ("筒灯", "嵌入式灯"), ("射灯", "嵌入式灯"),
    ("开关", "暗开关"), ("翘板开关", "暗开关"), ("跷板开关", "暗开关"),
    ("插座", "暗插座"), ("插座", "插座安装"),
    ("配电箱", "配电柜"), ("控制箱", "配电箱"),
    ("桥架", "电缆桥架"), ("线槽", "桥架"),
    ("风机盘管", "风机"), ("风口", "散流器"),
    ("洗脸盆", "洗手盆"), ("洗漱台", "洗脸盆"), ("台盆", "洗脸盆"),
    ("坐便器", "大便器"), ("蹲便器", "大便器"), ("马桶", "大便器"),
    ("地漏", "排水器具"),
    ("水表", "水量表"), ("电表", "电度表"),
    ("管卡", "管道支架"), ("支吊架", "管道支架"),
    ("凿槽", "刨沟"), ("凿槽", "开槽"),
]


def _extract_book(quota_id: str) -> str:
    """从定额编号提取册号，如 'C4-4-37' → 'C4', 'C10-1-10' → 'C10'"""
    if not quota_id:
        return ""
    m = re.match(r"(C\d+)", quota_id)
    return m.group(1) if m else ""


def _extract_dn(text: str) -> list[str]:
    """从文本中提取所有DN值，如 'DN25' → ['25'], '公称直径80' → ['80']"""
    if not text:
        return []
    # 匹配 DN25, dn32, DN 50, 公称直径(mm以内) 80 等
    patterns = [
        r'[Dd][Nn]\s*(\d+)',
        r'公称直径[^0-9]*(\d+)',
        r'De(\d+)',
    ]
    results = []
    for p in patterns:
        results.extend(re.findall(p, text))
    return results


def _extract_spec_numbers(text: str) -> list[str]:
    """提取规格相关数字（截面积、回路数等）"""
    if not text:
        return []
    # 匹配 "截面(mm2以内) 2.5", "回路以内 8" 等
    patterns = [
        r'截面[^0-9]*(\d+\.?\d*)',
        r'回路[^0-9]*(\d+)',
        r'半周长[^0-9]*(\d+\.?\d*)',
    ]
    results = []
    for p in patterns:
        results.extend(re.findall(p, text))
    return results


def _is_cross_book(algo_id: str, stored_ids: list[str]) -> bool:
    """判断算法选的定额和标准答案是否跨专业册"""
    algo_book = _extract_book(algo_id)
    if not algo_book:
        return False
    for sid in stored_ids:
        stored_book = _extract_book(sid)
        if stored_book and stored_book != algo_book:
            return True
    return False


def _has_dn_mismatch(text_a: str, text_b: str) -> bool:
    """判断两个文本的DN值是否不同"""
    dn_a = set(_extract_dn(text_a))
    dn_b = set(_extract_dn(text_b))
    if dn_a and dn_b and not dn_a.intersection(dn_b):
        return True
    return False


def _has_spec_mismatch(text_a: str, text_b: str) -> bool:
    """判断两个文本的规格参数（截面、回路等）是否不同"""
    spec_a = set(_extract_spec_numbers(text_a))
    spec_b = set(_extract_spec_numbers(text_b))
    if spec_a and spec_b and not spec_a.intersection(spec_b):
        return True
    return False


def _has_synonym_gap(bill_name: str, stored_names: list[str]) -> bool:
    """判断清单名称和标准答案之间是否存在同义词差异"""
    bill_lower = bill_name.lower()
    stored_text = " ".join(stored_names).lower()
    for word_a, word_b in SYNONYM_PAIRS:
        # 清单含A但定额含B，或反过来
        a_in_bill = word_a.lower() in bill_lower
        b_in_bill = word_b.lower() in bill_lower
        a_in_stored = word_a.lower() in stored_text
        b_in_stored = word_b.lower() in stored_text
        if (a_in_bill and b_in_stored and not a_in_stored):
            return True
        if (b_in_bill and a_in_stored and not b_in_stored):
            return True
    return False


def _is_multi_quota_miss(item: dict) -> bool:
    """判断是否为多定额遗漏：标准答案有多个定额，算法只给了一个"""
    stored_ids = item.get("stored_ids", [])
    algo_id = item.get("algo_id", "")
    # 标准答案有2个以上定额
    if len(stored_ids) >= 2:
        # 算法选的定额是标准答案之一（部分正确），说明是遗漏而非完全选错
        if algo_id in stored_ids:
            return True
    return False


def classify_error(item: dict) -> str:
    """
    对一条错题进行错误原因分类。

    分类逻辑（按优先级，第一个命中的为准）：
    1. 先判断"多定额遗漏"（标准答案多个，算法只给了其中一个）
    2. oracle_in_candidates=true → 排序问题，细分为参数/专业/普通排序偏差
    3. oracle_in_candidates=false → 召回问题，细分为模糊/同义词/参数/搜索词偏差
    """
    bill_name = item.get("bill_name", "")
    algo_id = item.get("algo_id", "")
    algo_name = item.get("algo_name", "")
    stored_ids = item.get("stored_ids", [])
    stored_names = item.get("stored_names", [])
    oracle_in = item.get("oracle_in_candidates", False)
    stored_names_text = " ".join(stored_names)

    # ── 优先判断：多定额遗漏 ──
    if _is_multi_quota_miss(item):
        return "多定额遗漏"

    # ── 分支1：正确答案在候选池（排序问题） ──
    if oracle_in:
        # 细分：DN/规格参数不匹配
        if _has_dn_mismatch(algo_name, stored_names_text):
            return "参数提取错(排序)"
        if _has_spec_mismatch(algo_name, stored_names_text):
            return "参数提取错(排序)"
        # 细分：跨专业选错
        if _is_cross_book(algo_id, stored_ids):
            return "专业分类错(排序)"
        # 普通排序偏差
        return "排序偏差"

    # ── 分支2：正确答案不在候选池（召回问题） ──

    # 清单名称太短或太模糊（<=4个有效字符）
    clean_name = re.sub(r'[A-Za-z0-9\-_\s\.#]', '', bill_name)  # 去掉字母数字符号
    if len(clean_name) <= 4 and len(bill_name) <= 8:
        return "清单太模糊"

    # 同义词缺口
    if _has_synonym_gap(bill_name, stored_names):
        return "同义词缺口"

    # DN参数不匹配
    if _has_dn_mismatch(bill_name, stored_names_text):
        return "参数提取错(召回)"

    # 兜底：搜索词偏差
    return "搜索词偏差"


def classify_all(data: dict) -> dict:
    """
    对所有错题进行分类，返回结构化结果。

    返回格式：
    {
        "summary": {"排序偏差": {"count": 100, "pct": "30.5%"}, ...},
        "by_province": {"北京...": {"total_wrong": 142, "categories": {...}, "examples": {...}}},
        "all_errors": [{"province": ..., "bill_name": ..., "category": ..., ...}]
    }
    """
    # 全局统计
    global_counts = defaultdict(int)  # 各错误类型的总数
    global_examples = defaultdict(list)  # 各错误类型的示例
    by_province = {}  # 按省份分别统计
    all_errors = []  # 所有错题明细

    total_wrong = 0

    for result in data.get("results", []):
        province = result.get("province", "未知省份")
        prov_counts = defaultdict(int)
        prov_examples = defaultdict(list)
        prov_wrong = 0

        for item in result.get("details", []):
            # 只处理错题
            if item.get("is_match", True):
                continue

            category = classify_error(item)
            prov_wrong += 1
            total_wrong += 1

            # 统计
            global_counts[category] += 1
            prov_counts[category] += 1

            # 记录错题明细
            error_record = {
                "province": province,
                "bill_name": item.get("bill_name", ""),
                "category": category,
                "algo_id": item.get("algo_id", ""),
                "algo_name": item.get("algo_name", ""),
                "stored_ids": item.get("stored_ids", []),
                "stored_names": item.get("stored_names", []),
                "confidence": item.get("confidence", 0),
                "oracle_in_candidates": item.get("oracle_in_candidates", False),
            }
            all_errors.append(error_record)

            # 示例（每种类型收集）
            example = {
                "bill_name": item.get("bill_name", ""),
                "algo": f"{item.get('algo_id', '')} {item.get('algo_name', '')}",
                "expected": f"{', '.join(item.get('stored_ids', []))} {', '.join(item.get('stored_names', []))}",
                "confidence": item.get("confidence", 0),
            }
            global_examples[category].append(example)
            prov_examples[category].append(example)

        # 省份结果
        by_province[province] = {
            "total_wrong": prov_wrong,
            "categories": dict(prov_counts),
            "examples": {k: v[:5] for k, v in prov_examples.items()},  # 每省每类最多5个示例
        }

    # 汇总（按数量降序排列）
    summary = {}
    for cat in sorted(global_counts, key=lambda x: global_counts[x], reverse=True):
        cnt = global_counts[cat]
        pct = f"{cnt / total_wrong * 100:.1f}%" if total_wrong > 0 else "0%"
        summary[cat] = {"count": cnt, "pct": pct}

    return {
        "total_wrong": total_wrong,
        "summary": summary,
        "by_province": by_province,
        "all_errors": all_errors,
        "global_examples": {k: v[:10] for k, v in global_examples.items()},  # 全局每类最多10个示例
    }


def print_report(result: dict, top_n: int = 10):
    """打印可读的分类报告到终端"""
    total = result["total_wrong"]
    summary = result["summary"]

    print("=" * 70)
    print(f"  Benchmark 错题原因分类报告（共 {total} 条错题）")
    print("=" * 70)

    # ── 总览 ──
    print("\n【总览】各错误类型占比：\n")
    print(f"  {'错误类型':<18} {'数量':>6} {'占比':>8}  {'说明'}")
    print(f"  {'-'*16:<18} {'-'*6:>6} {'-'*6:>8}  {'-'*30}")

    # 错误类型说明
    desc_map = {
        "排序偏差": "正确答案在候选池但没排第一",
        "参数提取错(排序)": "排序时DN/规格选错了",
        "专业分类错(排序)": "排序时跨专业选错了",
        "搜索词偏差": "候选池里找不到正确答案（兜底）",
        "同义词缺口": "清单和定额用词不同，搜不到",
        "参数提取错(召回)": "召回时DN/规格不匹配",
        "清单太模糊": "清单名称太短/太含糊",
        "多定额遗漏": "应匹配多个定额但只给了一个",
    }

    for cat, info in summary.items():
        desc = desc_map.get(cat, "")
        print(f"  {cat:<18} {info['count']:>6} {info['pct']:>8}  {desc}")

    # ── 按省份 ──
    print(f"\n{'='*70}")
    print("【按省份统计】\n")

    for prov, prov_data in sorted(result["by_province"].items(),
                                   key=lambda x: x[1]["total_wrong"], reverse=True):
        pw = prov_data["total_wrong"]
        # 省份名简化（取前6个字）
        short_name = prov[:20] if len(prov) > 20 else prov
        cats_str = ", ".join(f"{k}:{v}" for k, v in
                            sorted(prov_data["categories"].items(),
                                   key=lambda x: x[1], reverse=True))
        print(f"  {short_name:<22} 错{pw:>4}条  {cats_str}")

    # ── 示例 ──
    print(f"\n{'='*70}")
    print(f"【各类型示例（每类最多{top_n}个）】\n")

    for cat, info in summary.items():
        examples = result["global_examples"].get(cat, [])[:top_n]
        if not examples:
            continue
        print(f"--- {cat} ({info['count']}条, {info['pct']}) ---")
        for i, ex in enumerate(examples, 1):
            print(f"  [{i}] 清单: {ex['bill_name']}")
            print(f"      算法选: {ex['algo']}")
            print(f"      正确答: {ex['expected']}")
            print(f"      置信度: {ex['confidence']}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark错题原因分类器")
    parser.add_argument("--input", "-i", type=str, default=str(DEFAULT_INPUT),
                        help="输入文件路径（默认: tests/benchmark_papers/_latest_result.json）")
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
                        help="输出文件路径（默认: output/temp/error_classification.json）")
    parser.add_argument("--top", type=int, default=10,
                        help="每种错误类型展示几个示例（默认10个）")
    args = parser.parse_args()

    # 读取输入文件
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：找不到输入文件 {input_path}")
        print(f"请先运行 benchmark 生成结果：python tools/run_benchmark.py")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"读取: {input_path}")
    print(f"运行时间: {data.get('run_time', '未知')}")

    # 分类
    result = classify_all(data)

    # 打印报告
    print_report(result, top_n=args.top)

    # 保存结果
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n分类结果已保存到: {output_path}")


if __name__ == "__main__":
    main()
