# -*- coding: utf-8 -*-
"""
全国算法Benchmark统一入口 — 一键跑北京回归+跨省全量

流程：
  1. 北京4套试卷回归测试（绿率/红率置信度分布）
  2. 跨省全量命中率测试（逐省命中率+根因诊断）
  3. 输出统一报告（控制台+JSON）

用法：
  python tools/run_national_benchmark.py              # 跑全量
  python tools/run_national_benchmark.py --save        # 跑完保存为新基线
  python tools/run_national_benchmark.py --skip-beijing  # 跳过北京回归（省时间）
  python tools/run_national_benchmark.py --province 广东  # 跨省部分只跑指定省
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 压低日志噪声（在import子模块之前设置）
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


# ── 北京回归测试 ──────────────────────────────────────────

def run_beijing_regression(mode="search"):
    """跑北京4套试卷，返回指标字典"""
    from tools.run_benchmark import load_config, run_single_dataset, load_baseline

    config = load_config()
    datasets = config["datasets"]
    baseline = load_baseline()
    baseline_data = baseline.get("datasets", {}) if baseline else {}

    # 回归容差
    tolerance = config.get("regression_tolerance", {})
    green_tol = tolerance.get("green_rate", 0.02)
    red_tol = tolerance.get("red_rate", 0.03)

    results = {}
    for name, ds_config in datasets.items():
        metrics = run_single_dataset(name, ds_config, mode)
        if metrics is None or metrics.get("_failed"):
            results[name] = {"status": "skip", "error": str(metrics.get("error", "文件不存在"))}
            continue

        # 与基线对比
        base = baseline_data.get(name, {})
        passed = True
        if base:
            g_diff = metrics["green_rate"] - base.get("green_rate", 0)
            r_diff = metrics["red_rate"] - base.get("red_rate", 0)
            if g_diff < -green_tol or r_diff > red_tol:
                passed = False

        results[name] = {
            "status": "pass" if passed else "regression",
            "green_rate": metrics["green_rate"],
            "red_rate": metrics["red_rate"],
            "total": metrics["total"],
            "baseline_green": base.get("green_rate"),
            "baseline_red": base.get("red_rate"),
            "passed": passed,
        }

    return results


# ── 跨省命中率测试 ────────────────────────────────────────

def run_cross_province(province_filter=None, use_experience=False):
    """跑跨省全量测试，返回结果列表"""
    from tools.run_cross_benchmark import load_test_sets, run_province_test

    test_sets = load_test_sets(province_filter=province_filter)
    if not test_sets:
        print("  未找到跨省试卷文件")
        return []

    all_results = []
    for province, data in test_sets.items():
        items = data['items']
        prov_short = province.split('(')[0].split('（')[0][:14]
        print(f"  测试 {prov_short}（{len(items)}题）...", end="", flush=True)

        result = run_province_test(province, items, use_experience=use_experience)
        all_results.append(result)
        print(f" → {result['rate']:.1f}% ({result['elapsed']:.0f}s)")

    return all_results


# ── 基线管理 ──────────────────────────────────────────────

CROSS_BASELINE_FILE = PROJECT_ROOT / "tests" / "cross_province_baseline.json"

def load_cross_baseline():
    """加载跨省基线"""
    if CROSS_BASELINE_FILE.exists():
        return json.loads(CROSS_BASELINE_FILE.read_text(encoding="utf-8"))
    return None


def save_cross_baseline(results, version="national_v1"):
    """保存跨省基线（带版本号）"""
    total_q = sum(r['total'] for r in results)
    total_correct = sum(r['correct'] for r in results)
    overall_rate = round(total_correct / max(total_q, 1) * 100, 1)

    baseline = {
        "version": version,
        "created": datetime.now().strftime('%Y-%m-%d %H:%M'),
        "total_provinces": len(results),
        "total_questions": total_q,
        "overall_rate": overall_rate,
        "provinces": {},
    }
    for r in results:
        baseline["provinces"][r["province"]] = {
            "total": r["total"],
            "rate": r["rate"],
            "diagnosis": r["diagnosis"],
        }

    CROSS_BASELINE_FILE.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n跨省基线已保存: {CROSS_BASELINE_FILE}")


# ── 统一报告 ──────────────────────────────────────────────

def print_unified_report(beijing_results, cross_results, cross_baseline):
    """打印统一报告到控制台"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*78}")
    print(f"全国算法Benchmark — {today}")
    print(f"{'='*78}")

    # 北京回归
    if beijing_results:
        print(f"\n【北京回归测试】")
        all_passed = True
        for name, r in beijing_results.items():
            if r.get("status") == "skip":
                print(f"  {name}: 跳过 ({r.get('error', '')})")
                continue
            g = r["green_rate"]
            rd = r["red_rate"]
            bg = r.get("baseline_green")
            br = r.get("baseline_red")
            passed = r.get("passed", True)
            mark = "✅" if passed else "❌退化"
            if not passed:
                all_passed = False

            base_str = ""
            if bg is not None:
                base_str = f" (基线: 绿{bg*100:.1f}% 红{br*100:.1f}%)"

            # 简短名称
            short = name.replace("_", " ")
            print(f"  {short:18s}: 绿{g*100:.1f}% 红{rd*100:.1f}%{base_str} {mark}")

        if all_passed:
            print(f"  → 全部通过")
        else:
            print(f"  → ⚠️ 存在退化！")
    else:
        print(f"\n【北京回归测试】 已跳过")

    # 跨省命中率
    if cross_results:
        total_q = sum(r['total'] for r in cross_results)
        total_correct = sum(r['correct'] for r in cross_results)
        overall_rate = total_correct / max(total_q, 1) * 100

        print(f"\n【跨省命中率】({len(cross_results)}个省份，共{total_q}题)")
        # 表头
        header = f"  {'省份':<16} {'题数':>4} {'命中率':>8}"
        if cross_baseline:
            header += f" {'基线':>8} {'变化':>8}"
        header += f" {'同义词':>6} {'档位':>6} {'专业':>4} {'耗时':>5}"
        print(header)
        print(f"  {'-'*74}")

        # 按命中率降序排列
        sorted_results = sorted(cross_results, key=lambda x: -x['rate'])
        base_provs = cross_baseline.get("provinces", {}) if cross_baseline else {}

        # 根因汇总
        total_diag = Counter()

        for r in sorted_results:
            prov_short = r['province'].split('(')[0].split('（')[0][:14]
            diag = r['diagnosis']
            syn = diag.get('synonym_gap', 0)
            tier = diag.get('wrong_tier', 0)
            book = diag.get('wrong_book', 0)
            total_diag['synonym_gap'] += syn
            total_diag['wrong_tier'] += tier
            total_diag['wrong_book'] += book

            line = f"  {prov_short:<16} {r['total']:>4} {r['rate']:>7.1f}%"

            if cross_baseline and r['province'] in base_provs:
                br = base_provs[r['province']]['rate']
                delta = r['rate'] - br
                sign = '+' if delta > 0 else ''
                line += f" {br:>7.1f}% {sign}{delta:>6.1f}%"
            elif cross_baseline:
                line += f" {'新':>8} {'':>8}"

            line += f" {syn:>6} {tier:>6} {book:>4} {r['elapsed']:>4.0f}s"
            print(line)

        # 汇总行
        print(f"  {'-'*74}")
        total_line = f"  {'总计':<16} {total_q:>4} {overall_rate:>7.1f}%"
        if cross_baseline:
            old_overall = cross_baseline.get("overall_rate", 0)
            delta = overall_rate - old_overall
            sign = '+' if delta > 0 else ''
            total_line += f" {old_overall:>7.1f}% {sign}{delta:>6.1f}%"
        print(total_line)

        # 根因汇总
        total_wrong = sum(total_diag.values())
        if total_wrong > 0:
            print(f"\n【根因汇总】（共{total_wrong}题错误）")
            for cause, cnt in total_diag.most_common():
                pct = cnt / total_wrong * 100
                label = {'synonym_gap': '同义词缺口', 'wrong_tier': '选错档位',
                         'wrong_book': '搜偏专业', 'no_result': '无结果'}.get(cause, cause)
                hint = {'synonym_gap': '扩充同义词表',
                        'wrong_tier': '优化参数验证',
                        'wrong_book': '加强专业分类'}.get(cause, '')
                print(f"  {label}: {cnt}/{total_wrong} ({pct:.1f}%) — {hint}")

    print(f"\n{'='*78}")


