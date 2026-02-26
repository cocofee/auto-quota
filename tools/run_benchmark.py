"""
Benchmark 运行与基线采集脚本。

功能：
  1. 读取 tests/benchmark_config.json 中定义的数据集
  2. 对每个数据集调用 main.run() 执行匹配
  3. 计算指标（绿率/红率/经验命中率等）
  4. 保存基线到 tests/benchmark_baseline.json（--save）
  5. 与已有基线对比（--compare）

用法：
  # 采集基线（search模式，免费快速）
  python tools/run_benchmark.py --mode search --save

  # 采集基线（agent模式，需API Key）
  python tools/run_benchmark.py --mode agent --save

  # 只跑指定数据集
  python tools/run_benchmark.py --dataset B2_华佑电气 --save

  # 与基线对比（不保存）
  python tools/run_benchmark.py --mode search --compare

  # 查看当前基线
  python tools/run_benchmark.py --show-baseline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 配置和基线文件路径
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "tests" / "benchmark_config.json"
BASELINE_PATH = PROJECT_ROOT / "tests" / "benchmark_baseline.json"
HISTORY_PATH = PROJECT_ROOT / "tests" / "benchmark_history.json"  # 跑分历史记录
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_config() -> dict:
    """加载 benchmark 配置文件"""
    if not CONFIG_PATH.exists():
        print(f"错误：配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_baseline() -> dict | None:
    """加载已有基线数据（不存在则返回 None）"""
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def compute_metrics(results: list[dict], elapsed: float) -> dict:
    """从匹配结果列表计算 benchmark 指标。

    参数:
        results: main.run() 返回的 results 列表
        elapsed: 运行耗时（秒）

    返回:
        包含各项指标的字典
    """
    total_all = len(results)
    if total_all == 0:
        return {
            "total": 0, "skip_measure": 0,
            "green_rate": 0, "yellow_rate": 0, "red_rate": 0,
            "exp_hit_rate": 0, "fallback_rate": 0, "avg_time_sec": 0,
        }

    def _safe_confidence(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    # 措施项（正确跳过的非定额项）不计入绿/黄/红率分母
    skip_measure = sum(
        1 for r in results if r.get("match_source") == "skip_measure"
    )
    # 参与准确率计算的有效条目数
    total = total_all - skip_measure

    # 置信度分布（阈值：绿≥85, 黄60-84, 红<60）——仅统计有效条目
    matchable = [r for r in results if r.get("match_source") != "skip_measure"]
    high_conf = sum(1 for r in matchable if _safe_confidence(r.get("confidence", 0)) >= 85)
    mid_conf = sum(
        1 for r in matchable if 60 <= _safe_confidence(r.get("confidence", 0)) < 85
    )
    low_conf = total - high_conf - mid_conf

    # 经验库命中（match_source 以 "experience" 开头）
    exp_hits = sum(
        1 for r in results
        if str(r.get("match_source", "")).startswith("experience"))

    # Agent降级数（包含可恢复降级和任务异常降级）
    fallbacks = sum(
        1 for r in results
        if r.get("match_source") in {"agent_fallback", "agent_error"})

    denom = max(total, 1)  # 防除零
    return {
        "total": total_all,
        "skip_measure": skip_measure,
        "green_rate": round(high_conf / denom, 4),
        "yellow_rate": round(mid_conf / denom, 4),
        "red_rate": round(low_conf / denom, 4),
        "exp_hit_rate": round(exp_hits / total_all, 4),
        "fallback_rate": round(fallbacks / total_all, 4),
        "avg_time_sec": round(elapsed / total_all, 2),
    }


def run_single_dataset(name: str, ds_config: dict, mode: str) -> dict | None:
    """运行单个数据集的 benchmark。

    返回:
        指标字典，或 None（数据集不可用时）
    """
    path = ds_config["path"]

    # 相对路径转绝对路径
    if not Path(path).is_absolute():
        path = str(PROJECT_ROOT / path)

    if not Path(path).exists():
        print(f"  [SKIP] 跳过 {name}：文件不存在 ({path})")
        return None

    province = ds_config.get("province", "北京2024")
    print(f"  运行 {name}（{mode}模式）...")

    try:
        # 尽量压低第三方模型库噪声，Benchmark输出聚焦指标结果。
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("TQDM_DISABLE", "1")
        import main as main_module
        # Benchmark 输出以汇总指标为主，避免全链路 INFO 日志刷屏。
        try:
            log_level = os.getenv("BENCHMARK_LOG_LEVEL", "WARNING").upper()
            main_module.logger.remove()
            main_module.logger.add(sys.stderr, level=log_level)
        except Exception:
            pass
        start = time.time()
        result = main_module.run(
            input_file=path,
            mode=mode,
            province=province,
            interactive=False,
        )
        elapsed = time.time() - start

        results = result.get("results", [])
        metrics = compute_metrics(results, elapsed)

        # 检查条目数是否在预期范围内
        expected = ds_config.get("expected_items_range", [0, 99999])
        if not (expected[0] <= metrics["total"] <= expected[1]):
            print(f"  [WARN] 条目数 {metrics['total']} 不在预期范围 {expected} 内")

        return metrics

    except Exception as e:
        print(f"  [FAIL] {name} 运行失败: {e}")
        return {"_failed": True, "error": str(e)}


def format_rate(rate: float) -> str:
    """格式化比率为百分比字符串"""
    return f"{rate * 100:.1f}%"


def print_metrics_table(all_metrics: dict[str, dict], mode: str):
    """打印指标汇总表"""
    print(f"\n{'='*80}")
    print(f"Benchmark 结果汇总（{mode}模式）")
    print(f"{'='*80}")
    print(f"{'数据集':<20s} {'总数':>5s} {'绿率':>7s} {'黄率':>7s} "
          f"{'红率':>7s} {'经验命中':>8s} {'降级率':>7s} {'均耗':>7s}")
    print("-" * 80)

    for name, m in all_metrics.items():
        if m is None:
            print(f"{name:<20s}  {'跳过（文件不存在）':>50s}")
            continue
        if m.get("_failed"):
            err = str(m.get("error", "unknown"))[:42]
            print(f"{name:<20s}  {'失败（'+err+'）':>50s}")
            continue
        skip = m.get('skip_measure', 0)
        skip_str = f"(-{skip})" if skip > 0 else ""
        print(f"{name:<20s} {m['total']:5d}{skip_str:<5s}"
              f"{format_rate(m['green_rate']):>7s} "
              f"{format_rate(m['yellow_rate']):>7s} "
              f"{format_rate(m['red_rate']):>7s} "
              f"{format_rate(m['exp_hit_rate']):>8s} "
              f"{format_rate(m['fallback_rate']):>7s} "
              f"{m['avg_time_sec']:6.2f}s")

    print("=" * 80)


def compare_with_baseline(all_metrics: dict[str, dict], baseline: dict):
    """与基线对比，输出差异报告"""
    baseline_data = baseline.get("datasets", {})
    tolerance = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(
        "regression_tolerance", {})
    green_tol = tolerance.get("green_rate", 0.02)
    red_tol = tolerance.get("red_rate", 0.03)

    print(f"\n{'='*80}")
    print("与基线对比")
    print(f"基线版本: {baseline.get('version', '未知')}")
    print(f"基线日期: {baseline.get('date', '未知')}")
    print(f"基线模式: {baseline.get('mode', '未知')}")
    print(f"{'='*80}")

    has_regression = False

    for name, current in all_metrics.items():
        if current is None:
            continue
        if current.get("_failed"):
            has_regression = True
            print(f"\n{name}: 运行失败，无法与基线对比 -> {current.get('error', 'unknown')}")
            continue

        base = baseline_data.get(name)
        if base is None:
            print(f"\n{name}: 基线中无此数据集（新增）")
            continue

        print(f"\n{name}:")

        # 绿率对比
        g_diff = current["green_rate"] - base["green_rate"]
        g_arrow = "↑" if g_diff > 0 else ("↓" if g_diff < 0 else "→")
        g_status = "退化!" if g_diff < -green_tol else "正常"
        if g_diff < -green_tol:
            has_regression = True
        print(f"  绿率: {format_rate(base['green_rate'])} → "
              f"{format_rate(current['green_rate'])} "
              f"({g_arrow}{abs(g_diff)*100:.1f}pp) [{g_status}]")

        # 红率对比
        r_diff = current["red_rate"] - base["red_rate"]
        r_arrow = "↑" if r_diff > 0 else ("↓" if r_diff < 0 else "→")
        r_status = "退化!" if r_diff > red_tol else "正常"
        if r_diff > red_tol:
            has_regression = True
        print(f"  红率: {format_rate(base['red_rate'])} → "
              f"{format_rate(current['red_rate'])} "
              f"({r_arrow}{abs(r_diff)*100:.1f}pp) [{r_status}]")

        # 经验命中率对比
        e_diff = current["exp_hit_rate"] - base["exp_hit_rate"]
        e_arrow = "↑" if e_diff > 0 else ("↓" if e_diff < 0 else "→")
        print(f"  经验命中: {format_rate(base['exp_hit_rate'])} → "
              f"{format_rate(current['exp_hit_rate'])} ({e_arrow}{abs(e_diff)*100:.1f}pp)")

    print(f"\n{'='*80}")
    if has_regression:
        print("[WARN] 检测到退化！请检查最近的改动。")
    else:
        print("[OK] 无退化，所有指标在允许范围内。")
    print("=" * 80)

    return not has_regression


def save_baseline(all_metrics: dict[str, dict], mode: str, note: str = ""):
    """保存基线到 JSON 文件，同时追加到历史记录"""
    baseline = {
        "version": "L2-a_baseline",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "datasets": {
            name: metrics
            for name, metrics in all_metrics.items()
            if metrics is not None and not metrics.get("_failed")
        },
    }

    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n[OK] 基线已保存到 {BASELINE_PATH}")
    print(f"  包含 {len(baseline['datasets'])} 个数据集的指标")

    # 追加到历史记录
    _append_history(baseline, note)


def _append_history(baseline: dict, note: str = ""):
    """将本次跑分结果追加到历史记录文件

    历史记录是一个JSON数组，每条记录一次跑分，
    用于在前端展示算法改动的好坏趋势。
    """
    # 读取已有历史
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []

    # 构造历史条目（在baseline基础上加note字段）
    entry = {**baseline, "note": note}
    history.append(entry)

    # 写回文件
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[OK] 历史记录已追加（共 {len(history)} 条）: {HISTORY_PATH}")


def show_baseline():
    """显示当前基线内容"""
    baseline = load_baseline()
    if baseline is None:
        print("基线文件不存在。请先运行: python tools/run_benchmark.py --mode search --save")
        return

    print(f"版本: {baseline.get('version', '未知')}")
    print(f"日期: {baseline.get('date', '未知')}")
    print(f"模式: {baseline.get('mode', '未知')}")
    print(f"数据集: {len(baseline.get('datasets', {}))} 个")

    for name, m in baseline.get("datasets", {}).items():
        print(f"\n  {name}:")
        print(f"    总数={m['total']}, 绿率={format_rate(m['green_rate'])}, "
              f"红率={format_rate(m['red_rate'])}, "
              f"经验命中={format_rate(m['exp_hit_rate'])}")


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis Benchmark 运行与基线管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 采集基线（search模式，免费快速）
  python tools/run_benchmark.py --mode search --save

  # 与基线对比
  python tools/run_benchmark.py --mode search --compare

  # 只跑指定数据集
  python tools/run_benchmark.py --dataset B2_华佑电气 --save

  # 查看当前基线
  python tools/run_benchmark.py --show-baseline
""")
    parser.add_argument(
        "--mode", choices=["search", "agent"], default="search",
        help="匹配模式（默认search，免费快速）")
    parser.add_argument(
        "--dataset", default="all",
        help="指定数据集名称（默认all=全部）")
    parser.add_argument(
        "--save", action="store_true",
        help="将结果保存为新基线")
    parser.add_argument(
        "--compare", action="store_true",
        help="与已有基线对比")
    parser.add_argument(
        "--show-baseline", action="store_true",
        help="显示当前基线内容")
    parser.add_argument(
        "--note", default="",
        help="跑分备注（说明本次改动了什么，例如 '优化参数排序'）")

    args = parser.parse_args()

    # 显示基线模式
    if args.show_baseline:
        show_baseline()
        return 0

    # 加载配置
    config = load_config()
    datasets = config["datasets"]

    # 筛选数据集
    if args.dataset != "all":
        if args.dataset not in datasets:
            print(f"错误：未知数据集 '{args.dataset}'")
            print(f"可选: {', '.join(datasets.keys())}")
            return 1
        datasets = {args.dataset: datasets[args.dataset]}

    # 运行 benchmark
    print(f"开始 Benchmark（{args.mode}模式，{len(datasets)}个数据集）")
    print("-" * 60)

    all_metrics = {}
    for name, ds_config in datasets.items():
        metrics = run_single_dataset(name, ds_config, args.mode)
        all_metrics[name] = metrics

    non_skipped = [m for m in all_metrics.values() if m is not None]
    if non_skipped and all(m.get("_failed") for m in non_skipped):
        print("\n[FAIL] 所有可运行数据集均执行失败，终止。")
        return 1
    failed_datasets = [name for name, m in all_metrics.items() if m is not None and m.get("_failed")]

    # 打印结果表
    print_metrics_table(all_metrics, args.mode)

    # 对比基线
    if args.compare:
        baseline = load_baseline()
        if baseline is None:
            print("\n[WARN] 基线文件不存在，无法对比。")
            print("请先运行: python tools/run_benchmark.py --mode search --save")
        else:
            ok = compare_with_baseline(all_metrics, baseline)
            if not ok:
                return 1

    # 保存基线
    if args.save:
        if failed_datasets:
            print(f"\n[FAIL] 存在失败数据集，不保存基线: {', '.join(failed_datasets)}")
            return 1
        save_baseline(all_metrics, args.mode, note=args.note)

    if failed_datasets:
        print(f"\n[FAIL] 存在失败数据集: {', '.join(failed_datasets)}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
