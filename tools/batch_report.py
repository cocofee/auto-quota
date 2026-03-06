"""
错误分析报告生成器

功能：汇总批量匹配结果，生成 error_report.json 供算法Agent消费。

这个报告是算法改进的"食粮"——告诉算法Agent：
1. 哪些清单匹配得不好（低置信度）
2. 低置信度的模式是什么（同义词缺口/选错档位/搜偏专业）
3. 按省份/专业维度分组，看哪里最需要改进

用法：
    python tools/batch_report.py                     # 生成完整报告
    python tools/batch_report.py --province 广东     # 只看某省
    python tools/batch_report.py --top 50            # 只看TOP50错误模式
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.batch_scanner import DB_PATH, get_db, init_db

# 结果目录
RESULTS_DIR = Path(__file__).resolve().parent.parent / "output" / "batch" / "results"
# 报告输出路径
REPORT_PATH = Path(__file__).resolve().parent.parent / "output" / "batch" / "error_report.json"

# 置信度阈值
HIGH_CONFIDENCE = 85   # 绿灯
LOW_CONFIDENCE = 60    # 红灯


# ============================================================
# 结果收集
# ============================================================

def collect_results(province_filter: str = None) -> list[dict]:
    """收集所有匹配结果JSON文件。

    返回:
        列表，每个元素是一个文件的匹配结果（dict）
    """
    if not RESULTS_DIR.exists():
        print("结果目录不存在，请先跑 batch_runner.py")
        return []

    all_results = []
    for json_path in RESULTS_DIR.rglob("*.json"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 省份筛选
            if province_filter and data.get("province") != province_filter:
                continue

            all_results.append(data)
        except Exception as e:
            print(f"  警告: 读取结果文件失败 {json_path}: {e}")
            continue

    return all_results


# ============================================================
# 错误模式分析
# ============================================================

def analyze_patterns(all_results: list[dict], top_n: int = 100) -> dict:
    """分析所有结果，生成错误模式报告。

    分析维度：
    1. 总体统计
    2. 按省份/专业分组
    3. 低置信度清单的模式聚类（找出反复出错的清单名称）
    """
    # 总体统计
    total_files = len(all_results)
    total_items = 0
    skipped_measures = 0  # 已跳过的措施费（不计入红灯）
    high_count = 0
    low_count = 0
    mid_count = 0
    conf_sum = 0

    # 按省份/专业分组
    by_province = defaultdict(lambda: {"files": 0, "items": 0, "conf_sum": 0, "high": 0, "low": 0})
    by_specialty = defaultdict(lambda: {"items": 0, "conf_sum": 0, "high": 0, "low": 0})

    # 低置信度清单收集（用于模式聚类）
    low_conf_items = []

    for file_data in all_results:
        prov = file_data.get("province", "未知")
        spec = file_data.get("specialty", "未知")
        results = file_data.get("results", [])

        by_province[prov]["files"] += 1

        for r in results:
            name = r.get("name", "")
            matched_name = r.get("matched_quota_name", "")
            conf = r.get("confidence", 0)
            match_source = r.get("match_source", "")

            # 已跳过的措施费不计入统计（它们被正确识别并跳过了，不是算法错误）
            if match_source == "skip_measure":
                skipped_measures += 1
                continue

            total_items += 1
            conf_sum += conf

            by_province[prov]["items"] += 1
            by_province[prov]["conf_sum"] += conf
            by_specialty[spec]["items"] += 1
            by_specialty[spec]["conf_sum"] += conf

            if conf >= HIGH_CONFIDENCE:
                high_count += 1
                by_province[prov]["high"] += 1
                by_specialty[spec]["high"] += 1
            elif conf < LOW_CONFIDENCE:
                low_count += 1
                by_province[prov]["low"] += 1
                by_specialty[spec]["low"] += 1
                low_conf_items.append({
                    "name": name,
                    "description": r.get("description", ""),
                    "matched_quota_id": r.get("matched_quota_id", ""),
                    "matched_quota_name": matched_name,
                    "confidence": conf,
                    "match_source": match_source,
                    "province": prov,
                    "specialty": spec,
                })
            else:
                mid_count += 1

    avg_conf = conf_sum / total_items if total_items > 0 else 0

    # 格式化省份/专业统计
    province_stats = {}
    for prov, stats in sorted(by_province.items(), key=lambda x: -x[1]["items"]):
        avg = stats["conf_sum"] / stats["items"] if stats["items"] > 0 else 0
        province_stats[prov] = {
            "files": stats["files"],
            "items": stats["items"],
            "avg_confidence": round(avg, 1),
            "high_rate": round(stats["high"] / stats["items"] * 100, 1) if stats["items"] > 0 else 0,
            "low_rate": round(stats["low"] / stats["items"] * 100, 1) if stats["items"] > 0 else 0,
        }

    specialty_stats = {}
    for spec, stats in sorted(by_specialty.items(), key=lambda x: -x[1]["items"]):
        avg = stats["conf_sum"] / stats["items"] if stats["items"] > 0 else 0
        specialty_stats[spec] = {
            "items": stats["items"],
            "avg_confidence": round(avg, 1),
            "high_rate": round(stats["high"] / stats["items"] * 100, 1) if stats["items"] > 0 else 0,
            "low_rate": round(stats["low"] / stats["items"] * 100, 1) if stats["items"] > 0 else 0,
        }

    # 低置信度模式聚类（按清单名称的核心词分组）
    patterns = _cluster_low_confidence(low_conf_items, top_n)

    # 省份覆盖矩阵
    coverage = _build_coverage_matrix(province_stats)

    return {
        "report_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "algo_version": _get_algo_version(),
        "summary": {
            "total_files": total_files,
            "total_items": total_items,
            "skipped_measures": skipped_measures,
            "high_confidence": high_count,
            "mid_confidence": mid_count,
            "low_confidence": low_count,
            "avg_confidence": round(avg_conf, 1),
            "high_rate": round(high_count / total_items * 100, 1) if total_items > 0 else 0,
            "low_rate": round(low_count / total_items * 100, 1) if total_items > 0 else 0,
        },
        "by_province": province_stats,
        "by_specialty": specialty_stats,
        "low_confidence_patterns": patterns,
        "province_coverage": coverage,
    }


def _cluster_low_confidence(items: list[dict], top_n: int) -> list[dict]:
    """将低置信度清单按核心词聚类。

    比如"防火阀 500*320"和"防火阀 400*250"归到"防火阀"这个模式下。
    """
    # 提取核心词（去掉数字、规格参数、单位等）
    pattern_groups = defaultdict(list)

    for item in items:
        core = _extract_core_name(item["name"])
        if core:
            pattern_groups[core].append(item)

    # 按出现次数排序，取 TOP N
    sorted_patterns = sorted(pattern_groups.items(), key=lambda x: -len(x[1]))[:top_n]

    result = []
    for pattern, group_items in sorted_patterns:
        # 找出涉及的省份
        provinces = list(set(it["province"] for it in group_items))
        # 猜测根因
        root_cause = _guess_root_cause(group_items)
        # 典型案例
        typical = group_items[0]

        result.append({
            "pattern": pattern,
            "count": len(group_items),
            "provinces": provinces,
            "typical_bill": typical["name"],
            "typical_match": typical.get("matched_quota_name", ""),
            "avg_confidence": round(sum(it["confidence"] for it in group_items) / len(group_items), 1),
            "root_cause_guess": root_cause,
        })

    return result


def _extract_core_name(name: str) -> str:
    """提取清单名称的核心词（去掉规格参数）。

    "镀锌钢管 DN25" → "镀锌钢管"
    "70°防火阀 500*320" → "防火阀"
    "YJV电缆 4*25+1*16" → "电缆"
    """
    if not name:
        return ""
    # 去掉括号内容
    text = re.sub(r'[（(][^)）]*[)）]', '', name)
    # 去掉数字+单位组合
    text = re.sub(r'\d+[\*×xX]\d+', '', text)  # 500*320
    text = re.sub(r'DN\d+', '', text, flags=re.IGNORECASE)  # DN25
    text = re.sub(r'\d+(?:mm|m|kg|t|kv|kw|kva|°|度)', '', text, flags=re.IGNORECASE)
    # 去掉纯数字
    text = re.sub(r'\d+', '', text)
    # 去掉标点
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z]', '', text)
    # 太短的不要
    return text.strip() if len(text.strip()) >= 2 else ""


def _guess_root_cause(items: list[dict]) -> str:
    """猜测低置信度的根因。

    规则：
    - 匹配结果为空 → "search_miss"（搜索没结果）
    - 匹配名和清单名差异大 → "synonym_gap"（同义词缺口）
    - 匹配名相似但置信度低 → "wrong_tier"（选错档位/参数不匹配）
    """
    for item in items[:5]:  # 看前5个案例
        matched = item.get("matched_quota_name", "")
        bill_name = item.get("name", "")

        if not matched:
            return "search_miss"

        # 简单相似度判断
        common_chars = set(bill_name) & set(matched)
        if len(common_chars) < 2:
            return "synonym_gap"

    return "wrong_tier"


def _build_coverage_matrix(province_stats: dict) -> dict:
    """构建省份覆盖矩阵。"""
    provinces_dir = Path(__file__).resolve().parent.parent / "db" / "provinces"
    has_db = set()
    if provinces_dir.exists():
        for d in provinces_dir.iterdir():
            if d.is_dir():
                # 提取省份名（去掉年份后缀）
                name = re.sub(r'\d+$', '', d.name)
                has_db.add(name)

    has_files = set(province_stats.keys())

    return {
        "有定额库且有文件": sorted(has_db & has_files),
        "有文件无定额库": sorted(has_files - has_db),
        "有定额库无文件": sorted(has_db - has_files),
    }


def _get_algo_version() -> str:
    """获取当前算法版本。"""
    try:
        from tools.batch_scanner import ALGORITHM_VERSION
        return ALGORITHM_VERSION
    except Exception:
        return "unknown"


# ============================================================
# 报告生成
# ============================================================

def generate_report(province_filter: str = None, top_n: int = 100):
    """生成错误分析报告。"""
    print("收集匹配结果...")
    all_results = collect_results(province_filter)
    if not all_results:
        print("没有匹配结果。")
        return

    print(f"共 {len(all_results)} 个文件的结果")
    print("分析错误模式...")
    report = analyze_patterns(all_results, top_n)

    # 保存报告
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n报告已保存到: {REPORT_PATH}")

    # 打印摘要
    s = report["summary"]
    print(f"\n{'='*50}")
    print(f"错误分析报告摘要")
    print(f"{'='*50}")
    print(f"  总文件: {s['total_files']} | 总清单: {s['total_items']} | 措施费已跳过: {s.get('skipped_measures', 0)}")
    print(f"  绿灯(≥85%): {s['high_confidence']} ({s['high_rate']}%)")
    print(f"  红灯(<60%): {s['low_confidence']} ({s['low_rate']}%)")
    print(f"  平均置信度: {s['avg_confidence']}%")

    if report["low_confidence_patterns"]:
        print(f"\n低置信度 TOP 10 模式:")
        for i, p in enumerate(report["low_confidence_patterns"][:10]):
            print(f"  {i+1}. {p['pattern']} × {p['count']} | "
                  f"均{p['avg_confidence']}% | {p['root_cause_guess']} | "
                  f"省份: {','.join(p['provinces'][:3])}")

    # 省份排行
    if report["by_province"]:
        print(f"\n各省低置信度率:")
        for prov, stats in sorted(report["by_province"].items(),
                                   key=lambda x: -x[1]["low_rate"])[:10]:
            print(f"  {prov:8s} 红{stats['low_rate']:5.1f}% | "
                  f"绿{stats['high_rate']:5.1f}% | {stats['items']}条")


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="错误分析报告生成器")
    parser.add_argument("--province", help="只看某省（如 广东）")
    parser.add_argument("--top", type=int, default=100, help="只看TOP N错误模式（默认100）")

    args = parser.parse_args()

    generate_report(
        province_filter=args.province,
        top_n=args.top,
    )


if __name__ == "__main__":
    main()