def save_report_json(beijing_results, cross_results, cross_baseline):
    """保存统一报告JSON"""
    report_dir = PROJECT_ROOT / "output" / "temp"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "national_benchmark_report.json"

    total_q = sum(r['total'] for r in cross_results) if cross_results else 0
    total_correct = sum(r['correct'] for r in cross_results) if cross_results else 0
    overall_rate = round(total_correct / max(total_q, 1) * 100, 1)

    # 根因汇总
    total_diag = Counter()
    for r in (cross_results or []):
        for cause, cnt in r['diagnosis'].items():
            total_diag[cause] += cnt

    # 基线对比
    baseline_comparison = {}
    if cross_baseline and cross_results:
        old_overall = cross_baseline.get("overall_rate", 0)
        baseline_comparison["overall_delta"] = f"{'+' if overall_rate > old_overall else ''}{overall_rate - old_overall:.1f}%"
        base_provs = cross_baseline.get("provinces", {})
        improved = []
        regressed = []
        new_provs = []
        for r in cross_results:
            if r['province'] in base_provs:
                delta = r['rate'] - base_provs[r['province']]['rate']
                if delta > 1:
                    improved.append(f"{r['province'].split('(')[0][:10]}{delta:+.1f}%")
                elif delta < -1:
                    regressed.append(f"{r['province'].split('(')[0][:10]}{delta:+.1f}%")
            else:
                new_provs.append(r['province'].split('(')[0][:10])
        baseline_comparison["improved"] = improved
        baseline_comparison["regressed"] = regressed
        baseline_comparison["new_provinces"] = new_provs

    report = {
        "version": "national_v1",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "beijing_regression": beijing_results or {},
        "cross_province": {
            "total_provinces": len(cross_results) if cross_results else 0,
            "total_questions": total_q,
            "overall_rate": overall_rate,
            "diagnosis_total": dict(total_diag),
            "provinces": {
                r['province']: {
                    "total": r['total'],
                    "rate": r['rate'],
                    "diagnosis": r['diagnosis'],
                    "elapsed": r['elapsed'],
                }
                for r in (cross_results or [])
            },
        },
        "baseline_comparison": baseline_comparison,
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"详细报告已保存: {report_path}")
    return report


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='全国算法Benchmark统一入口')
    parser.add_argument('--save', action='store_true',
                        help='保存跨省结果为新基线')
    parser.add_argument('--skip-beijing', action='store_true',
                        help='跳过北京回归测试（省时间）')
    parser.add_argument('--province', type=str,
                        help='跨省部分只跑指定省（模糊匹配）')
    parser.add_argument('--with-experience', action='store_true',
                        help='跨省测试启用经验库')
    parser.add_argument('--mode', choices=['search', 'agent'], default='search',
                        help='北京试卷匹配模式（默认search）')
    args = parser.parse_args()

    start_all = time.time()

    # ── 1. 北京回归测试 ──
    beijing_results = None
    if not args.skip_beijing:
        print("【第1步】北京回归测试...")
        beijing_results = run_beijing_regression(mode=args.mode)
    else:
        print("【第1步】北京回归测试 → 已跳过")

    # ── 2. 跨省命中率测试 ──
    print(f"\n【第2步】跨省命中率测试...")
    cross_baseline = load_cross_baseline()
    if cross_baseline:
        old_ver = cross_baseline.get("version", "legacy")
        old_date = cross_baseline.get("created", "?")
        print(f"  基线: {old_ver} ({old_date})")

    cross_results = run_cross_province(
        province_filter=args.province,
        use_experience=args.with_experience)

    # ── 3. 输出统一报告 ──
    elapsed_all = time.time() - start_all
    print(f"\n总耗时: {elapsed_all:.0f}秒")

    print_unified_report(beijing_results, cross_results, cross_baseline)
    save_report_json(beijing_results, cross_results, cross_baseline)

    # ── 4. 保存基线 ──
    if args.save and cross_results:
        save_cross_baseline(cross_results)


if __name__ == '__main__':
    main()
